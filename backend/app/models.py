import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
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
      - No project-level `manufacturer` field -- brand is per-system and lives
        on `ProjectSystem`, read from the DRF's Systems table.
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
    other_information: Mapped[str | None] = mapped_column(Text, nullable=True)

    # When the Design Sheets were read into the BOQ. Set once, on the first
    # attempt, and never cleared: extraction is a starting point the engineer
    # then edits, so re-running it later would either duplicate their lines or
    # overwrite their corrections.
    boq_extracted_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    # Sheets that extraction could not read, kept because the read happens
    # once and cannot be repeated: returned only on the response that did it,
    # the warning was lost whenever that response was (React StrictMode
    # discards the first of its paired requests), leaving the sheet's lines
    # silently missing.
    boq_extraction_warnings: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

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
    systems: Mapped[list["ProjectSystem"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    boq_items: Mapped[list["ProjectBoqItem"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectBoqItem.position",
    )
    boq_revisions: Mapped[list["ProjectBoqRevision"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectBoqRevision.number",
    )


class ProjectSystem(Base):
    """One row per system marked on the DRF's Systems table.

    A system is on the project if its row carries any mark: a brand written
    in, or a Method Statement / Drawing tick. Those three are kept rather than
    collapsed to a name, because MS and DWG are per-system submittal
    commitments that the team tracks separately.
    """

    __tablename__ = "project_systems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    project: Mapped["Project"] = relationship(back_populates="systems")

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(128), nullable=True)
    method_statement: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    drawing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ProjectBoqItem(Base):
    """One Bill of Quantities line.

    Modelled on the Design Sheets in the archive, which are quotations laid
    out as qty / catalog no / description / unit price / total price, plus
    the plan's BOQ columns the sheets do not carry (manufacturer, unit,
    remarks). Lines are read out of the sheets once, then edited here.
    """

    __tablename__ = "project_boq_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    project: Mapped["Project"] = relationship(back_populates="boq_items")

    # Which system's sheet the line came from (FAS, ELS, ...); a project can
    # have several, and their lines are read and priced separately.
    system_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The quotations group sub-components under a heading (a panel, say) and
    # indent their parts beneath it. Without this the parts read as unrelated
    # loose items once they are in a flat table.
    group_heading: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Pre-filled on extraction from the brand the DRF gives the line's system;
    # the sheets name the maker in their header, not on each line.
    manufacturer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    catalog_no: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Text, not a number: the sheets use "Lot" as a quantity as readily as
    # they use "505", and rewriting that as a number would lose what the
    # document actually says.
    quantity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # "Nos", "m", "Set"... The sheets have no unit column, so it starts empty.
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)

    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProjectBoqRevision(Base):
    """An issued BOQ revision (Rev 00, Rev 01, ...): a frozen copy of the
    lines as they stood when it was issued.

    A snapshot rather than references to project_boq_items, because every
    BOQ save replaces those rows wholesale -- there is nothing lasting to
    point at. Nothing updates or deletes a revision: an issued BOQ is the
    record of what went out.
    """

    __tablename__ = "project_boq_revisions"
    __table_args__ = (UniqueConstraint("project_id", "number", name="uq_boq_revision_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    project: Mapped["Project"] = relationship(back_populates="boq_revisions")

    number: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ProjectBoqItemIn dicts, in BOQ order.
    items: Mapped[list[dict]] = mapped_column(JSON, nullable=False)

    issued_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    issued_by = relationship("User")
    issued_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)

    @property
    def label(self) -> str:
        return f"Rev {self.number:02d}"


class ProjectDesignSheet(Base):
    """One row per Design Sheet file matched for the project (one per
    system: FAS, ELS, PAVA, CBS, ...)."""

    __tablename__ = "project_design_sheets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    project: Mapped["Project"] = relationship(back_populates="design_sheets")

    system_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    document_path: Mapped[str] = mapped_column(Text, nullable=False)
