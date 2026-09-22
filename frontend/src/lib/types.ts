export type Role = "admin" | "design_manager" | "design_engineer" | "estimation_engineer" | "fire_fighting_engineer" | "elv_engineer" | "draftsman" | "viewer";

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

/** Roles that can change a project's information and BOQ. Mirrors
 * CREATOR_ROLES in the backend's projects router. */
export const PROJECT_EDITOR_ROLES: Role[] = ["admin", "design_manager", "design_engineer"];

export const ROLE_LABELS: Record<Role, string> = {
  admin: "Admin",
  design_manager: "Design Manager",
  design_engineer: "Design Engineer",
  estimation_engineer: "Estimation Engineer",
  fire_fighting_engineer: "Fire Fighting Engineer",
  elv_engineer: "ELV Engineer",
  draftsman: "Draftsman",
  viewer: "Viewer",
};

export interface DocumentCandidate {
  path: string;
  filename: string;
  system_guess: string | null;
  /** How the resolver matched it; after the first ";" come the notes worth
   * showing: a superseded revision, a system inferred from the DRF. */
  matched_via: string;
  /** The revision the filename declares (R1, R2); null when it declares none. */
  revision: number | null;
  /** Whether to attach it by default: false for a design superseded by a
   * later revision of the same system. */
  selected: boolean;
}

/** The system codes a design sheet can be filed under. PAVA is what the
 * archive spells PA, VA, VAS and PAVA; VES is VE too. */
// Emergency lighting is one system, ELS (CBS and EML are ELS); an Edwards fire
// alarm's voice evacuation is part of FAS (app/services/system_rules.py).
export const DESIGN_SHEET_SYSTEM_CODES = ["FAS", "VES", "PAVA", "ELS"] as const;

export interface ExtractedField {
  value: string;
  confidence: number | null;
  raw_label: string;
}

// Keys match the backend's drf_extractor field names.
export type ExtractedFieldName =
  | "project_title"
  | "plot_number"
  | "location"
  | "client"
  | "consultant"
  | "contractor"
  | "contact_person"
  | "contact_phone"
  | "contact_email";

export interface ProjectResolveResponse {
  ep_number: string;
  folder_found: boolean;
  is_ambiguous: boolean;
  matched_folders: string[];
  /** The folder the documents were found in: the one match, or the one the
   * engineer selected. The project's source folder. */
  source_folder: string | null;
  drf_candidates: DocumentCandidate[];
  design_sheet_candidates: DocumentCandidate[];
  warnings: string[];
  errors: string[];
  extracted_fields: Partial<Record<ExtractedFieldName, ExtractedField>>;
  extracted_scope_of_work: string | null;
  extracted_systems: ProjectSystemInput[];
  extracted_other_information: string | null;
  extraction_warnings: string[];
  ai_suggestions: AiSuggestion[];
}

/** A system marked on the DRF: a brand written in, or an MS / DWG tick. */
export interface ProjectSystemInput {
  name: string;
  brand?: string | null;
  method_statement: boolean;
  drawing: boolean;
}

export interface ProjectSystem extends ProjectSystemInput {
  id: number;
}

export interface ProjectLogDrawing {
  group_reference?: string | null;
  reference?: string | null;
  revision?: string;
  status?: string;
  floor?: string | null;
  reply_text?: string | null;
  page?: number;
  source?: string;
  system_code: string | null;
  name: string;
  path: string;
  modified: string;
}

/** The project's document index (`GET /projects/{id}/documents/status`):
 * when the folder was last synced, what is stale because a source changed,
 * and what could not be read. */
/** What a permanent deletion of a material submittal did (DELETE
 * /projects/{id}/submittals/{sid}, POST /projects/{id}/submittals/delete). */
export interface SubmittalDeleted {
  reference: string | null;
  files: string[];
  missing: string[];
  register_rows: number;
  map_rebuilt: boolean;
}

export interface DocumentStatus {
  synced_at: string | null;
  documents: number;
  by_state: Record<string, number>;
  failed: { path: string; error: string | null }[];
  stale: { dependent_type: string; dependent_id: string; reason: string; source: string; source_role: string; source_state: string }[];
  syncing: boolean;
  job: import("./useJob").Job | null;
  folder: string | null;
  folder_reachable: boolean;
}

/** Whether a system has had its sample board sent, read off the
 * transmittals in the project's Transmittal folder (and any sample approval
 * form). The other fields describe the latest board sent, or the latest
 * sample material when no board was. */
export interface SampleBoardCheck {
  system_code: string;
  system_name: string;
  state: "submitted" | "material_only" | "missing";
  reference: string | null;
  revision: string | null;
  status: string | null;
  submitted_on: string | null;
  path: string | null;
}

export interface ProjectLogs {
  scanning: boolean;
  processed_files: number;
  total_files: number;
  /** When the index was last synced with the folder; null until the first sync. */
  synced_at: string | null;
  samples: ProjectLogDrawing[];
  /** Every system of the project shall have a sample board: one entry per
   * system, empty until the folder has been synced. */
  sample_boards: SampleBoardCheck[];
  material_submittals: ProjectLogDrawing[];
  systems: string[];
  drawings: ProjectLogDrawing[];
  searched: string | null;
  warnings: string[];
}

/** A Bill of Quantities line. `quantity` is text because the Design Sheets
 * use "Lot" as readily as a number. Prices are sent as strings so decimals
 * survive the round trip without float rounding. */
export interface ProjectBoqItemInput {
  /** The stored line's id, sent back on save so the server keeps its
   * provenance. New lines have none. */
  id?: number | null;
  system_code?: string | null;
  /** Heading the line sits under on the Design Sheet, e.g. a panel whose
   * sub-components are listed beneath it. */
  group_heading?: string | null;
  manufacturer?: string | null;
  catalog_no?: string | null;
  description: string;
  quantity?: string | null;
  unit?: string | null;
  unit_price?: string | null;
  total_price?: string | null;
  remarks?: string | null;
}

/** Where a line came from: read off a sheet, read and then corrected by an
 * engineer, a reviewed row accepted (with or without a model's reading),
 * typed in, or stored before provenance was kept. */
export type BoqLineStatus = "extracted" | "corrected" | "ai_accepted" | "review_accepted" | "manual" | "legacy";

export interface QuantityParse {
  kind: string;
  raw: string | null;
  value: string | null;
  status: "ok" | "empty" | "ambiguous" | "rejected";
  rule: string;
  unit: string | null;
}

export interface ProjectBoqItem extends ProjectBoqItemInput {
  id: number;
  position: number;
  /** The building the sheet quotes the line for, one canonical name. */
  building: string | null;
  /** The part number as the library or the sheet spells it; catalog_no is what was read. */
  catalog_canonical: string | null;
  catalog_match: { source?: string | null; cleaned?: string | null; canonical?: string | null; reason?: string; library?: string | null; library_reason?: string } | null;
  origin: string;
  extraction_run_id: number | null;
  source_document_sha256: string | null;
  source_page: number | null;
  source_region: number[] | null;
  raw_values: {
    catalog_no?: string | null;
    description?: string | null;
    quantity?: string | null;
    quantity_parse?: QuantityParse | null;
  } | null;
  ocr_confidence: string | null;
  parser_version: string | null;
  extracted_values: Partial<Record<"system_code" | "group_heading" | "catalog_no" | "description" | "quantity", string | null>> | null;
  edited_at: string | null;
  created_at: string | null;
  /** The AI verification's verdict on this line against its Design Sheet. */
  ai_check?: AiCheck | null;
  status: BoqLineStatus;
}

export interface AiCheck {
  status: "confirmed" | "corrected" | "added" | "unresolved";
  verification_id: number;
  at: string;
  reason: string;
}

