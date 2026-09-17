"""Re-read a project's DRF and Design Sheets and report what differs from
what is stored.

Both extractions are otherwise one-off: the DRF is read once to fill the
review form at creation, and the Design Sheets once into the BOQ (guarded by
`projects.boq_extracted_at`). Neither document is looked at again. So a
re-scanned DRF, a Design Sheet filed after the project was created, or a
field mistyped during review all go unnoticed -- the archive moves on and the
project does not.

This re-reads both, every time it is called, against the project folder as it
stands now: the sheets are re-resolved rather than taken from the stored
rows, which is what lets a sheet added later appear at all.

It **writes nothing.** The BOQ and the project fields are the engineer's
work, and the once-only stamp exists precisely because a blind re-read
duplicated lines and discarded corrections. So a difference is reported for
someone to act on, never applied. That also makes this safe to run on every
open: the worst case is a slow, read-only answer.

The cost is OCR over every sheet, seconds per page, so it is a deliberate
action (`POST /projects/{id}/reextract`), not something a page render does on
its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.models import Project
from app.schemas_project import ProjectBoqItemIn
from app.services.boq_revisions import BoqChange, compare_boq
from app.services.design_sheet_extractor import (
    DesignSheetExtractionError,
    extract_boq_lines,
)
from app.services.drf_extractor import extract_drf_fields
from app.services.ep_resolver import (
    find_design_sheet_candidates,
    find_drf_candidates,
    find_ep_folders,
)

# The DRF's own field names (drf_extractor.FIELD_LABEL_KEYWORDS) against the
# columns they are stored in. Only project_title is renamed; the rest match.
# The same mapping is applied in the review form
# (frontend/src/pages/ReviewProjectForm.tsx) -- change one and look at the
# other, or a re-read reports a difference the form cannot show.
DRF_FIELD_TO_COLUMN: dict[str, str] = {
    "project_title": "project_name",
    "plot_number": "plot_number",
    "location": "location",
    "client": "client",
    "consultant": "consultant",
    "contractor": "contractor",
    "contact_person": "contact_person",
    "contact_phone": "contact_phone",
    "contact_email": "contact_email",
}

FieldStatus = Literal["match", "differs", "only_stored", "only_extracted", "absent"]
SheetStatus = Literal["known", "new", "missing"]


def _normalize(text: str | None) -> str:
    """Compare on letters and digits only.

    OCR differences that are not differences of *fact* -- doubled spaces, a
    trailing comma, "Ms." vs "Ms" -- would otherwise fill the report with
    noise and bury the real changes.
    """
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _is_difference(status: FieldStatus) -> bool:
    """Which comparison outcomes are worth an engineer's attention.

    Only two of the five are. "match" and "absent" (empty on both sides) are
    agreement. "only_stored" is a value the engineer typed because OCR could
    not read that row -- counting it would mark a perfectly reconciled
    project as differing on every run, which is how a report stops being
    read. "only_extracted" does count: the document carries something the
    project does not.
    """
    return status in ("differs", "only_extracted")


@dataclass
class FieldComparison:
    """One DRF field: what the project holds against what the document says."""

    field: str
    stored: str | None
    extracted: str | None
    status: FieldStatus
    confidence: float | None = None


@dataclass
class SystemComparison:
    name: str
    stored_brand: str | None
    extracted_brand: str | None
    status: FieldStatus


@dataclass
class SheetComparison:
    """A Design Sheet in the folder, on the project, or both.

    "new" is the one that matters: a sheet filed after creation is not on the
    project and its lines are not in the BOQ, and nothing else surfaces that.
    """

    system_code: str | None
    path: str
    filename: str
    status: SheetStatus
    lines_extracted: int = 0
    error: str | None = None


@dataclass
class ReextractionReport:
    ep_number: str
    folder_path: str | None = None
    folder_found: bool = False
    drf_path: str | None = None

    fields: list[FieldComparison] = field(default_factory=list)
    scope_of_work: FieldComparison | None = None
    systems: list[SystemComparison] = field(default_factory=list)
    sheets: list[SheetComparison] = field(default_factory=list)
    boq_changes: list[BoqChange] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def fields_differing(self) -> int:
        return sum(1 for f in self.fields if _is_difference(f.status))

    @property
    def has_differences(self) -> bool:
        return bool(
            self.fields_differing
            or self.boq_changes
            or any(s.status != "known" for s in self.sheets)
            or any(_is_difference(s.status) for s in self.systems)
            or (self.scope_of_work is not None and _is_difference(self.scope_of_work.status))
        )


def _compare_value(name: str, stored: str | None, extracted: str | None, confidence: float | None = None) -> FieldComparison:
    stored_clean = (stored or "").strip() or None
    extracted_clean = (extracted or "").strip() or None

    if stored_clean is None and extracted_clean is None:
        status: FieldStatus = "absent"
    elif extracted_clean is None:
        # The project carries a value the document no longer yields. Usually
        # an engineer typed it in because OCR could not read that row -- it
        # is not a regression, so it is reported as a state, not a conflict.
        status = "only_stored"
    elif stored_clean is None:
        status = "only_extracted"
    elif _normalize(stored_clean) == _normalize(extracted_clean):
        status = "match"
    else:
        status = "differs"

    return FieldComparison(
        field=name,
        stored=stored_clean,
        extracted=extracted_clean,
        status=status,
        confidence=confidence,
    )


def _resolve_folder(project: Project, projects_root: Path | None, report: ReextractionReport) -> Path | None:
    """The project folder as it stands now.

    The stored path is trusted first: it is what the engineer picked at
    creation, and re-resolving could land on a different folder where an EP
    number is ambiguous (a real case in the archive). Searching again is the
    fallback for a folder that has since moved or been renamed.
    """
    stored = Path(project.source_folder_path) if project.source_folder_path else None
    if stored is not None and stored.is_dir():
        return stored

    if stored is not None:
        report.warnings.append(f"The project's folder is no longer at {stored}; searching the archive for it.")

    if projects_root is None:
        report.errors.append("Project archive is not configured (PROJECTS_ROOT is unset)")
        return None
    if not projects_root.is_dir():
        report.errors.append("Project archive path is not reachable")
        return None

    matches = find_ep_folders(projects_root, project.ep_number, report.errors)
    if not matches:
        report.errors.append(f"No folder for EP-{project.ep_number} was found in the archive")
        return None
    if len(matches) > 1:
        # Same rule as resolution at creation: ambiguity is surfaced, never
        # resolved by picking one.
        report.warnings.append(
            f"{len(matches)} folders match EP-{project.ep_number}; read the first: " + str(matches[0])
        )
    return matches[0]


def _drf_reading(db, project: Project, drf: Path):
    """(fields by the DRF's names -> (value, confidence), scope of work, other
    information, systems by name -> brand, warnings). `extract_drf_fields` is
    the seam the tests stub; the real read is the model's."""
    from app.services import drf_extractor

    if extract_drf_fields is not drf_extractor.extract_drf_fields:
        extraction = extract_drf_fields(drf)
        return ({name: (f.value, f.confidence) for name, f in extraction.fields.items()},
                extraction.scope_of_work, extraction.other_information,
                {s.name: s.brand for s in extraction.systems}, list(extraction.warnings))
    from app.ai import verification as ai_verification

    fields, systems, run = ai_verification.read_drf(db, drf, project=project)
    return ({ai_verification.DRF_NAME.get(name, name): (value, None) for name, value in fields.items()
             if value and name not in ("scope_of_work", "other_information")},
            fields.get("scope_of_work") or None, fields.get("other_information") or None,
            {name: entry.get("brand") for name, entry in (systems or {}).items()}, list(run.notes))


