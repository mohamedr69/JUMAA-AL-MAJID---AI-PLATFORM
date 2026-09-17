r"""DWG to DXF, so a drawing can be read.

A DWG is Autodesk's own format and no Python library reads it. The
company's drawings arrive as DWG, so the platform converts them with a
converter installed beside it -- the ODA File Converter, which Open Design
Alliance gives away, is the one every office has -- and reads the DXF it
writes (app.services.floor_devices).

Nothing is converted silently: when no converter is configured the caller
is told which file could not be read and what to install, rather than
being handed a schedule with a drawing missing from it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import get_settings

# Where the ODA File Converter installs itself, newest first.
_SEARCH = (
    r"C:\Program Files\ODA",
    r"C:\Program Files (x86)\ODA",
)
_EXE = "ODAFileConverter.exe"
# The DXF the converter writes: a version every reader accepts.
_VERSION = "ACAD2018"
_TIMEOUT = 300


class ConversionError(RuntimeError):
    """A DWG that could not be turned into a DXF, and why."""


def converter() -> Path | None:
    """The DWG converter this server has, or None."""
    settings = get_settings()
    configured = (settings.dwg_converter or "").strip()
    if configured:
        path = Path(configured)
        if path.is_file():
            return path
        if path.is_dir() and (path / _EXE).is_file():
            return path / _EXE
        return None
    found = shutil.which(_EXE) or shutil.which("ODAFileConverter")
    if found:
        return Path(found)
    for root in _SEARCH:
        folder = Path(root)
        if not folder.is_dir():
            continue
        for candidate in sorted(folder.glob(f"*/{_EXE}"), reverse=True):
            return candidate
    return None


def unavailable() -> str | None:
    """Why a DWG cannot be converted here, or None when one can."""
    if converter() is not None:
        return None
    return ("No DWG converter is installed on this server, so a DWG cannot be read. Install the ODA File "
            "Converter (free, from opendesign.com) or set DWG_CONVERTER to it -- or hand in the DXF export "
            "of the drawing instead.")


def to_dxf(path: Path, out_dir: Path | None = None) -> Path:
    """The DXF of a DWG, converted beside it. A file that is already a DXF
    is returned as it is."""
    if path.suffix.lower() == ".dxf":
        return path
    reason = unavailable()
    if reason:
        raise ConversionError(reason)
    exe = converter()
    out_dir = Path(out_dir or tempfile.mkdtemp(prefix="dxf-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    # The converter works on folders, not files: the drawing is copied into
    # one of its own so nothing else is converted with it.
    staged = Path(tempfile.mkdtemp(prefix="dwg-"))
    source = staged / path.name
    shutil.copy2(path, source)
    command = [str(exe), str(staged), str(out_dir), _VERSION, "DXF", "0", "1", path.name]
    try:
        subprocess.run(command, check=False, timeout=_TIMEOUT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise ConversionError(f"{path.name}: the converter did not finish within {_TIMEOUT} seconds.") from exc
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    converted = out_dir / f"{path.stem}.dxf"
    if not converted.is_file():
        candidates = sorted(out_dir.glob("*.dxf"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            raise ConversionError(f"{path.name}: the converter wrote no DXF. Open the drawing and export a DXF instead.")
        converted = candidates[0]
    return converted


def ensure_dxf(paths: list[Path]) -> tuple[list[Path], dict[Path, str], list[str]]:
    """(the DXF of every drawing, the name each came from, what failed)."""
    ready: list[Path] = []
    names: dict[Path, str] = {}
    failed: list[str] = []
    for path in paths:
        try:
            dxf = to_dxf(path)
        except ConversionError as exc:
            failed.append(str(exc))
            continue
        ready.append(dxf)
        names[dxf] = path.name
    return ready, names, failed
