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
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Float,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutils import utc_now
from app.database import Base


class RoleEnum(str, enum.Enum):
    """Who someone is on the platform.

    An engineer's role is their discipline and what they do in it -- a
    fire alarm designer and a fire alarm estimator are not the same job,
    and neither is a fire alarm designer and an ELV one. Kept as one flat
    value rather than two columns because every permission in the
    platform is asked as "is this user one of these roles", and the
    helpers below are what keep that from becoming six names in every
    list (`DESIGN_ROLES`, `ESTIMATION_ROLES`).
    """

    admin = "admin"
    design_manager = "design_manager"

    fire_alarm_design_engineer = "fire_alarm_design_engineer"
    elv_design_engineer = "elv_design_engineer"
    fire_fighting_design_engineer = "fire_fighting_design_engineer"

    fire_alarm_estimation_engineer = "fire_alarm_estimation_engineer"
    elv_estimation_engineer = "elv_estimation_engineer"
    fire_fighting_estimation_engineer = "fire_fighting_estimation_engineer"

    draftsman = "draftsman"
    viewer = "viewer"


# The three disciplines, and what each is called where a project area is
# named (`app.deps`, and the frontend's DIVISIONS).
FIRE_ALARM, ELV, FIRE_FIGHTING = "fire_alarm", "elv", "fire_fighting"
DISCIPLINE_AREAS = {FIRE_ALARM: "estimation", ELV: "elv", FIRE_FIGHTING: "fire-fighting"}

DESIGN_ROLES = (
    RoleEnum.fire_alarm_design_engineer,
    RoleEnum.elv_design_engineer,
    RoleEnum.fire_fighting_design_engineer,
)
ESTIMATION_ROLES = (
    RoleEnum.fire_alarm_estimation_engineer,
    RoleEnum.elv_estimation_engineer,
    RoleEnum.fire_fighting_estimation_engineer,
)
_DISCIPLINE_OF = {
    RoleEnum.fire_alarm_design_engineer: FIRE_ALARM,
    RoleEnum.fire_alarm_estimation_engineer: FIRE_ALARM,
    RoleEnum.elv_design_engineer: ELV,
    RoleEnum.elv_estimation_engineer: ELV,
    RoleEnum.fire_fighting_design_engineer: FIRE_FIGHTING,
    RoleEnum.fire_fighting_estimation_engineer: FIRE_FIGHTING,
}


def discipline_of(role: "RoleEnum") -> str | None:
    """Which of the three systems this role designs or estimates, or None
    for a role that is not tied to one (an admin, a manager, a viewer)."""
    return _DISCIPLINE_OF.get(role)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[RoleEnum] = mapped_column(Enum(RoleEnum, length=40), nullable=False, default=RoleEnum.viewer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), default=utc_now, onupdate=utc_now, nullable=False
    )


class EstimationProject(Base):
    __tablename__ = "estimation_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    client: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)


class DivisionProject(Base):
    __tablename__ = "division_projects"
    __table_args__ = (UniqueConstraint("division", "reference", name="uq_division_project_reference"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    division: Mapped[str] = mapped_column(String(30), nullable=False)
    reference: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    client: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)


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
    # An Edwards fire alarm carries the voice evacuation and fire telephone in
    # one system (app.services.system_rules) unless this says a separate voice
    # evacuation panel is provided.
    separate_ve_panel: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # Whether this project's documents may be sent to an AI provider at all:
    # "allowed" or "blocked". Explicit and per project, because a client's
    # drawings and forms are theirs; checked at every point a request would
    # leave the platform (app.ai.project_policy).
    ai_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="allowed", server_default="allowed")

    # Where the project's specifications were found (app.routers.compliance):
    # the matches as found, with the warnings and when. The folder is searched
    # once; every later open reads these and goes straight to the files, and
    # searches again only on request or when a file is no longer where it was.
    spec_locations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    spec_warnings: Mapped[list | None] = mapped_column(JSON, nullable=True)
    specs_found_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    # When the document index was last synced with the folder, and the
    # folder's listing then (app.services.document_sync).
    documents_synced_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    documents_listing_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # When the shop drawing records were last brought up to the index
    # (app.services.shop_drawings.reconcile): older than the last sync, the
    # Drawings page catches up once, then reads the records.
    drawings_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

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

    # Bumped on every write to the BOQ lines / the project information. A
    # save names the version it was edited from; a different number means
    # someone else saved in between, and the save is refused rather than
    # silently replacing their work (app.services.concurrency).
    boq_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    details_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

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

    @property
    def voice_evacuation_integrated(self) -> bool:
        from app.services import system_rules

        return system_rules.project_integrated(self)

    @property
    def system_codes(self) -> list[str]:
        from app.services import system_rules

        return system_rules.project_codes(self)

    @property
    def drawings_in_scope(self) -> bool:
        from app.services import system_rules

        return system_rules.drawings_in_scope(self)


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
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

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