def _compare_drf(project: Project, folder: Path, report: ReextractionReport, db=None) -> None:
    stored_drf = Path(project.drf_document_path) if project.drf_document_path else None
    if stored_drf is not None and stored_drf.is_file():
        drf = stored_drf
    else:
        candidates = find_drf_candidates(folder, report.errors)
        if not candidates:
            report.warnings.append("No DRF was found in the project folder; its fields were not re-read.")
            return
        drf = candidates[0].path
        if stored_drf is not None:
            report.warnings.append(f"The project's DRF is no longer at {stored_drf}; read {drf.name} instead.")
        if len(candidates) > 1:
            report.warnings.append(f"{len(candidates)} DRFs in the folder; read {drf.name}.")

    report.drf_path = str(drf)

    try:
        fields, scope_of_work, _other, systems, warnings = _drf_reading(db, project, drf)
    except Exception as exc:  # noqa: BLE001
        # Best-effort, as in `resolve`: a form the model did not read must
        # not lose the Design Sheet comparison below it.
        report.errors.append(f"DRF field extraction failed ({drf.name}): {exc}")
        return

    report.warnings.extend(warnings)

    for drf_field, column in DRF_FIELD_TO_COLUMN.items():
        found = fields.get(drf_field)
        report.fields.append(
            _compare_value(
                column,
                getattr(project, column, None),
                found[0] if found else None,
                found[1] if found else None,
            )
        )

    report.scope_of_work = _compare_value("scope_of_work", project.scope_of_work, scope_of_work)

    stored_systems = {s.name: s for s in project.systems}
    for name in sorted(set(stored_systems) | set(systems)):
        stored = stored_systems.get(name)
        comparison = _compare_value(
            name,
            stored.brand if stored else None,
            systems.get(name),
        )
        report.systems.append(
            SystemComparison(
                name=name,
                stored_brand=comparison.stored,
                extracted_brand=comparison.extracted,
                status=comparison.status,
            )
        )