/** One thing an AI verification looked at, with what each source said. */
export interface AiVerificationItem {
  id: string;
  kind: string;
  label?: string;
  system_code?: string | null;
  document?: string | null;
  page?: number | null;
  held?: unknown;
  ocr?: unknown;
  ai?: unknown;
  ai2?: unknown;
  ai3?: unknown;
  final?: unknown;
  outcome: "confirmed" | "corrected" | "added" | "removed" | "unresolved" | "not_checked" | "not_an_item";
  reason: string;
}

export interface AiVerification {
  id: number;
  scope: "boq" | "details";
  status: "running" | "completed" | "failed" | "undone";
  summary: {
    confirmed?: number; corrected?: number; added?: number; removed?: number; unresolved?: number; not_checked?: number;
    lines?: number; changed?: boolean;
    /** New AI calls this check made, and readings it took from the database instead. */
    ai_calls?: number; readings_reused?: number;
  };
  items: AiVerificationItem[];
  notes: string[];
  error: string | null;
  stale: boolean;
  can_undo: boolean;
  models: string[];
  calls: number;
  started_at: string;
  finished_at: string | null;
}

export interface AiVerificationState {
  available: boolean;
  reason: string | null;
  auto: boolean;
  job: import("./useJob").Job | null;
  boq: AiVerification | null;
  details: AiVerification | null;
}

/** Reply from the BOQ open call. `extracted` is true only on the call that
 * actually read the Design Sheets, which happens once per project. */
export interface BoqEnsureResponse {
  items: ProjectBoqItem[];
  extracted: boolean;
  warnings: string[];
  /** The version a save names in If-Match. */
  version: number;
  /** When the AI has to read the Design Sheets first, that runs as a job and
   * this is it: the page follows it and asks again when it is done. Null
   * once the BOQ is read. */
  reading?: import("./useJob").Job | null;
}

export type CheckStatus = "ok" | "warning" | "blocked" | "unknown";

export interface ReadinessCheck {
  key: string;
  label: string;
  /** "boq" checks gate issuing a BOQ revision. */
  scope: "boq" | "calculations" | "compliance" | "details";
  status: CheckStatus;
  summary: string;
  items: string[];
  count: number;
  /** A path inside the project workspace, e.g. "boq" or "documents". */
  link: string | null;
}

export interface Readiness {
  status: CheckStatus;
  boq_ready_for_issue: boolean;
  boq_blockers: string[];
  checks: ReadinessCheck[];
}

export interface IntakeFinding {
  code: string;
  severity: "blocked" | "warning";
  message: string;
  detail: Record<string, unknown>;
}

export interface DocumentIntake {
  id: number;
  role: "drf" | "design_sheet";
  system_code: string | null;
  filename: string;
  relative_path: string | null;
  /** Only for those who can edit the project. */
  path: string | null;
  sha256: string | null;
  size: number | null;
  mime: string | null;
  page_count: number | null;
  printed_pages: { numbers: number[]; declared_totals: number[]; ocr: boolean } | null;
  intake_status: "unchecked" | "ok" | "warning" | "blocked";
  findings: IntakeFinding[];
  acknowledged: { code: string; reason: string; by_name: string; at: string }[];
  checked_at: string | null;
  intake_version: string | null;
}

/** A BOQ line as a re-read or a snapshot records it: the line's values and
 * its source record, as plain JSON. */
export interface BoqLineRecord {
  id?: number;
  cid?: string;
  position?: number;
  system_code: string | null;
  group_heading: string | null;
  manufacturer: string | null;
  catalog_no: string | null;
  description: string;
  quantity: string | null;
  unit: string | null;
  unit_price: string | null;
  total_price: string | null;
  remarks: string | null;
  origin: string;
  status?: string;
  extraction_run_id: number | null;
  source_page: number | null;
  source_region: number[] | null;
  raw_values: ProjectBoqItem["raw_values"];
  ocr_confidence: string | null;
  parser_version: string | null;
  document_name?: string;
}

export interface BoqCandidateChange {
  id: string;
  kind: "unchanged" | "changed" | "added" | "removed";
  /** How the old and new line were paired: "probable" pairs need a person. */
  match: "exact" | "probable" | null;
  old_id: number | null;
  before: BoqLineRecord | null;
  after: BoqLineRecord | null;
  fields: string[];
  reason: string;
}

export interface BoqCandidateSheet {
  run_id: number;
  document_name: string;
  system_code: string | null;
  outcome: string;
  lines: number;
  failure: string | null;
  open_issues: number;
  unprocessed_pages: number[];
}

export interface BoqCandidateSummary {
  id: number;
  status: "pending" | "applied" | "discarded" | "superseded";
  base_boq_version: number;
  parser_version: string;
  summary: {
    old_lines: number;
    new_lines: number;
    old_quantity: number;
    new_quantity: number;
    unchanged: number;
    changed: number;
    probable: number;
    added: number;
    removed: number;
    sheets: BoqCandidateSheet[];
    failed_sheets: string[];
  };
  created_at: string;
  decided_at: string | null;
  decisions_needed: number;
}

export interface BoqCandidate extends BoqCandidateSummary {
  changes: BoqCandidateChange[];
  boq_version: number;
  /** The BOQ was saved after this re-read was built: it cannot be applied. */
  stale: boolean;
}

export interface BoqSnapshotSummary {
  id: number;
  boq_version: number;
  reason: string;
  lines: number;
  created_at: string;
  created_by_name: string | null;
}

/** An issued BOQ revision (Rev 00, Rev 01, ...), frozen when issued. */
export interface BoqRevisionSummary {
  number: number;
  label: string;
  note: string | null;
  /** Naive UTC from the API -- see parseApiDate. */
  issued_at: string;
  issued_by_name: string;
  line_count: number;
}

export interface BoqRevision extends BoqRevisionSummary {
  items: ProjectBoqItemInput[];
}

export interface BoqChange {
  kind: "added" | "removed" | "changed";
  before: ProjectBoqItemInput | null;
  after: ProjectBoqItemInput | null;
  /** For "changed": which of manufacturer, quantity, unit, prices, remarks. */
  fields: (keyof ProjectBoqItemInput)[];
}

export interface BoqCompare {
  from_label: string;
  to_label: string;
  changes: BoqChange[];
}

export interface ProjectDesignSheetIn {
  system_code: string | null;
  document_path: string;
}

export interface ProjectDesignSheet extends ProjectDesignSheetIn {
  id: number;
}

/** The project information reviewed at creation and editable afterwards
 * (`PUT /projects/{id}`). Not the EP number or the document paths. */
export interface ProjectDetailsInput {
  project_name?: string | null;
  plot_number?: string | null;
  location?: string | null;
  client?: string | null;
  consultant?: string | null;
  contractor?: string | null;
  contact_person?: string | null;
  contact_phone?: string | null;
  contact_email?: string | null;
  scope_of_work?: string | null;
  systems: ProjectSystemInput[];
  other_information?: string | null;
  /** Edwards only: a separate voice evacuation panel, so VE is its own system. */
  separate_ve_panel?: boolean;
  /** Whether the project's documents may be sent to an AI provider. */
  ai_policy?: "allowed" | "blocked";
}

export interface ProjectCreate extends ProjectDetailsInput {
  ep_number: string;
  source_folder_path?: string | null;
  drf_document_path?: string | null;
  design_sheets: ProjectDesignSheetIn[];
}

export type ProjectStatus = "draft" | "active" | "archived";