class EquipmentCurrent(Base):
    """The equipment current table: what one part of a fire alarm system
    draws, shared by every project (app.services.equipment_currents).

    A mechanical part (`no_load`), a part built into another module
    (`no_load` with `included_in`), or a device with its standby and alarm
    figures and their source. One row per part number; `aliases` are the
    spellings scanned sheets have produced for it. A part in this table is
    settled: the battery calculation takes its figure from here and asks no
    engineer to confirm it.
    """

    __tablename__ = "equipment_currents"
    __table_args__ = (UniqueConstraint("manufacturer", "key", name="uq_equipment_current_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    manufacturer: Mapped[str] = mapped_column(String(64), nullable=False, default="EDWARDS")
    # part_key(part_no): how BOQ part numbers are matched.
    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    part_no: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # "mechanical" | "built_in" | "device" | "unknown"
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="device")
    no_load: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    standby_ma: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)
    alarm_ma: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)
    included_in: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    aliases: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # The datasheet in the library the row refers to: where a figure was
    # read (with the pages), or where a no-load part is listed. `datasheet_match`
    # says how the sheet was matched to the part -- "filename" / "family"
    # (the sheet is the part's own) or "text" (it only mentions the part).
    # The sheet's content hash is kept so a newer revision of it is noticed.
    datasheet_library: Mapped[str | None] = mapped_column(String(64), nullable=True)
    datasheet_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    datasheet_pages: Mapped[list | None] = mapped_column(JSON, nullable=True)
    datasheet_match: Mapped[str | None] = mapped_column(String(16), nullable=True)
    datasheet_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


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

    # The building the sheet quotes the line for, as one canonical name --
    # not only inside the free-text group heading, where OCR's spellings of
    # one banner made several buildings (app.extraction.identity).
    building: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # The part number as a library or the sheet itself spells it, for
    # matching; `catalog_no` stays what was read. {"source", "cleaned",
    # "canonical", "reason"}.
    catalog_canonical: Mapped[str | None] = mapped_column(String(128), nullable=True)
    catalog_match: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- provenance: where the line came from, as read, and who changed it.
    #
    # "extracted" (read off a Design Sheet by a recorded run), "ai_accepted"
    # (a dropped row an engineer accepted, with or without a model's reading),
    # "manual" (typed in), "legacy" (stored before provenance was kept).
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="manual", server_default="legacy")
    extraction_run_id: Mapped[int | None] = mapped_column(ForeignKey("extraction_runs.id"), nullable=True, index=True)
    source_document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # [x0, y0, x1, y1] at the extractor's render DPI.
    source_region: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # The OCR text as read and what the typed parser made of it:
    # {catalog_no, description, quantity, quantity_parse}.
    raw_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ocr_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The line's sheet-carried values as the machine first stored them, kept
    # when an engineer edits it, so "extracted" and "corrected" stay apart.
    extracted_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    edited_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True, default=utc_now)
    # The AI verification's verdict on this line against its Design Sheet
    # (app.ai.verification): {"status": "confirmed" | "corrected" | "added" |
    # "unresolved", "verification_id", "at", "sources", "reason"}. Cleared when
    # anyone changes a value the sheet carries.
    ai_check: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ProjectDocument(Base):
    """A document the project draws on, identified by its content, not its
    filename: the DRF, each Design Sheet, an upload.

    The intake gate (app.services.document_intake) fills this in: that the
    file is there and readable, how many pages it has against how many it
    says it has, whether the EP number in its name and folder is this
    project's, whether it lies inside the project's folder, and whether the
    same content is attached twice. `findings` holds what it found; a
    `blocked` document's values are not to be trusted until someone settles
    the finding. `path` is the absolute location and is not sent to viewers
    as-is; `relative_path` is what the pages show.
    """

    __tablename__ = "project_documents"
    __table_args__ = (UniqueConstraint("project_id", "role", "path", name="uq_project_document_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    # "drf" | "design_sheet"
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    system_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    relative_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mime: Mapped[str | None] = mapped_column(String(100), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # {"declared_total": 2, "numbers": [1], "pages_read": 1}
    printed_pages: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # "unchecked" | "ok" | "warning" | "blocked"
    intake_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unchecked", index=True)
    # [{"code", "severity", "message", "detail"}]
    findings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    intake_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    # A finding the engineer has looked at and accepted ("the file is named
    # EP-30088 but it is this project's sheet"), with who and why.
    acknowledged: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # --- the document index (app.services.document_sync) ---
    # Every file in the project folder is a row too, with `role`
    # "submittal_form" / "spec" / "document"; the sync compares size and
    # mtime first, the content hash second, and reads only what changed.
    mtime: Mapped[float | None] = mapped_column(Float, nullable=True)
    # "fresh" | "stale" | "processing" | "failed" | "removed"
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="fresh", server_default="fresh")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What was read off it: its reference, revision and status, the
    # document-control records (`extracted["records"]`) and, for a form,
    # the model's reading (`extracted["form"]`, stored in document_readings).
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    revision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    extracted: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reading_id: Mapped[int | None] = mapped_column(ForeignKey("document_readings.id"), nullable=True)
    index_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_processed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class DocumentDependency(Base):
    """What was built from a document, so a change to the document marks
    it stale and nothing else: a Design Sheet -> the BOQ, the DRF -> Project
    Info, a specification -> the compliance page, a submittal form -> its
    register row and its log entry. `last_validated_sha256` is the content
    the dependent was last built from."""

    __tablename__ = "document_dependencies"
    __table_args__ = (UniqueConstraint("source_document_id", "dependent_type", "dependent_id", name="uq_document_dependency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("project_documents.id"), nullable=False)
    # "boq" | "details" | "compliance" | "submittal" | "log"
    dependent_type: Mapped[str] = mapped_column(String(24), nullable=False)
    dependent_id: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    last_validated_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)


class ProjectFrcCables(Base):
    """The fire-rated cables a full-package project proposes: the brand,
    and the size of each system's cable (2C x 1.5 or 2C x 2.5 mm2). The
    warnings the choices raise are computed, not stored
    (app.services.frc_cables)."""

    __tablename__ = "project_frc_cables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, unique=True)
    brand: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fire_alarm_loop: Mapped[str | None] = mapped_column(String(16), nullable=True)
    voice_evacuation: Mapped[str | None] = mapped_column(String(16), nullable=True)
    power_24vdc: Mapped[str | None] = mapped_column(String(16), nullable=True)
    fire_telephone: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The emergency light monitoring cable, on a monitored self-contained
    # system: its brand (RAMCRO, the only one for now) and size (one size).
    monitoring_brand: Mapped[str | None] = mapped_column(String(64), nullable=True)
    monitoring_size: Mapped[str | None] = mapped_column(String(16), nullable=True)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now, nullable=False)


class ProjectFloorSchedule(Base):
    """A floor-wise BOQ read off the schedule an engineer keeps in Excel.

    The other way a floor-wise BOQ arrives. The drawings route counts
    symbols on a CAD layout (`project_floor_boq`); this is the workbook a
    project is actually run from, where the quantities are already
    written down -- a row per item, a column per floor, and "1 to 13" for
    the thirteen typical floors nobody writes out.

    The workbook itself is not kept; what it said is
    (`app.services.floor_schedule.Schedule`, as JSON). One row per
    project, replaced each time a schedule is handed in.
    """

    __tablename__ = "project_floor_schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, unique=True)
    # The workbook this was read from, for the page to name.
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sheet_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Where the workbook itself was filed in the project's own folder
    # ("03- Design/EP-30880 FLOOR WISE BOQ.xlsx"), relative to the project.
    # Null on a PC where that folder is not reachable: the schedule is
    # still read and shown, it is simply not filed.
    archive_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The workbook in the project folder this was last read from, and what
    # it contained then. The tab re-reads it by itself when the file
    # changes, so an engineer keeps the schedule in Excel and the platform
    # follows -- nothing is uploaded twice.
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Quantities an engineer set by hand, kept apart from the reading:
    # {item description: {floor: quantity}}. Held separately so that
    # re-reading a changed workbook does not throw the corrections away --
    # they are applied again over whatever the sheet now says.
    edits: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now, nullable=False)


class ProjectAmplifierDesign(Base):
    """What a project sets its voice evacuation speakers to, and anything
    an engineer has adjusted by hand.

    The schedule itself is not stored: it is worked out from the
    floor-wise BOQ every time the tab is opened
    (`app.services.amplifier_calculation`), so a speaker added to a floor
    shows in the amplifier loading without anything being re-imported.
    What is stored is only what cannot be derived -- the tapping each
    speaker is set to, and any quantity the engineer has overridden.
    """

    __tablename__ = "project_amplifier_design"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, unique=True)
    # {part number: watts} -- the tapping this project sets each speaker to.
    taps: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # {part number: milliamps} -- the current this project takes each 24 V
    # appliance at, where its datasheet gives more than one figure. The
    # power calculation is the other half of the same design, so it is kept
    # on the same row rather than in a table of its own.
    currents: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now, nullable=False)


class BrandSupplier(Base):
    """Who supplies a brand to the company -- the trading company, its
    contacts, its address -- one row per brand, for every project: shown
    beside the brand wherever it is chosen (the FRC cables), kept up to
    date by the engineers."""

    __tablename__ = "brand_suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    supplier: Mapped[str] = mapped_column(String(200), nullable=False)
    contact: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    emails: Mapped[str | None] = mapped_column(String(300), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    map_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    website: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now, nullable=False)


