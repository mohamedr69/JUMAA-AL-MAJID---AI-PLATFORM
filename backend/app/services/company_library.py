"""The company library: everything a submittal needs that is not about a
particular project.

A material submittal is two kinds of document. Some of it is the project --
its specification, its drawings, the schedule built from its BOQ. The rest is
the *company*: the profile, the trade licence, the ISO and civil-defence
certificates, the test reports, the cover and divider templates, and the
manufacturers' datasheets. That second kind is the same on every job, and it
was being read out of the synced project archive.

Reading it from there is wrong twice over.

**It is slow.** The archive is a OneDrive tree where every directory entry is
a cloud-placeholder lookup: indexing the 86 Edwards datasheets took about
eleven seconds on every start, and a folder walk that finds nothing has been
measured at over an hour. None of that work is about the project being opened.

**It is not portable.** The path
`Systems/01- FAVE/01- Edwards - UL&EN/01- EST4` is one company's filing, on
one synced drive. A second company, or the same company on a second machine,
has neither.

So the library lives in **one local folder with a fixed structure**, set by
`LIBRARY_ROOT` and defaulting to `backend/library`:

    library/
      datasheets/            one folder per manufacturer, its name the brand
        EDWARDS/             as the DRF and the BOQ spell it
        ROCKET/
      submittal/             the submittal builder, section by section
        Company Profile/
        Product Catalogue or Brochure/
        Trade License/
        ISO Certificates/
        Civil defence certificates/
        Test certificates/
        Project Reference List/
        Previous Approvals/
        Country Of Origin/COO.xlsx
        templates/           cover, index and dividers, draft warranty

A manufacturer is **discovered, not configured**: dropping a `MENVIER` folder
into `datasheets/` is all it takes for the platform to look parts up in it.
`DATASHEET_LIBRARIES` stays as an override for a library kept somewhere else.

The archive is still read as a **fallback**, so a machine that has not copied
the library across yet keeps working exactly as before; `scripts/sync_library.py`
makes the copy. Nothing here ever writes to the archive.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings

# `app/services/company_library.py` -> `backend/`. Resolved from this file,
# not from the working directory, so it does not matter where uvicorn was
# started from.
BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LIBRARY_DIRNAME = "library"
DEFAULT_CACHE_DIRNAME = ".cache"

# The two shelves of the library, and the folders the submittal builder is
# expected to hold. The section folders are the company's own names, which
# `submittal_package.LIBRARY_FOLDERS` maps to section numbers -- they are
# repeated here only so the scaffold and the sync script can create them.
DATASHEETS = "datasheets"
SUBMITTAL = "submittal"

SUBMITTAL_FOLDERS = (
    "Company Profile",
    "Product Catalogue or Brochure",
    "Trade License",
    "ISO Certificates",
    "Civil defence certificates",
    "Test certificates",
    "Project Reference List",
    "Previous Approvals",
    "Country Of Origin",
    "templates",
)


def library_root() -> Path:
    """The company library folder. Always a path; it need not exist yet."""
    configured = (get_settings().library_root or "").strip()
    if configured:
        return Path(os.path.expandvars(configured)).expanduser()
    return BACKEND_DIR / DEFAULT_LIBRARY_DIRNAME


def cache_root() -> Path:
    """Where indexes and other derived data are kept. Never the library
    itself: the library can be read-only, or synced, and a cache written
    into a synced folder is uploaded for no reason."""
    configured = (get_settings().cache_root or "").strip()
    if configured:
        return Path(os.path.expandvars(configured)).expanduser()
    return BACKEND_DIR / DEFAULT_CACHE_DIRNAME


def resolve(location: str | None, *, shelf: str | None = None) -> Path | None:
    """What a library location names, or None if it is nowhere.

    Tried in order, first hit wins:

    1. an absolute path, as given -- a library kept outside the platform;
    2. `<library>/<location>`;
    3. `<library>/<shelf>/<location>`, so a manufacturer can be configured
       as "EDWARDS" rather than "datasheets/EDWARDS";
    4. `<archive>/<location>` -- where these documents lived before the
       library existed, kept so a machine mid-migration still works.
    """
    location = (location or "").strip()
    if not location:
        return None

    candidate = Path(os.path.expandvars(location)).expanduser()
    if candidate.is_absolute():
        return candidate if candidate.exists() else None

    root = library_root()
    tried = [root / location]
    if shelf:
        tried.append(root / shelf / location)
    archive = (get_settings().projects_root or "").strip()
    if archive:
        tried.append(Path(archive) / location)

    for path in tried:
        try:
            if path.exists():
                return path
        except OSError:
            # A path too long for the platform, or an unreachable drive:
            # not a crash, just not this one.
            continue
    return None


# Files that are structure rather than content: a folder holding only these
# is an empty folder.
_NOT_A_DOCUMENT = {".gitkeep", "thumbs.db", ".ds_store"}
# A bound on the walk below, so asking "is there anything in here?" about a
# folder of the synced archive cannot turn into a full traversal of it.
_HOLDS_SCAN_LIMIT = 2000


def holds_documents(folder: Path) -> bool:
    """Whether a folder actually holds documents, stopping at the first one.

    This is what keeps the scaffold honest. `scaffold()` creates every folder
    of the structure empty, and an empty folder must not shadow the archive
    copy that still has the documents in it -- a package built from a library
    that "exists" and holds nothing comes out with no company documents and
    nothing saying why.
    """
    stack, seen = [folder], 0
    while stack:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for entry in entries:
            seen += 1
            if seen > _HOLDS_SCAN_LIMIT:
                return False
            if entry.name.startswith("."):
                continue
            if entry.is_dir(follow_symlinks=False):
                stack.append(Path(entry.path))
            elif entry.name.lower() not in _NOT_A_DOCUMENT:
                return True
    return False


def _stocked(path: Path | None) -> bool:
    return path is not None and path.is_dir() and holds_documents(path)


def datasheet_folders() -> dict[str, Path]:
    """Every manufacturer's datasheet folder, by brand as the BOQ spells it.

    Discovery first, configuration second, the archive last -- so a brand
    copied into the library is used even where the old archive path is still
    in `.env`.
    """
    settings = get_settings()
    found: dict[str, Path] = {}

    shelf = library_root() / DATASHEETS
    try:
        children = sorted(shelf.iterdir()) if shelf.is_dir() else []
    except OSError:
        children = []
    for child in children:
        # An empty brand folder is a place kept for a library not copied in
        # yet, not a library: it must not shadow the archive copy.
        if child.is_dir() and not child.name.startswith(".") and holds_documents(child):
            found[child.name.upper()] = child

    for name, location in (settings.datasheet_libraries or {}).items():
        path = resolve(location, shelf=DATASHEETS)
        if _stocked(path):
            found[name.upper()] = path

    for name, location in (settings.archive_datasheet_libraries or {}).items():
        if name.upper() in found:
            continue
        path = resolve(location, shelf=DATASHEETS)
        if _stocked(path):
            found[name.upper()] = path

    return found


def submittal_folder() -> Path | None:
    """The submittal builder folder, or None if it is not reachable.

    The library is preferred, but only once it holds something: an empty
    shelf means the copy has not been made yet, and the archive is still the
    place these documents live.
    """
    settings = get_settings()
    candidates = [
        resolve(location)
        for location in (settings.submittal_library, settings.archive_submittal_library)
    ]
    folders = [path for path in candidates if path is not None and path.is_dir()]
    for path in folders:
        if holds_documents(path):
            return path
    # Nothing has documents: name the first that exists, so the page reports
    # an empty library rather than no library at all.
    return folders[0] if folders else None


@dataclass(frozen=True)
class ShelfStatus:
    name: str
    path: str | None
    available: bool
    source: str  # "library", "archive" or "missing"
    detail: str = ""


def source_of(path: Path | None) -> str:
    """Whether a folder is held in the company library or still read out of
    the project archive."""
    if path is None:
        return "missing"
    try:
        return "library" if path.resolve().is_relative_to(library_root().resolve()) else "archive"
    except (OSError, ValueError):
        return "archive"


def status() -> list[ShelfStatus]:
    """What the library holds, for the setup page and the sync script."""
    rows: list[ShelfStatus] = []

    submittal = submittal_folder()
    rows.append(
        ShelfStatus(
            name="Submittal builder",
            path=str(submittal) if submittal else None,
            available=submittal is not None,
            source=source_of(submittal),
            detail="" if submittal else "Copy it in with scripts/sync_library.py, or set SUBMITTAL_LIBRARY.",
        )
    )

    for brand, folder in sorted(datasheet_folders().items()):
        try:
            count = sum(1 for _ in folder.rglob("*.pdf"))
        except OSError:
            count = 0
        rows.append(
            ShelfStatus(
                name=f"Datasheets: {brand}",
                path=str(folder),
                available=True,
                source=source_of(folder),
                detail=f"{count} PDF" + ("s" if count != 1 else ""),
            )
        )
    return rows


def scaffold(root: Path | None = None) -> Path:
    """Create the empty library structure. Used by the sync script and by a
    first run on a new machine; creating a folder that is already there is
    not an error."""
    root = root or library_root()
    (root / DATASHEETS).mkdir(parents=True, exist_ok=True)
    for folder in SUBMITTAL_FOLDERS:
        (root / SUBMITTAL / folder).mkdir(parents=True, exist_ok=True)
    return root
