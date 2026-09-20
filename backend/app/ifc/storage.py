"""Where a project's IFC drawings are kept on this machine.

Under the platform's uploads, a folder per project (as the compliance
uploads are): <uploads>/EP-<number>/ifc/. The database holds the path
relative to the uploads folder, never an absolute one, so the same
database works on another PC with its own uploads folder.
"""
from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings


def uploads_root() -> Path:
    return Path(get_settings().uploads_root)


def project_folder(project) -> Path:
    """<uploads>/EP-<number>/ifc -- made when something is put in it."""
    return uploads_root() / f"EP-{project.ep_number}" / "ifc"


def relative(path: Path) -> str:
    return path.resolve().relative_to(uploads_root().resolve()).as_posix()


def absolute(stored_path: str) -> Path:
    """A stored path back to a file on this machine. An absolute path (none
    are written, but one could be) is taken as it is."""
    path = Path(stored_path)
    return path if path.is_absolute() else uploads_root() / path


def dxf_path(drawing) -> Path:
    return absolute(drawing.stored_path)
