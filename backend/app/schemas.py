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
