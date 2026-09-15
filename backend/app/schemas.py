from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import RoleEnum


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    role: RoleEnum
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str = Field(min_length=8)
    role: RoleEnum


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: RoleEnum | None = None
    is_active: bool | None = None


class LoginErrorOut(BaseModel):
    detail: str


# --- a user's account record (app.services.activity) --------------------------


class ActivityEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: datetime
    action: str
    summary: str
    project_id: int | None = None
    project_label: str | None = None
    entity_type: str | None = None
    entity_id: int | None = None
    detail: dict | None = None


class ActivityPageOut(BaseModel):
    events: list[ActivityEventOut]
    total: int
    offset: int
    limit: int


class AccountProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None
    label: str
    ep_number: str | None = None
    project_name: str | None = None
    status: str | None = None
    created: bool
    assigned: bool
    opened_count: int
    changes: int
    last_activity_at: datetime | None = None
    deleted: bool


class AccountSubmittalOut(BaseModel):
    id: int
    project_id: int
    project_label: str
    title: str
    reference: str | None = None
    system_code: str | None = None
    manufacturer: str | None = None
    revision: str
    status: str
    reply_code: str | None = None
    created_by_user: bool
    changes_by_user: int
    updated_at: datetime


class AccountBoqRevisionOut(BaseModel):
    project_id: int
    project_label: str
    number: int
    label: str
    note: str | None = None
    lines: int
    issued_at: datetime


class AccountStatementOut(BaseModel):
    id: int
    project_id: int
    project_label: str
    kind: str
    system_code: str
    clauses: int
    created_by_user: bool
    approved_by_user: bool
    approved_at: datetime | None = None
    clause_changes_by_user: int
    updated_at: datetime


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user: UserOut
    counts: dict[str, int]
    projects: list[AccountProjectOut]
    submittals: list[AccountSubmittalOut]
    boq_revisions: list[AccountBoqRevisionOut]
    compliance_statements: list[AccountStatementOut]
    activity: list[ActivityEventOut]
    activity_total: int
    actions: list[str]
