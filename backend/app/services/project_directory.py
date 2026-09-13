"""Read-only indexes of files stored in a project's archive folder."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re


DRAWING_SUFFIXES = {".pdf", ".dwg", ".dxf", ".zip"}
SYSTEM_CODE_RE = re.compile(r"(?<![A-Z0-9])(?:FAS|EML|ELS|VES|CBS|FT|PAVA|PA|VA)(?![A-Z0-9])", re.IGNORECASE)
DRAWING_WORD_RE = re.compile(r"drawing|layout|shop[ _-]?drawing|as[ _-]?built|ifc", re.IGNORECASE)


@dataclass(frozen=True)
class ProjectDrawing:
    system_code: str | None
    name: str
    path: str
    modified: datetime


def find_drawings(root: Path, systems: set[str], *, material: bool = False, sample: bool = False) -> tuple[list[ProjectDrawing], list[str]]:
    """Find likely drawing files without changing the project or database."""
    drawings: list[ProjectDrawing] = []
    warnings: list[str] = []
    codes = set(systems) | {"FAS", "FA", "EML", "ELS", "VE", "VES", "CBS", "FT", "FRC", "PAVA", "PA", "VA", "VAS", "Fire Alarm", "Voice Evacuation", "Fire Telephone", "Emergency Light Monitoring", "Monitored Self Contained", "Public Address"}
    code_pattern = re.compile(r"(?<![A-Z0-9])(?:" + "|".join(re.escape(code) for code in sorted(codes, key=len, reverse=True)) + r")(?![A-Z0-9])", re.IGNORECASE)
    word_pattern = re.compile(r"material[ _-]*submittals?|submittals?|(?<![a-z])MAS(?![a-z])", re.IGNORECASE) if material else DRAWING_WORD_RE
    if sample:
        word_pattern = re.compile(r"samples?", re.IGNORECASE)
    suffixes = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip"} if material or sample else DRAWING_SUFFIXES
    try:
        entries = root.rglob("*")
        for path in entries:
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            relative = path.relative_to(root)
            searchable = str(relative).replace("\\", "/")
            if not sample and re.search(r"samples?", searchable, re.IGNORECASE):
                continue
            if not word_pattern.search(searchable) and (material or sample or path.suffix.lower() not in {".dwg", ".dxf"}):
                continue
            match = code_pattern.search(searchable.upper())
            code = match.group(0).upper() if match else None
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            drawings.append(ProjectDrawing(system_code=code, name=path.name, path=searchable, modified=modified))
    except OSError as exc:
        warnings.append(f"Could not read the project directory: {exc}")
    drawings.sort(key=lambda item: (item.system_code or "~", item.path.lower()))
    return drawings, warnings
