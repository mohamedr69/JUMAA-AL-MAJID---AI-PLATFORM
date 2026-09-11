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
