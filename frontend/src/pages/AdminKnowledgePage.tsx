import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../lib/api";
import type { KnowledgeImport, KnowledgeImportReport, KnowledgeStatus } from "../lib/types";

/** The API sends naive UTC; without a zone the browser would read it as
 * local time. */
function formatWhen(value: string | null): string {
  if (!value) return "never";
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const ROLE_LABELS: Record<string, string> = {
  canonical: "Database workbook",
  duplicate_export: "Markdown exports of the same records",
  index: "Indexes",
  intermediate: "Extraction working files",
  documentation: "Documentation",
  agent: "Assistant configuration",
  other: "Other files",
};

/** The compliance knowledge base, for administrators: what it holds, when
 *  it was last refreshed, and the one action that refreshes it. */
export function AdminKnowledgePage() {
  const [status, setStatus] = useState<KnowledgeStatus | null>(null);
  const [report, setReport] = useState<KnowledgeImportReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await api.get<KnowledgeStatus>("/admin/knowledge");
      setStatus(next);
      if (next.last && !next.running) setReport(await api.get<KnowledgeImportReport>(`/admin/knowledge/imports/${next.last.id}`));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not read the knowledge base status");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!status?.running) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [status?.running, load]);

  async function update(force = false) {
    setBusy(true);
    setError(null);
    try {
      setStatus(await api.post<KnowledgeStatus>(`/admin/knowledge/import${force ? "?force=true" : ""}`));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start the update");
    } finally {
      setBusy(false);
    }
  }

  const records = status?.records;
  const last: KnowledgeImport | null = status?.last ?? null;

  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-navy-900">Compliance Knowledge Base</h1>
          <p className="mt-1 text-sm text-gray-500">
            The company's historical compliance responses, imported from the Compliance Response Database and queried by Auto-fill on
            every project. Updating reads the source again; it never calls the AI.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => update(false)}
            disabled={busy || !status || status.running || !status.source_configured}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {status?.running ? "Updating…" : "Update knowledge base"}
          </button>
          <button
            onClick={() => update(true)}
            disabled={busy || !status || status.running || !status.source_configured}
            title="Import the workbook again even if its content has not changed"
            className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Re-import
          </button>
        </div>
      </div>

      {error && <div className="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {status && !status.source_configured && (
        <div className="mb-4 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
          No knowledge source is configured on this server, so the knowledge base cannot be updated from here. What was imported before
          keeps working.
        </div>
      )}
      {status?.running && (
        <div className="mb-4 rounded-lg bg-blue-50 px-3 py-2 text-sm text-brand-700">
          Updating: {status.phase ?? "starting"}
          {status.detail ? ` — ${status.detail}` : ""}
        </div>
      )}

      {records && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {(
            [
              ["Source documents", records.sources],
              ["Requirements", records.requirements],
              ["Responses", records.responses],
              ["Eligible for auto-fill", records.eligible_responses],
            ] as [string, number][]
          ).map(([label, value]) => (
            <div key={label} className="rounded-xl border border-gray-200 bg-white px-4 py-3">
              <div className="text-2xl font-semibold text-navy-900">{value.toLocaleString()}</div>
              <div className="text-xs text-gray-500">{label}</div>
            </div>
          ))}
        </div>
      )}

      {status && (
        <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4 text-sm">
          <div className="flex flex-wrap justify-between gap-2">
            <div>
              <span className="font-semibold text-navy-900">Last successful refresh:</span> {formatWhen(status.last_refreshed_at)}
              {status.last_successful?.workbook_built && (
                <span className="text-gray-500"> · workbook built {status.last_successful.workbook_built.replace("Built ", "")}</span>
              )}
            </div>
            {status.last_successful?.source_label && <div className="text-gray-500">Source: {status.last_successful.source_label}</div>}
          </div>
          {Object.keys(status.by_system).length > 0 && (
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <div>
                <div className="text-xs font-semibold uppercase text-gray-500">By system</div>
                <ul className="mt-1 space-y-0.5 text-gray-700">
                  {Object.entries(status.by_system)
                    .sort((a, b) => b[1].responses - a[1].responses)
                    .map(([system, n]) => (
                      <li key={system}>
                        {system}: {n.responses.toLocaleString()} responses, {n.eligible.toLocaleString()} eligible
                      </li>
                    ))}
                </ul>
              </div>
              <div>
                <div className="text-xs font-semibold uppercase text-gray-500">By manufacturer</div>
                <ul className="mt-1 space-y-0.5 text-gray-700">
                  {Object.entries(status.manufacturers).map(([maker, n]) => (
                    <li key={maker}>
                      {maker}: {n.toLocaleString()}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </section>
      )}

      {last && (
        <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4 text-sm">
          <h2 className="font-semibold text-navy-900">
            Last update · {formatWhen(last.started_at)} ·{" "}
            <span
              className={
                last.status === "failed" ? "text-red-700" : last.status === "running" ? "text-brand-700" : "text-green-700"
              }
            >
              {last.status}
            </span>
          </h2>
          {last.error && <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-red-700">{last.error}</div>}
          <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-gray-700 sm:grid-cols-4">
            <div>Files discovered: {last.files_discovered}</div>
            <div>Imported: {last.files_imported}</div>
            <div>Unchanged: {last.files_unchanged}</div>
            <div>Skipped: {last.files_skipped}</div>
            <div>Failed: {last.files_failed}</div>
            <div>Records added: {last.records_added.toLocaleString()}</div>
            <div>Updated: {last.records_updated.toLocaleString()}</div>
            <div>Made inactive: {last.records_inactive.toLocaleString()}</div>
            <div>Flagged (not eligible): {last.records_flagged.toLocaleString()}</div>
          </div>
          {report && report.id === last.id && report.files.length > 0 && (
            <table className="mt-3 w-full text-xs">
              <thead className="text-left text-gray-500">
                <tr>
                  <th className="py-1 pr-3">Files</th>
                  <th className="py-1 pr-3">Count</th>
                  <th className="py-1 pr-3">Action</th>
                  <th className="py-1">Why</th>
                </tr>
              </thead>
              <tbody className="text-gray-700">
                {report.files.map((f) => (
                  <tr key={f.role} className="border-t border-gray-100">
                    <td className="py-1 pr-3">
                      {ROLE_LABELS[f.role] ?? f.role}
                      {f.examples.length > 0 && <span className="text-gray-400"> — e.g. {f.examples.join(", ")}</span>}
                    </td>
                    <td className="py-1 pr-3">{f.count}</td>
                    <td className="py-1 pr-3">{f.action}</td>
                    <td className="py-1">{f.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
    </div>
  );
}
