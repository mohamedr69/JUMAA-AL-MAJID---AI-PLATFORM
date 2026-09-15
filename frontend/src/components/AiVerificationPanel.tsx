import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../lib/api";
import { formatApiDate } from "../lib/format";
import type { AiVerification, AiVerificationItem, AiVerificationState } from "../lib/types";
import { useJob } from "../lib/useJob";
import { JobProgress } from "./JobProgress";

const OUTCOME: Record<AiVerificationItem["outcome"], { label: string; className: string }> = {
  confirmed: { label: "Confirmed", className: "bg-emerald-50 text-emerald-800 ring-emerald-200" },
  corrected: { label: "Corrected", className: "bg-blue-50 text-blue-800 ring-blue-200" },
  added: { label: "Added", className: "bg-blue-50 text-blue-800 ring-blue-200" },
  removed: { label: "Removed", className: "bg-gray-100 text-gray-700 ring-gray-300" },
  unresolved: { label: "Could not settle", className: "bg-amber-50 text-amber-900 ring-amber-200" },
  not_checked: { label: "Not checked", className: "bg-gray-50 text-gray-600 ring-gray-200" },
  not_an_item: { label: "Heading, not a line", className: "bg-gray-50 text-gray-600 ring-gray-200" },
};

type Line = { quantity?: string | null; catalog_no?: string | null; description?: string | null };
type SystemMarks = { brand?: string | null; method_statement?: boolean; drawing?: boolean };

function lineText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value || "(blank)";
  const line = value as Line & SystemMarks;
  if ("method_statement" in line || "drawing" in line || "brand" in line) {
    const marks = [line.brand || "no brand", line.method_statement ? "MS" : "", line.drawing ? "DWG" : ""].filter(Boolean);
    return marks.join(" · ");
  }
  const parts = [line.quantity ? `${line.quantity} ×` : "", line.catalog_no ?? "", line.description ?? ""].filter(Boolean);
  return parts.join(" ") || "(blank)";
}

/** The AI's check of the BOQ against the Design Sheets, or of Project Info
 * against the DRF: it runs on its own the first time the page opens, applies
 * what it confirms, and lists what it changed with a one-step undo. */
