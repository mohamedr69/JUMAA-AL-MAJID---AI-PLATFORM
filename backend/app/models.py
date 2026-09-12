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
    design: Mapped["ProjectDesign | None"] = relationship(
        back_populates="project", cascade="all, delete-orphan", uselist=False
    )
    submittals: Mapped[list["ProjectSubmittal"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectSubmittal.id",
    )


class ProjectDesign(Base):
    """The project's design calculation inputs, as one document: the zone
    schedule (floors and stairs with device counts per zone) and each
    system's design over it -- so far Voice Evacuation.

    A JSON document validated by app.schemas_design rather than tables per
    part, because the engineer edits it as a whole (a schedule grid, a
    channel assignment) and every calculation reads all of it. Results are
    never stored: they are recomputed from this on every read, so a stored
    number can never disagree with its inputs.
    """

    __tablename__ = "project_designs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, unique=True)
    project: Mapped["Project"] = relationship(back_populates="design")

    document: Mapped[dict] = mapped_column(JSON, nullable=False)

    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_by = relationship("User")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), default=utc_now, onupdate=utc_now, nullable=False
    )


class DesignRule(Base):
    """A design rule the calculations draw on: an amplifier's rating and
    design limit, a speaker type's default tap. Stored in the database, not
    code, so it can be corrected without a release.

    Versioned, never edited in place: a change adds a row with the next
    version and marks the old one superseded. A project's design copies the
    values it uses (and the rule's id) when the engineer picks the rule, so
    correcting a rule never silently changes a calculation already made.
    """

    __tablename__ = "design_rules"
    __table_args__ = (UniqueConstraint("category", "key", "version", name="uq_design_rule_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # "ve.amplifier", "ve.speaker", ...
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Where the value comes from -- a datasheet, an approved calculation.
    source: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_by = relationship("User")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


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


class SubmittalStatus(str, enum.Enum):
    """Where a submittal stands with the consultant."""

    not_submitted = "not_submitted"
    under_review = "under_review"
    approved = "approved"
    rejected = "rejected"


class ProjectSubmittal(Base):
    """A material submittal document: one package of materials of one system,
    submitted to the consultant and revised until approved.

    The register is what the project tracks -- title, system, manufacturer,
    revision and where it stands -- and its history is kept as events rather
    than by overwriting, so "approved on the 12th, rejected before that"
    survives the next revision.
    """

    __tablename__ = "project_submittals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    project: Mapped["Project"] = relationship(back_populates="submittals")

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # The submittal's own reference on the form ("BBY006-GME-MAS-EL-FA-0001"),
    # which is what a scan of the project folder matches on.
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The consultant's reply code: A approved, B approved as noted, C resubmit.
    reply_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # The BOQ's system code (FAS, EML, ...); None for a package that spans them.
    system_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # "R00", "R01", ... as the submittal itself is numbered.
    revision: Mapped[str] = mapped_column(String(16), nullable=False, default="R00")
    status: Mapped[SubmittalStatus] = mapped_column(
        Enum(SubmittalStatus), nullable=False, default=SubmittalStatus.not_submitted
    )
    # The submittal document in the project archive, when there is one.
    document_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_by = relationship("User", foreign_keys=[created_by_id])
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), default=utc_now, onupdate=utc_now, nullable=False
    )

    events: Mapped[list["ProjectSubmittalEvent"]] = relationship(
        back_populates="submittal",
        cascade="all, delete-orphan",
        order_by="ProjectSubmittalEvent.at.desc()",
    )


class ProjectSubmittalEvent(Base):
    """What happened to a submittal: created, revised, or its status changed."""

    __tablename__ = "project_submittal_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submittal_id: Mapped[int] = mapped_column(ForeignKey("project_submittals.id"), nullable=False)
    submittal: Mapped["ProjectSubmittal"] = relationship(back_populates="events")

    # "created", "status", "revision", "updated"
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    by = relationship("User")
    at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
