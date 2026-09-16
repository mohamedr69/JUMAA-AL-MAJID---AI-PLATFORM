import { useCallback, useEffect, useState } from "react";
import { ApiError, api, apiUrl } from "../lib/api";
import type { AiBudget, ExtractionIssue, ExtractionRun, ExtractionState } from "../lib/types";

/** What a Design Sheet read could not settle, for the engineer to decide.
 *
 * A row whose quantity the OCR could not parse is shown with the cell's own
 * image and, when AI assistance is on, the reading the model proposed and
 * whether an independent OCR pass agreed with it. Nothing here is applied
 * until Accept; a pending proposal changes nothing in the BOQ. */
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
  const [busy, setBusy] = useState<number | "assist" | null>(null);
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
  const eligible = pending.some((p) => p.issue.state === "open" && p.issue.llm_eligible);
  const canAssist = state.ai_ready && eligible;
  // On but unusable -- no credential on the server -- is worth saying once,
  // where the button would have been.
  const aiProblem = state.ai_enabled && !state.ai_ready && eligible ? state.ai_status : null;

  if (pending.length === 0 && partial.length === 0) return null;

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

  return (
    <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-navy-900">
            {pending.length === 0
              ? "Some pages could not be read"
              : `${pending.length} row${pending.length === 1 ? "" : "s"} need${pending.length === 1 ? "s" : ""} review`}
          </div>
          <div className="text-xs text-amber-900">
            {pending.length > 0 && "The Design Sheet read these rows but could not settle their quantity; they are not in the BOQ until you accept one."}
            {partial.map((run) => (
              <div key={run.id}>
                {run.document_name}: page{run.unprocessed_pages.length === 1 ? "" : "s"} {run.unprocessed_pages.join(", ")} had no
                recognisable table and {run.unprocessed_pages.length === 1 ? "was" : "were"} not read.
              </div>
            ))}
            {starved.map((run) => (
              <div key={run.id}>
                {run.document_name}: AI assistance stopped at the {run.budget_exhausted?.replace(/_/g, " ")} limit; the rest is unreviewed, not resolved.
              </div>
            ))}
          </div>
        </div>
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
          <div className="max-w-xs rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs text-amber-800">
            {aiProblem}
          </div>
        )}
      </div>

      {error && <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}

      {pending.length > 0 && (
        <ul className="mt-3 space-y-2">
          {pending.map(({ run, issue }) => (
            <IssueRow
              key={issue.id}
              run={run}
              issue={issue}
              projectId={projectId}
              canEdit={canEdit}
              busy={busy === issue.id}
              onDecide={decide}
            />
          ))}
        </ul>
      )}
    </div>
  );
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
  const proposal = [...issue.proposals].reverse().find((p) => p.value !== null) ?? null;
  const [value, setValue] = useState(proposal?.value ?? "");
  const detail = issue.detail as { description?: string; catalog_no?: string | null; group_heading?: string | null; raw_quantity?: string | null };

  return (
    <li className="rounded-lg border border-gray-200 bg-white p-3 text-xs">
      <div className="flex flex-wrap items-start gap-3">
        {issue.has_evidence_image && (
          <img
            src={apiUrl(`/projects/${projectId}/extraction/issues/${issue.id}/evidence.png`)}
            alt="The quantity cell as scanned"
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
            {detail.raw_quantity ? ` · OCR read "${detail.raw_quantity}"` : " · OCR read nothing"}
          </div>
          {proposal && (
            <div className={`mt-1 ${proposal.state === "validated" ? "text-emerald-700" : "text-amber-800"}`}>
              AI suggested <span className="font-semibold">{proposal.value}</span>
              {proposal.state === "validated"
                ? " — an independent OCR pass agrees."
                : " — unconfirmed; check the cell image before accepting."}
              {proposal.from_cache && " (from an earlier identical read)"}
              {proposal.injection_flags && proposal.injection_flags.length > 0 && (
                <div className="mt-0.5 text-red-700">
                  The sheet's text around this row contains wording aimed at instructing a model, so this reading was not
                  confirmed automatically. Check the cell image yourself.
                </div>
              )}
            </div>
          )}
          {!proposal && issue.state === "starved" && <div className="mt-1 text-amber-800">{issue.state_reason}</div>}
          {!proposal && issue.state === "open" && issue.proposals.length > 0 && (
            <div className="mt-1 text-gray-500">AI could not read it: {issue.proposals[issue.proposals.length - 1].state_reason}</div>
          )}
        </div>
        {canEdit && (
          <div className="flex items-center gap-1">
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
              className="rounded-lg bg-brand-600 px-2.5 py-1 font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
            >
              Add line
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
