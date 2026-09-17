import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, model_validator

from app.models import ProjectStatus

_EP_PREFIX_RE = re.compile(r"^EP[-_ ]*", re.IGNORECASE)


def _normalize_ep_number(value: str) -> str:
    """"EP-30784", "ep 30784" and " 30784 " all name the same project. Left
    as typed they would be three different unique keys, and the prefixed form
    would find no folder, because the resolver adds the "EP" itself."""
    value = _EP_PREFIX_RE.sub("", value.strip()).strip()
    if not value:
        raise ValueError("EP number is required")
    return value


EpNumber = Annotated[str, AfterValidator(_normalize_ep_number)]


class ProjectResolveRequest(BaseModel):
    ep_number: EpNumber
    selected_folder: str | None = None


class DocumentCandidateOut(BaseModel):
    path: str
    filename: str
    system_guess: str | None = None
    # How the resolver matched it, including a note when it is a superseded
    # revision or its system was inferred from the DRF.
    matched_via: str = ""
    # The revision the filename declares (R1, R2); None when it declares none.
    revision: int | None = None
    # Whether to attach it by default. A design superseded by a later
    # revision of the same system is offered unticked.
    selected: bool = True


class ExtractedFieldOut(BaseModel):
    value: str
    # The OCR read gave a figure per field; the model's read gives none.
    confidence: float | None = None
    raw_label: str


class ProjectSystemIn(BaseModel):
    name: str
    brand: str | None = None
    method_statement: bool = False
    drawing: bool = False


class ProjectSystemOut(ProjectSystemIn):
    model_config = ConfigDict(from_attributes=True)

    id: int


class ProjectBoqItemIn(BaseModel):
    system_code: str | None = None
    group_heading: str | None = None
    manufacturer: str | None = None
    catalog_no: str | None = None
    description: str
    quantity: str | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    total_price: Decimal | None = None
    remarks: str | None = None


class ProjectBoqItemSave(ProjectBoqItemIn):
    """A line as the BOQ page saves it: the id it was loaded with, so the
    server can keep that line's provenance. New lines have none.

    The quantity is checked here, at the boundary, by the same typed parser
    the extractor uses: "1,250" is stored as 1250 and "10 Nos" as 10 with
    the unit Nos, while "12.5", "-3" or "2 x 10" are refused with the reason
    rather than stored as text a calculation would later misread."""

    id: int | None = None

    @model_validator(mode="after")
    def _typed_quantity(self):
        from app.extraction import values

        parsed = values.parse_quantity(self.quantity)
        if parsed.status == values.EMPTY:
            self.quantity = None
        elif parsed.ok:
            self.quantity = parsed.text()
            if parsed.unit and not (self.unit or "").strip():
                self.unit = parsed.unit
        else:
            label = self.catalog_no or self.description[:40] or "a line"
            raise ValueError(f"Quantity {self.quantity!r} on {label} is not a quantity: {parsed.rule}")
        for name in ("unit_price", "total_price"):
            price = getattr(self, name)
            if price is not None and price < 0:
                raise ValueError(f"{name.replace('_', ' ').capitalize()} on {self.catalog_no or 'a line'} cannot be negative")
        return self


BoqLineStatus = Literal["extracted", "corrected", "ai_accepted", "review_accepted", "manual", "legacy"]