export function AiVerificationPanel({
  projectId,
  scope,
  canEdit,
  onApplied,
}: {
  projectId: number;
  scope: "boq" | "details";
  canEdit: boolean;
  onApplied: () => void;
}) {
  const [state, setState] = useState<AiVerificationState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const onAppliedRef = useRef(onApplied);
  onAppliedRef.current = onApplied;

  const load = useCallback(async () => {
    try {
      setState(await api.get<AiVerificationState>(`/projects/${projectId}/ai-verification`));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not read the AI check");
    }
  }, [projectId]);

  const { job, active, error: jobError, start, cancel, follow } = useJob(
    projectId,
    "ai_verify",
    `/projects/${projectId}/jobs/ai-verify?scope=all`,
    () => {
      void load();
      onAppliedRef.current();
    }
  );

  useEffect(() => {
    let cancelled = false;
    const first = canEdit
      ? api.post<AiVerificationState>(`/projects/${projectId}/ai-verification/ensure`)
      : api.get<AiVerificationState>(`/projects/${projectId}/ai-verification`);
    first
      .then((next) => {
        if (cancelled) return;
        setState(next);
        if (next.job) follow(next.job);
      })
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Could not read the AI check"));
    return () => {
      cancelled = true;
    };
  }, [projectId, canEdit, follow]);

  async function undo(record: AiVerification) {
    const what = scope === "boq" ? "the BOQ lines" : "the project information";
    if (!window.confirm(`Put ${what} back as they were before this AI check?`)) return;
    setBusy(true);
    setError(null);
    try {
      setState(await api.post<AiVerificationState>(`/projects/${projectId}/ai-verification/${record.id}/undo`));
      onApplied();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The AI check could not be undone");
    } finally {
      setBusy(false);
    }
  }

  if (!state) return error ? <div className="mt-4 text-xs text-red-700">{error}</div> : null;
  const record = scope === "boq" ? state.boq : state.details;
  const source = scope === "boq" ? "the Design Sheets" : "the DRF";
  const s = record?.summary ?? {};
  const changes = (record?.items ?? []).filter((item) => item.outcome !== "confirmed");
  const unresolved = changes.filter((item) => item.outcome === "unresolved");

  return (
    <section
      className={`mt-4 rounded-xl border p-4 ${unresolved.length && !record?.stale ? "border-amber-200 bg-amber-50/40" : "border-violet-200 bg-violet-50/40"}`}
      aria-label="AI check"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-navy-900">AI check against {source}</div>
          <div className="mt-0.5 text-xs text-gray-600">
            {!state.available && <>AI is not available: {state.reason}</>}
            {state.available && !record && !active && "Not checked yet."}
            {record?.status === "running" && !active && "A check is running."}
            {record?.status === "failed" && <span className="text-red-700">The last check did not finish: {record.error}</span>}
            {record?.status === "undone" && "The last check was undone."}
            {record?.status === "completed" && (
              <>
                Checked {formatApiDate(record.finished_at ?? record.started_at, "short")}: {s.confirmed ?? 0} confirmed
                {s.corrected ? `, ${s.corrected} corrected` : ""}
                {s.added ? `, ${s.added} added` : ""}
                {s.removed ? `, ${s.removed} removed` : ""}
                {s.unresolved ? `, ${s.unresolved} it could not settle` : ""}
                {s.not_checked ? `, ${s.not_checked} not checked` : ""}.
                {record.stale && <span className="text-amber-800"> Changed since — check again to confirm the current values.</span>}
              </>
            )}
          </div>
        </div>
        {canEdit && state.available && (
          <div className="flex shrink-0 items-center gap-2">
            {record?.can_undo && (
              <button
                onClick={() => void undo(record)}
                disabled={busy || active}
                className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-60"
              >
                Undo these changes
              </button>
            )}
            <button
              onClick={() => void start()}
              disabled={active || busy}
              className="rounded-lg border border-violet-300 bg-white px-3 py-1.5 text-xs font-semibold text-violet-800 hover:bg-violet-50 disabled:opacity-60"
            >
              {active ? "Checking..." : record ? "Check again" : "Check now"}
            </button>
          </div>
        )}
      </div>

      {job && active && <JobProgress job={job} onCancel={canEdit ? () => void cancel() : undefined} what="the AI check" />}
      {(error || jobError) && <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error ?? jobError}</div>}

      {record?.status === "completed" && changes.length > 0 && (
        <div className="mt-2">
          <button onClick={() => setOpen(!open)} className="text-xs font-medium text-violet-800 hover:underline" aria-expanded={open}>
            {open ? "Hide" : "Show"} what the AI changed or could not settle ({changes.length})
          </button>
          {open && (
            <ul className="mt-2 max-h-96 space-y-1.5 overflow-y-auto pr-1">
              {changes.map((item) => (
                <li key={item.id} className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${OUTCOME[item.outcome].className}`}>
                      {OUTCOME[item.outcome].label}
                    </span>
                    <span className="font-medium text-navy-900">
                      {item.label ?? (item.system_code ? `${item.system_code}` : "")}
                      {item.document ? ` · ${item.document}${item.page ? ` p.${item.page}` : ""}` : ""}
                    </span>
                  </div>
                  <div className="mt-1 text-gray-700">
                    {item.outcome === "added" ? (
                      <>Added: {lineText(item.final)}</>
                    ) : item.outcome === "removed" ? (
                      <>Removed: {lineText(item.held)}</>
                    ) : item.outcome === "corrected" ? (
                      <>
                        {lineText(item.held)} <span className="text-gray-400">→</span>{" "}
                        <span className="font-medium text-navy-900">{lineText(item.final)}</span>
                      </>
                    ) : (
                      <>Kept: {lineText(item.held ?? item.final)}</>
                    )}
                  </div>
                  <div className="mt-0.5 text-gray-500">
                    {item.reason}
                    {item.outcome === "unresolved" && (
                      <>
                        {" "}
                        · OCR read {lineText(item.ocr)} · AI read {lineText(item.ai)}
                        {item.ai2 ? ` · second AI read ${lineText(item.ai2)}` : ""}
                        {item.ai3 ? ` · close-up AI read ${lineText(item.ai3)}` : ""}
                      </>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {record?.status === "completed" && record.notes.length > 0 && (
        <div className="mt-2 text-[11px] text-gray-500">{record.notes.join(" · ")}</div>
      )}
    </section>
  );
}

/** A BOQ line's AI verdict, beside its source badge. */
export function AiCheckBadge({ check, stale }: { check: { status: string; reason: string } | null | undefined; stale?: boolean }) {
  if (!check) return null;
  const styles: Record<string, [string, string]> = {
    confirmed: ["AI ✓", "bg-emerald-50 text-emerald-800 ring-emerald-200"],
    corrected: ["AI fixed", "bg-blue-50 text-blue-800 ring-blue-200"],
    added: ["AI added", "bg-blue-50 text-blue-800 ring-blue-200"],
    unresolved: ["AI unsure", "bg-amber-50 text-amber-900 ring-amber-200"],
  };
  const [label, className] = styles[check.status] ?? ["AI", "bg-gray-50 text-gray-600 ring-gray-200"];
  return (
    <span
      title={`${check.reason}${stale ? " (checked before later edits)" : ""}`}
      className={`ml-1 inline-block whitespace-nowrap rounded-full px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset ${className}`}
    >
      {label}
    </span>
  );
}