export interface Project {
  id: number;
  ep_number: string;
  status: ProjectStatus;
  project_name: string | null;
  plot_number: string | null;
  location: string | null;
  client: string | null;
  consultant: string | null;
  contractor: string | null;
  contact_person: string | null;
  contact_phone: string | null;
  contact_email: string | null;
  scope_of_work: string | null;
  systems: ProjectSystem[];
  other_information: string | null;
  source_folder_path: string | null;
  drf_document_path: string | null;
  design_sheets: ProjectDesignSheet[];
  /** Design Sheets the one-off BOQ read could not parse, by filename. */
  boq_extraction_warnings: string[] | null;
  created_at: string;
  updated_at?: string | null;
  /** Versions a save names in If-Match: someone else's save in between is refused. */
  details_version: number;
  boq_version: number;
  ai_policy: "allowed" | "blocked";
  /** After an edit: what the change was carried into elsewhere on the project. */
  propagated?: string[];
  separate_ve_panel: boolean;
  /** An Edwards fire alarm carrying the voice evacuation and fire telephone (one system, FAS). */
  voice_evacuation_integrated: boolean;
  /** The project's systems under their effective codes; every tab reads these. */
  system_codes: string[];
}

/** The AI check of project details against the DRF: suggestions only. */
export interface FieldSuggestion {
  field: string;
  label: string;
  current: string;
  suggested: string;
  reason: string;
}

export interface SystemSuggestion {
  name: string;
  change: "add" | "remove" | "update";
  current: ProjectSystemInput | null;
  suggested: ProjectSystemInput | null;
  reason: string;
}

export interface DetailsCheck {
  model: string;
  from_cache: boolean;
  fields: FieldSuggestion[];
  systems: SystemSuggestion[];
  confirmed: number;
  unreadable: string[];
  notes: string[];
}

// The rows of the DRF's Systems table, in template order.
export const SYSTEM_OPTIONS = [
  "Fire Alarm",
  "Voice Evacuation",
  "Fire Telephone",
  "Smoke Management",
  "Aspiration Smoke Detection",
  "Central Battery System",
  "Emergency Light Monitoring",
  "PA/VA & BGM",
  "CCTV",
  "Access Control",
  "Structured Cabling",
  "Gate Barrier",
  "SMATV & IPTV",
  "WIFI Solution",
  "ICT Switches",
  "Nurse Call / Disable Toilet Alarm",
  "Others",
] as const;

export const SCOPE_OF_WORK_OPTIONS = ["Full Package", "Design, Supply, T&C", "Supply Only"] as const;

// --- Voice Evacuation design (app/schemas_design.py) ---

export interface VeSpeakerType {
  key: string;
  name: string;
  model: string | null;
  tap_watts: number;
}

export interface VeZone {
  name: string;
  counts: Record<string, number>;
  sheet_watts: number | null;
}

export interface VeChannel {
  first_zone: number;
  label: string | null;
  amplifier_watts: number | null;
  sheet_required_watts: number | null;
}

export interface VeRack {
  name: string;
  location: string | null;
  first_channel: number;
}

export interface VeWorkbookSource {
  path: string;
  sheet: string;
  sheets: string[];
  imported_at: string;
  warnings: string[];
}

export interface VeDesign {
  source: VeWorkbookSource | null;
  speaker_types: VeSpeakerType[];
  zones: VeZone[];
  channels: VeChannel[];
  racks: VeRack[];
  max_load_fraction: number;
  max_load_rule_id: number | null;
}

export type VeChannelStatus = "ok" | "over_limit" | "over_rating" | "no_rating";

export interface VeZoneResult {
  index: number;
  name: string;
  watts: number;
  sheet_watts: number | null;
  sheet_mismatch: boolean;
  channel: number | null;
}

export interface VeChannelResult {
  index: number;
  label: string | null;
  first_zone: number;
  last_zone: number;
  required_watts: number;
  amplifier_watts: number | null;
  load_fraction: number | null;
  limit_watts: number | null;
  status: VeChannelStatus;
  sheet_required_watts: number | null;
  sheet_mismatch: boolean;
  rack: number | null;
}

export interface VeRackResult {
  index: number;
  name: string;
  location: string | null;
  first_channel: number;
  last_channel: number;
  required_watts: number;
  amplifier_watts: number | null;
}

export interface VeResult {
  zones: VeZoneResult[];
  channels: VeChannelResult[];
  racks: VeRackResult[];
  total_required_watts: number;
  max_load_fraction: number;
  unassigned_zones: number[];
  channels_failing: number;
  sheet_mismatches: number;
}

export interface DesignRule {
  id: number;
  category: string;
  key: string;
  version: number;
  data: Record<string, unknown>;
  source: string | null;
}

export interface VoiceEvacuationResponse {
  design: VeDesign | null;
  result: VeResult | null;
  rule: DesignRule | null;
  updated_at: string | null;
  updated_by: string | null;
}

export interface WorkbookCandidate {
  path: string;
  filename: string;
  likely: boolean;
}

// --- Panel battery calculation (app/services/battery_calculation.py) ---

export type BatteryPanelStatus = "ok" | "incomplete" | "no_selection";
export type BoqGroupTreatment = "panel" | "aps" | "bps" | "repeater" | "ungrouped" | "not_a_panel";

export interface BatteryLine {
  part_no: string | null;
  description: string;
  quantity: number | null;
  manufacturer: string | null;
  /** "not_cabinet_load": filed under an APS / BPS group but not the
   * cabinet's own equipment; powered elsewhere, not counted. */
  kind: "load" | "battery" | "no_part" | "not_cabinet_load";
  standby_ma: number | null;
  alarm_ma: number | null;
  total_standby_ma: number | null;
  total_alarm_ma: number | null;
  missing_current: boolean;
  current_rule_id: number | null;
  current_rule_version: number | null;
  current_source: string | null;
  datasheet_library: string | null;
  datasheet_path: string | null;
  datasheet_page: number | null;
  /** Set for a load the engineer added to the panel (not a BOQ line). */
  extra_index: number | null;
}

export interface BatterySet {
  part_no: string | null;
  capacity_ah: number;
  voltage: number;
  units: number;
  strings: number;
  brand: string | null;
  datasheet_library: string | null;
  datasheet_path: string | null;
}

export interface BatteryPanel {
  /** The previous calculation, shown because recalculating this panel failed (`error` says how). */
  stale?: boolean;
  error?: string | null;
  heading: string;
  system_code: string | null;
  /** "panel": one card per panel quoted; "aps" / "bps": one card for the
   * cabinet type, every cabinet carrying the same load. */
  kind: "panel" | "aps" | "bps";
  count: number;
  /** One card per physical panel. */
  key: string;
  instance: number;
  name: string;
  location: string | null;
  /** The sizing used, and which fields the engineer set over the rule. */
  settings: Record<string, number>;
  overridden: string[];
  lines: BatteryLine[];
  standby_ma: number;
  alarm_ma: number;
  standby_mah: number;
  alarm_mah: number;
  total_ah: number;
  required_ah: number;
  lower_bound: boolean;
  missing_parts: string[];
  quoted: BatterySet[];
  quoted_ah: number | null;
  status: BatteryPanelStatus;
  quoted_short: boolean;
  /** The battery selected for the panel (the ROCKET range). */
  selected: BatterySet[] | null;
  selected_ah: number | null;
  notes: string[];
}

export interface BoqGroup {
  heading: string | null;
  system_code: string | null;
  lines: number;
  treatment: BoqGroupTreatment;
}

export interface BatteryCalculation {
  /** The version a save names in If-Match. */
  design_version?: number;
  /** Panels read back from their saved calculation, and panels calculated on this read. */
  reused_panels?: number;
  recalculated_panels?: number;
  /** What the figures were made from and what they are (backend calc_integrity). */
  input_hash?: string;
  result_hash?: string;
  complete?: boolean;
  incomplete_reasons?: string[];
  needs_confirmation?: { part_no: string; description: string | null; reason: string | null; rule_id: number; rule_version: number }[];
  rule: DesignRule | null;
  panels: BatteryPanel[];
  groups: BoqGroup[];
  battery_units: DesignRule[];
  unlisted_batteries: { part_no: string; capacity_ah: number; voltage: number }[];
  /** Parts still without a current, and why the datasheets gave none. */
  unresolved: { part_no: string; description: string; reason: string }[];
  design: BatteryDesign;
  /** The brand batteries are selected from, and what is on file to choose. */
  selection_rule: DesignRule | null;
  selectable: BatterySet[];
}

