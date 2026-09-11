import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict

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


class ExtractedFieldOut(BaseModel):
    value: str
    confidence: float
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


class ProjectBoqItemOut(ProjectBoqItemIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    position: int


class BoqEnsureResponse(BaseModel):
    """The project's BOQ, plus whether this call was the one that read it out
    of the Design Sheets and which sheets could not be read. The warnings are
    stored, so every call returns them, not just the one that extracted."""

    items: list[ProjectBoqItemOut]
    extracted: bool
    warnings: list[str] = []


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
    drf_candidates: list[DocumentCandidateOut]
    design_sheet_candidates: list[DocumentCandidateOut]
    warnings: list[str]
    errors: list[str]
    extracted_fields: dict[str, ExtractedFieldOut] = {}
    extracted_scope_of_work: str | None = None
    extracted_systems: list[ProjectSystemIn] = []
    extraction_warnings: list[str] = []


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
