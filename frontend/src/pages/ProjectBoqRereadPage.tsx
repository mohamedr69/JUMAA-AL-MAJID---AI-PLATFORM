import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ApiError, api, apiUrl } from "../lib/api";
import { formatApiDate } from "../lib/format";
import {
  PROJECT_EDITOR_ROLES,
  type BoqCandidate,
  type BoqCandidateChange,
  type BoqCandidateSummary,
  type BoqLineRecord,
  type BoqSnapshotSummary,
} from "../lib/types";
import { useJob } from "../lib/useJob";
import { useUnsavedChanges } from "../lib/useUnsavedChanges";
import { JobProgress } from "../components/JobProgress";
import { useProject } from "./ProjectWorkspace";

type Decision = "accept" | "keep";
type KindFilter = "all" | "changed" | "probable" | "added" | "removed" | "unchanged" | "undecided";

const FIELDS: { key: keyof BoqLineRecord; label: string }[] = [
  { key: "system_code", label: "System" },
  { key: "group_heading", label: "Group" },
  { key: "catalog_no", label: "Part No." },
  { key: "description", label: "Description" },
  { key: "quantity", label: "Qty" },
];

const KIND_LABEL: Record<BoqCandidateChange["kind"], string> = {
  changed: "Changed",
  added: "New on the sheet",
  removed: "Not on the sheet",
  unchanged: "Unchanged",
};

/** Read the Design Sheets again, without touching the BOQ, and decide every
 * difference: take the sheet's value, or keep the BOQ's. Nothing is written
 * until Apply, and the BOQ as it was is kept as a snapshot. */