class PartDatasheetLink(Base):
    """Which datasheet in the company library documents a part whose number
    the library's file names do not carry -- a variant (NEXI300-3H-CGL-IPM
    is on NEXI300-3H-CGL.pdf), an assembly (SL2-42D3D-CGL-M+SL23I), a
    controller in another size. One link per part per manufacturer, kept
    for every project: set once, found everywhere."""

    __tablename__ = "part_datasheet_links"
    __table_args__ = (UniqueConstraint("manufacturer", "key", name="uq_part_datasheet_link"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    manufacturer: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # part_key(part_no): how BOQ part numbers are matched.
    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    part_no: Mapped[str] = mapped_column(String(120), nullable=False)
    library: Mapped[str] = mapped_column(String(64), nullable=False)
    # Relative to the library folder, as the datasheet listing gives it.
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="engineer", server_default="engineer")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)


class DatasheetDocument(Base):
    """The manufacturer's document number for one sheet in the library --
    Edwards "E85001-0495", printed in the footer of every page.

    The library is a folder of PDFs read at runtime, so this is the one
    place a fact about a file can be kept. Keyed by the file, not by the
    number: a number is *not* unique to a file. One Edwards datasheet
    covers a product family, and the library files the same document under
    each product -- E85001-0279 is SIGA-270, SIGA-278 and SIGA-270P -- so
    renaming the files to their number would collide and lose two of the
    three.

    `source` is "extracted" when the reader took it from the pages, and
    "engineer" when someone typed it in. Extraction never overwrites what
    an engineer entered: seventeen Edwards sheets print no number at all,
    and a hand-entered one is the only record there is.
    """

    __tablename__ = "datasheet_documents"
    __table_args__ = (UniqueConstraint("library", "path", name="uq_datasheet_document"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Relative to the library folder, as the datasheet listing gives it.
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    reference_no: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="engineer", server_default="engineer")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now, nullable=False)


class ProjectProposedMaterial(Base):
    """A material proposed for the project beyond what its BOQ quotes --
    added by an engineer on the Proposed Materials tab, with no quantity
    needed. The BOQ's own parts are proposed materials too, but they are
    read from the BOQ, not copied here."""

    __tablename__ = "project_proposed_materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    system_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    catalog_no: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    quantity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)