class ProjectBoqItemOut(ProjectBoqItemIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    position: int
    # Provenance (app.services.boq_provenance).
    building: str | None = None
    catalog_canonical: str | None = None
    catalog_match: dict | None = None
    origin: str = "legacy"
    extraction_run_id: int | None = None
    source_document_sha256: str | None = None
    source_page: int | None = None
    source_region: list[int] | None = None
    raw_values: dict | None = None
    ocr_confidence: Decimal | None = None
    parser_version: str | None = None
    extracted_values: dict | None = None
    edited_at: datetime | None = None
    created_at: datetime | None = None
    ai_check: dict | None = None
    status: BoqLineStatus = "legacy"

    @model_validator(mode="after")
    def _status(self):
        if self.origin == "extracted" and self.extracted_values and any(
            (self.extracted_values.get(name) or None) != (getattr(self, name) or None)
            for name in ("system_code", "group_heading", "catalog_no", "description", "quantity")
        ):
            self.status = "corrected"
        elif self.origin in ("extracted", "ai_accepted", "review_accepted", "manual", "legacy"):
            self.status = self.origin  # type: ignore[assignment]
        return self


class BoqEnsureResponse(BaseModel):
    """The project's BOQ, plus whether this call was the one that read it out
    of the Design Sheets and which sheets could not be read. The warnings are
    stored, so every call returns them, not just the one that extracted."""

    items: list[ProjectBoqItemOut]
    extracted: bool
    warnings: list[str] = []
    # When the model has to read the sheets first, that runs as a job and
    # this is it (app.routers.jobs.JobOut): the page follows it and asks
    # again when it is done. None once the BOQ is read.
    reading: dict | None = None
    # The version a save must name in If-Match (app.services.concurrency).
    version: int = 0


class BoqRevisionIssue(BaseModel):
    note: str | None = None


class BoqRevisionSummaryOut(BaseModel):
    number: int
    label: str
    note: str | None
    issued_at: datetime
    issued_by_name: str
    line_count: int


class BoqRevisionOut(BoqRevisionSummaryOut):
    items: list[ProjectBoqItemIn]


class BoqChangeOut(BaseModel):
    kind: Literal["added", "removed", "changed"]
    before: ProjectBoqItemIn | None
    after: ProjectBoqItemIn | None
    # For "changed": which of manufacturer, quantity, unit, prices, remarks.
    fields: list[str]


class BoqCompareOut(BaseModel):
    from_label: str
    to_label: str
    changes: list[BoqChangeOut]


class ProjectResolveResponse(BaseModel):
    ep_number: str
    folder_found: bool
    is_ambiguous: bool
    matched_folders: list[str]
    # The folder the documents were found in -- the one match, or the one
    # the engineer selected. This is the project's source folder; the first
    # of `matched_folders` is not, and taking it filed EP-31112 under the
    # wrong contractor.
    source_folder: str | None = None
    drf_candidates: list[DocumentCandidateOut]
    design_sheet_candidates: list[DocumentCandidateOut]
    warnings: list[str]
    errors: list[str]
    extracted_fields: dict[str, ExtractedFieldOut] = {}
    extracted_scope_of_work: str | None = None
    extracted_systems: list[ProjectSystemIn] = []
    extracted_other_information: str | None = None
    extraction_warnings: list[str] = []
    # What the model suggests, when assistance is on: a system for an
    # unlabelled sheet, a second reading of a low-confidence field. Shown
    # beside the form; never applied by the platform.
    ai_suggestions: list[dict] = []


class ProjectDesignSheetIn(BaseModel):
    system_code: str | None = None
    document_path: str


class ProjectDesignSheetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    system_code: str | None
    document_path: str


class ProjectDetailsIn(BaseModel):
    """The project information an engineer reviews at creation and can
    correct later. Not the EP number, which ties the project to its archive
    folder, nor the document paths the resolver found."""

    project_name: str | None = None
    plot_number: str | None = None
    location: str | None = None
    client: str | None = None
    consultant: str | None = None
    contractor: str | None = None
    contact_person: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    scope_of_work: str | None = None
    systems: list[ProjectSystemIn] = []
    other_information: str | None = None
    # Edwards only: a separate voice evacuation panel, so VE is its own system.
    separate_ve_panel: bool = False
    # Whether this project's documents may be sent to an AI provider.
    ai_policy: Literal["allowed", "blocked"] = "allowed"


class ProjectCreate(ProjectDetailsIn):
    ep_number: EpNumber
    source_folder_path: str | None = None
    drf_document_path: str | None = None
    design_sheets: list[ProjectDesignSheetIn] = []


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ep_number: str
    status: ProjectStatus
    project_name: str | None
    plot_number: str | None
    location: str | None
    client: str | None
    consultant: str | None
    contractor: str | None
    contact_person: str | None
    contact_phone: str | None
    contact_email: str | None
    scope_of_work: str | None
    systems: list[ProjectSystemOut]
    other_information: str | None
    source_folder_path: str | None
    drf_document_path: str | None
    design_sheets: list[ProjectDesignSheetOut]
    boq_extraction_warnings: list[str] | None
    created_at: datetime
    updated_at: datetime | None = None
    # Versions a save must name in If-Match (app.services.concurrency).
    details_version: int = 0
    boq_version: int = 0
    ai_policy: str = "allowed"
    separate_ve_panel: bool = False
    # From app.services.system_rules: whether FAS carries VE and FT, and the
    # systems the project has under their effective codes. Every tab reads these.
    voice_evacuation_integrated: bool = False
    system_codes: list[str] = []
    # After an edit: what the change was carried into elsewhere on the project.
    propagated: list[str] = []


# --- Re-extraction: re-reading the DRF and Design Sheets and comparing the
# result with what is stored (app/services/reextraction.py). Everything here
# is a report; none of it is applied.


class FieldComparisonOut(BaseModel):
    field: str
    stored: str | None
    extracted: str | None
    # "only_stored" is a value the engineer typed that OCR cannot see, not a
    # regression; "absent" is empty on both sides.
    status: Literal["match", "differs", "only_stored", "only_extracted", "absent"]
    confidence: float | None = None


class SystemComparisonOut(BaseModel):
    name: str
    stored_brand: str | None
    extracted_brand: str | None
    status: Literal["match", "differs", "only_stored", "only_extracted", "absent"]


class SheetComparisonOut(BaseModel):
    system_code: str | None
    path: str
    filename: str
    # "new": in the folder but not on the project -- its lines are missing
    # from the BOQ. "missing": on the project but no longer in the folder.
    status: Literal["known", "new", "missing"]
    lines_extracted: int
    error: str | None = None


class ReextractionReportOut(BaseModel):
    ep_number: str
    folder_found: bool
    folder_path: str | None
    drf_path: str | None
    fields: list[FieldComparisonOut]
    scope_of_work: FieldComparisonOut | None
    systems: list[SystemComparisonOut]
    sheets: list[SheetComparisonOut]
    boq_changes: list[BoqChangeOut]
    fields_differing: int
    has_differences: bool
    warnings: list[str]
    errors: list[str]


# --- The AI check of project details against the DRF (app/services/details_check.py).
# Suggestions only; the engineer applies what they accept and saves.


class DetailsCheckIn(BaseModel):
    """The values to check: the review form's draft, or Project Info's."""

    details: ProjectDetailsIn
    # At creation, the DRF the resolver found; ignored for an existing project,
    # which is checked against its own DRF.
    drf_path: str | None = None


class FieldSuggestionOut(BaseModel):
    field: str
    label: str
    current: str
    suggested: str
    reason: str


class SystemSuggestionOut(BaseModel):
    name: str
    change: Literal["add", "remove", "update"]
    current: ProjectSystemIn | None
    suggested: ProjectSystemIn | None
    reason: str


class DetailsCheckOut(BaseModel):
    model: str
    from_cache: bool
    fields: list[FieldSuggestionOut]
    systems: list[SystemSuggestionOut]
    confirmed: int
    unreadable: list[str]
    notes: list[str]