export interface DatasheetMatch {
  library: string;
  path: string;
  filename: string;
  document_no: string | null;
  matched_on: "filename" | "text";
  pages: number[];
  current_rows: { page: number; text: string }[];
  source: string;
}

export interface ExtraComponent {
  description: string;
  part_no: string | null;
  quantity: number;
  standby_ma: number;
  alarm_ma: number;
  source: string;
}

/** What the engineer sets for one panel; an empty setting follows the rule. */
export interface PanelSettings {
  name?: string | null;
  location?: string | null;
  standby_hours?: number | null;
  alarm_minutes?: number | null;
  spare_factor?: number | null;
  panel_voltage?: number | null;
  extra_components: ExtraComponent[];
}

export interface BatteryDesign {
  panels: Record<string, PanelSettings>;
}

// --- Material submittal (app/routers/submittal.py) ---

export interface MaterialItem {
  system_code: string | null;
  part_no: string;
  description: string;
  manufacturer: string | null;
  quantity: number | null;
  groups: string[];
  datasheet_library: string | null;
  datasheet_path: string | null;
  datasheet_filename: string | null;
  document_no: string | null;
  datasheet_named_for_part: boolean;
  /** The datasheet was linked to the part by hand, for every project. */
  datasheet_linked?: boolean;
}

export interface MaterialSubmittal {
  items: MaterialItem[];
  systems: string[];
  with_datasheet: number;
  libraries: string[];
}

export type SubmittalStatus = "not_submitted" | "under_review" | "approved" | "rejected";

export interface Submittal {
  id: number;
  title: string;
  /** Its reference on the form, and the consultant's reply code (A/B/C). */
  reference: string | null;
  reply_code: string | null;
  system_code: string | null;
  manufacturer: string | null;
  revision: string;
  status: SubmittalStatus;
  document_path: string | null;
  note: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  materials: number;
  materials_with_datasheet: number;
}

export interface SubmittalEvent {
  kind: string;
  detail: string;
  by: string | null;
  at: string;
  submittal_id: number;
  submittal_title: string;
}

export interface SubmittalSuggestion {
  title: string;
  system_code: string | null;
  manufacturer: string | null;
  materials: number;
  materials_with_datasheet: number;
}

export interface StorageFolder {
  name: string;
  path: string;
  items: number;
  modified: string | null;
}

export interface SubmittalRegister {
  items: Submittal[];
  counts: Record<string, number>;
  systems: string[];
  activity: SubmittalEvent[];
  suggestions: SubmittalSuggestion[];
  storage: StorageFolder[];
}

/** How one revision of a submittal stands, as the AI read it: under review,
 * approved, approved as noted, revise and resubmit, rejected. */
export type SubmittalCellStatus = "UR" | "A" | "ANN" | "RR" | "REJ";

export interface SubmittalMapCell {
  status: SubmittalCellStatus;
  file: string;
  date: string;
  reply_code: string;
  consultant: string;
  reply_date: string;
  evidence: string;
  /** A reply is on the form but was not verified as the consultant's. */
  unverified_reply: boolean;
  /** Copies of this revision found in the folder. */
  copies: number;
}

export interface SubmittalMapRow {
  reference: string;
  title: string;
  supplier: string;
  manufacturer: string;
  system_code: string | null;
  cells: Record<string, SubmittalMapCell>;
  latest: string;
  latest_status: SubmittalCellStatus;
  action: string | null;
}

/** The AI's map of the project's material submittals
 * (`GET /projects/{id}/submittals/map`): per system, a row per reference
 * and a column per revision, plus the actions it calls for. */
export interface SubmittalMap {
  available: boolean;
  reason: string | null;
  checked_at: string | null;
  model: string | null;
  revisions: string[];
  systems: { system_code: string | null; rows: SubmittalMapRow[] }[];
  actions: string[];
  submittals: number;
  forms: number;
  files: number;
  calls: number;
  reused: number;
  warnings: string[];
  register_counts: Record<string, number>;
  /** Whether the project folder changed since the map was drawn (or was
   * never checked): only then is the folder read again. */
  changed: boolean;
  listing_files: number;
  change_reason: string;
}

// --- Compliance statements (app/routers/compliance.py) ---

export interface SpecMatch {
  system_code: string;
  path: string;
  filename: string;
  member: string | null;
  kind: "document" | "section";
  section_no: string | null;
  heading: string | null;
  first_page: number | null;
  last_page: number | null;
  pages: number | null;
  snippet: string;
  matched_on: string;
  uploaded: boolean;
  verification: SpecVerification | null;
}

export type Sameness = "same" | "different" | "unknown";

/** Whether a specification is this project's, for this system. */
export interface SpecVerification {
  project: Sameness;
  system: Sameness;
  evidence: string[];
  names_in_spec: string[];
  decided_by: string;
}

export interface ComplianceSystem {
  code: string;
  name: string;
  specs: SpecMatch[];
}

/** One import of the compliance knowledge base. */
export interface KnowledgeImport {
  id: number;
  status: "running" | "succeeded" | "unchanged" | "failed";
  started_at: string;
  finished_at: string | null;
  source_label: string | null;
  workbook_built: string | null;
  files_discovered: number;
  files_imported: number;
  files_unchanged: number;
  files_failed: number;
  files_skipped: number;
  records_added: number;
  records_updated: number;
  records_inactive: number;
  records_flagged: number;
  error: string | null;
}

/** What the knowledge base holds and how it got there. */
export interface KnowledgeStatus {
  running: boolean;
  phase: string | null;
  detail: string | null;
  source_configured: boolean;
  last_successful: KnowledgeImport | null;
  last: KnowledgeImport | null;
  last_refreshed_at: string | null;
  records: { sources: number; requirements: number; responses: number; eligible_responses: number };
  by_system: Record<string, { responses: number; eligible: number }>;
  manufacturers: Record<string, number>;
}

export interface KnowledgeImportReport {
  id: number;
  status: string;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  workbook_built: string | null;
  counts: Record<string, unknown>;
  files: { role: string; action: string; note: string | null; count: number; examples: string[] }[];
  canonical: { path: string; role: string; action: string; note: string } | null;
}

export interface Compliance {
  systems: ComplianceSystem[];
  warnings: string[];
  searched: string | null;
  /** When the folder was searched, and whether this came from the database
   * (the folder is searched once; after that the files are read from where
   * they were found). */
  found_at: string | null;
  from_database: boolean;
  ai_available: boolean;
  knowledge: KnowledgeStatus | null;
}

export const COMPLIANCE_RESPONSES = [
  "Comply",
  "Noted",
  "Complied with remark",
  "Not applicable",
  "By others",
  "Deviation",
  "Clarification required",
] as const;

export interface StatementFinding {
  code: string;
  severity: "error" | "warning" | "info";
  message: string;
}

export type WorkflowStatus = "unfilled" | "autofilled" | "candidate" | "ai_pending" | "reviewed" | "recheck";
export type TechnicalStatus = "complies" | "does_not_comply" | "partially_complies" | "insufficient_evidence" | "not_applicable";

export const WORKFLOW_LABELS: Record<WorkflowStatus, string> = {
  unfilled: "Unfilled",
  autofilled: "Auto-filled draft",
  candidate: "Candidate requires review",
  ai_pending: "AI suggestion pending review",
  reviewed: "Engineer reviewed",
  recheck: "Needs recheck",
};

