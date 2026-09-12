"""The project design document (ProjectDesign.document) and what is
calculated from it.

The document holds inputs only -- the zone schedule and how the engineer
grouped it -- and the results are recomputed on every read (see
app.services.ve_calculation), so a stored number can never disagree with
the counts it came from.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SpeakerType(BaseModel):
    """One speaker column of the schedule. `key` is what a zone's `counts`
    refer to; `name` is the column heading as the engineer wrote it."""

    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    tap_watts: float = Field(gt=0, le=100)


class Zone(BaseModel):
    """One row of the schedule: a floor, a staircase, an area."""

    name: str = Field(min_length=1, max_length=200)
    counts: dict[str, int] = Field(default_factory=dict)
    # The engineer's own watts for the row, kept only to point out where the
    # sheet disagrees with its counts -- never used in the calculation.
    sheet_watts: float | None = None

    @model_validator(mode="after")
    def _counts_not_negative(self) -> "Zone":
        for key, count in self.counts.items():
            if count < 0:
                raise ValueError(f"Zone '{self.name}': the {key} count cannot be negative")
        return self


class Channel(BaseModel):
    """An amplifier channel: the run of zones from `first_zone` up to the
    next channel's first zone. Stored as boundaries rather than a zone list,
    because that is how the engineer draws it (a merged cell down the
    schedule) and it cannot describe a zone on two channels or on none
    between channels."""

    first_zone: int = Field(ge=0)
    label: str | None = Field(default=None, max_length=200)
    amplifier_watts: float | None = Field(default=None, gt=0)
    sheet_required_watts: float | None = None


class Rack(BaseModel):
    """An amplifier rack / APS cabinet: the run of channels from
    `first_channel` up to the next rack's first channel."""

    name: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    first_channel: int = Field(ge=0)


class WorkbookSource(BaseModel):
    """Where the design was imported from, relative to the project folder."""

    path: str
    sheet: str
    sheets: list[str] = Field(default_factory=list)
    imported_at: datetime
    # What the reader noticed and the engineer should check -- stored, so it
    # is shown on every visit rather than only on the response to the import.
    warnings: list[str] = Field(default_factory=list)


class VoiceEvacuationDesign(BaseModel):
    source: WorkbookSource | None = None
    speaker_types: list[SpeakerType]
    zones: list[Zone]
    channels: list[Channel] = Field(default_factory=list)
    racks: list[Rack] = Field(default_factory=list)
    # Copied from the DesignRule in force when the design was imported, with
    # the rule's id, so correcting the rule later does not silently change a
    # calculation already made.
    max_load_fraction: float = Field(gt=0, le=1)
    max_load_rule_id: int | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "VoiceEvacuationDesign":
        keys = [s.key for s in self.speaker_types]
        if len(set(keys)) != len(keys):
            raise ValueError("Speaker type keys must be unique")
        known = set(keys)
        for zone in self.zones:
            unknown = set(zone.counts) - known
            if unknown:
                raise ValueError(f"Zone '{zone.name}' counts an unknown speaker type: {sorted(unknown)}")

        starts = [c.first_zone for c in self.channels]
        if starts != sorted(set(starts)):
            raise ValueError("Channels must start at increasing, distinct zones")
        if starts and starts[-1] >= len(self.zones):
            raise ValueError("A channel starts after the last zone")

        rack_starts = [r.first_channel for r in self.racks]
        if rack_starts != sorted(set(rack_starts)):
            raise ValueError("Racks must start at increasing, distinct channels")
        if rack_starts and rack_starts[-1] >= len(self.channels):
            raise ValueError("A rack starts after the last channel")
        return self


class ExtraComponent(BaseModel):
    """A load added to a panel by hand -- one the BOQ does not list."""

    description: str = Field(min_length=1, max_length=200)
    part_no: str | None = Field(default=None, max_length=64)
    quantity: float = Field(gt=0, le=10000)
    standby_ma: float = Field(ge=0, le=100000)
    alarm_ma: float = Field(ge=0, le=100000)
    source: str = Field(min_length=3, max_length=500)


class PanelSettings(BaseModel):
    """What the engineer sets for one panel. A setting left empty follows the
    battery sizing rule, so a corrected rule still reaches it."""

    name: str | None = Field(default=None, max_length=60)
    location: str | None = Field(default=None, max_length=200)
    standby_hours: float | None = Field(default=None, gt=0, le=168)
    alarm_minutes: float | None = Field(default=None, gt=0, le=240)
    spare_factor: float | None = Field(default=None, ge=1, le=3)
    panel_voltage: float | None = Field(default=None, gt=0, le=60)
    extra_components: list[ExtraComponent] = Field(default_factory=list)


