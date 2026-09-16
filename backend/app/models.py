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
    # An Edwards fire alarm carries the voice evacuation and fire telephone in
    # one system (app.services.system_rules) unless this says a separate voice
    # evacuation panel is provided.
    separate_ve_panel: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # Whether this project's documents may be sent to an AI provider at all:
    # "allowed" or "blocked". Explicit and per project, because a client's
    # drawings and forms are theirs; checked at every point a request would
    # leave the platform (app.ai.project_policy).
    ai_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="allowed", server_default="allowed")

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


class BackgroundJob(Base):
    """Long work the server runs and keeps: its progress, whether a stop was
    asked for, and how it ended (app.services.jobs)."""

    __tablename__ = "background_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    # "boq_reread" | "documents_intake"
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
