r"""What a symbol on a drawing actually is.

A block name is what a draughtsman typed; it is not evidence. The same
smoke detector arrives as SD, SD01, SMOKE, FAS_DEVICE or BLOCK_123, and
on one real drawing the exit signs are anonymous blocks called *U29. The
geometry inside the block, on the other hand, is the symbol itself:

    SMOKE DETECTOR              1 circle  + text "S"
    SD WITH SOUNDER             2 circles + text "S"      (the sounder base)
    HEAT DETECTOR               1 circle  + text "H"
    MANUAL CALL POINT           2 circles + 4 lines
    FIRE ALARM BELL             5 lines   + 1 arc

-- read off the block definitions of one real layout. So a symbol is
recognised from several kinds of evidence, each weighted by how much it
deserves to be believed (`WEIGHTS`), and the block name deserves very
little:

    geometry      40   the fingerprint of the block's own entities
    visual        30   a classifier's reading of the rendered symbol (not built yet)
    legend        15   what this drawing's own legend calls the symbol
    attributes     5   what the block carries (TYP=EXIT)
    nearby text    5   what is written beside it on the plan (SD-01)
    block name     5   what it is called
    layer          5   what layer it sits on

The sources vote for a device; the confidence is the share of the
available evidence that agrees. Sources that cannot speak -- there is no
legend, there is no visual classifier -- are left out of the total rather
than counted against it, and the page says which spoke.

Two further things follow from doing it this way:

**Only unique symbols are classified.** A tower carries five thousand
devices and perhaps twenty symbols; the work is per symbol, and every
instance of it is then counted by arithmetic.

**The platform learns.** A drawing's legend is a dictionary for that
drawing: symbol, then what it is. What a legend teaches is kept
(`device_symbols`), so the next project recognises the same geometry with
no legend at all.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
from collections import Counter
from typing import Iterable

# --- how much each kind of evidence is worth ---------------------------------------------

WEIGHTS: dict[str, int] = {
    "geometry": 40,
    "visual": 30,
    "legend": 15,
    "attributes": 5,
    "nearby_text": 5,
    "block_name": 5,
    "layer": 5,
}

# Confidence is the share of the evidence that agreed -- but a symbol
# recognised only by what it is called has not really been recognised, and
# 5 out of 5 is not certainty. The share is therefore taken against at
# least this much evidence, which is what the geometry and the legend
# together are worth: the weak sources alone cannot reach a high score,
# and the engineer is asked.
MIN_EVIDENCE = 50

# What is done with a symbol at each confidence.
ACCEPTED, FLAGGED, REVIEW, UNRESOLVED = "accepted", "accepted_flagged", "review", "unresolved"


def band(confidence: float) -> str:
    """What a confidence means for the engineer."""
    if confidence >= 95:
        return ACCEPTED
    if confidence >= 80:
        return FLAGGED
    if confidence >= 60:
        return REVIEW
    return UNRESOLVED


# --- the geometry of a symbol --------------------------------------------------------------

# The letters a detector carries inside its circle, which is what tells a
# smoke detector from a heat detector when the shape is the same.
_LABEL_RE = re.compile(r"[A-Z0-9/+-]{1,6}$")


@dataclasses.dataclass(frozen=True)
class Geometry:
    """A block's own drawing, as something that can be compared.

    `fingerprint` is the exact shape -- the counts, the letters inside and
    the proportions -- and `shape` the coarse one, which survives a
    draughtsman redrawing the same symbol slightly differently.
    """

    fingerprint: str
    shape: str
    features: dict

    @property
    def empty(self) -> bool:
        return not self.features.get("entities")


def _bounds(points: list[tuple[float, float]]) -> tuple[float, float]:
    if not points:
        return 0.0, 0.0
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return max(xs) - min(xs), max(ys) - min(ys)


def geometry_of(block) -> Geometry:
    """The fingerprint of a block definition: what it is made of, the
    letters written in it, and its proportions -- normalised, so the same
    symbol drawn at another scale is the same symbol."""
    counts: Counter[str] = Counter()
    radii: list[float] = []
    labels: list[str] = []
    points: list[tuple[float, float]] = []
    for entity in block:
        kind = entity.dxftype()
        counts[kind] += 1
        try:
            if kind == "CIRCLE":
                radii.append(float(entity.dxf.radius))
                centre = entity.dxf.center
                points.append((float(centre.x), float(centre.y)))
            elif kind == "LINE":
                points.append((float(entity.dxf.start.x), float(entity.dxf.start.y)))
                points.append((float(entity.dxf.end.x), float(entity.dxf.end.y)))
            elif kind == "ARC":
                centre = entity.dxf.center
                points.append((float(centre.x), float(centre.y)))
                radii.append(float(entity.dxf.radius))
            elif kind == "LWPOLYLINE":
                for point in entity.get_points("xy"):
                    points.append((float(point[0]), float(point[1])))
            elif kind in ("TEXT", "ATTDEF"):
                text = str(getattr(entity.dxf, "text", "") or getattr(entity.dxf, "tag", "")).strip().upper()
                if text and _LABEL_RE.match(text):
                    labels.append(text)
        except Exception:  # noqa: BLE001 -- an entity whose geometry cannot be read adds nothing
            continue

    width, height = _bounds(points)
    largest = max(radii) if radii else 0.0
    features = {
        "entities": sum(counts.values()),
        "circles": counts.get("CIRCLE", 0),
        "lines": counts.get("LINE", 0),
        "arcs": counts.get("ARC", 0),
        "polylines": counts.get("LWPOLYLINE", 0) + counts.get("POLYLINE", 0),
        "hatches": counts.get("HATCH", 0),
        "texts": counts.get("TEXT", 0) + counts.get("MTEXT", 0),
        "attributes": counts.get("ATTDEF", 0),
        "nested": counts.get("INSERT", 0),
        "labels": sorted(set(labels)),
        # The circles' sizes relative to the biggest: a sounder base is a
        # second circle around the detector, not a different detector.
        "radii": sorted(round(radius / largest, 2) for radius in radii) if largest else [],
        "aspect": round(width / height, 2) if width and height else 0.0,
    }
    coarse = {key: features[key] for key in ("circles", "lines", "arcs", "polylines", "texts")}
    coarse["labels"] = features["labels"]
    return Geometry(
        fingerprint=_digest(features),
        shape=_digest(coarse),
        features=features,
    )


def _digest(payload: dict) -> str:
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


# --- the drawing's own legend ----------------------------------------------------------------


@dataclasses.dataclass
class LegendEntry:
    """One row of a legend: the symbol, and what the drawing calls it."""

    block: str
    description: str
    fingerprint: str = ""


def legend_entries(inserts: Iterable, labels: Iterable, box, geometries: dict[str, Geometry]) -> list[LegendEntry]:
    """The legend read as a dictionary: for each symbol in the legend, the
    text written beside it.

    This is the drawing's own word for the symbol, which beats anything the
    platform knows in general -- consultants do not share one symbol set.
    """
    if box is None:
        return []
    inside = [insert for insert in inserts if box.holds(insert.x, insert.y)]
    if not inside:
        return []
    heights = [abs(a.y - b.y) for a, b in zip(sorted(inside, key=lambda i: -i.y), sorted(inside, key=lambda i: -i.y)[1:])]
    row = min((height for height in heights if height > 0), default=0.0) or 1.0
    found: list[LegendEntry] = []
    for insert in inside:
        beside = [
            label
            for label in labels
            if abs(label.y - insert.y) <= row * 0.6 and label.x > insert.x and len(label.text.strip()) > 2
        ]
        if not beside:
            continue
        nearest = min(beside, key=lambda label: label.x - insert.x)
        description = re.sub(r"\s+", " ", nearest.text).strip(" -:\t")
        if not description or len(description) > 80:
            continue
        found.append(LegendEntry(block=insert.block, description=description,
                                 fingerprint=geometries.get(insert.block, Geometry("", "", {})).fingerprint))
    return found


# --- what a description means -----------------------------------------------------------------

# The devices the company builds with, and the words a drawing calls them
# by. The order matters: a smoke detector with a sounder base is not a
# smoke detector, so the combinations come first.
DEVICE_RULES: tuple[tuple[str, str], ...] = (
    ("Smoke detector with sounder base", r"(SMOKE|SD).{0,20}(SOUNDER|SOUNDER BASE|AUDIBLE BASE|WITH SOUNDER)|SOUNDER BASE"),
    ("Duct smoke detector", r"DUCT|SIGA-?SD\b|\bSD-?T\d"),
    ("Multisensor detector", r"MULTI[- ]?SENSOR|MULTISENSOR|OSHD|3D\b"),
    ("Smoke detector", r"SMOKE|PHOTO|\bSD\b|OSD|SIGA-?PS\b"),
    ("Heat detector", r"\bHEAT\b|\bHD\b|HRS\b|HRD\b|THERMAL|RATE OF RISE"),
    ("Manual call point", r"MANUAL CALL|CALL[- ]?POINT|\bMCP\b|PULL[- ]?STATION|BREAK[- ]?GLASS|\bBGU\b|SIGA-?278"),
    ("Speaker/strobe", r"SPEAKER.{0,6}STROBE|SPK.{0,4}STR|SS70|757-\d+A-SS"),
    ("Horn/strobe", r"HORN.{0,6}STROBE|\bHS\b|757-\d+A-T|G1AV|WSTIA"),
    ("Strobe", r"STROBE|BEACON|FLASHER|XENON|202-\d"),
    ("Speaker", r"SPEAKER|\bSPK\b|S186|G4S"),
    ("Sounder", r"SOUNDER|\bBELL\b|HOOTER|HORN|G1A"),
    ("Fire telephone", r"TELEPHONE|\bFT\b|6833|6830|TCS-\d"),
    ("Monitor module", r"MONITOR MODULE|INPUT MODULE|\bMM\b|SIGA-?CT|SIGA-?CC"),
    ("Control module", r"CONTROL MODULE|OUTPUT MODULE|RELAY|\bCM\b|SIGA-?CR|SIGA-?IO|SIGA-?UM"),
    ("Isolator", r"ISOLATOR|\bIB\b"),
    ("Door holder", r"DOOR[- ]?HOLD|MAGNET"),
    ("Repeater panel", r"REPEATER|ANNUNCIATOR|\bANN\b"),
    ("Control panel", r"\bFACP\b|CONTROL PANEL|\bEST4\b|FIRE ALARM PANEL"),
    ("Power supply", r"\bBPS\b|\bAPS\b|POWER SUPPLY|BOOSTER"),
    ("Exit light", r"\bEXIT\b"),
    ("Emergency light", r"EMERGENCY LIGHT|\bEMERGENCY\b|\bEM LIGHT\b"),
)


def device_in(text: str | None) -> str | None:
    """The device a piece of text names, or None."""
    if not text:
        return None
    for device, pattern in DEVICE_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return device
    return None


# --- putting the evidence together --------------------------------------------------------------


@dataclasses.dataclass
class Evidence:
    source: str
    device: str
    weight: int
    detail: str = ""

    def as_dict(self) -> dict:
        return {"source": self.source, "device": self.device, "weight": self.weight, "detail": self.detail}


@dataclasses.dataclass
class Classification:
    device: str
    confidence: float
    state: str
    evidence: list[Evidence] = dataclasses.field(default_factory=list)
    # The block name says one device and the evidence another: the sort of
    # thing an engineer must see.
    conflict: str | None = None

    @property
    def method(self) -> str:
        """Which kinds of evidence agreed, for the record."""
        return " + ".join(dict.fromkeys(e.source for e in self.evidence if e.device == self.device)) or "none"

    def as_dict(self) -> dict:
        return {
            "device": self.device,
            "confidence": round(self.confidence, 1),
            "state": self.state,
            "method": self.method,
            "conflict": self.conflict,
            "evidence": [e.as_dict() for e in self.evidence],
        }


def classify(
    *,
    block: str,
    layer: str = "",
    geometry: Geometry | None = None,
    legend: dict[str, str] | None = None,
    library: "SymbolLibrary | None" = None,
    attributes: dict[str, str] | None = None,
    nearby: str | None = None,
    visual: tuple[str, float] | None = None,
) -> Classification:
    """What this symbol is, and how sure the platform is.

    Every source that can speak votes for a device with its own weight;
    the confidence is the share of the evidence that agreed. A source that
    cannot speak is left out of the total, so a drawing with no legend is
    not punished for it -- the page shows what was used.
    """
    votes: list[Evidence] = []

    legend_text = (legend or {}).get(block.upper()) if legend else None
    legend_device = device_in(legend_text) or (legend_text.strip().title() if legend_text else None)
    if legend_device:
        votes.append(Evidence("legend", legend_device, WEIGHTS["legend"], legend_text or ""))

    known = library.match(geometry) if (library is not None and geometry is not None) else None
    if known:
        device, how = known
        votes.append(Evidence("geometry", device, WEIGHTS["geometry"], how))

    attribute_text = " ".join(f"{key} {value}" for key, value in (attributes or {}).items())
    attribute_device = device_in(attribute_text)
    if attribute_device:
        votes.append(Evidence("attributes", attribute_device, WEIGHTS["attributes"], attribute_text.strip()))

    nearby_device = device_in(nearby)
    if nearby_device:
        votes.append(Evidence("nearby_text", nearby_device, WEIGHTS["nearby_text"], (nearby or "").strip()[:60]))

    name_device = device_in(block)
    name_detail = block
    if not name_device and library is not None:
        # A name the platform was taught on an earlier project. It is still
        # only a name, and worth only what a name is worth.
        taught = library.by_block.get(block.upper())
        if taught:
            name_device, name_detail = taught, f"{block}, taught on an earlier project"
    if name_device:
        votes.append(Evidence("block_name", name_device, WEIGHTS["block_name"], name_detail))

    layer_device = device_in(layer)
    if layer_device:
        votes.append(Evidence("layer", layer_device, WEIGHTS["layer"], layer))

    if visual:
        device, share = visual
        votes.append(Evidence("visual", device, WEIGHTS["visual"], f"{share:.0f}% match"))

    if not votes:
        # Nothing could speak for it: the block's own name is all there is,
        # and it is reported as itself rather than guessed at.
        return Classification(device=block.strip() or "Unnamed symbol", confidence=0.0, state=UNRESOLVED)

    scores: dict[str, int] = {}
    for vote in votes:
        scores[vote.device] = scores.get(vote.device, 0) + vote.weight
    device = max(scores, key=lambda name: (scores[name], name))
    available = max(sum(vote.weight for vote in votes), MIN_EVIDENCE)
    confidence = 100.0 * scores[device] / available if available else 0.0

    conflict = None
    if name_device and name_device != device:
        conflict = f"The block is called {block!r}, which reads as {name_device}; the evidence says {device}."
    return Classification(device=device, confidence=confidence, state=band(confidence), evidence=votes, conflict=conflict)


# --- what the platform has learned ---------------------------------------------------------------


class SymbolLibrary:
    """Every symbol the platform has been taught, by geometry.

    Built from the `device_symbols` table (what legends taught on earlier
    projects, and what an engineer corrected), and used before any legend
    or name is read.
    """

    def __init__(self, rows: Iterable = ()):
        self.by_fingerprint: dict[str, str] = {}
        self.by_shape: dict[str, set[str]] = {}
        self.by_block: dict[str, str] = {}
        for row in rows:
            device = row.device
            for fingerprint in row.fingerprints or []:
                self.by_fingerprint.setdefault(fingerprint, device)
            for shape in row.shapes or []:
                self.by_shape.setdefault(shape, set()).add(device)
            for name in row.block_names or []:
                self.by_block.setdefault(str(name).upper(), device)

    def match(self, geometry: Geometry | None) -> tuple[str, str] | None:
        """(device, how it matched) -- the exact shape first, then the
        coarse one, and only when that names a single device."""
        if geometry is None or geometry.empty:
            return None
        exact = self.by_fingerprint.get(geometry.fingerprint)
        if exact:
            return exact, "the same symbol, drawn the same way"
        candidates = self.by_shape.get(geometry.shape) or set()
        if len(candidates) == 1:
            return next(iter(candidates)), "the same shape, drawn a little differently"
        return None

    def learn(self, device: str, *, geometry: Geometry | None, block: str | None) -> None:
        if geometry is not None and not geometry.empty:
            self.by_fingerprint.setdefault(geometry.fingerprint, device)
            self.by_shape.setdefault(geometry.shape, set()).add(device)
        if block:
            self.by_block.setdefault(block.upper(), device)


# --- the library as the database keeps it -------------------------------------------------------


def load_library(db) -> SymbolLibrary:
    """Every symbol the platform has been taught."""
    from app.models import DeviceSymbol

    return SymbolLibrary(db.query(DeviceSymbol).all())


def remember(db, taught: Iterable[dict], *, source: str = "legend", user_id: int | None = None) -> int:
    """Keep what a drawing (or an engineer) taught: the device, and the
    geometry and name the symbol was drawn and called by. A device already
    known gains the new spelling rather than replacing the old one.

    Returns how many rows were written or widened.
    """
    from app.models import DeviceSymbol

    changed = 0
    for entry in taught:
        device = (entry.get("device") or "").strip()
        if not device:
            continue
        row = db.query(DeviceSymbol).filter(DeviceSymbol.device == device).first()
        if row is None:
            row = DeviceSymbol(device=device, fingerprints=[], shapes=[], block_names=[], layers=[],
                               source=source, learned_from=entry.get("file"), created_by_id=user_id,
                               description=entry.get("description"))
            db.add(row)
            changed += 1
        was = (list(row.fingerprints or []), list(row.shapes or []), list(row.block_names or []))
        for field, value in (("fingerprints", entry.get("fingerprint")), ("shapes", entry.get("shape")),
                             ("block_names", (entry.get("block") or "").upper() or None)):
            if not value:
                continue
            values = list(getattr(row, field) or [])
            if value not in values:
                values.append(value)
                setattr(row, field, values)
        if (list(row.fingerprints or []), list(row.shapes or []), list(row.block_names or [])) != was:
            changed += 1
            if source == "engineer":
                row.source = source
    if changed:
        db.commit()
    return changed