class BatteryPanelResult(Base):
    """One panel's battery calculation as last made, under the hash of
    everything it was made from: its BOQ lines, its settings, the currents
    of its parts, the batteries on file. Read back while those stand, so a
    panel nothing changed for is not recomputed; recomputed for that panel
    alone when they move; kept and marked `stale` when a recalculation
    fails, so the previous figures stay visible."""

    __tablename__ = "battery_panel_results"
    __table_args__ = (UniqueConstraint("project_id", "panel_key", name="uq_battery_panel_result"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    # "<system>|<BOQ group heading>|<n>", as app.schemas_design.BatteryDesign keys its panels.
    panel_key: Mapped[str] = mapped_column(String(200), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # app.schemas_design.BatteryPanelOut, as JSON.
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    # "fresh" | "stale"
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="fresh", server_default="fresh")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now, nullable=False)


class BoqCandidate(Base):
    """A re-read of the Design Sheets waiting on an engineer.

    Re-extraction never replaces the BOQ. It records its runs, builds the
    lines it read as a candidate, and lays them against the BOQ as it stood
    (`base_boq_version`): row by row, added / removed / changed. The engineer
    decides each change -- take the new, keep the old -- and only the taken
    ones are written, after the BOQ as it was is kept as a snapshot.
    """

    __tablename__ = "boq_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    # "pending" | "applied" | "discarded" | "superseded"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    base_boq_version: Mapped[int] = mapped_column(Integer, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    run_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # The lines read, each with its provenance.
    lines: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # [{"id", "kind", "match", "before", "after", "fields", "confidence", "reason"}]
    changes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    # {change_id: "accept" | "keep" | "edit"} and any edited values.
    decisions: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class BoqSnapshot(Base):
    """The BOQ exactly as it stood before something replaced part of it --
    an applied candidate, a bulk repair. Never updated or deleted: it is how
    an engineer's lines survive a re-read they later regret."""

    __tablename__ = "boq_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    boq_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    # Every line with its provenance columns.
    items: Mapped[list] = mapped_column(JSON, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)


class AiVerification(Base):
    """One run of the AI check of a project against its source documents:
    the BOQ against the Design Sheets, or Project Info against the DRF.

    Every item it looked at is kept with what each source said -- the value
    held, the fresh OCR read, the AI's reading and, where they disagreed, a
    second independent AI reading -- and what was decided. Changes are applied
    by the run itself; `undo` holds what is needed to put them back (the BOQ
    snapshot taken before, or the previous Project Info values)."""

    __tablename__ = "ai_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    # "boq" | "details"
    scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # "running" | "completed" | "failed" | "undone"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    # {"confirmed", "corrected", "added", "removed", "unresolved", "not_checked"}
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    items: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    notes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The version of the BOQ / details the run left behind: a later save makes it stale.
    version_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    undo: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    models: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


_ACTIVE_SYNC = "kind = 'sync_documents' AND status IN ('queued', 'running')"
_ACTIVE_DEDUP = "dedup_key IS NOT NULL AND status IN ('queued', 'running')"


class BackgroundJob(Base):
    """Long work the server runs and keeps: its progress, whether a stop was
    asked for, and how it ended (app.services.jobs).

    Most kinds run on a thread of the API. A document sync runs in the
    worker process (app.workers.sync_worker), which claims it from this
    table: the table is the queue, and the one place both processes agree
    on what is running."""

    __tablename__ = "background_jobs"
    # One queued-or-running sync per project, refused by the database itself:
    # a check in Python lets two requests both see "none running" and both
    # insert one (app.services.jobs.enqueue).
    __table_args__ = (
        Index("uq_background_jobs_one_active_sync", "project_id", "kind", unique=True,
              sqlite_where=text(_ACTIVE_SYNC), postgresql_where=text(_ACTIVE_SYNC)),
        # One queued-or-running job per `dedup_key`: the same file sent twice
        # for the same drawing and revision is one read (app.services.jobs.enqueue).
        Index("uq_background_jobs_active_dedup", "dedup_key", unique=True,
              sqlite_where=text(_ACTIVE_DEDUP), postgresql_where=text(_ACTIVE_DEDUP)),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    # "boq_reread" | "documents_intake" | "sync_documents" | ...
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # "queued" | "running" | "succeeded" | "failed" | "cancelled"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    progress: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    # The worker holding the job, and when it last said it was alive: a
    # running job whose heartbeat stops was left by a worker that died.
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    # How many times a worker died while running it (app.services.jobs.
    # recover_stale); a job that keeps taking its worker down is failed
    # rather than restarted for ever.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # What a worker job needs to run, written by the API when it queues it:
    # the worker has nothing else (an IFC read: the staged file, the drawing
    # and revision it is issued as).
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # What makes two jobs the same job ("ifc_read:<project>:<sha256>:<drawing>:<revision>").
    dedup_key: Mapped[str | None] = mapped_column(String(200), nullable=True)


class BackgroundWorker(Base):
    """A worker process (app.workers.sync_worker) and its heartbeat, so the
    API can tell a sync that is waiting its turn from one that has no
    worker to run it."""

    __tablename__ = "background_workers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    current_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The kinds of job it runs: "sync" (document syncs) or "ifc" (IFC reads).
    lane: Mapped[str | None] = mapped_column(String(16), nullable=True)


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


_ONE_PER_BRAND = "system_code IS NOT NULL"


class ProjectSubmittal(Base):
    """A material submittal: the package of materials of one system from one
    brand, submitted to the consultant and revised (R0, R1, ...) until
    approved (app.services.submittal_identity).

    A system has one material submittal per brand, however many revisions,
    replies, files or references it goes through: the forms filed as our
    own copy and as the main contractor's are revisions of it
    (`revisions`), and the database refuses a second one for the same
    system and brand. A system submitted from several brands -- fire rated
    cable offered from Fireguard, Frontier and Tianjie -- has one for each.
    `revision`, `status` and `reply_code` here are its latest revision's,
    kept beside it so the register reads the way it always did.

    History is kept, not overwritten: each revision's status changes are in
    its `history`, and the register's events say what happened when.
    """

    __tablename__ = "project_submittals"
    __table_args__ = (
        Index("uq_project_submittals_one_per_brand", "project_id", "system_code", "brand_key", unique=True,
              sqlite_where=text(_ONE_PER_BRAND), postgresql_where=text(_ONE_PER_BRAND)),
    )

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
    # The brand this submittal is for, in one spelling ("" when unknown):
    # with the system, what makes it the one submittal it is.
    brand_key: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
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
    revisions: Mapped[list["ProjectSubmittalRevision"]] = relationship(
        back_populates="submittal",
        cascade="all, delete-orphan",
        order_by="ProjectSubmittalRevision.revision",
    )


class ProjectSubmittalRevision(Base):
    """One revision of a material submittal (R0, R1, ...) and where it stands
    now -- one current status, updated in place when the consultant answers.
    What it was before is in `history`, never a second revision."""

    __tablename__ = "project_submittal_revisions"
    __table_args__ = (UniqueConstraint("submittal_id", "revision", name="uq_submittal_revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submittal_id: Mapped[int] = mapped_column(ForeignKey("project_submittals.id"), nullable=False, index=True)
    submittal: Mapped["ProjectSubmittal"] = relationship(back_populates="revisions")
    # "R00", "R01", ... as the parent's revision is written.
    revision: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[SubmittalStatus] = mapped_column(Enum(SubmittalStatus), nullable=False)
    reply_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # The form this revision is on file as, and the references of the other
    # forms filed as the same revision (our own copy's, another supplier's).
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    also_filed_as: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    manufacturer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    document_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The consultant's words on this revision, where a reply has been read.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)

    history: Mapped[list["ProjectSubmittalStatusChange"]] = relationship(
        back_populates="revision_row",
        cascade="all, delete-orphan",
        order_by="ProjectSubmittalStatusChange.changed_at",
    )


class ProjectSubmittalStatusChange(Base):
    """A revision's status as it changed: "R0 under review -> approved".
    History only -- nothing is counted from it."""

    __tablename__ = "project_submittal_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("project_submittal_revisions.id"), nullable=False, index=True)
    revision_row: Mapped["ProjectSubmittalRevision"] = relationship(back_populates="history")
    previous_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    new_status: Mapped[str] = mapped_column(String(16), nullable=False)
    reply_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # What changed it: "ai_check" (the folder read), "manual", "filed", "migrated".
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)


class ProjectAction(Base):
    """Something the project is waiting on, held once and shown by every
    page that lists it (Home, Material Submittals): "BBY006-...-FA-0003 R0
    was returned; R1 is not filed". Opened and resolved by
    app.services.project_state from the project's own records, never by a
    page: when R1 is filed the one row is resolved and it is gone
    everywhere at once."""

    __tablename__ = "project_actions"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_project_actions_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    # What the action is for, the same every time it is worked out:
    # "submittal:FRC|FRONTIER:R01", "system:ELS:no-submittal".
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    system_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="warning")
    # The page that deals with it, relative to the project ("submittal").
    link: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProjectChange(Base):
    """PROJECT_DATA_CHANGED: one row per change to a project's records, in
    the transaction that made it. A page asks for the changes after the
    last one it saw (GET /projects/{id}/changes?since=) and reloads what
    they touch -- the only way a change made by the worker process, or by
    another user, reaches a page already open."""

    __tablename__ = "project_changes"
    # Ids never reused once old rows are pruned: a page's "since" stays true.
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    # "submittal", "documents", "action", "drawing".
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    system_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # "created", "updated", "deleted", "synced", "opened", "resolved".
    change_type: Mapped[str] = mapped_column(String(40), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False, index=True)


class SubmittalReply(Base):
    """Our answer to the consultant's comments on one submittal revision.

    The consultant returns a submittal with remarks; the reply sheet takes
    them one by one and says how each is met. It is a document the
    engineer writes and re-writes until it is sent, so it is kept here and
    exported, not assembled fresh each time.

    Keyed by the submittal's **reference and revision**, not by a register
    row: most of the submittals that need one were prepared outside the
    platform and are read from the project folder, where they have no row
    of their own. The reference is what both kinds share.

    `rows` is the sheet as the engineer edits it -- a list of
    `{sn, comment, reply, remark}` -- kept whole rather than a table of
    lines, because that is how it is written and how it is exported.
    """

    __tablename__ = "submittal_replies"
    __table_args__ = (
        UniqueConstraint("project_id", "reference", "revision", name="uq_submittal_reply"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    # As the submittal's own form carries it, matching ProjectSubmittal.
    reference: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[str] = mapped_column(String(16), nullable=False, default="R00")
    system_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The sheet's own heading, where it differs from the project's.
    consultant: Mapped[str | None] = mapped_column(String(255), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    rows: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), default=utc_now, onupdate=utc_now, nullable=False
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


# --- Extraction runs, issues, AI proposals and usage -------------------------
#
# A run records one deterministic read of one document: what it covered,
# its outcome, and the issues it could not settle. A proposal is what a
# model answered about one issue, with the platform's verdict on it; it
# moves nothing until an engineer accepts it. Usage is every call, cache
# hit included, for the diagnostics view and the daily budget.


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    # "design_sheet" | "drf"
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    document_path: Mapped[str] = mapped_column(Text, nullable=False)
    document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    system_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    lines_accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coverage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    failure: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "auto" (on first open) | "manual" (Assist / Retry)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    # Who read the lines: "ocr" (Tesseract alone) or "ai" (the model read
    # every page, with the OCR read as the witness -- app.ai.sheet_reader).
    reader: Mapped[str] = mapped_column(String(8), nullable=False, default="ocr", server_default="ocr")
    # The stored AI reading the lines came from, when `reader` is "ai".
    reading_id: Mapped[int | None] = mapped_column(ForeignKey("document_readings.id"), nullable=True)
    ai_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_cost: Mapped[float] = mapped_column(Numeric(10, 5), nullable=False, default=0)
    budget_exhausted: Mapped[str | None] = mapped_column(String(48), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    issues: Mapped[list["ExtractionIssue"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ExtractionIssue.id"
    )


class ExtractionIssue(Base):
    __tablename__ = "extraction_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("extraction_runs.id"), nullable=False, index=True)
    run: Mapped["ExtractionRun"] = relationship(back_populates="issues")

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    region: Mapped[list | None] = mapped_column(JSON, nullable=True)   # [x0, y0, x1, y1] at the render dpi
    target: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # "open" | "proposed" | "resolved" | "rejected" | "starved"
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    state_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_by = relationship("User")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    resolved_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    proposals: Mapped[list["AiProposal"]] = relationship(
        back_populates="issue", cascade="all, delete-orphan", order_by="AiProposal.id"
    )


class AiProposal(Base):
    __tablename__ = "ai_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("extraction_issues.id"), nullable=False, index=True)
    issue: Mapped["ExtractionIssue"] = relationship(back_populates="proposals")

    task: Mapped[str] = mapped_column(String(32), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(8), nullable=False)
    proposal: Mapped[dict] = mapped_column(JSON, nullable=False)
    # "validated" | "needs_human_review" | "rejected" | "insufficient_evidence"
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    state_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_cache: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Instruction-like wording found in the document text the call carried;
    # such a proposal is never shown as validated.
    injection_flags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # What the engineer did with the issue, recorded against every proposal on
    # it so the model's answers can be measured against reviewed truth:
    # "accepted" (the value taken as proposed) | "corrected" (another value
    # taken) | "rejected" (the issue closed with no value) | "abstained" (the
    # model gave no value; the engineer decided) | "superseded".
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    outcome_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)


class AiUsage(Base):
    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("extraction_runs.id"), nullable=True)
    task: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    escalated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # "ok" | "transport" | "rate_limit" | "invalid_response" | "refused" | "auth" | "rejected"
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False, index=True)


class DocumentReading(Base):
    """What the AI read off one document, kept for good.

    One row per document content (its SHA-256), kind and reading prompt: the
    model's page-by-page reading of a Design Sheet, or its reading of a
    DRF's fields and Systems table. A project opened later whose document
    has the same content -- the same project reopened, or another project
    filed with the same sheet -- takes the reading from here and calls no
    model. Only a completed reading is reused; a failed one is recorded so
    the page can say why, and is read again on the next open.
    """

    __tablename__ = "document_readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The project that first asked; access follows the document's content.
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    # "design_sheet" | "drf"
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    document_path: Mapped[str] = mapped_column(Text, nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # design_sheet: {"pages": [{"page", "width", "height", "rows": [{"kind",
    # "quantity", "catalog_no", "description", "readable", "box"}]}]}
    # drf: {"fields": {name: value | None}, "systems": {name: {...}} | None}
    reading: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # "completed" | "failed"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)


class ResultCache(Base):
    __tablename__ = "result_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


# --- Compliance statements ----------------------------------------------------
#
# A prepared statement is the specification's clauses with an answer each and
# where the answer came from (a rule, a past statement, the model, the
# engineer). A check is a submitted statement laid against the specification,
# with what is missing, unanswered or contradicted. Both keep the
# specification they were made against and the verdict on whether it is this
# project's.


class ComplianceStatement(Base):
    __tablename__ = "compliance_statements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    # "prepare" | "check"
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    system_code: Mapped[str] = mapped_column(String(16), nullable=False)
    # {path, member, first_page, last_page, filename, uploaded, sha256,
    #  section_numbers, title, header_lines}
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    verification: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # prepare: [{id, ref, label, level, text, page, heading, response, remark,
    #            source, state, reference}]
    # check:   [{id, ref, ..., statement_text, response, remark, findings}]
    rows: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reference_files: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # The statement checked, for kind "check".
    statement_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The engineer's sign-off on a prepared statement. Nothing is exported
    # without it, and it is withdrawn the moment an answer changes under it
    # (app.compliance.service.approval_fingerprint).
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Kept as signed, so the exported statement names the engineer even if
    # the account is renamed later.
    approved_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The answers as approved: the fingerprint of every row's response,
    # remark, technical status and workflow at the moment of approval.
    approved_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now, nullable=False)


class ComplianceLearnedAnswer(Base):
    """An answer an engineer signed off -- a clause marked reviewed, or every
    answered clause of an approved statement. The next statement reuses it:
    the same wording is drafted from it without a model, and the nearest ones
    travel with the clauses the AI answers, as the company's preferred answers.

    One row per statement clause; reviewing it again updates it, and taking
    the review back retires it."""

    __tablename__ = "compliance_learned_answers"
    __table_args__ = (UniqueConstraint("statement_id", "clause_id", name="uq_learned_statement_clause"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("compliance_statements.id"), nullable=False, index=True)
    clause_id: Mapped[str] = mapped_column(String(16), nullable=False)
    clause_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    system_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    clause_text: Mapped[str] = mapped_column(Text, nullable=False)
    # app.knowledge.normalize.requirement_hash of the clause: what "the same wording" means.
    clause_hash: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    response: Mapped[str] = mapped_column(String(48), nullable=False)
    remark: Mapped[str] = mapped_column(Text, nullable=False, default="")
    technical_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The project's manufacturers for the system when it was signed off, canonical and comma-separated.
    manufacturers: Mapped[str | None] = mapped_column(String(255), nullable=True)
    project_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


# --- the compliance knowledge base -------------------------------------------------
#
# The company's historical compliance-statement responses, imported from the
# Compliance Response Database workbook (app.knowledge.importer) into the
# application's own database, so that autofill is a query and never opens
# the source files. IDs are the source's stable content-derived IDs (SRC-,
# REQ-, MAP-, RSP-) so a record can always be traced back. Rows that a later
# import no longer finds are kept, inactive, not deleted.


class KnowledgeImport(Base):
    """One run of the import: what it found, what it changed, how it ended."""

    __tablename__ = "knowledge_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    # "running" | "succeeded" | "unchanged" | "failed"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    # The source collection's own name, never its filesystem path.
    source_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workbook_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workbook_built: Mapped[str | None] = mapped_column(String(32), nullable=True)
    files_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_inactive: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_flagged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # {"files": [{path, role, action, note}], "counts": {...}, "errors": [...]}
    report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class KnowledgeFile(Base):
    """A file of the source collection as last seen: its hash decides whether
    the next import reads it again."""

    __tablename__ = "knowledge_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    relative_path: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    # "canonical" | "index" | "duplicate_export" | "intermediate" | "documentation" | "agent" | "other"
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    sha1: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mtime: Mapped[float | None] = mapped_column(Numeric(16, 3), nullable=True)
    # "imported" | "unchanged" | "skipped" | "failed" | "online_only"
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_import_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_imports.id"), nullable=True)


class KnowledgeSource(Base):
    """A past submittal document the records were read from."""

    __tablename__ = "knowledge_sources"

    source_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duplicate_copies: Mapped[str | None] = mapped_column(Text, nullable=True)
    project: Mapped[str | None] = mapped_column(String(255), nullable=True)
    job_number: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    client: Mapped[str | None] = mapped_column(String(255), nullable=True)
    system: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    brand: Mapped[str | None] = mapped_column(String(64), nullable=True)
    specification_family: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    section_numbers: Mapped[str | None] = mapped_column(String(255), nullable=True)
    document_revision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    document_date: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_review_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Issues the source's review report logged against this document.
    source_review_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("knowledge_imports.id"), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


class KnowledgeRequirement(Base):
    """A distinct specification requirement, as printed."""

    __tablename__ = "knowledge_requirements"

    requirement_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    system: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    subsystem: Mapped[str | None] = mapped_column(String(64), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(128), nullable=True)
    exact_requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_hash: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    merge_review_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    technical_variant: Mapped[str | None] = mapped_column(Text, nullable=True)
    numbers: Mapped[str | None] = mapped_column(Text, nullable=True)
    n_sources: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    import_id: Mapped[int] = mapped_column(ForeignKey("knowledge_imports.id"), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


class KnowledgeMapping(Base):
    """Where a requirement occurred: which specification, section, clause and
    source page. A requirement maps to many."""

    __tablename__ = "knowledge_mappings"

    mapping_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("knowledge_requirements.requirement_id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("knowledge_sources.source_id"), nullable=False, index=True)
    specification_family: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    specification_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    specification_edition: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    section_number: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    clause_number: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    clause_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    heading_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_requirement_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_page: Mapped[str | None] = mapped_column(String(16), nullable=True)
    printed_page: Mapped[str | None] = mapped_column(String(16), nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(String(24), nullable=True)
    pairing_confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("knowledge_imports.id"), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


class KnowledgeMappingReview(Base):
    """An administrator's verdict on one source mapping: that the answer the
    extraction paired with a clause really is that clause's answer
    ("verified") or is not ("rejected"). Kept by mapping id, apart from the
    imported tables, so a re-import does not erase the review
    (app.knowledge.eligibility)."""

    __tablename__ = "knowledge_mapping_reviews"

    mapping_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    # "verified" | "rejected"
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)


class KnowledgeResponse(Base):
    """What the company answered to a requirement, once per distinct wording
    and manufacturer."""

    __tablename__ = "knowledge_responses"

    response_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("knowledge_requirements.requirement_id"), nullable=False, index=True)
    system: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    brand: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    applicable_models: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    responsible_party: Mapped[str | None] = mapped_column(String(128), nullable=True)
    historical_response: Mapped[str] = mapped_column(Text, nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    historical_compliance_status: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    cited_references: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_flag: Mapped[str | None] = mapped_column(Text, nullable=True)
    specification_families: Mapped[str | None] = mapped_column(String(255), nullable=True)
    n_sources: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Every source link superseded by a later revision of the same document.
    superseded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    # "eligible" | "blocked" -- the policy in app.knowledge.autofill, applied at import.
    autofill_eligibility: Mapped[str] = mapped_column(String(16), nullable=False, default="blocked", index=True)
    eligibility_reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("knowledge_imports.id"), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


class KnowledgeResponseSource(Base):
    """A response as it appeared in one source document, with that
    document's review outcome."""

    __tablename__ = "knowledge_response_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    response_id: Mapped[str] = mapped_column(ForeignKey("knowledge_responses.response_id"), nullable=False, index=True)
    mapping_id: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("knowledge_sources.source_id"), nullable=False, index=True)
    pdf_page: Mapped[str | None] = mapped_column(String(16), nullable=True)
    historical_review_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    consultant_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    superseded_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("knowledge_imports.id"), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class KnowledgeModel(Base):
    """A model number a response names, one row each, for lookup."""

    __tablename__ = "knowledge_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    response_id: Mapped[str] = mapped_column(ForeignKey("knowledge_responses.response_id"), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class KnowledgeEquivalence(Base):
    """Two requirements the source proposed as equivalent wording. Only an
    engineer's validation makes one usable for autofill."""

    __tablename__ = "knowledge_equivalences"
    __table_args__ = (UniqueConstraint("source_requirement_id", "equivalent_requirement_id", name="uq_knowledge_equivalence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_requirement_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    equivalent_requirement_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    similarity: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    proposal: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # "proposed" | "validated" | "rejected"
    engineer_validation_status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed", index=True)
    validated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    applicability_conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("knowledge_imports.id"), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class KnowledgeIssue(Base):
    """An unresolved issue the source's review report logged against a record."""

    __tablename__ = "knowledge_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    record_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("knowledge_imports.id"), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ComplianceAudit(Base):
    """Every change to a compliance statement row: what it was, what it
    became, from where, by whom, against which inputs."""

    __tablename__ = "compliance_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("compliance_statements.id"), nullable=False, index=True)
    clause_id: Mapped[str] = mapped_column(String(16), nullable=False)
    # "autofill" | "manual" | "ai_review" | "ai_accept" | "ai_reject" | "reviewed" | "recheck"
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    # "manual" | "database" | "ai" | "system"
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    spec_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    boq_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scope_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    knowledge_import_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    knowledge_record_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    review_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ai_prompt_version: Mapped[str | None] = mapped_column(String(48), nullable=True)
    ai_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False, index=True)


class ActivityEvent(Base):
    """What one user did, and when: signed in, opened a project, saved its
    BOQ, issued a revision, changed a submittal, approved a statement.

    One table for every kind of action, read by user, so an account's
    history is a single query (app.services.activity). The project is kept
    as an id and a label rather than a foreign key: deleting a project must
    not erase -- or be blocked by -- the record of who worked on it and who
    deleted it. `detail` holds counts and short before/after values, never
    whole documents: a BOQ save says how many lines, and the issued revision
    is where the lines themselves are kept. Clause-level compliance edits
    stay in compliance_audit, which already names the user.
    """

    __tablename__ = "activity_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    user = relationship("User")
    at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False, index=True)
    # "auth.login", "project.opened", "boq.saved", "submittal.updated", ...
    action: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # "EP-30784 Skyblade", as it was named when the action happened.
    project_label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # "project", "boq_revision", "submittal", "compliance_statement", ...
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


# --- BOQ as per IFC drawings (app/ifc) -------------------------------------------------------
#
# The fire alarm devices counted off an issued-for-construction drawing. A
# symbol is known by what it looks like -- its fingerprint -- not its block
# name, and the engineer verifies every symbol the library does not know
# before any quantity is given. The library (device types, verified
# symbols, the block names seen on them) is the company's, shared by every
# project; a drawing belongs to one project.


class IfcDeviceType(Base):
    """A BOQ line item an IFC symbol can be verified as: "SD Smoke Detector
    (Addressable)". `category` is the tab it counts in."""

    __tablename__ = "ifc_device_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)  # fire_alarm | emergency_light | other
    unit: Mapped[str] = mapped_column(String(10), nullable=False, default="Nos")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)

    symbols: Mapped[list["IfcSymbol"]] = relationship(back_populates="device_type")


class IfcSymbol(Base):
    """A verified symbol: a name-independent fingerprint mapped to a device
    type, or marked as not a device. The library grows with every drawing
    an engineer verifies."""

    __tablename__ = "ifc_symbols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signature: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    inner_label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    raster_hex: Mapped[str] = mapped_column(Text, nullable=False, default="")
    svg: Mapped[str] = mapped_column(Text, nullable=False, default="")
    entity_counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    block_names: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    device_type_id: Mapped[int | None] = mapped_column(ForeignKey("ifc_device_types.id"), nullable=True)
    is_ignored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_drawing: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now, nullable=False)
    # Who decided it, in order of authority: "engineer" (an engineer's
    # answer; never overwritten by anything else), "deterministic" (the
    # symbol's letters and block name named the device, app.ifc.services.
    # symbol_matching), "ai" (the AI's answer that passed every check,
    # app.ifc.services.ai_symbol_review). Only an engineer changes an
    # engineer's answer.
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="engineer", server_default="engineer")
    # The AI's confidence, for source "ai".
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The engineer who last answered or approved it. A plain number, not a
    # foreign key: adding one would rebuild this table, and a rebuild with
    # foreign keys enforced cascades into every block alias.
    reviewed_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    device_type: Mapped["IfcDeviceType | None"] = relationship(back_populates="symbols")
    aliases: Mapped[list["IfcBlockAlias"]] = relationship(back_populates="symbol", cascade="all, delete-orphan")


class IfcBlockAlias(Base):
    """A block name seen on a verified symbol. Used only to *suggest*: a
    known name with different geometry is exactly the wrongly-named case.

    A name is not the same thing on every consultant's drawings. When a
    second symbol with the same block name is answered as something else,
    the name is marked `is_ambiguous` -- the first meaning is not
    overwritten, and an ambiguous name suggests nothing."""

    __tablename__ = "ifc_block_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    block_name: Mapped[str] = mapped_column(String(300), unique=True, index=True, nullable=False)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("ifc_symbols.id", ondelete="CASCADE"), nullable=False)
    is_ambiguous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    symbol: Mapped["IfcSymbol"] = relationship(back_populates="aliases")


class IfcSymbolReview(Base):
    """One AI classification of one symbol signature: what it was asked
    (the candidates), what it answered, and what the backend made of the
    answer. It is the audit of every AI decision and the AI's cache: a row
    with the same `cache_key` (signature, candidates, stage, prompt, model)
    is reused instead of asking again. Errors are recorded, never reused.

    What the engineer then did with the symbol is written back here
    (`outcome`), so the AI's answers can be measured against theirs.
    Project, drawing and job are kept as plain numbers: the review outlives
    them, as the library does."""

    __tablename__ = "ifc_symbol_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signature: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # "metadata" (the symbol's words and counts) | "visual" (its small picture)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    candidate_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # "device" | "not_device" | "uncertain"; None when the call failed
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    device_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    requires_engineer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The backend's gate: "accepted" (taken into the library) | "rejected"
    # (with `validation_reason`) | "uncertain" | "error"
    validation: Mapped[str] = mapped_column(String(16), nullable=False)
    validation_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    drawing_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    # Unverifying the symbol invalidates what the AI said about it: it is not reused.
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    # "approved" (the engineer kept the AI's answer) | "corrected" (another
    # device type) | "not_device" | "device" (the AI was uncertain; the engineer answered)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    outcome_device_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


_LIVE_REVISION = "supersedes_id IS NOT NULL AND deleted_at IS NULL"


class ProjectIfcDrawing(Base):
    """An IFC drawing uploaded to a project, and the symbol groups read off it.

    The DXF read (converted from the DWG where one was uploaded) is kept
    under the platform's uploads, at `stored_path` relative to it; the file
    as uploaded is filed in the project's own folder at `archive_path`.
    What an engineer decided per drawing -- skipped symbols, a sheet's
    number of floors -- is in `meta`.

    A drawing is known by its `drawing_reference` ("FA-101"), not by its
    file name: FA-101-R00.dwg and FA-101-R01.dwg are two revisions of one
    drawing. The database refuses a fork of the revision chain -- two live
    revisions of the same drawing -- whatever two workers do at once
    (app.ifc.services.revisions). A reference read off a file name is
    evidence, not a key: two towers' "FA LAYOUT.dwg" are two drawings, so
    a matching reference asks the engineer rather than being refused. A
    revision with history is archived (`deleted_at`), never removed, so
    R0 -> R1 -> R2 stays readable."""

    __tablename__ = "project_ifc_drawings"
    __table_args__ = (
        Index("uq_project_ifc_drawings_one_revision", "supersedes_id", unique=True,
              sqlite_where=text(_LIVE_REVISION), postgresql_where=text(_LIVE_REVISION)),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    archive_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    units: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    dxf_version: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    groups: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # The IFC revision the drawing was issued as ("R0", "R1" ...), and the
    # revision it replaces: a drawing another supersedes is history, the
    # one nothing supersedes is the drawing in force.
    revision: Mapped[str] = mapped_column(String(10), nullable=False, default="R0")
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("project_ifc_drawings.id"), nullable=True)
    # The drawing's identity, whatever its file is called ("FA-101").
    drawing_reference: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    # The SHA-256 of the file as uploaded: the same file is not imported twice.
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Archived: kept for the revision history, out of every list and BOQ.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    # Who archived it (a user id; plain, so the migration adds columns without rebuilding the table).
    deleted_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


# --- The Drawings page: building floors, shop drawings, revisions -----------------------
#
# IFC defines the building; the shop drawings define the submission history.
# The IFC drawings give the Drawings Log one thing, the building's floors
# (project_building_floors); every system then keeps its own shop drawing
# per floor (project_shop_drawings), each with the revisions the consultant
# answered (shop_drawing_revisions) and the files found that nobody has yet
# shown were submitted (shop_drawing_candidates). The sync discovers the
# documents and writes these records (app.services.shop_drawings); the page
# reads the records. What an engineer confirms is never overwritten by a
# sync or by the AI.


class ProjectBuildingFloor(Base):
    """One floor of the building, as the IFC drawings in force name it:
    the registry every system's Drawings Log is a row of. A typical sheet
    for floors 3 to 14 is twelve floors here, each with its own shop
    drawing state. A floor the latest IFC no longer has keeps its history
    and is flagged; only one with none is made inactive."""

    __tablename__ = "project_building_floors"
    __table_args__ = (UniqueConstraint("project_id", "floor_key", name="uq_project_building_floor"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    # The floor's canonical identity ("B3", "GF", "P1", "L12", "RF", "MECHANICAL#1"): app.services.drawing_log.floor_identity
    floor_key: Mapped[str] = mapped_column(String(80), nullable=False)
    # As the engineers write it ("Ground Floor", "1st Mechanical Floor").
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Its height in the building's order (basements negative), for sorting rows the same way everywhere.
    elevation: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # "ifc" | "shop_drawing" (a floor a shop drawing names that no IFC plan has) | "engineer"
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="ifc")
    # The IFC sheet it came from, for the details panel ("FA 111 · TYPICAL 3RD TO 16TH FLOOR").
    ifc_sheet: Mapped[str | None] = mapped_column(String(300), nullable=True)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    last_detected_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)


class ProjectShopDrawing(Base):
    """One shop drawing of one system: the drawing reference the floor is
    submitted under. A floor has one per system, and the fire alarm's and
    the emergency lighting's share nothing but the floor."""

    __tablename__ = "project_shop_drawings"
    __table_args__ = (UniqueConstraint("project_id", "system_code", "drawing_reference", name="uq_project_shop_drawing"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    system_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    drawing_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    # The floors it is the drawing for (a typical sheet stands for a run), and how the sheet names them.
    floor_keys: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    floor_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Whether it is one drawing issued for a run of floors ("TYPICAL 3RD TO 16TH").
    typical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # An engineer set the reference or the floors by hand: the sync does not change them.
    confirmed_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    remarks: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now, nullable=False)

    revisions: Mapped[list["ShopDrawingRevision"]] = relationship(back_populates="drawing", cascade="all, delete-orphan",
                                                                   order_by="ShopDrawingRevision.number")
    candidates: Mapped[list["ShopDrawingCandidate"]] = relationship(back_populates="drawing", cascade="all, delete-orphan")


class ShopDrawingRevision(Base):
    """One official revision of a shop drawing: submitted (the evidence says
    so), and the consultant's answer to it. Its status is what the reply
    said, never worked out from a later revision; a revision a later one
    proves was submitted, whose reply is not on file, says exactly that."""

    __tablename__ = "shop_drawing_revisions"
    __table_args__ = (UniqueConstraint("shop_drawing_id", "revision", name="uq_shop_drawing_revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_drawing_id: Mapped[int] = mapped_column(ForeignKey("project_shop_drawings.id", ondelete="CASCADE"),
                                                 nullable=False, index=True)
    revision: Mapped[str] = mapped_column(String(10), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # "under_review" | "approved" | "approved_as_noted" | "not_approved" | "reply_not_found"
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="under_review")
    submitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    submission_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    reply_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    reply_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    reply_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The drawing's file in the project folder, and the page its title block is on.
    drawing_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    drawing_page: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    drawing_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The file is no longer in the folder (or OneDrive has not brought it down): the status stands.
    source_missing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # "sync" (read off the documents) | "submission" (a transmittal proved it) | "engineer" | "ai"
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="sync")
    # Why it reads as it does ("R2 was submitted, so R1 was submitted and answered; ...").
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # An engineer set the status by hand: the sync and the AI leave it.
    confirmed_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now, nullable=False)

    drawing: Mapped["ProjectShopDrawing"] = relationship(back_populates="revisions")


class ShopDrawingCandidate(Base):
    """A drawing file found in the folder at a revision nothing proves was
    submitted: R1 on the drive while R0 stands approved. A file found is
    not a revision submitted. It waits here -- shown as "R1 available",
    never as R1's status -- until a submission or a reply proves it, an
    engineer confirms it, or an engineer ignores it (remembered against
    the file's hash, so the next sync does not ask again)."""

    __tablename__ = "shop_drawing_candidates"
    __table_args__ = (UniqueConstraint("shop_drawing_id", "revision", "file_sha256", name="uq_shop_drawing_candidate"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_drawing_id: Mapped[int] = mapped_column(ForeignKey("project_shop_drawings.id", ondelete="CASCADE"),
                                                 nullable=False, index=True)
    revision: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    page: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    detected_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    # "available" | "confirmed" | "ignored" | "superseded" | "conflict"
    candidate_status: Mapped[str] = mapped_column(String(16), nullable=False, default="available")
    # What the AI or the rules found for it ({"suggested": "confirm_submission", "submission": "SD-249", ...}).
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decided_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    drawing: Mapped["ProjectShopDrawing"] = relationship(back_populates="candidates")


class DrawingIssue(Base):
    """One thing on a system's drawings that needs an engineer's eye: a
    revision gap, a candidate revision, a reference used twice, a floor
    the latest IFC no longer has. Found by the rules ("system") or by the
    AI ("ai"), held once by key, resolved when it no longer holds or when
    an engineer settles it. Review & Issues lists the open ones."""

    __tablename__ = "drawing_issues"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_drawing_issue_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    system_code: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    shop_drawing_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    floor_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    key: Mapped[str] = mapped_column(String(240), nullable=False)
    # "revision_candidate" | "revision_gap" | "history_inconsistent" | "reply_missing" | "reference_conflict" |
    # "revision_conflict" | "system_mismatch" | "floor_unknown" | "floor_not_in_ifc" | "source_missing" |
    # "reply_unmatched" | "status_conflict" | "ai_review_required"
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # "info" | "warning" | "error"
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="warning")
    # "system" (deterministic) | "ai"
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="system")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The AI's structured answer, where the finding is its ({"confidence", "reason_code", "suggested_action"}).
    ai: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    resolved_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)


class DrawingRequirementState(Base):
    """Where one of a system's required documents stands (Drawings > Actions
    Required): when it was asked of the contractor, and by whom. What is
    received comes from the folder; this is the part the folder cannot say."""

    __tablename__ = "drawing_requirement_states"
    __table_args__ = (UniqueConstraint("project_id", "system_code", "requirement_key", name="uq_drawing_requirement"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    system_code: Mapped[str] = mapped_column(String(16), nullable=False)
    requirement_key: Mapped[str] = mapped_column(String(40), nullable=False)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    requested_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    remarks: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now, nullable=False)


class ShopDrawingEvent(Base):
    """What happened to a system's shop drawings, in order: R1 detected, R1
    confirmed, R1 ignored, a reply received, a status changed by an
    engineer, a requirement requested. The Activity / History tab. A user
    where one acted; none when the sync found it."""

    __tablename__ = "shop_drawing_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    system_code: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    shop_drawing_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    floor_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # "revision.detected" | "revision.confirmed" | "revision.ignored" | "revision.submitted" | "reply.received" |
    # "status.changed" | "reference.corrected" | "requirement.requested" | "issue.resolved" | "ai.finding" ...
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False, index=True)


class EpArchiveRoot(Base):
    """One configured archive and the state of its directory scan.

    This is discovery metadata, separate from an engineer's saved Project.
    root_key is a SHA-256 of the normalized absolute root path; it keeps
    long Windows paths out of unique database indexes. The scanner that
    supplies these keys and timestamps is introduced in the next stage.
    """

    __tablename__ = "ep_archive_roots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    root_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    root_path: Mapped[str] = mapped_column(Text, nullable=False)
    # pending -> scanning -> ready, or failed with last_error populated.
    scan_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    scan_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scan_started_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    scan_finished_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    last_successful_scan_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)

    folders: Mapped[list["EpArchiveFolder"]] = relationship(
        back_populates="archive", cascade="all, delete-orphan", passive_deletes=True
    )


class EpArchiveFolder(Base):
    """A project folder found in an archive, not an application Project.

    The same EP number may identify multiple locations. Only an archive
    plus a normalized relative path identifies a single directory entry.
    A future successful scan can mark a missing entry unavailable without
    deleting history; a failed/incomplete scan must not do so.
    """

    __tablename__ = "ep_archive_folders"
    __table_args__ = (
        UniqueConstraint("archive_id", "path_key", name="uq_ep_archive_folder_path"),
        Index("ix_ep_archive_folders_lookup", "archive_id", "ep_number", "is_available"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    archive_id: Mapped[int] = mapped_column(ForeignKey("ep_archive_roots.id", ondelete="CASCADE"), nullable=False)
    # Same convention as Project.ep_number: store "29495", not "EP-29495".
    ep_number: Mapped[str] = mapped_column(String(32), nullable=False)
    folder_name: Mapped[str] = mapped_column(Text, nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    path_key: Mapped[str] = mapped_column(String(64), nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    last_seen_scan_token: Mapped[str | None] = mapped_column(String(32), nullable=True)

    archive: Mapped["EpArchiveRoot"] = relationship(back_populates="folders")
