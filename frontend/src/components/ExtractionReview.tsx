import { useCallback, useEffect, useState } from "react";
import { ApiError, api, apiUrl } from "../lib/api";
import type { AiBudget, ExtractionIssue, ExtractionRun, ExtractionState } from "../lib/types";

/** What a Design Sheet read could not settle, for the engineer to decide.
 *
 * Each row shows what the first reading made of it, whether and how it
 * was verified, and the real reason it is here -- a structured code from
 * the read (app.extraction.issues.ReviewReason), worded below -- with the
 * row's own image. A reason about the process (the verification not
 * completed, a timeout, the time budget) leaves the first reading
 * standing: the row is not "unknown", it is unverified, and its quantity
 * is offered as read. Nothing here is applied until Accept. */

const REASON_TEXT: Record<string, string> = {
  VERIFICATION_NOT_COMPLETED: "The verification of this row was not completed",
  PART_NUMBER_CONFLICT: "The readings disagree on the part number",
  QUANTITY_CONFLICT: "The readings disagree on the quantity",
  ROW_ASSOCIATION_AMBIGUOUS: "Which row the quantity belongs to is ambiguous",
  GROUP_UNRESOLVED: "Which heading this row belongs under is unresolved",
  MULTIPLE_QUANTITY_CANDIDATES: "More than one quantity was found at this row",
  AI_TIMEOUT: "The AI did not answer in time",
  AI_PROVIDER_ERROR: "The AI provider failed",
  AI_INVALID_RESPONSE: "The AI gave no usable reading",
  AI_BUDGET_EXHAUSTED: "The AI call allowance for this read was used up",
  TIME_BUDGET_EXHAUSTED: "The processing time budget was reached",
  LOW_CONFIDENCE: "Neither verification could read the row",
  UNREADABLE: "The quantity could not be read with confidence",
};

// Reasons about the process, not the row: the first reading stands, unverified.
const PROCESS_REASONS = new Set([
  "VERIFICATION_NOT_COMPLETED", "AI_TIMEOUT", "AI_PROVIDER_ERROR", "AI_INVALID_RESPONSE", "AI_BUDGET_EXHAUSTED",
  "TIME_BUDGET_EXHAUSTED",
]);

interface ReviewDetail {
  description?: string;
  catalog_no?: string | null;
  group_heading?: string | null;
  raw_quantity?: string | null;
  reason?: string;
  reason_code?: string;
  row_id?: string;
  source_stage?: "primary" | "second";
  pending?: boolean;
  primary?: { quantity?: string | null; catalog_no?: string | null; description?: string | null } | null;
  verification?: {
    status?: string;
    field?: string;
    reason?: string;
    heading?: string | null;
    close_up?: { quantity?: string; catalog_no?: string } | null;
    tier1?: { quantity?: string; catalog_no?: string } | null;
  } | null;
  evidence?: { score?: number; level?: string } | null;
  ai_reading?: { quantity?: string; catalog_no?: string } | null;
}

