import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, apiUrl } from "../../lib/api";
import { useOnProjectChange } from "../../lib/projectChanges";
import { SyncDocumentsCard } from "../SyncDocumentsCard";
import { DrawingDetailPanel } from "./DrawingDetailPanel";
import { Chip, EyeIcon, FolderIcon, HintBadge, StatusIcon } from "./StatusChip";
import type { DrawingsLog, LogRow, Status, SystemSummary } from "./types";
import { STATUS_LABEL } from "./types";

const SUMMARY_ORDER: Status[] = ["approved", "approved_as_noted", "under_review", "not_approved", "not_submitted", "reply_not_found"];
const PAGE_SIZES = [12, 25, 50, 100];

/** The Drawings Log: every floor of the building, with the selected
 *  system's shop drawing at each official revision. A file found at a
 *  newer revision is a hint ("R1 available"), never a status. */
export function DrawingsLogTab({
  projectId,
  canEdit,
  system,
  summary,
  openDrawing,
  onOpenDrawing,
  onSystemsKnown,
  onChanged,
  onReview,
}: {
  projectId: number;
  canEdit: boolean;
  system: string;
  summary: SystemSummary | null;
  openDrawing: number | null;
  onOpenDrawing: (id: number | null) => void;
  onSystemsKnown: (codes: string[]) => void;
  onChanged: () => void;
  onReview: () => void;
}) {
  const [log, setLog] = useState<DrawingsLog | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [floor, setFloor] = useState("");
  const [status, setStatus] = useState<Status | "">("");
  const [query, setQuery] = useState("");
  const [issuesOnly, setIssuesOnly] = useState(false);
  const [showDetected, setShowDetected] = useState(true);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(12);

  const load = useCallback(() => {
    api
      .get<DrawingsLog>(`/projects/${projectId}/drawings/log?system=${encodeURIComponent(system)}`)
      .then((d) => {
        setLog(d);
        setError("");
        onSystemsKnown(d.systems);
      })
      .catch((e) => setError(`The drawings log could not be loaded: ${e.message}`));
  }, [projectId, system, onSystemsKnown]);

  useEffect(() => {
    load();
    setPage(1);
  }, [load]);
  // The records are the sync's and the engineer's: either changing reloads the log here.
  useOnProjectChange(["documents", "drawing"], load);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (log?.rows ?? []).filter(
      (r) =>
        (!floor || r.key === floor) &&
        (!status || r.latest_status === status) &&
        (!issuesOnly || r.hints.length > 0) &&
        (!q || r.floor.toLowerCase().includes(q) || (r.reference ?? "").toLowerCase().includes(q) || r.remarks.toLowerCase().includes(q)),
    );
  }, [log, floor, status, query, issuesOnly]);
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const shown = rows.slice((Math.min(page, pages) - 1) * pageSize, Math.min(page, pages) * pageSize);

  const openFolder = async (path: string | null) => {
    setNotice("");
    try {
      await api.post(`/projects/${projectId}/drawings/open-folder`, path ? { path, system } : { system });
    } catch (e) {
      setNotice((e as Error).message);
    }
  };

  const exportLog = () =>
    api
      .download(`/projects/${projectId}/drawings/log/export.xlsx?system=${encodeURIComponent(system)}`, `EP-${log?.project.ep_number ?? projectId} Drawings Log ${system}.xlsx`)
      .catch((e) => setError(e.message));

  const selected = openDrawing !== null ? (log?.rows.find((r) => r.id === openDrawing) ?? null) : null;

  return (
    <div className="space-y-4">
      <SyncDocumentsCard projectId={projectId} canEdit={canEdit} compact onSynced={() => { load(); onChanged(); }} />
      {error && <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm text-rose-800">{error}</div>}
      {notice && <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-900">{notice}</div>}
      {log?.warnings.map((w) => (
        <div key={w} className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-900">{w}</div>
      ))}

      {/* The system's numbers: each status at the latest revision. Click one to filter. */}
      {log && (
        <div className="rounded-xl border border-gray-200 bg-white px-5 py-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <div className="text-base font-bold text-navy-900">{log.system_name} ({log.system})</div>
              <div className="text-xs text-gray-500">Shop drawing status summary · {log.rows.length} floors</div>
            </div>
            <div className="text-xs text-gray-500">
              Floors from {log.ifc.length ? log.ifc.map((d) => `${d.filename} ${d.revision}`).join(", ") : "the shop drawings (no IFC drawing imported yet)"}
            </div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-7">
            <Tile label="Total Floors" value={log.rows.length} active={!status && !issuesOnly} onClick={() => { setStatus(""); setIssuesOnly(false); setPage(1); }} />
            {SUMMARY_ORDER.map((s) => (
              <Tile key={s} label={STATUS_LABEL[s]} value={log.counts[s] ?? 0} icon={<StatusIcon status={s} className="h-5 w-5" />} active={status === s} onClick={() => { setStatus(status === s ? "" : s); setIssuesOnly(false); setPage(1); }} />
            ))}
            <Tile label="Review Items" value={log.review_items} tone="rose" active={issuesOnly} onClick={onReview} />
          </div>
        </div>
      )}

      <div className="flex gap-4">
        <div className={`min-w-0 flex-1 rounded-xl border border-gray-200 bg-white ${selected ? "hidden xl:block" : ""}`}>
          <div className="flex flex-wrap items-end justify-between gap-3 border-b border-gray-100 p-5">
            <div>
              <h2 className="text-xl font-bold">Drawings Log</h2>
              <p className="mt-1 text-sm text-gray-500">Each floor with its shop drawing's revision status, the latest consultant response and detected new revisions.</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <select value={floor} onChange={(e) => { setFloor(e.target.value); setPage(1); }} className="rounded-lg border border-gray-300 px-3 py-2 text-sm" aria-label="Floor">
                <option value="">All Floors</option>
                {(log?.rows ?? []).map((r) => (
                  <option key={r.key} value={r.key}>{r.floor}</option>
                ))}
              </select>
              <select value={status} onChange={(e) => { setStatus(e.target.value as Status | ""); setPage(1); }} className="rounded-lg border border-gray-300 px-3 py-2 text-sm" aria-label="Status">
                <option value="">All Status</option>
                {SUMMARY_ORDER.map((s) => (
                  <option key={s} value={s}>{STATUS_LABEL[s]}{log?.counts[s] ? ` (${log.counts[s]})` : ""}</option>
                ))}
              </select>
              <input value={query} onChange={(e) => { setQuery(e.target.value); setPage(1); }} placeholder="Search floor or drawing reference…" className="w-60 rounded-lg border border-gray-300 px-3 py-2 text-sm" aria-label="Search floor or drawing reference" />
              {(floor || status || query || issuesOnly) && (
                <button type="button" onClick={() => { setFloor(""); setStatus(""); setQuery(""); setIssuesOnly(false); setPage(1); }} className="rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50">Clear</button>
              )}
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input type="checkbox" checked={issuesOnly} onChange={(e) => { setIssuesOnly(e.target.checked); setPage(1); }} className="h-4 w-4" /> Issues only
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700" title="Show files found at a newer revision than the official latest">
                <input type="checkbox" checked={showDetected} onChange={(e) => setShowDetected(e.target.checked)} className="h-4 w-4" /> Show detected revisions
              </label>
              <button type="button" onClick={exportLog} disabled={!log?.rows.length} className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-50">Export</button>
              <button type="button" onClick={() => openFolder(null)} disabled={!log?.folder} className="flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-50" title={log?.folder ?? "The project has no folder"}>
                <FolderIcon /> Folder
              </button>
            </div>
          </div>

          {log === null ? (
            <div className="p-6 text-sm text-gray-500">{error ? "" : "Loading the drawings log…"}</div>
          ) : log.rows.length === 0 ? (
            <div className="p-6 text-sm text-gray-600">
              No shop drawing of this system is in the project folder yet, and no floor is known. Sync the documents once one is filed; the floors
              still to draw show here once the IFC drawing is imported on{" "}
              <Link to={`/projects/${projectId}/boq`} className="font-medium text-brand-700 hover:underline">BOQ &gt; As per IFC Drawings</Link>.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="border-b border-gray-200 bg-gray-50 text-left text-sm font-semibold text-navy-900">
                  <tr>
                    <th className="px-3 py-3">#</th>
                    <th className="px-3 py-3">Floor</th>
                    <th className="px-3 py-3">Drawing Reference</th>
                    {log.revisions.map((r) => (
                      <th key={r} className="px-3 py-3 text-center">{r}</th>
                    ))}
                    <th className="px-3 py-3 text-center">Latest Rev</th>
                    <th className="px-3 py-3">Latest Status</th>
                    <th className="px-3 py-3">Issues / Hints</th>
                    <th className="px-3 py-3 text-center">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {shown.map((r) => (
                    <LogTableRow
                      key={r.key}
                      row={r}
                      index={log.rows.indexOf(r) + 1}
                      revisions={log.revisions}
                      showDetected={showDetected}
                      selected={selected?.key === r.key}
                      projectId={projectId}
                      onOpen={() => r.id !== null && onOpenDrawing(r.id)}
                      onFolder={() => openFolder(r.latest_path)}
                      folder={!!log.folder}
                    />
                  ))}
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={log.revisions.length + 7} className="px-4 py-6 text-center text-gray-500">No floor matches the filter.</td>
                    </tr>
                  )}
                </tbody>
              </table>
              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 px-5 py-3 text-sm text-gray-500">
                <span>Showing {shown.length ? (Math.min(page, pages) - 1) * pageSize + 1 : 0} to {(Math.min(page, pages) - 1) * pageSize + shown.length} of {rows.length} floors</span>
                <div className="flex items-center gap-2">
                  <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)} className="rounded-md border border-gray-300 px-2 py-1 disabled:opacity-40" aria-label="Previous page">‹</button>
                  {Array.from({ length: pages }, (_, i) => i + 1).slice(Math.max(0, Math.min(page, pages) - 3), Math.max(0, Math.min(page, pages) - 3) + 6).map((n) => (
                    <button key={n} type="button" onClick={() => setPage(n)} aria-current={n === Math.min(page, pages)} className={`rounded-md px-2.5 py-1 ${n === Math.min(page, pages) ? "bg-brand-600 text-white" : "border border-gray-300 hover:bg-gray-50"}`}>{n}</button>
                  ))}
                  <button type="button" disabled={page >= pages} onClick={() => setPage(page + 1)} className="rounded-md border border-gray-300 px-2 py-1 disabled:opacity-40" aria-label="Next page">›</button>
                  <span className="ml-3">Rows per page</span>
                  <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }} className="rounded-md border border-gray-300 px-2 py-1" aria-label="Rows per page">
                    {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                </div>
              </div>
            </div>
          )}
        </div>

        {selected && selected.id !== null && (
          <DrawingDetailPanel
            projectId={projectId}
            drawingId={selected.id}
            canEdit={canEdit}
            summary={summary}
            onClose={() => onOpenDrawing(null)}
            onChanged={() => { load(); onChanged(); }}
          />
        )}
      </div>
    </div>
  );
}

