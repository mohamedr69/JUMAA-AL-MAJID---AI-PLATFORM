"""How far an IFC drawing's read has got, and how long it has left.

A read is a few stages, and two of them say nothing while they run:
AutoCAD's Core Console converting the DWG, and ezdxf opening the DXF. So
each stage has an estimate, from the file's size and the rates measured on
the reads before it, and a ticker turns the time spent into a percentage
and the seconds left -- a stage that runs past its estimate stretches it,
so the bar slows down near the end of a stage rather than sitting at 100%.
The walk over the symbols and the architecture's outline report their own
fraction, which replaces the estimate for that stage.

Once the DXF is open the extractor counts what the walk and the
architecture will cost in drawn pieces (`plan`), and those stages are
re-estimated from it: a drawing with the whole Revit model in it spends
minutes on the architecture's outline, one with a plain floor plan a
second. An estimate that grows never takes the bar backwards: the
percentage holds where it is and the rest is spread over the time left.

Rates start from what was measured on EP-30880's 4.4 MB DWG (converted in
about 4 s, a 28 MB DXF opened at about 3 MB/s, 410,000 pieces walked in
11 s, a 406,000-piece architecture outlined in 108 s) and follow each
finished read, so they settle to the PC the platform runs on.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

# Learned rates (a moving average over finished reads), kept for the server's life.
_RATES = {
    "convert_base_s": 3.0,     # the Core Console starting and stopping
    "convert_s_per_mb": 0.4,   # of DWG
    "dxf_mb_per_dwg_mb": 6.4,  # a DWG's DXF is this much bigger
    "read_mb_per_s": 3.0,      # of DXF, opening it
    "walk_share": 0.25,        # the walk, as a share of the opening time
    "finish_share": 1.0,       # the architecture's outline, likewise -- until the pieces are counted
    "walk_s_per_piece": 2.7e-5,
    "finish_s_per_piece": 2.7e-4,
}
_LOCK = threading.Lock()
_SMOOTH = 0.35

STAGES = {
    "save": "Saving the drawing",
    "convert": "Converting the DWG to DXF with {converter}",
    "read": "Opening the drawing",
    "walk": "Reading the symbols: blocks, and symbols drawn without one",
    "finish": "Finding the sheets, floors and architecture",
    "classify": "Identifying the symbols",
    "file": "Saving the drawing and filing it in the project folder",
}
# What the "classify" stage is doing at a given moment (app.ifc.services.classification).
SUBSTAGES = {
    "matching_symbols": "Matching the symbols against the library",
    "deterministic_review": "Checking the symbols' letters and block names",
    "ai_review_metadata": "AI reviewing the symbols nothing else identified",
    "ai_review_visual": "AI looking at the symbols it was not sure of",
}


def _learn(key: str, value: float) -> None:
    if value <= 0:
        return
    with _LOCK:
        _RATES[key] = (1 - _SMOOTH) * _RATES[key] + _SMOOTH * value


@dataclass
class ReadTimer:
    """Stage estimates for one read, and the ticker that reports them."""

    report: callable                      # report(percent, message, stage, eta_seconds)
    converter: str = "the DWG converter"
    estimates: dict[str, float] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    done: dict[str, float] = field(default_factory=dict)      # finished stages: seconds taken
    stage: str | None = None
    stage_started: float = 0.0
    fraction: float | None = None                             # a stage's own report of how far it is
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    error: BaseException | None = None
    pieces: dict[str, int] = field(default_factory=dict)       # what the extractor counted
    _shown: int = 0                                            # the last percentage reported
    _anchor: tuple[int, float] | None = None                   # (percent, seconds left) when an estimate grew
    substage: str | None = None                                # what the classify stage is doing

    @classmethod
    def for_upload(cls, report, *, is_dwg: bool, size_mb: float, converter: str | None) -> "ReadTimer":
        with _LOCK:
            r = dict(_RATES)
        dxf_mb = size_mb * r["dxf_mb_per_dwg_mb"] if is_dwg else size_mb
        read = max(dxf_mb / r["read_mb_per_s"], 0.5)
        timer = cls(report=report, converter=converter or "the DWG converter")
        timer.order = ["save"] + (["convert"] if is_dwg else []) + ["read", "walk", "finish", "classify", "file"]
        timer.estimates = {"save": 0.3, "read": read, "walk": max(read * r["walk_share"], 0.5),
                           "finish": max(read * r["finish_share"], 0.8), "classify": 1.0, "file": 0.4}
        if is_dwg:
            timer.estimates["convert"] = r["convert_base_s"] + size_mb * r["convert_s_per_mb"]
        return timer

    # --- the stages ---------------------------------------------------------------------

    def begin(self, stage: str) -> None:
        now = time.monotonic()
        if self.stage is not None and self.stage not in self.done:
            self.done[self.stage] = now - self.stage_started
        self.stage, self.stage_started, self.fraction, self.substage = stage, now, None, None
        self.tick()

    def sub(self, substage: str, fraction: float | None = None) -> None:
        """What the current stage is doing now (the classify stage's steps)."""
        self.substage = substage
        if fraction is not None:
            self.at(fraction)
        self.tick()

    def at(self, fraction: float) -> None:
        """The current stage says how far it is (the walk over the symbols)."""
        self.fraction = min(max(fraction, 0.0), 1.0)

    def dxf_size(self, size_mb: float) -> None:
        """The DXF is known once the DWG is converted: the opening is re-estimated from it."""
        with _LOCK:
            read = max(size_mb / _RATES["read_mb_per_s"], 0.5)
            walk = max(read * _RATES["walk_share"], 0.5)
            finish = max(read * _RATES["finish_share"], 0.8)
        self.estimates["read"], self.estimates["walk"], self.estimates["finish"] = read, walk, finish

    def plan(self, pieces: dict[str, int]) -> None:
        """The extractor's count of what a stage will cost: its estimate follows."""
        with _LOCK:
            for stage, n in pieces.items():
                self.pieces[stage] = n
                if stage in ("walk", "finish"):
                    self.estimates[stage] = max(n * _RATES[f"{stage}_s_per_piece"], 0.5 if stage == "walk" else 0.8)

    # --- percent and time left ----------------------------------------------------------------

    def _spent_and_left(self) -> tuple[float, float]:
        now = time.monotonic()
        spent = sum(self.done.values())
        left = 0.0
        for stage in self.order:
            if stage in self.done:
                continue
            estimate = self.estimates.get(stage, 0.5)
            if stage == self.stage:
                elapsed = now - self.stage_started
                if self.fraction is not None and self.fraction > 0.02:
                    # The pace measured so far, trusted more the further the stage is.
                    f = self.fraction
                    measured = elapsed * (1 - f) / f
                    remaining = f * measured + (1 - f) * max(estimate - elapsed, estimate * (1 - f))
                else:
                    # Past its estimate, a stage is taken to need a little more than it has had.
                    remaining = max(estimate - elapsed, elapsed * 0.15, 0.2)
                spent += elapsed
                left += remaining
            else:
                left += estimate
        return spent, left

    def tick(self) -> None:
        spent, left = self._spent_and_left()
        percent = 100 * spent / max(spent + left, 1e-6)
        if self._anchor is not None or percent < self._shown:
            # An estimate grew: hold the bar and spread what is left over the time left.
            if self._anchor is None or left > self._anchor[1]:
                self._anchor = (self._shown, max(left, 1e-6))
            held, left_then = self._anchor
            percent = held + (100 - held) * (1 - left / left_then)
        percent = int(min(99, max(percent, self._shown)))
        self._shown = percent
        stage = self.stage or "save"
        message = STAGES.get(stage, stage).format(converter=self.converter)
        if self.substage:
            message = SUBSTAGES.get(self.substage, message)
        self.report(percent, message, self.substage or stage, round(left))

    def start(self, every: float = 0.5) -> "ReadTimer":
        def loop():
            while not self._stop.wait(every):
                try:
                    self.tick()
                except BaseException as exc:  # noqa: BLE001 -- a stop asked for, or a lost write: the read goes on
                    self.error = exc
                    return
        self._thread = threading.Thread(target=loop, name="ifc-read-progress", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    # --- learning from a finished read ----------------------------------------------------------

    def learned(self, *, dwg_mb: float | None, dxf_mb: float) -> None:
        if dwg_mb and "convert" in self.done:
            _learn("convert_s_per_mb", max(self.done["convert"] - _RATES["convert_base_s"], 0.1) / max(dwg_mb, 0.1))
            _learn("dxf_mb_per_dwg_mb", dxf_mb / max(dwg_mb, 0.01))
        if self.done.get("read", 0) > 0.2:
            _learn("read_mb_per_s", dxf_mb / self.done["read"])
            if self.done.get("walk", 0) > 0:
                _learn("walk_share", self.done["walk"] / self.done["read"])
            if self.done.get("finish", 0) > 0:
                _learn("finish_share", self.done["finish"] / self.done["read"])
        for stage in ("walk", "finish"):
            if self.pieces.get(stage, 0) >= 20000 and self.done.get(stage, 0) > 0.5:
                _learn(f"{stage}_s_per_piece", self.done[stage] / self.pieces[stage])