export const TECHNICAL_LABELS: Record<TechnicalStatus, string> = {
  complies: "Complies",
  does_not_comply: "Does not comply",
  partially_complies: "Partially complies",
  insufficient_evidence: "Insufficient evidence",
  not_applicable: "Not applicable",
};

/** A past answer the knowledge base holds for a clause. */
export interface KnowledgeCandidate {
  requirement_id: string | null;
  requirement_text: string;
  /** Null for an engineer-approved answer, which is not a knowledge-base record. */
  response_id: string | null;
  historical_response: string;
  historical_status: string | null;
  proposed_status: TechnicalStatus;
  response: string;
  remarks: string | null;
  manufacturer: string | null;
  brand: string | null;
  models: string | null;
  eligibility: "eligible" | "blocked";
  eligibility_reasons: string | null;
  similarity: number | null;
  sources: KnowledgeSourceRef[];
}

export interface KnowledgeSourceRef {
  source_id: string;
  filename: string;
  project: string | null;
  job_number: string | null;
  page: string | null;
  review_status: string | null;
  superseded: boolean;
  document_date: string | null;
  document_revision: string | null;
}

/** What the knowledge base found for a clause: the match, or why not. */
export interface KnowledgeMatch {
  result: "eligible" | "flagged" | "conflict" | "missing_model" | "scope" | "candidate" | "none";
  explanation: string;
  unresolved: string[];
  candidates: KnowledgeCandidate[];
  requirement_id?: string;
  requirement_text?: string;
  equivalence?: boolean;
  /** The candidates answered the same wording (not merely similar). */
  same_wording?: boolean;
  /** Drafted from an answer an engineer signed off on an earlier statement. */
  learned?: boolean;
  response_id?: string;
  historical_response?: string;
  historical_status?: string | null;
  manufacturer?: string | null;
  brand?: string | null;
  models?: string | null;
  remarks?: string | null;
  scope_conditions?: string | null;
  responsible_party?: string | null;
  sources?: KnowledgeSourceRef[];
  boq_item?: { id: number; description: string; manufacturer: string | null; model: string | null; quantity: string | null; unit: string | null } | null;
}

/** The model's review of one clause, kept on the row. */
export interface AiReview {
  request_id: string;
  status: "done" | "failed";
  at: string;
  model: string;
  prompt_version: string;
  instruction: string;
  from_cache: boolean;
  decision: "accepted" | "edited" | "rejected" | null;
  error: string | null;
  suggestion: {
    clause_id: string;
    suggested_response: string;
    suggested_remark: string;
    proposed_compliance_status: TechnicalStatus;
    evidence_references: string[];
    missing_information: string[];
    deviations: string[];
    review_notes: string;
  } | null;
}

/** One clause of a prepared or checked statement. `source` says where the
 *  answer came from: heading | lead_in | rule | database | ai | engineer |
 *  none (prepare); statement | missing (check). */
export type AiFillClass = "filled" | "confirmed" | "needs_review" | "conflict";

export interface StatementRow {
  id: string;
  ref: string;
  label: string;
  level: number;
  text: string;
  page: number;
  heading: boolean;
  response: string;
  remark: string;
  source: string;
  state: "ok" | "review";
  note: string | null;
  origin?: "none" | "rule" | "database" | "ai" | "manual";
  workflow?: WorkflowStatus;
  technical?: { status: TechnicalStatus | null; origin: "historical" | "engineer" | "ai" | "rule" | null; verified: boolean } | null;
  match?: KnowledgeMatch | null;
  ai_review?: AiReview | null;
  /** How an AI fill classified the row. needs_review and conflict are highlighted. */
  ai_class?: AiFillClass | null;
  /** The submitted statement's row (check). */
  reference?: { label: string; text: string; response: string; similarity: number } | null;
  findings?: StatementFinding[];
}

export interface StatementSummary {
  id: number;
  kind: "prepare" | "check";
  system_code: string;
  spec: {
    path: string;
    member: string | null;
    first_page: number;
    last_page: number | null;
    filename: string;
    section_numbers: string[];
    title: string | null;
    header_lines: string[];
    clauses: number;
    warnings: string[];
  };
  verification: SpecVerification;
  summary: {
    clauses: number;
    by_source: Record<string, number>;
    by_response: Record<string, number>;
    review: number;
    notes: string[];
    general?: StatementFinding[];
    finding_counts?: Record<string, number>;
    statement_rows?: number;
    statement_answered?: number;
    rows_not_in_spec?: number;
    by_workflow?: Record<string, number>;
    autofill?: { filled: number; flagged?: number; candidates: number; unmatched: number; blocked: number; learned?: number };
    /** Auto-fill with AI, running in the background or as it last ended. */
    ai_job?: {
      running: boolean;
      scope: string;
      total: number;
      done: number;
      learned: number;
      counts: { filled: number; confirmed: number; needs_review: number; conflict: number; unanswered: number };
      started_at: string;
      stopped: string | null;
      interrupted?: boolean;
      error?: string;
    } | null;
    inputs?: { spec_sha256: string; boq_hash: string; scope_hash: string; knowledge_import_id: number | null };
  };
  statement_name: string | null;
  ai_calls: number;
  created_at: string;
  updated_at: string;
  /** The engineer's approval. Export is refused without it, and any change
   *  to an answer withdraws it. */
  approved: boolean;
  approved_at: string | null;
  approved_by_name: string | null;
  /** Why the statement cannot be approved yet; empty when it can. */
  approval_blockers: string[];
  /** Answerable clauses counted by where they stand; score is the share settled. */
  readiness?: StatementReadiness | null;
  version?: number;
}

export interface StatementReadiness {
  clauses: number;
  unanswered: number;
  candidate: number;
  recheck: number;
  ai_pending: number;
  reviewed: number;
  autofilled: number;
  score: number;
  to_do: number;
}

export interface Statement extends StatementSummary {
  rows: StatementRow[];
  reference_files: unknown[];
}

export interface StatementFile {
  path: string;
  filename: string;
  uploaded: boolean;
}


export interface DraftMail {
  to: string | null;
  to_name: string | null;
  subject: string;
  body: string;
}

/** A material submittal package: what each index section would contribute
 *  (GET /projects/{id}/submittal/package/plan). */
export interface PackageDocument {
  name: string;
  source: string;
  part_no: string | null;
  /** BOQ parts one shared datasheet serves; it is merged once. */
  covers: string[];
  included: boolean;
  missing_reason: string | null;
}

export interface PackageSection {
  number: number;
  name: string;
  selected: boolean;
  found: number;
  missing: number;
  note: string | null;
  documents: PackageDocument[];
}

export interface PackagePlan {
  /** Whether this system's submittal encloses a battery calculation (the section is left out when not), and why not. */
  /** `note` is set only when a battery calculation is coming for this system (the central battery
   *  system); a system that never has one says nothing. */
  battery_calculation?: { applies: boolean; reason: string | null; note?: string | null } | null;
  sections: PackageSection[];
  library_found: boolean;
  library_path: string | null;
  system_code: string | null;
  warnings: string[];
}

/** What a filled-in material submittal checklist ticks
 *  (POST /projects/{id}/submittal/package/checklist). */
export interface ChecklistRead {
  /** Section number -> "yes" | "no" | "na". A row with no tick is absent. */
  answers: Record<number, string>;
  /** The sections ticked Yes, ready to build. */
  sections: number[];
  warnings: string[];
}

