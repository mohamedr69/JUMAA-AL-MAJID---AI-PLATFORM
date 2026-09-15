import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { ApiError, api } from "../lib/api";
import { formatApiDate } from "../lib/format";
import { PROJECT_EDITOR_ROLES, type DocumentIntake, type IntakeFinding } from "../lib/types";
import { useJob } from "../lib/useJob";
import { JobProgress } from "./JobProgress";
import { StatusPill } from "./ReadinessPanel";

const MANAGER_ONLY = new Set([
  "EP_MISMATCH_FILENAME",
  "OUTSIDE_SOURCE_FOLDER",
  "SOURCE_FOLDER_EP_MISMATCH",
  "SAME_CONTENT_ON_ANOTHER_PROJECT",
  "INCOMPLETE_SOURCE",
  "OUTSIDE_ARCHIVE",
]);
const NOT_ACCEPTABLE = new Set(["MISSING_FILE", "EMPTY_FILE", "UNREADABLE", "UNSUPPORTED_FORMAT", "CONTENT_TYPE_MISMATCH"]);

/** What the intake gate found about each document: whether it is this
 * project's, whole, readable and attached once. A blocked finding stops the
 * BOQ being issued until it is fixed or accepted with a reason. */
export function DocumentIntakePanel({ projectId, refreshKey }: { projectId: number; refreshKey: unknown }) {
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);
  const isManager = user?.role === "admin" || user?.role === "design_manager";
  const [rows, setRows] = useState<DocumentIntake[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .get<DocumentIntake[]>(`/projects/${projectId}/documents/intake`)
      .then(setRows)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not load the document check"));
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  // Scanned pages are OCR'd for their printed numbers, which takes a while:
  // the check runs as a server job the panel follows and can stop.
  const intake = useJob(projectId, "documents_intake", `/projects/${projectId}/jobs/documents-intake`, (job) => {
    if (job.status === "failed") setError(job.error ?? "The documents could not be checked");
    load();
  });
  const checking = intake.active;

  async function check() {
    setError(null);
    await intake.start();
  }

  async function accept(row: DocumentIntake, finding: IntakeFinding, reason: string) {
    setError(null);
    try {
      const updated = await api.post<DocumentIntake>(`/projects/${projectId}/documents/intake/${row.id}/acknowledge`, {
        code: finding.code,
        reason,
      });
      setRows((prev) => (prev ?? []).map((r) => (r.id === updated.id ? updated : r)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The finding could not be accepted");
    }
  }

  const lastChecked = (rows ?? []).map((r) => r.checked_at).filter(Boolean).sort().pop();

  return (
    <section aria-labelledby="intake-title" className="mt-6 rounded-xl border border-gray-200 bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="intake-title" className="text-xs font-semibold uppercase tracking-wide text-gray-400">
            Document check
          </h2>
          <p className="mt-1 text-xs text-gray-500">
            Present and readable, every page there, named for this project, inside its folder, attached once.
            {lastChecked ? ` Last checked ${formatApiDate(lastChecked, "short")}.` : " Not checked yet."}
          </p>
        </div>
        {canEdit && (
          <button
            onClick={check}
            disabled={checking}
            className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
          >
            {checking ? "Checking..." : rows && rows.length > 0 ? "Check again" : "Check the documents"}
          </button>
        )}
      </div>
      {intake.job && intake.active && <JobProgress job={intake.job} what="the document check" onCancel={intake.cancel} />}
      {intake.error && <p className="mt-2 text-xs text-red-700">{intake.error}</p>}
      {error && (
        <div role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </div>
      )}

      {rows && rows.length > 0 && (
        <ul className="mt-3 divide-y divide-gray-100">
          {rows.map((row) => {
            const acked = new Map(row.acknowledged.map((a) => [a.code, a]));
            return (
              <li key={row.id} className="py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill status={row.intake_status === "unchecked" ? "unknown" : row.intake_status} />
                  <span className="text-sm font-medium text-navy-900">
                    {row.role === "drf" ? "DRF" : `Design Sheet ${row.system_code ?? "(no system)"}`}
                  </span>
                  <span className="break-all text-xs text-gray-500" title={row.path ?? row.relative_path ?? row.filename}>
                    {row.relative_path ?? row.filename}
                  </span>
                </div>
                <div className="mt-1 text-[11px] text-gray-400">
                  {row.page_count !== null && `${row.page_count} page${row.page_count === 1 ? "" : "s"}`}
                  {row.printed_pages?.declared_totals?.length ? ` · printed "of ${row.printed_pages.declared_totals.join(", ")}"` : ""}
                  {row.sha256 && ` · content ${row.sha256.slice(0, 12)}…`}
                </div>
                {row.findings.length === 0 ? (
                  <p className="mt-1 text-xs text-emerald-700">Nothing found.</p>
                ) : (
                  <ul className="mt-2 space-y-2">
                    {row.findings.map((finding) => (
                      <FindingRow
                        key={finding.code}
                        finding={finding}
                        accepted={acked.get(finding.code)}
                        canAccept={
                          canEdit &&
                          finding.severity === "blocked" &&
                          !NOT_ACCEPTABLE.has(finding.code) &&
                          (!MANAGER_ONLY.has(finding.code) || isManager)
                        }
                        needsManager={canEdit && finding.severity === "blocked" && MANAGER_ONLY.has(finding.code) && !isManager}
                        onAccept={(reason) => accept(row, finding, reason)}
                      />
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function FindingRow({
  finding,
  accepted,
  canAccept,
  needsManager,
  onAccept,
}: {
  finding: IntakeFinding;
  accepted?: { reason: string; by_name: string; at: string };
  canAccept: boolean;
  needsManager: boolean;
  onAccept: (reason: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const tone = accepted
    ? "border-gray-200 bg-gray-50 text-gray-700"
    : finding.severity === "blocked"
      ? "border-red-200 bg-red-50 text-red-800"
      : "border-amber-200 bg-amber-50 text-amber-900";

  return (
    <li className={`rounded-lg border px-3 py-2 text-xs ${tone}`}>
      <div className="font-semibold">
        {finding.severity === "blocked" && !accepted ? "Blocked: " : accepted ? "Accepted: " : "Check: "}
        {finding.message}
      </div>
      {accepted && (
        <div className="mt-0.5">
          Accepted by {accepted.by_name} on {formatApiDate(accepted.at, "short")}: “{accepted.reason}”
        </div>
      )}
      {!accepted && needsManager && <div className="mt-0.5">A design manager or admin can accept this with a reason.</div>}
      {!accepted && canAccept && !open && (
        <button onClick={() => setOpen(true)} className="mt-1 font-semibold underline">
          Accept with a reason
        </button>
      )}
      {!accepted && canAccept && open && (
        <form
          className="mt-2 flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            onAccept(reason.trim());
          }}
        >
          <label className="min-w-64 flex-1 text-gray-700">
            Why this is acceptable
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              minLength={10}
              maxLength={500}
              required
              className="input mt-1 py-1 text-xs"
              placeholder="e.g. The file is misnamed; it is this project's sheet"
            />
          </label>
          <button type="submit" disabled={reason.trim().length < 10} className="rounded-lg bg-navy-900 px-3 py-1.5 font-semibold text-white disabled:opacity-50">
            Accept
          </button>
          <button type="button" onClick={() => setOpen(false)} className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 font-semibold text-gray-700">
            Cancel
          </button>
        </form>
      )}
    </li>
  );
}
