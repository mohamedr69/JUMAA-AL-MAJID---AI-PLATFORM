/** The Drawings page's records (backend app/routers/drawings.py). */

export type Status =
  | "approved"
  | "approved_as_noted"
  | "under_review"
  | "not_approved"
  | "reply_not_found"
  | "not_submitted";

export const STATUS_LABEL: Record<Status, string> = {
  approved: "Approved",
  approved_as_noted: "Approved as Noted",
  under_review: "Under Review",
  not_approved: "Not Approved",
  reply_not_found: "Answered – Reply Not Found",
  not_submitted: "Not Submitted",
};

export const OFFICIAL_STATUSES: Status[] = ["under_review", "approved", "approved_as_noted", "not_approved", "reply_not_found"];

/** A file found at a revision nothing proves was submitted. */
export interface Candidate {
  id: number;
  revision: string;
  path: string | null;
  page: number;
  file_sha256: string;
  detected_at: string | null;
  status: "available" | "confirmed" | "ignored" | "superseded" | "conflict";
  evidence: Record<string, unknown> & { note?: string; ai?: AiVerdict };
  decided_at: string | null;
  label: string;
}

export interface LogCell {
  revision?: string;
  status: Status;
  label: string;
  reference?: string | null;
  path?: string | null;
  page?: number;
  remarks?: string | null;
  modified?: string | null;
  /** Why a revision reads as it does ("R2 was submitted, so R1 was submitted and answered; ..."). */
  note?: string | null;
  /** "sync" | "submission" | "engineer" | "ai" */
  source?: string;
  /** An engineer set this status: it stands over the folder and the AI. */
  confirmed?: boolean;
  source_missing?: boolean;
  submitted_at?: string | null;
  reply_at?: string | null;
  /** Not an official revision: a file found ("R1 available"). */
  candidate?: Candidate;
}

export interface Hint {
  kind: string;
  label: string;
  severity: "info" | "warning" | "error";
  revision?: string;
  note?: string | null;
  source?: "system" | "ai";
  issue_id?: number;
  candidate_id?: number;
  path?: string | null;
}

/** A shop drawing, or an IFC floor no shop drawing covers yet (`reference` null). */
export interface LogRow {
  key: string;
  id: number | null;
  source: "shop_drawing" | "ifc_floor";
  reference: string | null;
  floor: string;
  floor_named: string | null;
  floor_keys: string[];
  floors: number;
  typical: boolean;
  confirmed: boolean;
  remarks: string;
  cells: Record<string, LogCell>;
  revisions: Record<string, LogCell>;
  latest_revision: string | null;
  latest_status: Status;
  latest_note: string | null;
  latest_path: string | null;
  latest_page: number;
  candidates: Candidate[];
  hints: Hint[];
  issues: number;
}

export interface DrawingsLog {
  revisions: string[];
  rows: LogRow[];
  counts: Partial<Record<Status, number>>;
  submissions: number;
  review_items: number;
  candidates: number;
  floors: { key: string; name: string; source: string; ifc_sheet: string | null }[];
  project: { id: number; ep_number: string; name: string | null };
  system: string;
  system_name: string;
  systems: string[];
  ifc: { id: number; filename: string; revision: string }[];
  synced_at: string | null;
  reconciled_at: string | null;
  folder: string | null;
  warnings: string[];
}

export interface SystemSummary {
  code: string;
  name: string;
  floors: number;
  approved: number;
  approved_as_noted: number;
  under_review: number;
  not_approved: number;
  not_submitted: number;
  reply_not_found: number;
  approved_total: number;
  review_items: number;
  candidates: number;
  counts: Partial<Record<Status, number>>;
}

export interface DrawingsSummary {
  project: { id: number; ep_number: string; name: string | null };
  systems: SystemSummary[];
  synced_at: string | null;
}

/** The AI's structured answer about one finding: no reasoning, ever. */
export interface AiVerdict {
  task?: string;
  drawing_reference?: string | null;
  revision?: string | null;
  status?: string | null;
  assessment?: string | null;
  confidence?: number | null;
  reason_code?: string | null;
  requires_engineer?: boolean;
  validation?: string;
  validation_reason?: string | null;
  error?: string;
}

export interface Issue {
  id: number;
  key: string;
  kind: string;
  label: string;
  severity: "info" | "warning" | "error";
  source: "system" | "ai";
  system: string | null;
  shop_drawing_id: number | null;
  floor_key: string | null;
  text: string;
  detail: Record<string, unknown> & { revision?: string; candidate_id?: number; path?: string };
  ai: AiVerdict | null;
  created_at: string | null;
  updated_at: string | null;
  resolved_at: string | null;
  resolution: string | null;
}

export interface Issues {
  system: string;
  systems: string[];
  total: number;
  system_checks: Issue[];
  ai_review: Issue[];
}

export interface DrawingEvent {
  id: number;
  kind: string;
  text: string;
  system: string | null;
  shop_drawing_id: number | null;
  floor_key: string | null;
  detail: Record<string, unknown>;
  user_id: number | null;
  at: string | null;
}

export interface DrawingDetail extends LogRow {
  system: string;
  system_name: string;
  floor_source: Record<string, { source: string; ifc_sheet: string | null; active: boolean }>;
  revision_history: LogCell[];
  issues_list: Issue[];
  events: DrawingEvent[];
}

export function day(value: string | null | undefined): string {
  if (!value) return "–";
  const date = new Date(value.endsWith("Z") || value.includes("+") ? value : value + "Z");
  return date.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
}

export function when(value: string | null | undefined): string {
  if (!value) return "–";
  const date = new Date(value.endsWith("Z") || value.includes("+") ? value : value + "Z");
  return date.toLocaleString(undefined, { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}
