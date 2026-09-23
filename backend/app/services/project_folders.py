r"""The project's folder structure on OneDrive, made when the project is opened.

Every project keeps the same folders under its archive path, so a document
controller finds a submittal or a drawing in the same place on every job,
and so the document index (app.services.document_sync) knows, from the
folder a file sits in, what it is and which system it belongs to:

    01- Scan\                              the DRF and the Design Sheets (only
                                            when no scan / commercial folder exists)
    02- Material Submittals\FA\R0\Submitted\  what we sent
    02- Material Submittals\FA\R0\Received\   what came back, stamped
    03- Drawings\IFC\Electrical\{ACS, FA, Light, Power}\   the BOQ as per IFC files its FA drawings in FA
    03- Drawings\IFC\Mechanical\{FF, SM}\
    03- Drawings\IFC\RCP\                 03- Drawings\IFC\Builder Work\
    03- Drawings\SD\{FA, ELS}\             the shop drawings we submit
    03- Drawings\SD\Approved\

Only the systems the project actually has: a fire-alarm-only job grows
no ELS folder, because an empty one reads as a submittal we owe and
have not sent. The drawing folders are made only when the shop drawings
are ours (`system_rules.drawings_in_scope`, read off the DRF's own
drawing column).

A project already filed under the older shape -- the submittals loose
under the system folder, the returned ones under "Approved" -- keeps it:
moving an issued submittal would break the link to the transmittal that
sent it. Both shapes are read; only new folders are made the new way.

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
# Where the design schedules an engineer works from are kept -- the
# floor-wise BOQ workbook among them. Unlike the folders above it is made
# when something is first filed into it rather than when the project is
# opened, so a project nobody has handed a schedule for does not grow an
# empty folder.
DESIGN = "03- Design"
APPROVED = "Approved"

# The platform's system code -> the folder name the archive uses for it.
SYSTEM_FOLDERS: dict[str, str] = {"FAS": "FA", "ELS": "ELS", "FRC": "FRC"}

# What we send the consultant, and what comes back from them. The archive
# used to keep the outgoing submittals loose under the system's own folder
# and the returned ones under "Approved"; the two halves are now named for
# what they are.
#
# **Only new folders are made this way.** A project already filed under
# the old shape keeps it -- moving a submittal that has been issued would
# break the link between the file and the transmittal that sent it -- and
# the scanner reads both (`submittal_scanner.APPROVAL_FOLDER_RE`).
SUBMITTED = "Submitted"
RECEIVED = "Received"
APPROVED = "Approved"  # the old name for RECEIVED, still read


def submittal_structure(codes: list[str]) -> list[str]:
    """The submittal folders for the systems this project actually has.

    A project with only a fire alarm has no business growing an ELS
    folder: an empty folder in the archive reads as a system we owe a
    submittal for and have not sent.
    """
    folders: list[str] = []
    for code in codes:
        name = SYSTEM_FOLDERS.get(code)
        if not name:
            continue
        # Per revision, because a resubmission is answered separately:
        # R0 has what we sent and what came back on it, R1 its own pair.
        folders.append(f"{MATERIAL_SUBMITTALS}/{name}/R0/{SUBMITTED}")
        folders.append(f"{MATERIAL_SUBMITTALS}/{name}/R0/{RECEIVED}")
    return folders


# The drawings we are given and the ones we produce. Made only when shop
# drawings are ours on this project (`system_rules.drawings_in_scope`).
DRAWINGS_STRUCTURE: tuple[str, ...] = (
    f"{DRAWINGS}/IFC/Electrical/ACS",
    f"{DRAWINGS}/IFC/Electrical/FA",
    f"{DRAWINGS}/IFC/Electrical/Light",
    f"{DRAWINGS}/IFC/Electrical/Power",
    f"{DRAWINGS}/IFC/Electrical/Load Schedule",
    f"{DRAWINGS}/IFC/Mechanical/FF",
    f"{DRAWINGS}/IFC/Mechanical/SM",
    f"{DRAWINGS}/IFC/RCP",
    f"{DRAWINGS}/IFC/Builder Work",
    # From the contractor before shop drawings start (Drawings > Actions Required).
    f"{DRAWINGS}/Title Block",
    f"{DRAWINGS}/SD Reference No",
    f"{DRAWINGS}/SD/{APPROVED}",
)


def drawings_structure(codes: list[str]) -> list[str]:
    """The drawing folders. The IFC tree is fixed -- those come from the
    contractor and cover every trade, ours among them -- but the shop
    drawings we produce follow the project's own systems, the same way
    the submittals do."""
    folders = list(DRAWINGS_STRUCTURE)
    for code in codes:
        name = SYSTEM_FOLDERS.get(code)
        if name:
            folders.append(f"{DRAWINGS}/SD/{name}")
    return folders


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
    # The submittal folders follow the project's own systems, and the
    # drawing folders are made only when the drawings are ours.
    codes = system_rules.project_codes(project)
    wanted = submittal_structure(codes)
    if system_rules.drawings_in_scope(project):
        wanted += drawings_structure(codes)
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


def design_folder(project) -> Path | None:
    """Where this project's design schedules are filed:
    <project>/03- Design. None when the project's folder is not reachable
    on this PC -- the platform never invents a path outside the archive.

    The folder itself is not made here; `file_design_document` makes it
    when there is something to put in it.
    """
    if not project.source_folder_path:
        return None
    root = Path(project.source_folder_path)
    if not _is_dir(root):
        return None
    return root / DESIGN


def design_documents(project, suffixes: tuple[str, ...] = (".xlsx", ".xlsm")) -> list[Path]:
    """The design documents already filed in the project's own folder,
    newest first.

    This is what lets the floor-wise BOQ keep itself up to date: the
    workbook an engineer edits in 03- Design is the source, and the
    platform reads it again when it changes rather than asking for it to
    be uploaded a second time.
    """
    folder = design_folder(project)
    if folder is None or not _is_dir(folder):
        return []
    found: list[tuple[float, Path]] = []
    try:
        for entry in os.scandir(document_control._os_path(folder)):
            if not entry.is_file() or not entry.name.lower().endswith(suffixes):
                continue
            if entry.name.startswith("~$"):
                continue          # Excel's own lock file for an open workbook
            found.append((entry.stat().st_mtime, folder / entry.name))
    except OSError:
        return []
    return [path for _mtime, path in sorted(found, key=lambda pair: -pair[0])]


def file_design_document(project, name: str, content: bytes) -> str | None:
    """Keep a design document in the project's own folder on OneDrive, and
    say where it went, relative to the project.

    None when the project's folder is not reachable: the platform reads
    the archive from wherever OneDrive syncs it, and on a PC where that
    folder is not there the upload is still read and shown -- it is simply
    not filed. Paths past Windows' 260-character limit are written through
    the long-path API, as everything else here is.
    """
    folder = design_folder(project)
    if folder is None:
        return None
    os.makedirs(document_control._os_path(folder), exist_ok=True)
    path = folder / Path(name).name
    with open(document_control._os_path(path), "wb") as handle:
        handle.write(content)
    return path.relative_to(Path(project.source_folder_path)).as_posix()


# Where the fire alarm IFC drawings are kept: the BOQ as per IFC drawings
# files what it reads here.
IFC_FIRE_ALARM = f"{DRAWINGS}/IFC/Electrical/FA"


def file_ifc_drawing(project, name: str, content: bytes, *, stamp: str) -> str | None:
    """Keep an uploaded IFC drawing in the project's own folder, under
    03- Drawings/IFC/Electrical/FA, and say where it went, relative to the
    project. None when the project's folder is not reachable on this PC.

    A file already there is never overwritten: the same drawing is left as
    it is and named, and a different one under the same name gets the
    upload's time in its name ("FA-105 (uploaded 2026-09-19 1405).dwg")."""
    if not project.source_folder_path:
        return None
    root = Path(project.source_folder_path)
    if not _is_dir(root):
        return None
    folder = root / IFC_FIRE_ALARM
    os.makedirs(document_control._os_path(folder), exist_ok=True)
    path = folder / Path(name).name
    if os.path.isfile(document_control._os_path(path)):
        with open(document_control._os_path(path), "rb") as handle:
            if handle.read() == content:
                return path.relative_to(root).as_posix()
        path = folder / f"{path.stem} (uploaded {stamp}){path.suffix}"
    with open(document_control._os_path(path), "wb") as handle:
        handle.write(content)
    return path.relative_to(root).as_posix()


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
