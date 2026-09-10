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

export interface ProjectResolveResponse {
  ep_number: string;
  folder_found: boolean;
  is_ambiguous: boolean;
  matched_folders: string[];
  drf_candidates: DocumentCandidate[];
  design_sheet_candidates: DocumentCandidate[];
  warnings: string[];
  errors: string[];
}

export interface ProjectDesignSheetIn {
  system_code: string | null;
  document_path: string;
}

export interface ProjectDesignSheet extends ProjectDesignSheetIn {
  id: number;
}

export interface ProjectCreate {
  ep_number: string;
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
  systems: string[];
  other_information?: string | null;
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
  systems: string | null;
  other_information: string | null;
  source_folder_path: string | null;
  drf_document_path: string | null;
  design_sheets: ProjectDesignSheet[];
  created_at: string;
}

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
  "Others",
] as const;

export const SCOPE_OF_WORK_OPTIONS = ["Full Package", "Design, Supply, T&C", "Supply Only"] as const;