class BatteryDesign(BaseModel):
    # Keyed by panel: "<system>|<BOQ group heading>|<n>", n counting the
    # identical panels a group quotes.
    panels: dict[str, PanelSettings] = Field(default_factory=dict)


class DesignDocument(BaseModel):
    """ProjectDesign.document. One key per system."""

    voice_evacuation: VoiceEvacuationDesign | None = None
    battery: BatteryDesign | None = None


# --- results -----------------------------------------------------------------

ChannelStatus = Literal["ok", "over_limit", "over_rating", "no_rating", "empty"]


class ZoneResult(BaseModel):
    index: int
    name: str
    watts: float
    sheet_watts: float | None
    # The sheet's own figure differs from counts x taps.
    sheet_mismatch: bool
    channel: int | None


class ChannelResult(BaseModel):
    index: int
    label: str | None
    first_zone: int
    last_zone: int
    required_watts: float
    amplifier_watts: float | None
    # required / rating; None when there is no rating to divide by.
    load_fraction: float | None
    # rating x the design limit: the most this channel may carry.
    limit_watts: float | None
    status: ChannelStatus
    sheet_required_watts: float | None
    sheet_mismatch: bool
    rack: int | None


class RackResult(BaseModel):
    index: int
    name: str
    location: str | None
    first_channel: int
    last_channel: int
    required_watts: float
    amplifier_watts: float | None


class VoiceEvacuationResult(BaseModel):
    zones: list[ZoneResult]
    channels: list[ChannelResult]
    racks: list[RackResult]
    total_required_watts: float
    max_load_fraction: float
    # Zones before the first channel: carried by no amplifier.
    unassigned_zones: list[int]
    channels_failing: int
    sheet_mismatches: int


# --- API ---------------------------------------------------------------------


class DesignRuleOut(BaseModel):
    id: int
    category: str
    key: str
    version: int
    data: dict
    source: str | None


class VoiceEvacuationOut(BaseModel):
    design: VoiceEvacuationDesign | None
    result: VoiceEvacuationResult | None
    # The load-limit rule the design is held to (or, before an import, the
    # one it would be).
    rule: DesignRuleOut | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None


class WorkbookCandidateOut(BaseModel):
    path: str
    filename: str
    # Its name looks like an amplifier calculation.
    likely: bool


class VoiceEvacuationImportIn(BaseModel):
    path: str
    sheet: str | None = None


# --- battery calculation -------------------------------------------------------

# "ok": the load is fully known and a battery is selected; "incomplete": it
# is not (a current missing, a quantity unreadable, nothing itemized);
# "no_selection": no battery of the selection brand is on file to choose.
BatteryPanelStatus = Literal["ok", "incomplete", "no_selection"]
BoqGroupTreatment = Literal["panel", "skipped_aps_bps", "ungrouped", "not_a_panel"]


class PartCurrentIn(BaseModel):
    """A part's current draw, from its datasheet. `source` names the
    datasheet (document, page); a value without one is not accepted."""

    part_no: str = Field(min_length=1, max_length=64)
    standby_ma: float = Field(ge=0, le=100000)
    alarm_ma: float = Field(ge=0, le=100000)
    description: str | None = Field(default=None, max_length=300)
    source: str = Field(min_length=3, max_length=500)


class BatteryUnitIn(BaseModel):
    """One battery as sold (a 12 V block), from its datasheet."""

    part_no: str = Field(min_length=1, max_length=64)
    capacity_ah: float = Field(gt=0, le=10000)
    voltage: float = Field(gt=0, le=100)
    brand: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=300)
    source: str = Field(min_length=3, max_length=500)


class BatteryLineOut(BaseModel):
    part_no: str | None
    description: str
    quantity: float | None
    # As the BOQ line gives it; picks the datasheet library to search.
    manufacturer: str | None = None
    # "load": draws current; "battery": the panel's own battery; "no_part":
    # a line without a part number, not counted.
    kind: Literal["load", "battery", "no_part"]
    standby_ma: float | None = None
    alarm_ma: float | None = None
    total_standby_ma: float | None = None
    total_alarm_ma: float | None = None
    # The part has no current in the catalogue yet.
    missing_current: bool = False
    current_rule_id: int | None = None
    current_rule_version: int | None = None
    # Where the current came from (the datasheet and page).
    current_source: str | None = None
    # The datasheet it was read from, to open at that page.
    datasheet_library: str | None = None
    datasheet_path: str | None = None
    datasheet_page: int | None = None
    # A load the engineer added to this panel (its index in the panel's
    # extra_components), not a BOQ line.
    extra_index: int | None = None