export function ProjectBoqRereadPage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);

  const [candidate, setCandidate] = useState<BoqCandidate | null>(null);
  const [history, setHistory] = useState<BoqCandidateSummary[]>([]);
  const [snapshots, setSnapshots] = useState<BoqSnapshotSummary[]>([]);
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [loading, setLoading] = useState(true);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [filter, setFilter] = useState<KindFilter>("all");

  const pending = candidate?.status === "pending";
  const decidedCount = Object.keys(decisions).length;
  useUnsavedChanges(Boolean(pending && decidedCount > 0 && !applying), "Your decisions on this re-read are not applied yet. Leave anyway?");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, shots] = await Promise.all([
        api.get<BoqCandidateSummary[]>(`/projects/${project.id}/boq/candidates`),
        api.get<BoqSnapshotSummary[]>(`/projects/${project.id}/boq/snapshots`),
      ]);
      setHistory(list);
      setSnapshots(shots);
      const open = list.find((c) => c.status === "pending");
      setCandidate(open ? await api.get<BoqCandidate>(`/projects/${project.id}/boq/candidates/${open.id}`) : null);
      setDecisions({});
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the re-reads");
    } finally {
      setLoading(false);
    }
  }, [project.id]);

  useEffect(() => {
    void load();
  }, [load]);

  // Reading scanned sheets takes minutes: it runs as a server job the page
  // follows, can stop, and finds again after a refresh.
  const reread = useJob(project.id, "boq_reread", `/projects/${project.id}/jobs/boq-reread`, (job) => {
    if (job.status === "succeeded") {
      setNotice(null);
      void load();
    } else if (job.status === "failed") {
      setError(job.error ?? "The sheets could not be read");
    } else if (job.status === "cancelled") {
      setNotice("The re-read was stopped. Nothing was recorded and the BOQ is unchanged.");
    }
  });
  const building = reread.active;

  async function build() {
    setError(null);
    setNotice(null);
    await reread.start();
  }

  async function apply() {
    if (!candidate) return;
    setApplying(true);
    setError(null);
    try {
      const result = await api.post<{ applied: Record<string, number>; boq_version: number }>(
        `/projects/${project.id}/boq/candidates/${candidate.id}/apply`,
        { decisions }
      );
      const a = result.applied;
      setNotice(
        `Applied: ${a.changed} changed, ${a.added} added, ${a.removed} removed, ${a.kept} kept as they were` +
          (a.sources_updated ? `, ${a.sources_updated} source records updated` : "") +
          ". The BOQ as it was is kept below as a snapshot."
      );
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.isStaleWrite) {
        setError(`${err.message}`);
      } else {
        setError(err instanceof ApiError ? err.message : "The re-read could not be applied");
      }
    } finally {
      setApplying(false);
    }
  }

  async function discard() {
    if (!candidate || !window.confirm("Discard this re-read? The BOQ stays exactly as it is.")) return;
    try {
      await api.post(`/projects/${project.id}/boq/candidates/${candidate.id}/discard`);
      setNotice("Re-read discarded. The BOQ was not changed.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not discard the re-read");
    }
  }

  async function restore(shot: BoqSnapshotSummary) {
    if (
      !window.confirm(
        `Put the BOQ back as it was (${shot.lines} lines, ${shot.reason})? The BOQ as it is now is kept as another snapshot first.`
      )
    ) {
      return;
    }
    try {
      await api.post(`/projects/${project.id}/boq/snapshots/${shot.id}/restore`);
      setNotice(`The BOQ was restored from snapshot #${shot.id}.`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not restore the snapshot");
    }
  }

  const decidable = useMemo(() => (candidate?.changes ?? []).filter((c) => c.kind !== "unchanged"), [candidate]);
  const visible = (candidate?.changes ?? []).filter((c) => {
    switch (filter) {
      case "all":
        return c.kind !== "unchanged";
      case "probable":
        return c.match === "probable";
      case "undecided":
        return c.kind !== "unchanged" && !decisions[c.id];
      default:
        return c.kind === filter;
    }
  });

  function setAll(predicate: (c: BoqCandidateChange) => boolean, decision: Decision) {
    setDecisions((prev) => {
      const next = { ...prev };
      decidable.filter(predicate).forEach((c) => {
        next[c.id] = decision;
      });
      return next;
    });
  }

  const s = candidate?.summary;

  return (
    <div>
      <div className="text-xs text-gray-400">
        EP-{project.ep_number} / <Link to=".." relative="path" className="hover:underline">BOQ</Link> / Re-read
      </div>
      <h1 className="text-3xl font-bold text-navy-900">Re-read the Design Sheets</h1>
      <p className="mt-1 max-w-3xl text-sm text-gray-500">
        Reads every Design Sheet again with the current reader and compares the result with the BOQ, line by line. The BOQ does not
        change until you decide each difference and apply; the BOQ as it was is kept as a snapshot you can restore.
      </p>

      {notice && (
        <div role="status" className="mt-4 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          {notice}
        </div>
      )}
      {error && (
        <div role="alert" className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      {loading ? (
        <div className="mt-6 text-sm text-gray-500">Loading...</div>
      ) : !candidate ? (
        <div className="mt-6 rounded-xl border border-gray-200 bg-white p-6">
          <div className="text-sm text-navy-900">
            {project.design_sheets.length === 0
              ? "The project has no Design Sheets. Attach them under Documents first."
              : `${project.design_sheets.length} Design Sheet${project.design_sheets.length > 1 ? "s" : ""} will be read: ${project.design_sheets
                  .map((sheet) => sheet.system_code ?? "no system")
                  .join(", ")}.`}
          </div>
          {canEdit && project.design_sheets.length > 0 && (
            <button
              onClick={build}
              disabled={building}
              className="mt-4 rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
            >
              {building ? "Reading the sheets..." : "Read the sheets again"}
            </button>
          )}
          {reread.job && (reread.active || reread.job.status === "failed") && (
            <JobProgress job={reread.job} what="the re-read" onCancel={reread.cancel} />
          )}
          {reread.error && <p className="mt-2 text-xs text-red-700">{reread.error}</p>}
          {building && (
            <p className="mt-2 text-xs text-gray-500">
              Scanned sheets are read page by page. You can leave this page; the read carries on and is here when you come back.
            </p>
          )}
        </div>
      ) : (
        <>
          <section className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Figure label="Lines" value={`${s!.old_lines} → ${s!.new_lines}`} note="in the BOQ → read now" />
            <Figure label="Total quantity" value={`${s!.old_quantity.toLocaleString()} → ${s!.new_quantity.toLocaleString()}`} />
            <Figure
              label="Differences"
              value={String(candidate.decisions_needed)}
              note={`${s!.changed} changed (${s!.probable} uncertain) · ${s!.added} new · ${s!.removed} not on the sheet`}
            />
            <Figure label="Unchanged" value={String(s!.unchanged)} note={`read ${formatApiDate(candidate.created_at, "short")}`} />
          </section>

          <section className="mt-3 rounded-xl border border-gray-200 bg-white p-4 text-xs">
            <h2 className="font-semibold text-navy-900">What was read</h2>
            <ul className="mt-2 space-y-1">
              {s!.sheets.map((sheet) => (
                <li key={sheet.run_id} className={sheet.failure || sheet.unprocessed_pages.length ? "text-amber-800" : "text-gray-600"}>
                  <span className="font-medium">{sheet.document_name}</span> ({sheet.system_code ?? "no system"}): {sheet.lines} lines ·{" "}
                  {sheet.outcome.replace(/_/g, " ").toLowerCase()}
                  {sheet.failure && ` · could not be read: ${sheet.failure}`}
                  {sheet.unprocessed_pages.length > 0 && ` · page ${sheet.unprocessed_pages.join(", ")} not read`}
                  {sheet.open_issues > 0 && ` · ${sheet.open_issues} rows need review on the BOQ page before they can be added`}
                </li>
              ))}
            </ul>
            <div className="mt-2 text-gray-400">Reader version {candidate.parser_version}</div>
          </section>

          {candidate.stale && (
            <div role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
              The BOQ was saved after this re-read was made, so this comparison is out of date and cannot be applied.
              {canEdit && (
                <button onClick={build} disabled={building} className="ml-2 font-semibold underline disabled:opacity-60">
                  {building ? "Reading..." : "Read the sheets again"}
                </button>
              )}
            </div>
          )}

          {decidable.length > 0 && (
            <div className="sticky top-0 z-10 mt-4 flex flex-wrap items-center gap-2 border-b border-gray-200 bg-gray-50/95 py-2 backdrop-blur">
              <label className="flex items-center gap-2 text-sm text-gray-600">
                Show
                <select value={filter} onChange={(e) => setFilter(e.target.value as KindFilter)} className="input w-auto py-1.5">
                  <option value="all">All differences ({decidable.length})</option>
                  <option value="undecided">Not decided yet ({decidable.length - decidedCount})</option>
                  <option value="probable">Uncertain pairings ({s!.probable})</option>
                  <option value="changed">Changed ({s!.changed})</option>
                  <option value="added">New on the sheet ({s!.added})</option>
                  <option value="removed">Not on the sheet ({s!.removed})</option>
                  <option value="unchanged">Unchanged ({s!.unchanged})</option>
                </select>
              </label>
              {canEdit && pending && !candidate.stale && (
                <>
                  <button
                    onClick={() => setAll((c) => c.kind === "changed" && c.match === "exact" && c.before?.status !== "corrected", "accept")}
                    className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-navy-900 hover:bg-gray-100"
                  >
                    Take exact changes to lines nobody corrected
                  </button>
                  <button
                    onClick={() => setAll((c) => c.kind === "added", "accept")}
                    className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-navy-900 hover:bg-gray-100"
                  >
                    Add all new lines
                  </button>
                  <button
                    onClick={() => setAll(() => true, "keep")}
                    className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-navy-900 hover:bg-gray-100"
                  >
                    Keep the BOQ for the rest
                  </button>
                </>
              )}
              <span className="ml-auto text-xs text-gray-600" aria-live="polite">
                {decidedCount} of {decidable.length} decided
              </span>
            </div>
          )}

          <ul className="mt-3 space-y-3">
            {visible.map((change) => (
              <ChangeCard
                key={change.id}
                projectId={project.id}
                candidateId={candidate.id}
                change={change}
                decision={decisions[change.id]}
                disabled={!canEdit || !pending || candidate.stale}
                onDecide={(decision) => setDecisions((prev) => ({ ...prev, [change.id]: decision }))}
              />
            ))}
            {visible.length === 0 && (
              <li className="rounded-xl border border-dashed border-gray-300 bg-white p-6 text-center text-sm text-gray-400">
                {decidable.length === 0 ? "The sheets read exactly as the BOQ holds them." : "Nothing matches this filter."}
              </li>
            )}
          </ul>

          {canEdit && pending && (
            <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
              <button
                onClick={discard}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50"
              >
                Discard this re-read
              </button>
              <button
                onClick={apply}
                disabled={applying || candidate.stale || decidedCount < decidable.length}
                title={decidedCount < decidable.length ? "Decide every difference first" : undefined}
                className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
              >
                {applying ? "Applying..." : decidable.length === 0 ? "Update source records" : `Apply ${decidedCount} decisions`}
              </button>
            </div>
          )}
        </>
      )}

      <section className="mt-8">
        <h2 className="text-sm font-semibold text-navy-900">Snapshots of the BOQ</h2>
        <p className="text-xs text-gray-500">The BOQ exactly as it was before a re-read or restore replaced part of it.</p>
        {snapshots.length === 0 ? (
          <p className="mt-2 text-xs text-gray-400">None yet.</p>
        ) : (
          <div className="mt-2 overflow-x-auto rounded-xl border border-gray-200 bg-white">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">Taken</th>
                  <th scope="col" className="px-3 py-2 font-medium">Why</th>
                  <th scope="col" className="px-3 py-2 font-medium">By</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Lines</th>
                  <th scope="col" className="px-3 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {snapshots.map((shot) => (
                  <tr key={shot.id}>
                    <td className="px-3 py-2 text-gray-600">{formatApiDate(shot.created_at, "short")}</td>
                    <td className="px-3 py-2 text-gray-600">{shot.reason}</td>
                    <td className="px-3 py-2 text-gray-600">{shot.created_by_name ?? "—"}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-gray-600">{shot.lines}</td>
                    <td className="px-3 py-2 text-right">
                      {canEdit && (
                        <button onClick={() => restore(shot)} className="text-xs font-semibold text-brand-700 hover:underline">
                          Restore
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {history.length > 1 && (
          <p className="mt-3 text-xs text-gray-400">
            Earlier re-reads:{" "}
            {history
              .filter((c) => c.id !== candidate?.id)
              .slice(0, 5)
              .map((c) => `#${c.id} ${c.status} ${formatApiDate(c.created_at, "short")}`)
              .join(" · ")}
          </p>
        )}
      </section>
    </div>
  );
}

function Figure({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white px-4 py-3">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-xl font-bold tabular-nums text-navy-900">{value}</div>
      {note && <div className="text-xs text-gray-500">{note}</div>}
    </div>
  );
}

function ChangeCard({
  projectId,
  candidateId,
  change,
  decision,
  disabled,
  onDecide,
}: {
  projectId: number;
  candidateId: number;
  change: BoqCandidateChange;
  decision: Decision | undefined;
  disabled: boolean;
  onDecide: (decision: Decision) => void;
}) {
  const [showImage, setShowImage] = useState(false);
  const engineerChanged = change.before?.status === "corrected" || change.before?.origin === "manual";
  const hasImage = Boolean((change.after ?? change.before)?.source_region);
  const takeLabel = change.kind === "removed" ? "Remove from the BOQ" : change.kind === "added" ? "Add to the BOQ" : "Take the sheet's value";
  const keepLabel = change.kind === "removed" ? "Keep the line" : change.kind === "added" ? "Do not add" : "Keep the BOQ's value";
  const name = `decision-${change.id}`;

  return (
    <li
      className={`rounded-xl border bg-white p-4 ${
        change.match === "probable" ? "border-amber-300" : decision ? "border-gray-200" : "border-gray-300"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-full bg-gray-100 px-2 py-0.5 font-semibold text-navy-900">{KIND_LABEL[change.kind]}</span>
        {change.match === "probable" && (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 font-semibold text-amber-900">Uncertain pairing — check it</span>
        )}
        {engineerChanged && (
          <span className="rounded-full bg-violet-100 px-2 py-0.5 font-semibold text-violet-900">
            {change.before?.origin === "manual" ? "Typed in by an engineer" : "Corrected by an engineer"}
          </span>
        )}
        <span className="text-gray-500">{change.reason}</span>
        {hasImage && (
          <button
            type="button"
            onClick={() => setShowImage((v) => !v)}
            aria-expanded={showImage}
            className="ml-auto font-semibold text-brand-700 hover:underline"
          >
            {showImage ? "Hide the sheet row" : "Show the sheet row"}
          </button>
        )}
      </div>

      {showImage && (
        <img
          src={apiUrl(`/projects/${projectId}/boq/candidates/${candidateId}/changes/${change.id}/evidence.png`)}
          alt={`The row on page ${(change.after ?? change.before)?.source_page} of the Design Sheet`}
          loading="lazy"
          className="mt-2 max-w-full rounded border border-gray-200"
        />
      )}

      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[640px] text-xs">
          <thead className="text-left text-gray-500">
            <tr>
              <th scope="col" className="w-24 py-1 pr-2 font-medium" />
              {FIELDS.map((field) => (
                <th key={field.key} scope="col" className="py-1 pr-2 font-medium">
                  {field.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {change.before && <LineRow label="BOQ now" line={change.before} fields={change.fields} />}
            {change.after && <LineRow label="Sheet read" line={change.after} fields={change.fields} highlight />}
          </tbody>
        </table>
      </div>
      {change.after?.raw_values?.quantity_parse && change.fields.includes("quantity") && (
        <div className="mt-1 text-[11px] text-gray-500">
          The sheet read gave the quantity as "{change.after.raw_values.quantity ?? ""}" → {change.after.raw_values.quantity_parse.value ?? "—"} (
          {change.after.raw_values.quantity_parse.rule})
        </div>
      )}

      {change.kind !== "unchanged" && (
        <fieldset className="mt-3 flex flex-wrap gap-4 text-sm" disabled={disabled}>
          <legend className="sr-only">Decision</legend>
          <label className="flex items-center gap-2">
            <input type="radio" name={name} checked={decision === "accept"} onChange={() => onDecide("accept")} />
            {takeLabel}
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" name={name} checked={decision === "keep"} onChange={() => onDecide("keep")} />
            {keepLabel}
          </label>
        </fieldset>
      )}
    </li>
  );
}

function LineRow({ label, line, fields, highlight }: { label: string; line: BoqLineRecord; fields: string[]; highlight?: boolean }) {
  return (
    <tr className="align-top">
      <th scope="row" className="py-1 pr-2 text-left font-semibold text-gray-600">
        {label}
        {line.source_page ? <div className="font-normal text-gray-400">page {line.source_page}</div> : null}
      </th>
      {FIELDS.map((field) => {
        const differs = fields.includes(field.key as string);
        return (
          <td
            key={field.key}
            className={`py-1 pr-2 ${differs ? (highlight ? "bg-emerald-50 font-semibold text-emerald-900" : "bg-red-50 text-red-900") : "text-gray-700"}`}
          >
            {(line[field.key] as string | null) ?? "—"}
          </td>
        );
      })}
    </tr>
  );
}