function Tile({ label, value, icon, active, onClick, tone }: { label: string; value: number; icon?: React.ReactNode; active?: boolean; onClick?: () => void; tone?: "rose" }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-left ${active ? "border-brand-500 bg-brand-50" : "border-gray-200 bg-white hover:bg-gray-50"}`}
    >
      {icon}
      <div className="min-w-0">
        <div className={`text-lg font-bold leading-tight ${tone === "rose" && value > 0 ? "text-rose-700" : "text-navy-900"}`}>{value}</div>
        <div className="truncate text-[11px] text-gray-500">{label}</div>
      </div>
    </button>
  );
}

function LogTableRow({ row, index, revisions, showDetected, selected, projectId, onOpen, onFolder, folder }: {
  row: LogRow; index: number; revisions: string[]; showDetected: boolean; selected: boolean; projectId: number;
  onOpen: () => void; onFolder: () => void; folder: boolean;
}) {
  const hints = showDetected ? row.hints : row.hints.filter((h) => h.kind !== "revision_candidate");
  return (
    <tr className={`${selected ? "bg-brand-50" : "hover:bg-gray-50"} ${row.id !== null ? "cursor-pointer" : ""}`} onClick={row.id !== null ? onOpen : undefined}>
      <td className="px-3 py-2.5 text-gray-500">{index}</td>
      <td className="px-3 py-2.5">
        <div className="font-medium text-navy-900">{row.floor}</div>
        {row.floor_secondary && <div className="text-xs text-gray-500">{row.floor_secondary}</div>}
        {row.floors > 1 && <div className="text-xs text-gray-500">{row.floors} floors</div>}
      </td>
      <td className="px-3 py-2.5 font-mono text-xs text-gray-700">
        {row.reference ?? <span className="font-sans text-gray-400">—</span>}
        {row.confirmed && <span className="ml-1 text-[10px] text-gray-400" title="Corrected by an engineer">✎</span>}
      </td>
      {revisions.map((rev) => {
        const cell = row.cells[rev];
        const beyond = row.latest_revision === null || Number(rev.slice(1)) > Number(row.latest_revision.slice(1));
        return (
          <td key={rev} className="px-2 py-2.5 text-center">
            <Chip cell={cell.candidate && !showDetected ? { ...cell, candidate: undefined } : cell} blank={beyond && !cell.candidate} />
          </td>
        );
      })}
      <td className="px-3 py-2.5 text-center font-medium">{row.latest_revision ?? "–"}</td>
      <td className="px-3 py-2.5"><Chip cell={{ status: row.latest_status, label: STATUS_LABEL[row.latest_status], path: row.latest_path }} blank={row.latest_revision === null} /></td>
      <td className="px-3 py-2.5">
        <div className="flex flex-wrap gap-1">
          {hints.length === 0 ? <span className="text-gray-300">—</span> : hints.slice(0, 3).map((h, i) => <HintBadge key={i} hint={h} />)}
          {hints.length > 3 && <span className="text-xs text-gray-500">+{hints.length - 3}</span>}
        </div>
      </td>
      <td className="px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-center gap-3 text-brand-600">
          {row.latest_path ? (
            <a href={apiUrl(`/projects/${projectId}/logs/file?path=${encodeURIComponent(row.latest_path)}#page=${row.latest_page}`)} target="_blank" rel="noreferrer" title={`View ${row.latest_revision}`} className="hover:text-brand-800"><EyeIcon /></a>
          ) : (
            <span className="text-gray-300" title="Nothing submitted yet"><EyeIcon /></span>
          )}
          <button type="button" onClick={onFolder} disabled={!folder} title={row.latest_path ? "Open the folder of the latest submission" : "Open the shop drawings folder"} className="hover:text-brand-800 disabled:text-gray-300"><FolderIcon /></button>
          {row.id !== null && (
            <button type="button" onClick={onOpen} className="rounded px-1.5 text-lg leading-none text-gray-500 hover:bg-gray-100" aria-label={`Details of ${row.reference}`}>⋯</button>
          )}
        </div>
      </td>
    </tr>
  );
}