/** What a Design Sheet read could not settle (`GET /projects/{id}/extraction`). */
export interface AiProposalOut {
  id: number;
  task: string;
  model: string;
  state: "validated" | "needs_human_review" | "rejected" | "insufficient_evidence";
  state_reason: string;
  value: string | null;
  from_cache: boolean;
  /** Instruction-like wording found in the document text the call carried. */
  injection_flags?: string[] | null;
  /** What the engineer did with the issue, once decided. */
  outcome?: "accepted" | "corrected" | "rejected" | "abstained" | "superseded" | null;
  created_at: string;
}

/** `GET /projects/{id}/ai/budget`. */
export interface AiBudget {
  calls_last_24h: number;
  calls_per_day_limit: number;
  calls_remaining: number;
  cost_last_24h: number;
  priced: boolean;
  cost_per_job_limit: number | null;
  calls_per_document_limit: number;
  elapsed_s_per_job_limit: number;
  ai_enabled: boolean;
  ai_ready: boolean;
  policy: "allowed" | "blocked";
  allowed: boolean;
}

export interface AiProposalMetrics {
  task: string;
  prompt_version: string;
  model: string;
  proposals: number;
  from_cache: number;
  validated: number;
  needs_human_review: number;
  rejected_by_validator: number;
  insufficient_evidence: number;
  flagged_injection: number;
  decided: number;
  accepted: number;
  corrected: number;
  rejected: number;
  abstained: number;
  precision: number | null;
  validated_precision: number | null;
  recall: number | null;
  abstention_rate: number | null;
  correction_rate: number | null;
  false_validations: { proposal_id: number; issue_id: number; proposed: string | null; decided: string | null; outcome: string }[];
}

export interface AiUsageMetrics {
  task: string;
  model: string;
  calls: number;
  cache_hits: number;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: number;
  average_latency_ms: number;
  errors: Record<string, number>;
}

export interface AiEvaluationReport {
  task: string;
  file: string;
  at: string;
  prompt_version: string;
  model: string;
  provider: string;
  score: {
    cases: number;
    scored: number;
    errors: number;
    proposed: number;
    correct: number;
    precision: number | null;
    recall: number | null;
    abstention_rate: number | null;
    false_validations: number;
    estimated_cost: number | null;
  };
  gate: { gated: boolean; passed: boolean | null; reasons: string[] };
}

export interface AiMetrics {
  since: string | null;
  proposals: AiProposalMetrics[];
  usage: AiUsageMetrics[];
  budget: Omit<AiBudget, "ai_enabled" | "ai_ready" | "policy" | "allowed">;
  evaluations: AiEvaluationReport[];
  gates: { task: string; open: boolean; runnable: boolean; min_cases: number; min_precision: number; max_false_validations: number }[];
  /** Tasks switched off on the server (AI_DISABLED_TASKS). */
  disabled_tasks: string[];
}

export interface BackupRow {
  name: string;
  size: number;
  created_at: string;
}

export interface BackupVerification {
  name: string;
  restores: boolean;
  integrity: string;
  counts: Record<string, number>;
  schema_version: string | null;
  latest_revision: { ep_number: string; number: number } | null;
}

export interface ExtractionIssue {
  id: number;
  code: string;
  severity: string;
  page: number | null;
  target: string;
  detail: Record<string, unknown>;
  state: "open" | "proposed" | "resolved" | "rejected" | "starved";
  state_reason: string | null;
  llm_eligible: boolean;
  human_required: boolean;
  has_evidence_image: boolean;
  proposals: AiProposalOut[];
}

export interface ExtractionRun {
  id: number;
  kind: string;
  document_name: string;
  system_code: string | null;
  outcome: string;
  lines_accepted: number;
  failure: string | null;
  unprocessed_pages: number[];
  ai_calls: number;
  ai_cost: number;
  budget_exhausted: string | null;
  trigger: string;
  /** Who read the lines: the AI; "ocr" only on runs recorded before 2026-09-17. */
  reader: "ocr" | "ai";
  /** What the reader noted about the read. */
  notes: string[];
  started_at: string;
  issues: ExtractionIssue[];
}

/** One row of the equipment current table (`/design-rules/equipment-currents`):
 * what a part draws, settled once for every project. */
export interface EquipmentCurrent {
  id: number;
  manufacturer: string;
  part_no: string;
  key: string;
  description: string | null;
  kind: "mechanical" | "built_in" | "device" | "unknown";
  no_load: boolean;
  standby_ma: number | null;
  alarm_ma: number | null;
  included_in: string | null;
  source: string;
  confirmed_by: string | null;
  aliases: string[];
  /** Whether the row answers the question (no load, or a figure). */
  settled: boolean;
  /** The datasheet in the library the row refers to, and how it was matched
   * to the part: "filename" / "family" (its own sheet) or "text" (a mention). */
  datasheet_library: string | null;
  datasheet_path: string | null;
  datasheet_pages: number[];
  datasheet_match: "filename" | "family" | "text" | null;
  created_at: string;
  updated_at: string | null;
}

export interface EquipmentAuditFinding {
  id: number;
  part_no: string;
  kind: string;
  status: "no_datasheet" | "unlinked" | "link_broken" | "text_match_only" | "figure_not_found" | "figure_differs" | "sheet_changed" | "no_library";
  detail: string;
  datasheet_path: string | null;
}

export interface EquipmentAudit {
  rows: number;
  findings: EquipmentAuditFinding[];
  linked: number;
}

/** A stored AI reading of one of the project's documents: made once, kept
 * for good, reused by every later open of a document with the same content. */
export interface DocumentReading {
  id: number;
  kind: "design_sheet" | "drf";
  document_name: string;
  model: string;
  pages: number;
  status: "completed" | "failed";
  error: string | null;
  calls: number;
  created_at: string;
}

export interface ExtractionState {
  ai_enabled: boolean;
  /** Enabled is not the same as usable: false here means the flag is on but
   * the server has no credential. `ai_status` says which. */
  ai_ready: boolean;
  ai_status: string;
  runs: ExtractionRun[];
  open_issues: number;
  readings: DocumentReading[];
}

/** A suggestion the model made during resolution; shown, never applied. */
export interface AiSuggestion {
  kind: "sheet_system" | "drf_field" | "error";
  target: string;
  state: string;
  value: string | null;
  reason: string;
}

// --- a user's account record (backend app/services/activity.py) --------------

export interface ActivityEvent {
  id: number;
  at: string;
  action: string;
  summary: string;
  project_id: number | null;
  project_label: string | null;
  entity_type: string | null;
  entity_id: number | null;
  detail: Record<string, unknown> | null;
}

export interface ActivityPage {
  events: ActivityEvent[];
  total: number;
  offset: number;
  limit: number;
}

export interface AccountProject {
  id: number | null;
  label: string;
  ep_number: string | null;
  project_name: string | null;
  status: string | null;
  created: boolean;
  assigned: boolean;
  opened_count: number;
  changes: number;
  last_activity_at: string | null;
  deleted: boolean;
}

export interface AccountSubmittal {
  id: number;
  project_id: number;
  project_label: string;
  title: string;
  reference: string | null;
  system_code: string | null;
  manufacturer: string | null;
  revision: string;
  status: string;
  reply_code: string | null;
  created_by_user: boolean;
  changes_by_user: number;
  updated_at: string;
}

export interface AccountBoqRevision {
  project_id: number;
  project_label: string;
  number: number;
  label: string;
  note: string | null;
  lines: number;
  issued_at: string;
}

export interface AccountStatement {
  id: number;
  project_id: number;
  project_label: string;
  kind: string;
  system_code: string;
  clauses: number;
  created_by_user: boolean;
  approved_by_user: boolean;
  approved_at: string | null;
  clause_changes_by_user: number;
  updated_at: string;
}

export interface Account {
  user: User;
  counts: Record<string, number>;
  projects: AccountProject[];
  submittals: AccountSubmittal[];
  boq_revisions: AccountBoqRevision[];
  compliance_statements: AccountStatement[];
  activity: ActivityEvent[];
  activity_total: number;
  actions: string[];
}

