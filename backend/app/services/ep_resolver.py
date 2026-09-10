"""Deterministic EP-folder and document resolution against a project file tree.

This is a local-filesystem implementation for dev/test, used against the
synced OneDrive archive. Production should use Microsoft Graph search against
SharePoint instead (indexed, not a full tree walk) -- this module's matching
rules (find_ep_folders / find_drf_candidates / find_design_sheet_candidates)
are the part that should carry over; only the traversal mechanism changes.

Naming rules below were derived from real project folders (EP-29495,
EP-30851, EP-28929, EP-13705), not guessed:
  - DRF: a file with "DRF" in its name, inside a subfolder named
    "Scan Document" or "Scan Documents".
  - Design Sheet: a file with "Design" in its name, inside a subfolder
    containing "Commercial" (e.g. "EP-#### Commercial", "Commercial
    Document"). A project can have more than one (one per system: FAS, ELS,
    PAVA, CBS, ...).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# EP folders were observed at depth 1-3 under the projects root (root itself,
# client\EP-####, client\subfolder\EP-####). Capped to bound worst-case
# traversal cost when an EP number doesn't exist anywhere in the tree.
MAX_EP_SEARCH_DEPTH = 4

# Within a resolved project folder, DRF/Design Sheet subfolders were observed
# at depth 1 (immediate child). A small margin is kept for variants.
MAX_DOCUMENT_SEARCH_DEPTH = 3

DRF_FOLDER_RE = re.compile(r"scan documents?", re.IGNORECASE)
DRF_FILENAME_RE = re.compile(r"drf", re.IGNORECASE)
DRF_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff")

DESIGN_SHEET_FOLDER_RE = re.compile(r"commercial", re.IGNORECASE)
DESIGN_SHEET_FILENAME_RE = re.compile(r"design", re.IGNORECASE)
DESIGN_SHEET_EXTENSIONS = (".pdf", ".xlsx", ".xls")

SYSTEM_CODE_RE = re.compile(r"\b(FAS|ELS|PAVA|CBS|VES|NAC)\b", re.IGNORECASE)


def _ep_folder_pattern(ep_number: str) -> re.Pattern:
    """Match an EP folder name as a *prefix*: "EP-29495 IVY Garden 2" for
    ep_number="29495", but not "EP-294950..." (longer number)."""
    return re.compile(rf"^EP[-_ ]?{re.escape(ep_number)}(?!\d)", re.IGNORECASE)


# Matches *any* EP-numbered folder, not a specific number. A project folder
# never contains another project folder, so this is used to prune descent
# during search -- without it, an unrelated internal subfolder that happens
# to reuse an EP-number-shaped name (observed in the archive, e.g. a "Cabinet
# Sample" folder named after a different EP number) gets misread as a
# duplicate top-level match for that other number.
ANY_EP_FOLDER_RE = re.compile(r"^EP[-_ ]?\d{4,6}(?!\d)", re.IGNORECASE)


def _walk_with_errors(root: Path, max_depth: int, errors: list[str]):
    root = Path(root)

    def on_error(exc: OSError) -> None:
        errors.append(f"{exc.filename}: {exc.strerror or exc}")

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth >= max_depth:
            dirnames[:] = []
            continue
        yield dirpath, dirnames, filenames


def find_ep_folders(root: Path, ep_number: str, errors: list[str] | None = None) -> list[Path]:
    """Find all folders under root whose name identifies this EP number.

    Returns every match (not just the first) so duplicate EP numbers --
    a real scenario in the archive -- surface as ambiguity rather than
    silently picking one.
    """
    if errors is None:
        errors = []
    pattern = _ep_folder_pattern(ep_number)
    matches: list[Path] = []

    for dirpath, dirnames, _filenames in _walk_with_errors(root, MAX_EP_SEARCH_DEPTH, errors):
        for name in list(dirnames):
            if pattern.match(name):
                matches.append(Path(dirpath) / name)
        # Never descend into any EP-numbered folder -- a project folder does
        # not contain another project folder, whether or not its number is
        # the one being searched for.
        dirnames[:] = [d for d in dirnames if not ANY_EP_FOLDER_RE.match(d)]

    return matches


@dataclass
class DocumentMatch:
    path: Path
    system_guess: str | None
    matched_via: str


def find_drf_candidates(
    project_folder: Path, errors: list[str] | None = None
) -> list[DocumentMatch]:
    if errors is None:
        errors = []
    results: list[DocumentMatch] = []
    for dirpath, _dirnames, filenames in _walk_with_errors(
        project_folder, MAX_DOCUMENT_SEARCH_DEPTH, errors
    ):
        if not DRF_FOLDER_RE.search(Path(dirpath).name):
            continue
        for fname in filenames:
            if DRF_FILENAME_RE.search(fname) and fname.lower().endswith(DRF_EXTENSIONS):
                results.append(
                    DocumentMatch(
                        path=Path(dirpath) / fname,
                        system_guess=None,
                        matched_via="folder~Scan Document(s), filename~DRF",
                    )
                )
    return results


def find_design_sheet_candidates(
    project_folder: Path, errors: list[str] | None = None
) -> list[DocumentMatch]:
    if errors is None:
        errors = []
    results: list[DocumentMatch] = []
    for dirpath, _dirnames, filenames in _walk_with_errors(
        project_folder, MAX_DOCUMENT_SEARCH_DEPTH, errors
    ):
        if not DESIGN_SHEET_FOLDER_RE.search(Path(dirpath).name):
            continue
        for fname in filenames:
            if DESIGN_SHEET_FILENAME_RE.search(fname) and fname.lower().endswith(
                DESIGN_SHEET_EXTENSIONS
            ):
                system_match = SYSTEM_CODE_RE.search(fname)
                results.append(
                    DocumentMatch(
                        path=Path(dirpath) / fname,
                        system_guess=system_match.group(1).upper() if system_match else None,
                        matched_via="folder~Commercial, filename~Design",
                    )
                )
    return results


@dataclass
class ProjectResolution:
    ep_number: str
    matched_folders: list[Path] = field(default_factory=list)
    drf_candidates: list[DocumentMatch] = field(default_factory=list)
    design_sheet_candidates: list[DocumentMatch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def folder_not_found(self) -> bool:
        return len(self.matched_folders) == 0

    @property
    def is_ambiguous(self) -> bool:
        return len(self.matched_folders) > 1


def resolve_project(
    root: Path, ep_number: str, selected_folder: Path | None = None
) -> ProjectResolution:
    """Resolve an EP number to its documents.

    When multiple folders match, this does NOT guess -- it returns the
    candidates and no document scan, so the caller can present a "which
    folder is this?" choice. Pass the folder the user picked back in as
    `selected_folder` to complete the resolution. This matters here: EP
    numbers are not unique folder identifiers in the real archive (the same
    number can legitimately label several folders across different
    contractors/scope variants for one building), so "duplicate EP numbers"
    is the common case, not an edge case.
    """
    errors: list[str] = []
    folders = find_ep_folders(root, ep_number, errors)
    resolution = ProjectResolution(ep_number=ep_number, matched_folders=folders, errors=errors)

    if not folders:
        resolution.warnings.append(f"No folder found for EP number '{ep_number}'")
        return resolution

    if len(folders) > 1:
        resolution.warnings.append(
            f"Multiple folders found for EP number '{ep_number}': "
            + "; ".join(str(f) for f in folders)
        )
        if selected_folder is None:
            resolution.warnings.append("Select one of the matched folders to continue")
            return resolution
        if selected_folder not in folders:
            resolution.warnings.append("Selected folder is not one of the matched folders")
            return resolution

    target_folder = selected_folder if selected_folder is not None else folders[0]
    resolution.drf_candidates = find_drf_candidates(target_folder, errors)
    resolution.design_sheet_candidates = find_design_sheet_candidates(target_folder, errors)

    if not resolution.drf_candidates:
        resolution.warnings.append("No DRF found in project folder")
    if not resolution.design_sheet_candidates:
        resolution.warnings.append("No Design Sheet found in project folder")

    return resolution
