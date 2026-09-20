"""DWG -> DXF conversion, so a DWG can go through the same extraction.

Converters, best first:
  1. AutoCAD Core Console (accoreconsole.exe): AutoCAD itself, no window.
     Converting FA-105 this way gives a DXF whose symbols and counts are
     identical to one saved from AutoCAD by hand. Found under
     C:\\Program Files\\Autodesk\\AutoCAD <year>\\, newest first, or set
     BOQ_ACCORECONSOLE to its full path.
  2. ODA File Converter (free, opendesign.com), through ezdxf's odafc add-on,
     for PCs without AutoCAD.

The DWG is copied to a temporary folder under a plain ASCII name first:
AutoCAD's script parser and long paths with spaces do not mix.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

TIMEOUT_S = 300
DXF_VERSION = "2018"
_lock = threading.Lock()  # one conversion at a time: each Core Console is ~250 MB


class ConversionError(Exception):
    pass


@dataclass
class Converter:
    name: str
    kind: str  # "accoreconsole" | "oda"
    path: str


@dataclass
class ConversionResult:
    converter: str
    seconds: float
    log_tail: str


def _year(p: str) -> int:
    m = re.search(r"AutoCAD (\d{4})", p)
    return int(m.group(1)) if m else 0


def find_converter() -> Converter | None:
    # The platform's setting (ACCORECONSOLE_PATH), or the original tool's variable.
    from app.core.config import get_settings

    env = get_settings().accoreconsole_path or os.environ.get("BOQ_ACCORECONSOLE")
    if env and Path(env).is_file():
        return Converter("AutoCAD Core Console", "accoreconsole", env)
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", r"C:\Program Files")]
    found: set[str] = set()
    for root in roots:
        # AutoCAD and its verticals (AutoCAD MEP, Electrical, ...) all ship the Core Console
        found.update(glob.glob(os.path.join(root, "Autodesk", "AutoCAD*", "accoreconsole.exe")))
    if found:
        best = max(found, key=lambda p: (_year(p), p))
        label = Path(best).parent.name  # e.g. "AutoCAD 2027"
        return Converter(f"{label} Core Console", "accoreconsole", best)
    try:
        from ezdxf.addons import odafc

        if odafc.is_installed():
            return Converter("ODA File Converter", "oda", "odafc")
    except Exception:
        pass
    return None


def _decode(raw: bytes) -> str:
    # The Core Console writes UTF-16 when its output is redirected
    if raw[:200].count(b"\x00") > 20:
        return raw.decode("utf-16-le", errors="ignore")
    return raw.decode("utf-8", errors="ignore")


def _tail(text: str, n: int = 15) -> str:
    lines = [l for l in text.splitlines() if l.strip() and "CoreHeartBeat" not in l]
    return "\n".join(lines[-n:])


def _with_accoreconsole(exe: str, dwg: Path, dxf: Path) -> str:
    script = dwg.parent / "to_dxf.scr"
    # CMDDIA/FILEDIA 0 keep every prompt on the command line. DXFOUT asks for
    # the file name, then accuracy/options: pick the version, then 16 places.
    # Plain LF line ends, written as bytes: with CRLF the Core Console reads
    # the CR as an extra Enter and every answer lands one prompt late.
    script.write_bytes(
        "\n".join([
            "_.FILEDIA", "0",
            "_.CMDDIA", "0",
            "_.DXFOUT", f'"{dxf}"', "_V", DXF_VERSION, "16",
            "_.QUIT", "_Y", "",
        ]).encode("utf-8")
    )
    try:
        p = subprocess.run(
            [exe, "/i", str(dwg), "/s", str(script), "/l", "en-US"],
            capture_output=True,
            timeout=TIMEOUT_S,
            cwd=str(dwg.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        raise ConversionError(f"AutoCAD did not finish within {TIMEOUT_S} s. The DWG may be very large or damaged.")
    return _decode(p.stdout) + _decode(p.stderr)


def _with_oda(dwg: Path, dxf: Path) -> str:
    from ezdxf.addons import odafc

    odafc.convert(str(dwg), str(dxf), version="R2018", replace=True)
    return ""


def convert_dwg_to_dxf(dwg_path: str | Path, dxf_path: str | Path, converter: Converter | None = None) -> ConversionResult:
    conv = converter or find_converter()
    if conv is None:
        raise ConversionError(
            "No DWG converter on this PC. Install AutoCAD, or the free ODA File Converter "
            "(opendesign.com/guestfiles/oda_file_converter), or upload a DXF instead."
        )
    dwg_path, dxf_path = Path(dwg_path), Path(dxf_path)
    t0 = time.time()
    with _lock, tempfile.TemporaryDirectory(prefix="boq-dwg-") as tmp:
        work = Path(tmp)
        src = work / "drawing.dwg"
        out = work / "drawing.dxf"
        shutil.copyfile(dwg_path, src)
        if conv.kind == "accoreconsole":
            log = _with_accoreconsole(conv.path, src, out)
        else:
            log = _with_oda(src, out)
        if not out.exists() or out.stat().st_size == 0:
            raise ConversionError(f"{conv.name} did not produce a DXF.\n{_tail(log)}".strip())
        shutil.move(str(out), dxf_path)
    return ConversionResult(converter=conv.name, seconds=time.time() - t0, log_tail=_tail(log))