/** One datasheet in a manufacturer's library. The library is shared, so the
 * same files are on every project. */
export interface DatasheetFile {
  library: string;
  path: string;
  folder: string;
  filename: string;
  document_no: string | null;
  pages: number;
  size: number;
  reads_as_datasheet: boolean;
  unreadable: boolean;
  /** Seconds since the epoch: when the file last changed in the synced
   * library. Nothing records who put it there. */
  modified: number;
  /** The manufacturer's document number, "E85001-0495" -- what an engineer
   * quotes when they name a sheet. Null for a sheet that prints none and
   * has had none entered; that one keeps its file name. */
  reference_no: string | null;
}

/** A manufacturer the platform can look a part up in: a folder under the
 * datasheet library. `available` is false for a brand that is configured
 * but whose folder is nowhere. */
export interface DatasheetLibrarySummary {
  name: string;
  folder: string;
  available: boolean;
  source: string;
  datasheets: number;
}

/** One system's share of the datasheet library. The systems are the
 * platform's own (FAS, the two kinds of emergency lighting, FRC); a
 * manufacturer with `available: false` is one the company uses whose
 * sheets have not been filed in the library yet. */
export interface DatasheetSystem {
  code: string;
  label: string;
  description: string;
  datasheets: number;
  manufacturers: { name: string; datasheets: number; available: boolean }[];
}

/** One thing the datasheet search can be asked for. A "document" is a file
 * the library holds, named for itself; a "part" is a part number recorded
 * against a sheet that is *not* named for it. Both are needed: a part
 * whose sheet carries its number has no link, and a variant is only ever
 * found through one. */
export interface DatasheetSuggestion {
  kind: "part" | "document";
  label: string;
  reference_no: string | null;
  description: string | null;
  library: string;
  path: string;
  document_no: string | null;
}


/** A system on the Proposed Materials tab, with the brand its materials are for. */
export interface ProposedSystem {
  code: string;
  title: string;
  brand: string | null;
}

/** A proposed material: a part the BOQ quotes ("boq") or one added on the tab ("added"). */
export interface ProposedMaterial extends MaterialItem {
  source: "boq" | "added" | "battery" | "cable";
  id?: number | null;
  note?: string | null;
  added_at?: string | null;
}

export interface ProposedMaterials {
  systems: ProposedSystem[];
  items: ProposedMaterial[];
}

/** A part number on file for a brand (GET /parts/search). */
export interface PartSuggestion {
  part_no: string;
  description: string;
  sources: string[];
}


/** The fire-rated cables of a full-package project (GET/PUT /projects/{id}/frc-cables). */
export interface FrcCable {
  field: string;
  name: string;
  size: string | null;
  standard: string;
  warning: string | null;
}

/** Who supplies a brand (brand_suppliers), for every project. */
export interface Supplier {
  brand: string;
  supplier: string;
  contact?: string | null;
  phone?: string | null;
  emails?: string | null;
  address?: string | null;
  map_url?: string | null;
  website?: string | null;
  notes?: string | null;
  updated_at?: string | null;
}

export interface FrcCables {
  brand: string | null;
  brands: string[];
  sizes: string[];
  cables: FrcCable[];
  /** The emergency light monitoring cable: only on a monitored self-contained system, brand and size given. */
  monitoring: { applies: boolean; name: string; brand: string | null; brands: string[]; size: string | null; supplier?: Supplier | null };
  /** Who supplies the chosen brand. */
  supplier?: Supplier | null;
  updated_at: string | null;
}

/** Where this PC keeps the platform's data (GET /data-location): the
 * database in use, whether it is a folder shared between PCs, and what it
 * holds. */
export interface DataLocation {
  machine: string;
  database: string;
  shared: boolean;
  data_root: string | null;
  uploads: string;
  backups: string | null;
  projects: number;
  users: number;
  advice: string;
}


/** One line of the floor-wise schedule read off an engineer's workbook
 * (the BOQ page's "BOQ Floor Wise" tab). `per_floor` is already expanded:
 * a column headed "1 to 13" has put its quantity against each of the
 * thirteen floors. */
export interface FloorScheduleItem {
  description: string;
  catalog_no: string | null;
  unit: string | null;
  manufacturer: string | null;
  remarks: string | null;
  /** What the line is, in the platform's own vocabulary ("Smoke
   * detector"), read from the description. Null where the wording names
   * no device the platform knows -- never guessed at. */
  device: string | null;
  /** The family it is ordered under ("Detectors"), as the drawings tab
   * groups its columns. */
  family: string | null;
  /** "FAS" or "ELS": a fire alarm job and an emergency lighting job are
   * two BOQs, so the schedule is shown as two. Null where the line names
   * no device the platform knows -- shown under neither rather than
   * dropped. */
  system: string | null;
  per_floor: Record<string, number>;
  /** Which of the project's proposed materials the line is ordered as,
   * once an engineer settles it. */
  material?: { part_no: string; description: string | null; manufacturer: string | null } | null;
  /** True once a quantity on the line has been set by hand, so a row no
   * longer claiming to match the sheet's own total is expected. */
  edited?: boolean;
  total: number;
  /** What the workbook's own total column said, where it had one. */
  stated_total: number | null;
  row: number;
}

/** A column of the workbook, and what it was taken to be. */
export interface FloorScheduleColumn {
  heading: string;
  kind: string;
  floors: string[];
  /** True where the column stands for a range of floors ("1 to 13"). */
  typical: boolean;
}

export interface FloorScheduleResult {
  sheet: string;
  /** Every floor the schedule covers, lowest first, each once -- the
   * typical ranges already written out. */
  floors: string[];
  items: FloorScheduleItem[];
  columns: FloorScheduleColumn[];
  /** "per_floor" (a typical column's quantity is the quantity on each of
   * its floors) or "across" (it is shared between them). A thirteenfold
   * difference, so `typical_reason` says what settled it. */
  typical_reading: "per_floor" | "across";
  typical_reason: string;
  warnings: string[];
  /** The devices the schedule carries, each once. */
  devices: string[];
  /** What each system's rows come to. A line the platform cannot name is
   * counted under "" and shown all the same, so these always sum to
   * `grand_total` and nothing can be lost between the tabs. */
  systems: Record<string, number>;
  totals: Record<string, number>;
  grand_total: number;
  /** What the sheet's own total column comes to, where it has one. It
   * includes lines whose floor cells could not be read as numbers, so the
   * two differ by exactly what could not be counted per floor. */
  stated_grand_total: number | null;
}

/** GET/POST /projects/{id}/floor-schedule */
export interface FloorSchedule {
  result: FloorScheduleResult | null;
  file_name: string | null;
  sheet_name: string | null;
  /** Where the workbook was filed in the project's own OneDrive folder
   * ("03- Design/..."), or null when that folder is not reachable on this
   * PC -- the schedule is read and shown all the same. */
  archive_path: string | null;
  /** The workbook in the project's own folder the tab keeps itself in step
   * with. Set once a schedule has been read from (or filed into)
   * `03- Design`; the upload control is only offered when it is null. */
  source_path: string | null;
  /** What the last sync did, when it did something: read the workbook for
   * the first time, or read it again because it had changed. */
  filed_note: string | null;
  updated_at: string | null;
}

/** GET /projects/{id}/floor-schedule/check -- the schedule's own totals
 * beside the design sheet BOQ, which are read from different documents
 * and should agree. */
export interface FloorScheduleCheck {
  rows: {
    description: string;
    catalog_no: string | null;
    schedule_total: number;
    boq_quantity: number | null;
    difference: number | null;
  }[];
  matched: number;
  only_in_schedule: { description: string; catalog_no: string | null; total: number }[];
  only_in_boq: { catalog_no: string | null; description: string; quantity: number }[];
}