class BatterySetOut(BaseModel):
    part_no: str | None
    capacity_ah: float
    voltage: float
    # Units of this battery quoted / selected, and the number of panel-voltage
    # strings they make.
    units: float
    strings: float
    brand: str | None = None
    # The battery's datasheet, for a selected battery read from the library.
    datasheet_library: str | None = None
    datasheet_path: str | None = None


class BatteryPanelOut(BaseModel):
    heading: str
    system_code: str | None
    # Identical panels this group quotes (the heading line's quantity); every
    # figure below is per panel.
    count: int
    # One card per panel: a group quoting two identical panels gives two, each
    # with its own name, location and settings.
    key: str = ""
    instance: int = 1
    name: str = ""
    location: str | None = None
    # The sizing this panel is calculated with, and which of it the engineer
    # set rather than the rule.
    settings: dict = Field(default_factory=dict)
    overridden: list[str] = Field(default_factory=list)
    lines: list[BatteryLineOut]
    standby_ma: float
    alarm_ma: float
    standby_mah: float
    alarm_mah: float
    total_ah: float
    required_ah: float
    # Parts are missing a current, so the load and required Ah are at least
    # these figures, not these figures.
    lower_bound: bool
    missing_parts: list[str]
    # The battery the BOQ quotes for the panel, and whether it is smaller than
    # the requirement (which a partial load can already prove).
    quoted: list[BatterySetOut]
    quoted_ah: float | None
    quoted_short: bool = False
    status: BatteryPanelStatus
    # The battery selected for the panel: the nearest of the selection brand
    # that covers the requirement, or strings of them in parallel.
    selected: list[BatterySetOut] | None
    selected_ah: float | None
    notes: list[str] = Field(default_factory=list)


class BoqGroupOut(BaseModel):
    heading: str | None
    system_code: str | None
    lines: int
    treatment: BoqGroupTreatment


class BatteryCalculationOut(BaseModel):
    rule: DesignRuleOut | None
    panels: list[BatteryPanelOut]
    groups: list[BoqGroupOut]
    battery_units: list[DesignRuleOut]
    # Battery lines in the BOQ whose part is not in the catalogue, with what
    # their description says, to pre-fill an entry.
    unlisted_batteries: list[dict]
    # Parts still without a current, and why the datasheets did not give one.
    unresolved: list["UnresolvedPartOut"] = Field(default_factory=list)
    # What the engineer has set per panel, as stored.
    design: BatteryDesign = Field(default_factory=BatteryDesign)
    # The brand batteries are selected from, and what is on file to choose.
    selection_rule: DesignRuleOut | None = None
    selectable: list[BatterySetOut] = Field(default_factory=list)


# --- datasheet library -----------------------------------------------------------


class DatasheetRowOut(BaseModel):
    page: int
    text: str


class DatasheetMatchOut(BaseModel):
    library: str
    path: str
    filename: str
    document_no: str | None
    # "filename": the file is named for the part; "text": the part is
    # mentioned inside it.
    matched_on: str
    pages: list[int]
    current_rows: list[DatasheetRowOut]
    # Pre-fills a catalogue entry's source.
    source: str


class DatasheetLibraryOut(BaseModel):
    name: str
    folder: str
    available: bool


class UnresolvedPartOut(BaseModel):
    """A part whose current could not be filled from the datasheets."""

    part_no: str
    description: str
    reason: str


class FilledCurrentOut(BaseModel):
    part_no: str
    standby_ma: float
    alarm_ma: float
    source: str


class BatteryFillOut(BaseModel):
    filled: list[FilledCurrentOut]


# --- material submittal ----------------------------------------------------------


class MaterialItemOut(BaseModel):
    """One material of the project (a BOQ part), with the datasheet found for
    it in the manufacturer's library."""

    system_code: str | None
    part_no: str
    description: str
    manufacturer: str | None
    # Total quantity across the BOQ; None when a line quotes it as a word.
    quantity: float | None
    # BOQ group headings it appears under.
    groups: list[str] = Field(default_factory=list)
    datasheet_library: str | None = None
    datasheet_path: str | None = None
    datasheet_filename: str | None = None
    document_no: str | None = None
    # The datasheet is named for the part (rather than only mentioning it).
    datasheet_named_for_part: bool = False


class MaterialSubmittalOut(BaseModel):
    items: list[MaterialItemOut]
    systems: list[str]
    with_datasheet: int
    libraries: list[str]


