import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutils import utc_now
from app.database import Base


class RoleEnum(str, enum.Enum):
    admin = "admin"
    design_manager = "design_manager"
    design_engineer = "design_engineer"
    draftsman = "draftsman"
    viewer = "viewer"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[RoleEnum] = mapped_column(Enum(RoleEnum), nullable=False, default=RoleEnum.viewer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), default=utc_now, onupdate=utc_now, nullable=False
    )


class ProjectStatus(str, enum.Enum):
    draft = "draft"  # resolved from OneDrive, not yet confirmed by an engineer
    active = "active"  # confirmed and created
    archived = "archived"


class Project(Base):
    """Field choices are anchored to the real DRF template (Document
    Reference SSD-P-06 B/IQF.17) seen in the archive, not the mockup's field
    list, where the two disagree:
      - One `contractor` field (the DRF has one), not split Main/MEP.
      - No `manufacturer` field -- brand is per-system, sourced from the
        Design Sheet(s), not the DRF, and belongs with BOQ/design data
        (Phase 3), not project info.
      - `design_engineer` is an assignment (FK to a user), never extracted.
    """

    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("ep_number", name="uq_projects_ep_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ep_number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus), default=ProjectStatus.draft, nullable=False
    )

    project_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plot_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)

    client: Mapped[str | None] = mapped_column(String(255), nullable=True)
    consultant: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contractor: Mapped[str | None] = mapped_column(String(255), nullable=True)

    contact_person: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    scope_of_work: Mapped[str | None] = mapped_column(String(64), nullable=True)
    systems: Mapped[str | None] = mapped_column(String(255), nullable=True)  # comma-separated
    other_information: Mapped[str | None] = mapped_column(Text, nullable=True)

    design_engineer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    design_engineer = relationship("User", foreign_keys=[design_engineer_id])

    source_folder_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    drf_document_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_by = relationship("User", foreign_keys=[created_by_id])

    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), default=utc_now, onupdate=utc_now, nullable=False
    )

    design_sheets: Mapped[list["ProjectDesignSheet"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectDesignSheet(Base):
    """One row per Design Sheet file matched for the project (one per
    system: FAS, ELS, PAVA, CBS, ...)."""

    __tablename__ = "project_design_sheets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    project: Mapped["Project"] = relationship(back_populates="design_sheets")

    system_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    document_path: Mapped[str] = mapped_column(Text, nullable=False)
