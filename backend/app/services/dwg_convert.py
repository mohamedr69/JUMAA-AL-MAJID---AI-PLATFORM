r"""DWG to DXF, so a drawing can be read.

A DWG is Autodesk's own format and no Python library reads it. The
company's drawings arrive as DWG, so the platform converts them with
whatever converter the PC already has:

* **AutoCAD's core console** (`accoreconsole.exe`, installed with AutoCAD
  and with the free DWG TrueView): the drawing is opened headlessly and
  written out with DXFOUT. This is the one the engineers' PCs have.
* **The ODA File Converter** (free, from opendesign.com), which converts a
  whole folder at once.

Whichever is found is used; `DWG_CONVERTER` in `backend/.env` names one
explicitly. Nothing is converted silently: when no converter is there the
caller is told which file could not be read and what to install, rather
than being handed a schedule with a drawing missing from it.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings

# Where the converters install themselves. AutoCAD's console is tried
# before DWG TrueView's: the viewer's console cannot always write a DXF.
_ACCORE = "accoreconsole.exe"
_ODA = "ODAFileConverter.exe"
_AUTODESK = (r"C:\Program Files\Autodesk", r"C:\Program Files (x86)\Autodesk")
_ODA_ROOTS = (r"C:\Program Files\ODA", r"C:\Program Files (x86)\ODA")
# The DXF the ODA converter writes: a version every reader accepts.
_VERSION = "ACAD2018"
_TIMEOUT = 300


class ConversionError(RuntimeError):
    """A DWG that could not be turned into a DXF, and why."""


@dataclass(frozen=True)
class Converter:
    """A converter this PC has: its program and how it is driven."""

    kind: str          # "accoreconsole" or "oda"
    path: Path

    @property
    def name(self) -> str:
        return "AutoCAD core console" if self.kind == "accoreconsole" else "ODA File Converter"


def _autocad_consoles() -> list[Path]:
    """Every accoreconsole on this PC, AutoCAD before DWG TrueView and the
    newest release first."""
    found: list[Path] = []
    for root in _AUTODESK:
        folder = Path(root)
        if not folder.is_dir():
            continue
        for product in sorted(folder.iterdir(), reverse=True):
            console = product / _ACCORE
            if console.is_file():
                found.append(console)
    found.sort(key=lambda path: ("trueview" in path.parent.name.lower(), path.parent.name), reverse=False)
    return found


def converter() -> Converter | None:
    """The converter this server will use, or None."""
    settings = get_settings()
    configured = (settings.dwg_converter or "").strip()
    if configured:
        path = Path(configured)
        if path.is_dir():
            for name, kind in ((_ACCORE, "accoreconsole"), (_ODA, "oda")):
                if (path / name).is_file():
                    return Converter(kind, path / name)
            return None
        if path.is_file():
            return Converter("accoreconsole" if path.name.lower() == _ACCORE.lower() else "oda", path)
        return None

    for console in _autocad_consoles():
        return Converter("accoreconsole", console)
    on_path = shutil.which(_ACCORE)
    if on_path:
        return Converter("accoreconsole", Path(on_path))
    for root in _ODA_ROOTS:
        folder = Path(root)
        if folder.is_dir():
            for candidate in sorted(folder.glob(f"*/{_ODA}"), reverse=True):
                return Converter("oda", candidate)
    found = shutil.which(_ODA) or shutil.which("ODAFileConverter")
    if found:
        return Converter("oda", Path(found))
    return None


def unavailable() -> str | None:
    """Why a DWG cannot be converted here, or None when one can."""
    if converter() is not None:
        return None
    return ("No DWG converter was found on this server, so a DWG cannot be read. Install AutoCAD or the free DWG "
            "TrueView (either brings accoreconsole.exe), or the ODA File Converter -- or set DWG_CONVERTER in "
            "backend/.env to it. A DXF needs no converter.")


def _run(command: list[str]) -> None:
    try:
        subprocess.run(command, check=False, timeout=_TIMEOUT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise ConversionError(f"The converter did not finish within {_TIMEOUT} seconds.") from exc
    except OSError as exc:
        raise ConversionError(f"The converter could not be started ({exc}).") from exc


def _with_accoreconsole(exe: Path, source: Path, out_dir: Path) -> Path:
    """Open the drawing headlessly and write it out as a DXF.

    The script is AutoLISP rather than a plain DXFOUT script because the
    command's prompts differ between releases, and the path is written
    with forward slashes: a backslash inside a LISP string is an escape,
    which is how the first attempt lost half the path.
    """
    target = out_dir / f"{source.stem}.dxf"
    script = out_dir / "convert.scr"
    script.write_text(
        f'(command "_.DXFOUT" "{target.as_posix()}" "16")\n(princ)\n',
        encoding="utf-8",
    )
    _run([str(exe), "/i", str(source), "/s", str(script)])
    script.unlink(missing_ok=True)
    if not target.is_file():
        raise ConversionError(
            f"{source.name}: {exe.parent.name} opened the drawing but wrote no DXF. Open it in AutoCAD and save a "
            "DXF, or install the ODA File Converter."
        )
    return target


def _with_oda(exe: Path, source: Path, out_dir: Path) -> Path:
    """The ODA converter works on folders: the drawing is staged in one of
    its own so nothing else is converted with it."""
    staged = Path(tempfile.mkdtemp(prefix="dwg-"))
    shutil.copy2(source, staged / source.name)
    try:
        _run([str(exe), str(staged), str(out_dir), _VERSION, "DXF", "0", "1", source.name])
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    target = out_dir / f"{source.stem}.dxf"
    if target.is_file():
        return target
    written = sorted(out_dir.glob("*.dxf"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not written:
        raise ConversionError(f"{source.name}: the ODA File Converter wrote no DXF.")
    return written[0]


def to_dxf(path: Path, out_dir: Path | None = None) -> Path:
    """The DXF of a DWG. A file that is already a DXF is returned as it is."""
    if path.suffix.lower() == ".dxf":
        return path
    tool = converter()
    if tool is None:
        raise ConversionError(unavailable() or "No DWG converter is available.")
    out_dir = Path(out_dir or tempfile.mkdtemp(prefix="dxf-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    if tool.kind == "accoreconsole":
        return _with_accoreconsole(tool.path, path, out_dir)
    return _with_oda(tool.path, path, out_dir)


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
