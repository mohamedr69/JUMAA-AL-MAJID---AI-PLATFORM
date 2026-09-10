from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import ProjectStatus


class ProjectResolveRequest(BaseModel):
    ep_number: str
    selected_folder: str | None = None


class DocumentCandidateOut(BaseModel):
    path: str
    filename: str
    system_guess: str | None = None


class ExtractedFieldOut(BaseModel):
    value: str
    confidence: float
    raw_label: str


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
    extraction_warnings: list[str] = []


class ProjectDesignSheetIn(BaseModel):
    system_code: str | None = None
    document_path: str


class ProjectDesignSheetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    system_code: str | None
    document_path: str


class ProjectCreate(BaseModel):
    ep_number: str
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
    systems: list[str] = []
    other_information: str | None = None
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
    systems: str | None
    other_information: str | None
    source_folder_path: str | None
    drf_document_path: str | None
    design_sheets: list[ProjectDesignSheetOut]
    created_at: datetime
