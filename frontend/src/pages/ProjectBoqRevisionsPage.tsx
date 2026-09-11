import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ApiError, api } from "../lib/api";
import {
  PROJECT_EDITOR_ROLES,
  type BoqChange,
  type BoqCompare,
  type BoqRevisionSummary,
  type ProjectBoqItemInput,
} from "../lib/types";
import { useProject } from "./ProjectWorkspace";

/** The API sends naive UTC; without a zone the browser would read it as
 * local time. */
function formatIssued(value: string): string {
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const FIELD_LABELS: Partial<Record<keyof ProjectBoqItemInput, string>> = {
  group_heading: "Group",
  catalog_no: "Part no.",
  description: "Description",
  manufacturer: "Manufacturer",
  quantity: "Qty",
  unit: "Unit",
  unit_price: "Unit price",
  total_price: "Total price",
  remarks: "Remarks",
};

export function ProjectBoqRevisionsPage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);

  const [revisions, setRevisions] = useState<BoqRevisionSummary[] | null>(null);
  const [pending, setPending] = useState<BoqCompare | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [issuing, setIssuing] = useState(false);
  const [openCompare, setOpenCompare] = useState<{ number: number; result: BoqCompare } | null>(null);

  const fetchRevisions = useCallback(async () => {
    const list = await api.get<BoqRevisionSummary[]>(`/projects/${project.id}/boq/revisions`);
    const latest = list[0];
    // What the next revision would contain that the last one did not.
    const since = latest
      ? await api.get<BoqCompare>(`/projects/${project.id}/boq/compare?from_rev=${latest.number}`)
      : null;
    return { list, since };
  }, [project.id]);

  useEffect(() => {
    let cancelled = false;
    fetchRevisions()
      .then(({ list, since }) => {
        if (cancelled) return;
        setRevisions(list);
        setPending(since);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load the revisions");
      });
    return () => {
      cancelled = true;
    };
  }, [fetchRevisions]);

  async function issue(e: FormEvent) {
    e.preventDefault();
    setIssuing(true);
    setError(null);
    try {
      await api.post<BoqRevisionSummary>(`/projects/${project.id}/boq/revisions`, { note });
      setNote("");
      setOpenCompare(null);
      const { list, since } = await fetchRevisions();
      setRevisions(list);
      setPending(since);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to issue the revision");
    } finally {
      setIssuing(false);
    }
  }

  async function toggleCompare(revision: BoqRevisionSummary) {
    if (openCompare?.number === revision.number) {
      setOpenCompare(null);
      return;
    }
    try {
      const result = await api.get<BoqCompare>(
        `/projects/${project.id}/boq/compare?from_rev=${revision.number - 1}&to_rev=${revision.number}`
      );
      setOpenCompare({ number: revision.number, result });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to compare the revisions");
    }
  }

  async function exportRevision(revision: BoqRevisionSummary) {
    try {
      await api.download(
        `/projects/${project.id}/boq/revisions/${revision.number}/export.xlsx`,
        `EP-${project.ep_number} BOQ ${revision.label}.xlsx`
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to export the revision");
    }
  }

  const latest = revisions?.[0];
  const nextLabel = `Rev ${String((latest?.number ?? -1) + 1).padStart(2, "0")}`;
  const unchanged = latest !== undefined && pending !== null && pending.changes.length === 0;

  return (
    <div className="max-w-5xl">
      <Link to=".." relative="path" className="text-sm font-medium text-gray-500 hover:text-brand-600">
        &larr; Bill of Quantities
      </Link>
      <h1 className="mt-2 text-2xl font-bold text-navy-900">BOQ Revisions</h1>
      <p className="mt-1 text-sm text-gray-500">
        Issuing a revision freezes the saved BOQ as it stands. Later edits never change an issued revision.
      </p>

      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

      {revisions === null ? (
        <div className="mt-6 text-sm text-gray-400">Loading...</div>
      ) : (
        <>
          <section className="mt-6 rounded-xl border border-gray-200 bg-white p-5">
            <h2 className="text-sm font-semibold text-navy-900">
              {latest ? `Changes since ${latest.label}` : "Not issued yet"}
            </h2>
            {latest && pending && (
              <div className="mt-3">
                {unchanged ? (
                  <p className="text-sm text-gray-500">The saved BOQ is the same as {latest.label}.</p>
                ) : (
                  <ChangeList changes={pending.changes} />
                )}
              </div>
            )}
            {!latest && (
              <p className="mt-1 text-sm text-gray-500">
                The first issue of the BOQ becomes Rev 00. Save any edits on the BOQ page first -- only the
                saved BOQ is issued.
              </p>
            )}

            {canEdit && (
              <form onSubmit={issue} className="mt-4 flex flex-wrap items-end gap-2 border-t border-gray-100 pt-4">
                <label className="min-w-64 flex-1 text-sm font-medium text-gray-700">
                  Note for {nextLabel}
                  <input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="e.g. Issued for approval, updated to IFC drawings"
                    className="input mt-1"
                  />
                </label>
                <button
                  type="submit"
                  disabled={issuing || unchanged}
                  className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
                >
                  {issuing ? "Issuing..." : `Issue ${nextLabel}`}
                </button>
              </form>
            )}
          </section>

          <section className="mt-6">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-400">Issued</h2>
            {revisions.length === 0 ? (
              <p className="mt-2 text-sm text-gray-400">No revisions have been issued.</p>
            ) : (
              <div className="mt-2 overflow-x-auto rounded-xl border border-gray-200 bg-white">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                    <tr>
                      <th className="px-3 py-2 font-medium">Revision</th>
                      <th className="px-3 py-2 font-medium">Issued</th>
                      <th className="px-3 py-2 font-medium">By</th>
                      <th className="px-3 py-2 text-right font-medium">Lines</th>
                      <th className="px-3 py-2 font-medium">Note</th>
                      <th className="px-3 py-2" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {revisions.map((revision) => (
                      <RevisionRow
                        key={revision.number}
                        revision={revision}
                        compare={openCompare?.number === revision.number ? openCompare.result : null}
                        onCompare={() => toggleCompare(revision)}
                        onExport={() => exportRevision(revision)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function RevisionRow({
  revision,
  compare,
  onCompare,
  onExport,
}: {
  revision: BoqRevisionSummary;
  compare: BoqCompare | null;
  onCompare: () => void;
  onExport: () => void;
}) {
  return (
    <>
      <tr>
        <td className="px-3 py-2 font-semibold text-navy-900">{revision.label}</td>
        <td className="px-3 py-2 text-gray-600">{formatIssued(revision.issued_at)}</td>
        <td className="px-3 py-2 text-gray-600">{revision.issued_by_name}</td>
        <td className="px-3 py-2 text-right tabular-nums text-gray-600">{revision.line_count}</td>
        <td className="px-3 py-2 text-gray-600">{revision.note ?? "—"}</td>
        <td className="whitespace-nowrap px-3 py-2 text-right">
          {revision.number > 0 && (
            <button onClick={onCompare} className="mr-3 text-xs font-medium text-brand-600 hover:text-brand-700">
              {compare ? "Hide changes" : "Changes"}
            </button>
          )}
          <button onClick={onExport} className="text-xs font-medium text-brand-600 hover:text-brand-700">
            Export Excel
          </button>
        </td>
      </tr>
      {compare && (
        <tr>
          <td colSpan={6} className="bg-gray-50/60 px-3 py-3">
            <div className="mb-2 text-xs text-gray-500">
              {compare.from_label} &rarr; {compare.to_label}
            </div>
            <ChangeList changes={compare.changes} />
          </td>
        </tr>
      )}
    </>
  );
}

const KIND_STYLE: Record<BoqChange["kind"], { label: string; classes: string }> = {
  added: { label: "Added", classes: "bg-green-50 text-green-700" },
  removed: { label: "Removed", classes: "bg-red-50 text-red-700" },
  changed: { label: "Changed", classes: "bg-amber-50 text-amber-800" },
};

function ChangeList({ changes }: { changes: BoqChange[] }) {
  if (changes.length === 0) return <p className="text-sm text-gray-500">No differences.</p>;

  const counts = (["added", "removed", "changed"] as const)
    .map((kind) => [kind, changes.filter((c) => c.kind === kind).length] as const)
    .filter(([, count]) => count > 0);

  return (
    <div>
      <div className="flex flex-wrap gap-2 text-xs">
        {counts.map(([kind, count]) => (
          <span key={kind} className={`rounded-full px-2 py-0.5 font-medium ${KIND_STYLE[kind].classes}`}>
            {count} {KIND_STYLE[kind].label.toLowerCase()}
          </span>
        ))}
      </div>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-gray-400">
            <tr>
              <th className="w-24 py-1 pr-3 font-medium" />
              <th className="w-16 py-1 pr-3 font-medium">System</th>
              <th className="w-40 py-1 pr-3 font-medium">Part No.</th>
              <th className="py-1 pr-3 font-medium">Description</th>
              <th className="py-1 font-medium">Detail</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {changes.map((change, i) => {
              const line = change.after ?? change.before!;
              return (
                <tr key={i} className={change.kind === "removed" ? "text-gray-400" : "text-navy-900"}>
                  <td className="py-1 pr-3">
                    <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${KIND_STYLE[change.kind].classes}`}>
                      {KIND_STYLE[change.kind].label}
                    </span>
                  </td>
                  <td className="py-1 pr-3">{line.system_code ?? "—"}</td>
                  <td className="py-1 pr-3">{line.catalog_no ?? "—"}</td>
                  <td className={`py-1 pr-3 ${change.kind === "removed" ? "line-through" : ""}`}>
                    {line.description}
                  </td>
                  <td className="py-1 text-xs text-gray-600">
                    {change.kind === "changed"
                      ? change.fields.map((name) => (
                          <div key={name}>
                            {FIELD_LABELS[name] ?? name}: {change.before?.[name] ?? "—"} &rarr;{" "}
                            <strong>{change.after?.[name] ?? "—"}</strong>
                          </div>
                        ))
                      : `Qty ${line.quantity ?? "—"}`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
