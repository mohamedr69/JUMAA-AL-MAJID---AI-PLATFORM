export type Role = "admin" | "design_manager" | "design_engineer" | "draftsman" | "viewer";

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
  draftsman: "Draftsman",
  viewer: "Viewer",
};

export interface DocumentCandidate {
  path: string;
  filename: string;
  system_guess: string | null;
}

export interface ExtractedField {
  value: string;
  confidence: number;
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
  drf_candidates: DocumentCandidate[];
  design_sheet_candidates: DocumentCandidate[];
  warnings: string[];
  errors: string[];
  extracted_fields: Partial<Record<ExtractedFieldName, ExtractedField>>;
  extracted_scope_of_work: string | null;
  extracted_systems: ProjectSystemInput[];
  extraction_warnings: string[];
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

/** A Bill of Quantities line. `quantity` is text because the Design Sheets
 * use "Lot" as readily as a number. Prices are sent as strings so decimals
 * survive the round trip without float rounding. */
export interface ProjectBoqItemInput {
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

export interface ProjectBoqItem extends ProjectBoqItemInput {
  id: number;
  position: number;
}

/** Reply from the BOQ open call. `extracted` is true only on the call that
 * actually read the Design Sheets, which happens once per project. */
export interface BoqEnsureResponse {
  items: ProjectBoqItem[];
  extracted: boolean;
  warnings: string[];
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
export type BoqGroupTreatment = "panel" | "skipped_aps_bps" | "ungrouped" | "not_a_panel";

export interface BatteryLine {
  part_no: string | null;
  description: string;
  quantity: number | null;
  manufacturer: string | null;
  kind: "load" | "battery" | "no_part";
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
  heading: string;
  system_code: string | null;
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

export interface ScannedForm {
  reference: string;
  revision: string;
  title: string;
  system_code: string | null;
  supplier: string | null;
  reply_code: string | null;
  reply_text: string | null;
  status: SubmittalStatus;
  path: string;
  read_by_ocr: boolean;
}

export interface SubmittalScan {
  found: number;
  created: number;
  updated: number;
  unchanged: number;
  warnings: string[];
  forms: ScannedForm[];
  updated_register: SubmittalRegister;
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
}

export interface ComplianceSystem {
  code: string;
  name: string;
  specs: SpecMatch[];
}

export interface Compliance {
  systems: ComplianceSystem[];
  warnings: string[];
  searched: string | null;
}

export interface DraftMail {
  to: string | null;
  to_name: string | null;
  subject: string;
  body: string;
}