/** One part a schedule line may be settled as: the project's own proposed
 * materials, devices only (GET /projects/{id}/floor-schedule/materials).
 * Back boxes, panel parts and batteries are never offered -- they are
 * ordered with a device or for a panel, not counted on a floor. */
export interface FloorScheduleMaterial {
  part_no: string;
  description: string;
  manufacturer: string | null;
  system_code: string | null;
  source: string;
}


/** One speaker the project proposes, as a column of the amplifier
 * schedule. `tap` is null where the platform has no datasheet tapping for
 * the part -- the engineer picks one and nothing is assumed. */
export interface AmplifierColumn {
  key: string;
  description: string;
  taps: number[];
  tap: number | null;
  lines: string[];
  /** The schedule line is not settled as a part on the Proposed Materials
   * tab, so the column is named after the schedule's own wording. */
  unsettled: boolean;
}

export interface AmplifierFloorRow {
  floor: string;
  counts: Record<string, number>;
  watts: number;
  speakers: number;
  amplifier: string | null;
  /** How many audio riser modules the floor needs: one, unless its load is
   * more than a single module may carry. Nought where it has no speakers. */
  modules: number;
}

export interface AmplifierUnit {
  name: string;
  floors: string[];
  watts: number;
  cabinet: string | null;
  /** The floor carries more speakers than one amplifier can feed, so it is
   * shown alone and over its limit rather than split quietly. */
  over_limit: boolean;
  /** The staircase circuits it feeds; a floor amplifier feeds floors. */
  circuits: string[];
}

/** One staircase circuit: a run of floors of one stair, on one module. */
export interface StairCircuit {
  /** "ST1-1": stair 1, its first circuit. */
  name: string;
  stair: number;
  floors: string[];
  speakers: number;
  watts: number;
  amplifier: string | null;
  over_limit: boolean;
}

/** The staircase speakers, on circuits of their own. */
export interface StaircaseResult {
  columns: AmplifierColumn[];
  /** How many stairs the building has: its busiest floor's count of
   * staircase speakers, one to a stair. */
  stairs: number;
  floors: {
    floor: string;
    counts: Record<string, number>;
    stairs: number;
    speakers: number;
    watts: number;
    /** The circuit each of the floor's stairs is on, stair 1 first. */
    circuits: string[];
  }[];
  circuits: StairCircuit[];
  amplifiers: AmplifierUnit[];
  /** What one staircase circuit may carry: the amplifier's limit, or the
   * module's rating where that is lower. */
  circuit_limit_watts: number;
  total_circuits: number;
  total_speakers: number;
  total_watts: number;
  total_watts_with_spare: number;
  totals_by_column: Record<string, number>;
  warnings: string[];
}

export interface AmplifierResult {
  columns: AmplifierColumn[];
  floors: AmplifierFloorRow[];
  amplifiers: AmplifierUnit[];
  cabinets: { name: string; amplifiers: string[] }[];
  amplifier_part: string;
  amplifier_watts: number;
  /** What one amplifier may be loaded to: its rating times the design
   * rule's fraction. */
  limit_watts: number;
  amplifiers_per_cabinet: number;
  /** The audio riser module each floor is fed through, and what one may
   * carry, from the `ve.module` design rule. */
  module_part: string;
  module_max_watts: number;
  total_modules: number;
  total_floors: number;
  total_speakers: number;
  total_watts: number;
  spare_fraction: number;
  total_watts_with_spare: number;
  totals_by_column: Record<string, number>;
  warnings: string[];
  /** Null where the project keeps no staircase apart. */
  staircase: StaircaseResult | null;
  /** What the job orders, the floors' and the staircases' together. */
  job: { amplifiers: number; modules: number; cabinets: number; speakers: number; watts: number };
}

/** GET/PUT /projects/{id}/design/amplifier */
export interface AmplifierSchedule {
  result: AmplifierResult;
  schedule_file: string | null;
  updated_at: string | null;
}

/** GET /design/speakers -- what each speaker can be tapped at. */
export interface SpeakerDatabaseRow {
  part_no: string;
  description: string | null;
  taps: number[];
  default_tap: number | null;
}


/** One 24 V appliance the project proposes, as a column of the power
 * schedule. `current_ma` is null until an engineer chooses one of the
 * datasheet's figures -- nothing is assumed. */
export interface PowerColumn {
  key: string;
  description: string;
  device: string;
  /** Every figure the datasheet gives; `default` marks the setting the
   * device leaves the factory at, which it draws until set otherwise. */
  currents: { ma: number; label?: string; default?: boolean }[];
  current_ma: number | null;
  /** A sounder base is powered but sits on the Signature loop, so it needs
   * no notification-circuit module. */
  needs_module: boolean;
  lines: string[];
  unsettled: boolean;
}

export interface PowerFloorRow {
  floor: string;
  counts: Record<string, number>;
  current_ma: number;
  devices: number;
  supply: string | null;
  /** The notification circuit of its supply the floor is wired to. */
  circuit: string | null;
  modules: number;
}

/** One notification circuit of a supply, and the floors wired to it. */
export interface PowerCircuit {
  name: string;
  supply: string;
  floors: string[];
  current_ma: number;
  over_limit: boolean;
}

export interface PowerResult {
  columns: PowerColumn[];
  floors: PowerFloorRow[];
  supplies: { name: string; floors: string[]; current_ma: number; over_limit: boolean; circuits: string[] }[];
  circuits: PowerCircuit[];
  supply_part: string;
  supply_amps: number;
  /** What one supply may be worked to, in milliamps. */
  limit_ma: number;
  /** How many notification circuits one supply has. */
  circuits_per_supply: number;
  /** What one circuit may be worked to, in milliamps: the supply's rating
   * shared between its circuits, at the same spare. */
  circuit_limit_ma: number;
  module_part: string;
  total_floors: number;
  total_devices: number;
  total_modules: number;
  total_ma: number;
  total_amps: number;
  totals_by_column: Record<string, number>;
  warnings: string[];
}

/** GET/PUT /projects/{id}/design/power */
export interface PowerSchedule {
  result: PowerResult;
  schedule_file: string | null;
  updated_at: string | null;
}

/** GET /design/device-currents -- what each 24 V appliance draws. */
export interface DeviceCurrentRow {
  part_no: string;
  description: string | null;
  currents: { ma: number; label?: string }[];
}

/** GET /archive/search -- one line of the EP search box's dropdown. The
 * archive's EP folders are indexed in the database, so typing suggests
 * projects instead of walking OneDrive for every keystroke. */
export interface ArchiveSuggestion {
  ep_number: string;
  /** The project's own name once the platform has one (read off the DRF),
   * otherwise the name the archive folder carries. */
  project_name: string | null;
  folder_name: string;
  /** Below the archive root, never the whole path: the index is shared
   * between machines and the archive sits under each user's own profile. */
  relative_path: string;
  /** How many folders carry this EP number. More than one and creating it
   * still goes through the "which folder is this?" step. */
  locations: number;
  /** The platform's project for this number, when it already has one. */
  project_id: number | null;
  project_status: ProjectStatus | null;
}

/** GET /archive/status -- whether the EP search box can answer from the
 * index, and how fresh the index is. */
export interface ArchiveStatus {
  configured: boolean;
  reachable: boolean;
  archive_path: string | null;
  /** pending | scanning | ready | partial | failed */
  scan_status: string;
  scanning: boolean;
  /** Distinct EP numbers, and the folders they are filed in. */
  projects: number;
  folders: number;
  searchable: boolean;
  last_scan_at: string | null;
  last_successful_scan_at: string | null;
  last_error: string | null;
}
