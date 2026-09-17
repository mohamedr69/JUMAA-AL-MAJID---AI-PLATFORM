r"""The project's folder structure on OneDrive, made when the project is opened.

Every project keeps the same folders under its archive path, so a document
controller finds a submittal or a drawing in the same place on every job,
and so the document index (app.services.document_sync) knows, from the
folder a file sits in, what it is and which system it belongs to:

    01- Scan\                              the DRF and the Design Sheets (only
                                            when no scan / commercial folder exists)
    02- Material Submittals\FA\R0\         one folder per system, a folder per revision
    02- Material Submittals\ELS\R0\
    02- Material Submittals\Approved\FA\   the stamped copies the consultant returns
    02- Material Submittals\Approved\ELS\
    03- Drawings\IFC\Electrical\{ACS, FA, Light, Power}\
    03- Drawings\IFC\Mechanical\{FF, SM}\
    03- Drawings\IFC\RCP\                 03- Drawings\IFC\Builder Work\
    03- Drawings\SD\{FA, ELS}\             the shop drawings we submit
    03- Drawings\SD\Approved\

Folders are only ever added: nothing that exists is moved or renamed, and a
project whose folder is not reachable on this PC is left as it is. Paths
past Windows' 260-character limit are made through the long-path API.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.services import document_control, system_rules

SCAN = "01- Scan"
MATERIAL_SUBMITTALS = "02- Material Submittals"
DRAWINGS = "03- Drawings"
APPROVED = "Approved"

# The platform's system code -> the folder name the archive uses for it.
SYSTEM_FOLDERS: dict[str, str] = {"FAS": "FA", "ELS": "ELS"}

STRUCTURE: tuple[str, ...] = (
    f"{MATERIAL_SUBMITTALS}/FA/R0",
    f"{MATERIAL_SUBMITTALS}/ELS/R0",
    f"{MATERIAL_SUBMITTALS}/{APPROVED}/FA",
    f"{MATERIAL_SUBMITTALS}/{APPROVED}/ELS",
    f"{DRAWINGS}/IFC/Electrical/ACS",
    f"{DRAWINGS}/IFC/Electrical/FA",
    f"{DRAWINGS}/IFC/Electrical/Light",
    f"{DRAWINGS}/IFC/Electrical/Power",
    f"{DRAWINGS}/IFC/Mechanical/FF",
    f"{DRAWINGS}/IFC/Mechanical/SM",
    f"{DRAWINGS}/IFC/RCP",
    f"{DRAWINGS}/IFC/Builder Work",
    f"{DRAWINGS}/SD/FA",
    f"{DRAWINGS}/SD/ELS",
    f"{DRAWINGS}/SD/{APPROVED}",
)


def _is_dir(path: Path) -> bool:
    return os.path.isdir(document_control._os_path(path))


def _has_scan_folder(root: Path) -> bool:
    """Whether the archive already has the folder the intake reads the DRF
    and the Design Sheets from (app.services.ep_resolver: scan / commercial)."""
    from app.services.ep_resolver import DOCUMENT_FOLDER_RE

    try:
        return any(entry.is_dir() and DOCUMENT_FOLDER_RE.search(entry.name) for entry in root.iterdir())
    except OSError:
        return False


def ensure(project) -> list[str]:
    """Make every folder of the structure the project does not have yet.
    Returns the folders made, relative to the project folder; [] when the
    folder is not reachable or nothing was missing."""
    if not project.source_folder_path:
        return []
    root = Path(project.source_folder_path)
    if not _is_dir(root):
        return []
    wanted = list(STRUCTURE)
    if not _has_scan_folder(root):
        wanted.insert(0, SCAN)
    created: list[str] = []
    for relative in wanted:
        target = root / relative
        if _is_dir(target):
            continue
        os.makedirs(document_control._os_path(target), exist_ok=True)
        created.append(relative)
    return created


def system_folder(system_code: str | None) -> str | None:
    """The archive's folder name for a system code, in any spelling the
    platform knows (FAS, FA, ELS, EML, CBS ...); None for a system that has
    no folder of its own."""
    return SYSTEM_FOLDERS.get(system_rules.canonical(system_code) or "")


def submittal_folder(project, system_code: str | None, revision: str) -> Path | None:
    """Where a material submittal of this system and revision is filed:
    <project>/02- Material Submittals/<FA|ELS>/<R#>. None when the project
    has no reachable folder or the system has no folder."""
    folder = system_folder(system_code)
    if not project.source_folder_path or folder is None:
        return None
    root = Path(project.source_folder_path)
    if not _is_dir(root):
        return None
    return root / MATERIAL_SUBMITTALS / folder / revision.strip().upper()