def _read_sheet(db, project: Project, candidate) -> list:
    """The lines of one sheet as the platform reads it now. `extract_boq_lines`
    is the seam the tests stub: when it is the real function the model's read
    is used, and a sheet the model did not read raises with the reason."""
    from app.services import design_sheet_extractor

    if extract_boq_lines is not design_sheet_extractor.extract_boq_lines:
        return list(extract_boq_lines(candidate.path))
    from app.models import ProjectDesignSheet
    from app.routers import projects as projects_router

    sheet = ProjectDesignSheet(system_code=candidate.system_guess, document_path=str(candidate.path))
    result = projects_router._read_design_sheet(db, project, sheet)
    if result.failure:
        raise DesignSheetExtractionError(result.failure)
    return list(result.lines)


def _compare_sheets(project: Project, folder: Path, report: ReextractionReport, db=None) -> None:
    """Re-read every Design Sheet in the folder and diff the result against
    the stored BOQ. The sheets are read the way the BOQ is: by the model
    (app.ai.sheet_reader), through the same seam the test suite stubs.

    Sheets come from the folder, not from `project.design_sheets`, so one
    filed after creation is read too -- that sheet's lines are missing from
    the BOQ entirely, which is the difference most worth catching.
    """
    stored_paths = {Path(ds.document_path) for ds in project.design_sheets}
    found = find_design_sheet_candidates(folder, report.errors)
    found_paths = {c.path for c in found}

    extracted: list[ProjectBoqItemIn] = []

    for candidate in sorted(found, key=lambda c: c.path.name):
        comparison = SheetComparison(
            system_code=candidate.system_guess,
            path=str(candidate.path),
            filename=candidate.path.name,
            status="known" if candidate.path in stored_paths else "new",
        )
        try:
            lines = _read_sheet(db, project, candidate)
        except DesignSheetExtractionError as exc:
            comparison.error = str(exc)
            report.warnings.append(f"{candidate.path.name}: {exc}")
        else:
            comparison.lines_extracted = len(lines)
            extracted.extend(
                ProjectBoqItemIn(
                    system_code=candidate.system_guess,
                    group_heading=line.group_heading,
                    catalog_no=line.catalog_no,
                    description=line.description,
                    quantity=line.quantity,
                )
                for line in lines
            )
        report.sheets.append(comparison)

    for missing in sorted(stored_paths - found_paths):
        report.sheets.append(
            SheetComparison(
                system_code=next(
                    (ds.system_code for ds in project.design_sheets if Path(ds.document_path) == missing),
                    None,
                ),
                path=str(missing),
                filename=missing.name,
                status="missing",
            )
        )

    # Only the fields a sheet actually carries take part. Manufacturer, unit,
    # prices and remarks are filled in by the platform or the engineer and are
    # not on the sheets, so comparing them would report every line as changed.
    stored_lines = [
        ProjectBoqItemIn(
            system_code=item.system_code,
            group_heading=item.group_heading,
            catalog_no=item.catalog_no,
            description=item.description,
            quantity=item.quantity,
        )
        for item in project.boq_items
    ]
    report.boq_changes = compare_boq(stored_lines, extracted)


def reextract_project(project: Project, projects_root: Path | None, db=None) -> ReextractionReport:
    """Re-read the project's documents and report the differences.

    Never writes: see the module docstring.
    """
    report = ReextractionReport(ep_number=project.ep_number)

    folder = _resolve_folder(project, projects_root, report)
    if folder is None:
        return report

    report.folder_found = True
    report.folder_path = str(folder)

    _compare_drf(project, folder, report, db)
    _compare_sheets(project, folder, report, db)
    return report