export function ExtractionReview({
  projectId,
  canEdit,
  onAccepted,
}: {
  projectId: number;
  canEdit: boolean;
  onAccepted: () => void;
}) {
  const [state, setState] = useState<ExtractionState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | "assist" | "resume" | null>(null);
  const [budget, setBudget] = useState<AiBudget | null>(null);

  const load = useCallback(() => {
    api
      .get<ExtractionState>(`/projects/${projectId}/extraction`)
      .then(setState)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not read the extraction status"));
    // The budget line is a courtesy: the page works without it.
    api.get<AiBudget>(`/projects/${projectId}/ai/budget`).then(setBudget).catch(() => setBudget(null));
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  if (!state) return error ? <div className="mt-4 text-xs text-red-700">{error}</div> : null;

  // Only a row can be added or rejected. A page or a sheet the read could
  // not handle is an issue too, but it is said above ("page 1 had no
  // recognisable table") and on the sheet banner; shown here it became a
  // blank row asking for a quantity.
  const pending = state.runs.flatMap((run) =>
    run.issues
      .filter((i) => i.target.startsWith("boq_line:"))
      .filter((i) => i.state === "open" || i.state === "proposed" || i.state === "starved")
      .map((i) => ({ run, issue: i }))
  );
  // A sheet that could not be read at all is said on the BOQ page banner;
  // listing its page here too said the same thing twice.
  const partial = state.runs.filter((r) => r.unprocessed_pages.length > 0 && !r.failure);
  const starved = state.runs.filter((r) => r.budget_exhausted);
  const unfinished = state.runs.filter((r) => r.state && r.state !== "completed" && !r.failure);
  const eligible = pending.some((p) => p.issue.state === "open" && p.issue.llm_eligible);
  const canAssist = state.ai_ready && eligible;
  // On but unusable -- no credential on the server -- is worth saying once,
  // where the button would have been.
  const aiProblem = state.ai_enabled && !state.ai_ready && eligible ? state.ai_status : null;
  const unverified = pending.filter((p) => PROCESS_REASONS.has(String((p.issue.detail as ReviewDetail).reason_code ?? ""))).length;

  if (pending.length === 0 && partial.length === 0 && unfinished.length === 0) return null;

  async function decide(issue: ExtractionIssue, action: "accept" | "reject", value?: string) {
    setBusy(issue.id);
    setError(null);
    try {
      await api.post(`/projects/${projectId}/extraction/issues/${issue.id}/${action}`, { value: value ?? null });
      if (action === "accept") onAccepted();
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The decision could not be saved");
    } finally {
      setBusy(null);
    }
  }

  async function assist() {
    setBusy("assist");
    setError(null);
    try {
      setState(await api.post<ExtractionState>(`/projects/${projectId}/extraction/assist`));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "AI assistance did not complete");
    } finally {
      setBusy(null);
    }
  }

  async function resume() {
    // The re-read job resumes a read that stopped: only what is pending is
    // read, and the result is laid against the BOQ for the engineer.
    setBusy("resume");
    setError(null);
    try {
      await api.post(`/projects/${projectId}/jobs/boq-reread`);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The read could not be resumed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-navy-900">
            {pending.length === 0
              ? "The read did not finish"
              : `${pending.length} row${pending.length === 1 ? "" : "s"} need${pending.length === 1 ? "s" : ""} review`}
          </div>
          <div className="text-xs text-amber-900">
            {pending.length > 0 && (
              <div>
                {unverified > 0
                  ? `${unverified} of these were read but not verified (the read stopped before it could); the rest the readings could not settle. `
                  : "The Design Sheet read these rows but could not settle them. "}
                None is in the BOQ until you accept it.
              </div>
            )}
            {unfinished.map((run) => (
              <div key={`u${run.id}`}>
                {run.document_name}: the read {run.state === "timed_out" ? "ran out of time" : `is ${(run.state ?? "unfinished").replace(/_/g, " ")}`}
                {run.budget_exhausted ? ` (${run.budget_exhausted.replace(/_/g, " ")} limit)` : ""}; resuming it reads only what is left.
              </div>
            ))}
            {partial.map((run) => (
              <div key={run.id}>
                {run.document_name}: page{run.unprocessed_pages.length === 1 ? "" : "s"} {run.unprocessed_pages.join(", ")} had no
                recognisable table and {run.unprocessed_pages.length === 1 ? "was" : "were"} not read.
              </div>
            ))}
            {starved
              .filter((run) => !unfinished.includes(run))
              .map((run) => (
                <div key={`s${run.id}`}>
                  {run.document_name}: AI assistance stopped at the {run.budget_exhausted?.replace(/_/g, " ")} limit; the rest is unreviewed, not resolved.
                </div>
              ))}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1">
          {canEdit && unfinished.length > 0 && (
            <button
              onClick={resume}
              disabled={busy !== null}
              className="rounded-lg border border-brand-300 bg-white px-3 py-1.5 text-xs font-semibold text-brand-700 hover:bg-brand-50 disabled:opacity-60"
            >
              {busy === "resume" ? "Resuming..." : "Resume the read"}
            </button>
          )}
          {canEdit && canAssist && (
            <div className="flex flex-col items-end gap-0.5">
              <button
                onClick={assist}
                disabled={busy !== null || budget?.calls_remaining === 0}
                className="rounded-lg border border-brand-300 bg-white px-3 py-1.5 text-xs font-semibold text-brand-700 hover:bg-brand-50 disabled:opacity-60"
              >
                {busy === "assist" ? "Asking..." : "Ask AI to read the cells"}
              </button>
              {budget && (
                <span className={`text-[11px] ${budget.calls_remaining === 0 ? "text-red-700" : "text-gray-500"}`}>
                  {budget.calls_remaining === 0
                    ? "Today's AI allowance for this project is used up"
                    : `${budget.calls_remaining} of ${budget.calls_per_day_limit} AI calls left in the last 24 h`}
                  {budget.priced && budget.cost_last_24h > 0 ? ` · spent ${budget.cost_last_24h.toFixed(2)}` : ""}
                </span>
              )}
            </div>
          )}
          {canEdit && aiProblem && (
            <div className="max-w-xs rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs text-amber-800">{aiProblem}</div>
          )}
        </div>
      </div>

      {error && <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}

      {pending.length > 0 && (
        <ul className="mt-3 space-y-2">
          {pending.map(({ run, issue }) => (
            <IssueRow key={issue.id} run={run} issue={issue} projectId={projectId} canEdit={canEdit} busy={busy === issue.id} onDecide={decide} />
          ))}
        </ul>
      )}
    </div>
  );
}

function reasonText(detail: ReviewDetail): string {
  const code = detail.reason_code ?? "";
  return REASON_TEXT[code] ?? detail.reason ?? "The row could not be settled";
}

function IssueRow({
  run,
  issue,
  projectId,
  canEdit,
  busy,
  onDecide,
}: {
  run: ExtractionRun;
  issue: ExtractionIssue;
  projectId: number;
  canEdit: boolean;
  busy: boolean;
  onDecide: (issue: ExtractionIssue, action: "accept" | "reject", value?: string) => void;
}) {
  const detail = issue.detail as ReviewDetail;
  const proposal = [...issue.proposals].reverse().find((p) => p.value !== null) ?? null;
  const primaryQty = detail.primary?.quantity ?? detail.ai_reading?.quantity ?? detail.raw_quantity ?? null;
  const process = PROCESS_REASONS.has(String(detail.reason_code ?? ""));
  const verification = detail.verification ?? null;
  const [value, setValue] = useState(proposal?.value ?? (process ? primaryQty ?? "" : ""));
  const evidence = detail.evidence ?? null;
  const other = verification?.close_up ?? verification?.tier1 ?? null;

  let verificationText: string;
  if (verification?.status === "not_completed" || process) {
    verificationText = "Not completed";
  } else if (verification?.status === "conflict") {
    verificationText = `Disagrees on the ${verification.field === "catalog_no" ? "part number" : "quantity"}`;
  } else if (verification?.status === "group_unresolved") {
    verificationText = "Values agreed; the heading is the question";
  } else if (verification?.status === "unreadable") {
    verificationText = "Could not read the row";
  } else {
    verificationText = "Not settled";
  }

  return (
    <li className="rounded-lg border border-gray-200 bg-white p-3 text-xs">
      <div className="flex flex-wrap items-start gap-3">
        {issue.has_evidence_image && (
          <img
            src={apiUrl(`/projects/${projectId}/extraction/issues/${issue.id}/evidence.png`)}
            alt="The row as scanned"
            className="max-h-14 rounded border border-gray-200"
          />
        )}
        <div className="min-w-0 flex-1">
          <div className="font-medium text-navy-900">
            {detail.catalog_no ? `${detail.catalog_no} — ` : ""}
            {detail.description}
          </div>
          <div className="text-gray-500">
            {run.document_name}, page {issue.page}
            {detail.group_heading ? ` · ${detail.group_heading}` : ""}
            {detail.source_stage === "second" ? " · seen by the second reading only" : ""}
          </div>
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-gray-700">
            <dt className="text-gray-500">Read as</dt>
            <dd>
              {primaryQty ? (
                <>
                  Qty <span className="font-semibold">{primaryQty}</span>
                  {detail.primary?.catalog_no ? `, part ${detail.primary.catalog_no}` : ""}
                </>
              ) : (
                "no quantity read"
              )}
              {evidence?.level && evidence.level !== "unknown" && (
                <span className="ml-2 text-gray-500">
                  · page geometry: {evidence.level}
                  {typeof evidence.score === "number" ? ` (${evidence.score.toFixed(2)})` : ""}
                </span>
              )}
            </dd>
            <dt className="text-gray-500">Verification</dt>
            <dd>
              {verificationText}
              {other && (other.quantity || other.catalog_no) && verification?.status === "conflict" && (
                <span className="ml-1 text-amber-800">
                  (verified as{other.catalog_no ? ` part ${other.catalog_no}` : ""}
                  {other.quantity ? ` qty ${other.quantity}` : ""})
                </span>
              )}
            </dd>
            <dt className="text-gray-500">Reason</dt>
            <dd className={process ? "text-gray-700" : "text-amber-800"}>
              {reasonText(detail)}
              {detail.pending ? " · resuming the read asks about this row again" : ""}
            </dd>
            {verification?.status === "group_unresolved" && (
              <>
                <dt className="text-gray-500">Heading</dt>
                <dd>{verification.heading ?? "none"} — the row may be its own item, not part of it</dd>
              </>
            )}
          </dl>
          {proposal && (
            <div className={`mt-1 ${proposal.state === "validated" ? "text-emerald-700" : "text-amber-800"}`}>
              AI cell reading <span className="font-semibold">{proposal.value}</span>
              {proposal.state === "validated" ? " — a second AI reading agrees." : " — unconfirmed; check the row image before accepting."}
              {proposal.from_cache && " (from an earlier identical read)"}
              {proposal.injection_flags && proposal.injection_flags.length > 0 && (
                <div className="mt-0.5 text-red-700">
                  The sheet's text around this row contains wording aimed at instructing a model, so this reading was not
                  confirmed automatically. Check the row image yourself.
                </div>
              )}
            </div>
          )}
          {!proposal && issue.state === "starved" && <div className="mt-1 text-amber-800">{issue.state_reason}</div>}
        </div>
        {canEdit && (
          <div className="flex items-center gap-1">
            {process && primaryQty && (
              <button
                onClick={() => onDecide(issue, "accept", primaryQty)}
                disabled={busy}
                className="rounded-lg bg-brand-600 px-2.5 py-1 font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
              >
                Accept {primaryQty}
              </button>
            )}
            <input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="Qty"
              aria-label="Quantity to accept"
              className="input w-20 py-1 text-xs"
            />
            <button
              onClick={() => onDecide(issue, "accept", value)}
              disabled={busy || !value.trim()}
              className={`rounded-lg px-2.5 py-1 font-semibold disabled:opacity-60 ${
                process && primaryQty ? "border border-gray-200 text-gray-700 hover:bg-gray-50" : "bg-brand-600 text-white hover:bg-brand-700"
              }`}
            >
              {process && primaryQty ? "Edit" : "Add line"}
            </button>
            <button
              onClick={() => onDecide(issue, "reject")}
              disabled={busy}
              className="rounded-lg border border-gray-200 px-2.5 py-1 text-gray-600 hover:bg-gray-50 disabled:opacity-60"
            >
              Not an item
            </button>
          </div>
        )}
      </div>
    </li>
  );
}