# --- material submittal register --------------------------------------------------

SubmittalStatusName = Literal["not_submitted", "under_review", "approved", "rejected"]


class SubmittalIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    system_code: str | None = Field(default=None, max_length=16)
    manufacturer: str | None = Field(default=None, max_length=120)
    revision: str = Field(default="R00", min_length=1, max_length=16)
    status: SubmittalStatusName = "not_submitted"
    document_path: str | None = None
    note: str | None = Field(default=None, max_length=2000)


class SubmittalPatch(BaseModel):
    """Only the fields sent are changed."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    system_code: str | None = Field(default=None, max_length=16)
    manufacturer: str | None = Field(default=None, max_length=120)
    revision: str | None = Field(default=None, min_length=1, max_length=16)
    status: SubmittalStatusName | None = None
    document_path: str | None = None
    note: str | None = Field(default=None, max_length=2000)


class SubmittalEventOut(BaseModel):
    kind: str
    detail: str
    by: str | None
    at: datetime
    submittal_id: int
    submittal_title: str


class SubmittalOut(BaseModel):
    id: int
    title: str
    # The submittal's own reference on its form, and the consultant's reply
    # code (A / B / C) where a scan has read one.
    reference: str | None = None
    reply_code: str | None = None
    system_code: str | None
    manufacturer: str | None
    revision: str
    status: SubmittalStatusName
    document_path: str | None
    note: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    # Materials of this system in the BOQ, and how many have a datasheet.
    materials: int = 0
    materials_with_datasheet: int = 0


class SubmittalSuggestionOut(BaseModel):
    """A submittal the project's BOQ implies but the register does not have."""

    title: str
    system_code: str | None
    manufacturer: str | None
    materials: int
    materials_with_datasheet: int


class StorageFolderOut(BaseModel):
    name: str
    path: str
    items: int
    modified: datetime | None


class SubmittalRegisterOut(BaseModel):
    items: list[SubmittalOut]
    counts: dict[str, int]
    systems: list[str]
    activity: list[SubmittalEventOut]
    suggestions: list[SubmittalSuggestionOut]
    storage: list[StorageFolderOut]


class ScannedFormOut(BaseModel):
    """A material submittal form found in the project folder."""

    reference: str
    revision: str
    title: str
    system_code: str | None
    supplier: str | None
    # The consultant's reply: "A", "B", "C", or None where there is none yet.
    reply_code: str | None
    reply_text: str | None
    status: SubmittalStatusName
    path: str
    # The reply was read from the consultant's stamp by OCR.
    read_by_ocr: bool


class SubmittalScanOut(BaseModel):
    found: int
    created: int
    updated: int
    unchanged: int
    warnings: list[str]
    forms: list[ScannedFormOut]
    # The register as the scan leaves it, so the page needs no second call.
    updated_register: SubmittalRegisterOut


# --- compliance statements -------------------------------------------------------


class SpecMatchOut(BaseModel):
    """A specification found for a system: a document of its own, or the
    pages of one inside a larger specification."""

    system_code: str
    path: str
    filename: str
    # The member inside the archive, when the specification is zipped.
    member: str | None
    kind: Literal["document", "section"]
    section_no: str | None
    heading: str | None
    first_page: int | None
    last_page: int | None
    pages: int | None
    snippet: str
    matched_on: str
    # Uploaded through the platform rather than found in the archive.
    uploaded: bool = False


class ComplianceSystemOut(BaseModel):
    code: str
    name: str
    specs: list[SpecMatchOut]


class ComplianceOut(BaseModel):
    systems: list[ComplianceSystemOut]
    warnings: list[str]
    # The folder that was searched.
    searched: str | None


class DraftMailOut(BaseModel):
    to: str | None
    to_name: str | None
    subject: str
    body: str


class ProjectLogDrawingOut(BaseModel):
    group_reference: str | None = None
    reference: str | None = None
    revision: str = "R0"
    status: str = "UR"
    floor: str | None = None
    reply_text: str | None = None
    page: int = 1
    source: str = "document"
    system_code: str | None
    name: str
    path: str
    modified: datetime
    issued: date | None = None
    note: str | None = None


class ProjectLogsOut(BaseModel):
    scanning: bool = False
    processed_files: int = 0
    total_files: int = 0
    samples: list[ProjectLogDrawingOut] = Field(default_factory=list)
    material_submittals: list[ProjectLogDrawingOut] = Field(default_factory=list)
    systems: list[str]
    drawings: list[ProjectLogDrawingOut]
    searched: str | None
    warnings: list[str]
