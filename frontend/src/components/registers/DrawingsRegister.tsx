import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, apiUrl } from "../../lib/api";
import { useOnProjectChange } from "../../lib/projectChanges";
import type { DrawingsRegister as RegisterOut, RegisterRow } from "../../lib/types";
import { DrawingDetailPanel } from "../drawings/DrawingDetailPanel";
import { Chip, EyeIcon, FolderIcon, HintBadge, StatusIcon } from "../drawings/StatusChip";
import { STATUS_LABEL, type Status } from "../drawings/types";

export const ALL_SYSTEMS = "ALL";
const SUMMARY_ORDER: Status[] = ["approved", "approved_as_noted", "under_review", "not_approved", "not_submitted", "reply_not_found"];
const PAGE_SIZES = [25, 50, 100];

/** Logs > Drawings: the consolidated register -- one row per logical shop
 *  drawing (and per floor still to draw), across every drawn system or
 *  one. The rows are the Drawings page's own records, read through one
 *  compact endpoint: this page works out no status of its own. Row details,
 *  candidate decisions and folder opening call the Drawings page's endpoints. */
export function DrawingsRegister({ projectId, epNumber, canEdit, system, systemName }: {
  projectId: number; epNumber: string; canEdit: boolean;
  /** "ALL" or a system code the project draws. */
  system: string; systemName: string;
}) {
  const [data, setData] = useState<RegisterOut | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [floor, setFloor] = useState("");
  const [status, setStatus] = useState<Status | "">("");
  const [revision, setRevision] = useState("");
  const [issuesOnly, setIssuesOnly] = useState(false);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [open, setOpen] = useState<number | null>(null);

  const load = useCallback(() => {
    const params = new URLSearchParams({ system, page: String(page), page_size: String(pageSize) });
    if (floor) params.set("floor", floor);
    if (status) params.set("status", status);
    if (revision) params.set("revision", revision);
    if (issuesOnly) params.set("issues_only", "true");
    if (search.trim()) params.set("search", search.trim());
    api
      .get<RegisterOut>(`/projects/${projectId}/logs/drawings?${params}`)
      .then((d) => { setData(d); setError(""); })
      .catch((e) => setError(`The drawings register could not be loaded: ${e.message}`));
  }, [projectId, system, page, pageSize, floor, status, revision, issuesOnly, search]);

  useEffect(() => { load(); }, [load]);
  // The records are the sync's and the engineer's: either changing reloads the register.
  useOnProjectChange(["documents", "drawing"], load);
  useEffect(() => { setPage(1); setOpen(null); }, [system, floor, status, revision, issuesOnly, search, pageSize]);

  const reset = () => { setFloor(""); setStatus(""); setRevision(""); setIssuesOnly(false); setSearch(""); };
  const openFolder = async (path: string | null, rowSystem: string) => {
    setNotice("");
    try {
      await api.post(`/projects/${projectId}/drawings/open-folder`, path ? { path, system: rowSystem } : { system: rowSystem });
    } catch (e) {
      setNotice((e as Error).message);
    }
  };
  const exportRegister = (scope: string) =>
    api
      .download(`/projects/${projectId}/logs/drawings/export.xlsx?system=${encodeURIComponent(scope)}`,
        `EP-${epNumber} Drawings Register ${scope === ALL_SYSTEMS ? "All" : scope}.xlsx`)
      .catch((e) => setError(e.message));

  const all = system === ALL_SYSTEMS;
  const revisions = data?.available_revisions ?? [];
  const filtering = Boolean(floor || status || revision || issuesOnly || search.trim());
  const selected = open !== null ? data?.rows.find((r) => r.drawing_id === open) ?? null : null;

  return (
    <div className="space-y-4">
      {error && <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm text-rose-800">{error}</div>}
      {notice && <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-900">{notice}</div>}
      {data?.warnings.map((w) => (
        <div key={w} className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-900">{w}</div>
      ))}

      {/* The summary: logical drawings, never files, scoped to the selection. Click one to filter. */}
      {data && (
        <div className="rounded-xl border border-gray-200 bg-white px-5 py-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <div className="text-base font-bold text-navy-900">{all ? "All systems" : `${systemName} (${system})`}</div>
              <div className="text-xs text-gray-500">Shop drawing register · {data.summary.total} {data.summary.total === 1 ? "record" : "records"}</div>
            </div>
            <div className="text-xs text-gray-500">
              Last sync {data.synced_at ? new Date(data.synced_at.endsWith("Z") ? data.synced_at : `${data.synced_at}Z`).toLocaleString() : "never"}
            </div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
            <Tile label="Total Drawings" value={data.summary.total} active={!status && !issuesOnly} onClick={() => { setStatus(""); setIssuesOnly(false); }} />
            {SUMMARY_ORDER.map((s) => (
              <Tile key={s} label={STATUS_LABEL[s]} value={data.summary[s] ?? 0} icon={<StatusIcon status={s} className="h-5 w-5" />} active={status === s} onClick={() => { setStatus(status === s ? "" : s); setIssuesOnly(false); }} />
            ))}
            <Tile label="Review Items" value={data.summary.review_items} tone="rose" active={issuesOnly} onClick={() => { setIssuesOnly(!issuesOnly); setStatus(""); }} />
          </div>
        </div>
      )}

      <div className="flex gap-4">
        <div className={`min-w-0 flex-1 rounded-xl border border-gray-200 bg-white ${selected ? "hidden xl:block" : ""}`}>
          <div className="flex flex-wrap items-end justify-between gap-3 border-b border-gray-100 p-5">
            <div>
              <h2 className="text-xl font-bold">Drawings Register</h2>
              <p className="mt-1 text-sm text-gray-500">One row per shop drawing, each revision as the consultant answered it. Actions and issues are handled on the Drawings page.</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <select value={floor} onChange={(e) => setFloor(e.target.value)} className="rounded-lg border border-gray-300 px-3 py-2 text-sm" aria-label="Floor">
                <option value="">All Floors</option>
                {(data?.floors ?? []).map((f) => <option key={f.key} value={f.key}>{f.name}</option>)}
              </select>
              <select value={status} onChange={(e) => setStatus(e.target.value as Status | "")} className="rounded-lg border border-gray-300 px-3 py-2 text-sm" aria-label="Latest status">
                <option value="">All Statuses</option>
                {SUMMARY_ORDER.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
              </select>
              <select value={revision} onChange={(e) => setRevision(e.target.value)} className="rounded-lg border border-gray-300 px-3 py-2 text-sm" aria-label="Revision">
                <option value="">All Revisions</option>
                {revisions.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input type="checkbox" checked={issuesOnly} onChange={(e) => setIssuesOnly(e.target.checked)} className="h-4 w-4" /> Issues only
              </label>
              <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search title, reference, floor, submission or reply…" className="w-72 rounded-lg border border-gray-300 px-3 py-2 text-sm" aria-label="Search the register" />
              {filtering && (
                <button type="button" onClick={reset} className="rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50">Clear</button>
              )}
              <button type="button" onClick={() => exportRegister(system)} disabled={!data?.summary.total} className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-50">
                Export {all ? "all systems" : system}
              </button>
              {!all && (
                <button type="button" onClick={() => exportRegister(ALL_SYSTEMS)} className="rounded-lg border border-gray-300 px-3 py-2 text-sm text-navy-900 hover:bg-gray-50">Export all</button>
              )}
              {!all && (
                <button type="button" onClick={() => openFolder(null, system)} disabled={!data?.folder} className="flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-50" title={data?.folder ?? "The project has no folder"}>
                  <FolderIcon /> Open Folder
                </button>
              )}
            </div>
          </div>

          {data === null ? (
            <div className="p-6 text-sm text-gray-500">{error ? "" : "Loading the drawings register…"}</div>
          ) : data.summary.total === 0 ? (
            <div className="p-6 text-sm text-gray-600">
              {all ? "No Shop Drawing records found." : `No ${systemName} Shop Drawing records found.`}
              <div className="mt-1 text-gray-500">
                Building floors come from the IFC drawing on{" "}
                <Link to={`/projects/${projectId}/boq`} className="font-medium text-brand-700 hover:underline">BOQ &gt; As per IFC Drawings</Link>;
                sync the project documents to detect {all ? "" : `${system} `}Shop Drawings.
              </div>
            </div>
          ) : (
            <div className="max-h-[70vh] overflow-auto">
              <table className="min-w-full text-sm">
                <thead className="sticky top-0 z-20 border-b border-gray-200 bg-gray-50 text-left text-sm font-semibold text-navy-900">
                  <tr>
                    <th className="bg-gray-50 px-3 py-3">#</th>
                    {all && <th className="bg-gray-50 px-3 py-3">System</th>}
                    <th className="sticky left-0 z-10 w-[9.5rem] min-w-[9.5rem] max-w-[9.5rem] bg-gray-50 px-3 py-3">Floor</th>
                    <th className="bg-gray-50 px-3 py-3">Drawing Title</th>
                    <th className="sticky left-[9.5rem] z-10 bg-gray-50 px-3 py-3">Drawing Reference</th>
                    {revisions.map((r) => <th key={r} className="bg-gray-50 px-3 py-3 text-center">{r}</th>)}
                    <th className="bg-gray-50 px-3 py-3 text-center">Latest Rev</th>
                    <th className="bg-gray-50 px-3 py-3">Latest Status</th>
                    <th className="bg-gray-50 px-3 py-3">Issues / Hints</th>
                    <th className="bg-gray-50 px-3 py-3">Updated</th>
                    <th className="bg-gray-50 px-3 py-3 text-center">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {data.rows.map((r, i) => (
                    <RegisterTableRow
                      key={r.key}
                      row={r}
                      index={(data.pagination.page - 1) * data.pagination.page_size + i + 1}
                      revisions={revisions}
                      showSystem={all}
                      selected={selected?.key === r.key}
                      projectId={projectId}
                      onOpen={() => r.drawing_id !== null && setOpen(r.drawing_id)}
                      onFolder={() => openFolder(r.latest_path, r.system)}
                    />
                  ))}
                  {data.rows.length === 0 && (
                    <tr>
                      <td colSpan={revisions.length + 10} className="px-4 py-6 text-center text-gray-500">No record matches the filter.</td>
                    </tr>
                  )}
                </tbody>
              </table>
              <div className="sticky bottom-0 flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 bg-white px-5 py-3 text-sm text-gray-500">
                <span>
                  Showing {data.rows.length ? (data.pagination.page - 1) * data.pagination.page_size + 1 : 0} to {(data.pagination.page - 1) * data.pagination.page_size + data.rows.length} of {data.pagination.total} records
                  {filtering && data.pagination.total !== data.summary.total && ` (${data.summary.total} in all)`}
                </span>
                <div className="flex items-center gap-2">
                  <button type="button" disabled={data.pagination.page <= 1} onClick={() => setPage(data.pagination.page - 1)} className="rounded-md border border-gray-300 px-2 py-1 disabled:opacity-40" aria-label="Previous page">‹</button>
                  {Array.from({ length: data.pagination.pages }, (_, n) => n + 1).slice(Math.max(0, data.pagination.page - 3), Math.max(0, data.pagination.page - 3) + 6).map((n) => (
                    <button key={n} type="button" onClick={() => setPage(n)} aria-current={n === data.pagination.page} className={`rounded-md px-2.5 py-1 ${n === data.pagination.page ? "bg-brand-600 text-white" : "border border-gray-300 hover:bg-gray-50"}`}>{n}</button>
                  ))}
                  <button type="button" disabled={data.pagination.page >= data.pagination.pages} onClick={() => setPage(data.pagination.page + 1)} className="rounded-md border border-gray-300 px-2 py-1 disabled:opacity-40" aria-label="Next page">›</button>
                  <span className="ml-3">Rows per page</span>
                  <select value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))} className="rounded-md border border-gray-300 px-2 py-1" aria-label="Rows per page">
                    {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                </div>
              </div>
            </div>
          )}
        </div>

        {selected && selected.drawing_id !== null && (
          <DrawingDetailPanel
            projectId={projectId}
            drawingId={selected.drawing_id}
            canEdit={canEdit}
            summary={null}
            onClose={() => setOpen(null)}
            onChanged={load}
          />
        )}
      </div>
    </div>
  );
}

function RegisterTableRow({ row, index, revisions, showSystem, selected, projectId, onOpen, onFolder }: {
  row: RegisterRow; index: number; revisions: string[]; showSystem: boolean; selected: boolean; projectId: number;
  onOpen: () => void; onFolder: () => void;
}) {
  const clickable = row.drawing_id !== null;
  const cellBg = selected ? "bg-brand-50" : "bg-white";
  return (
    <tr className={`${selected ? "bg-brand-50" : "hover:bg-gray-50"} ${clickable ? "cursor-pointer" : ""}`} onClick={clickable ? onOpen : undefined}>
      <td className="px-3 py-2.5 text-gray-500">{index}</td>
      {showSystem && <td className="px-3 py-2.5 font-semibold text-navy-900">{row.system}</td>}
      <td className={`sticky left-0 z-10 w-[9.5rem] min-w-[9.5rem] max-w-[9.5rem] px-3 py-2.5 ${cellBg}`}>
        <div className="font-medium text-navy-900">{row.floor}</div>
        {row.floor_secondary && <div className="text-xs text-gray-500">{row.floor_secondary}</div>}
        {row.floors > 1 && <div className="text-xs text-gray-500">{row.floors} floors</div>}
      </td>
      <td className="max-w-xs px-3 py-2.5 text-gray-700">
        <div className="truncate" title={row.title ?? undefined}>{row.title ?? <span className="text-gray-400">—</span>}</div>
      </td>
      <td className={`sticky left-[9.5rem] z-10 px-3 py-2.5 font-mono text-xs text-gray-700 ${cellBg}`}>
        {row.reference ?? <span className="font-sans text-gray-400">No shop drawing yet</span>}
        {row.confirmed && <span className="ml-1 text-[10px] text-gray-400" title="Corrected by an engineer">✎</span>}
      </td>
      {revisions.map((rev) => {
        const cell = row.cells[rev];
        const beyond = row.latest_revision === null || Number(rev.slice(1)) > Number(row.latest_revision.slice(1));
        return (
          <td key={rev} className="px-2 py-2.5 text-center">
            <Chip cell={cell} blank={beyond && !cell.candidate} />
          </td>
        );
      })}
      <td className="px-3 py-2.5 text-center font-medium">{row.latest_revision ?? "–"}</td>
      <td className="px-3 py-2.5"><Chip cell={{ status: row.latest_status, label: STATUS_LABEL[row.latest_status], path: row.latest_path }} blank={row.latest_revision === null} /></td>
      <td className="px-3 py-2.5">
        <div className="flex flex-wrap gap-1">
          {row.hints.length === 0 ? <span className="text-gray-300">—</span> : row.hints.slice(0, 3).map((h, i) => <HintBadge key={i} hint={h} />)}
          {row.hints.length > 3 && <span className="text-xs text-gray-500">+{row.hints.length - 3}</span>}
        </div>
      </td>
      <td className="whitespace-nowrap px-3 py-2.5 text-xs text-gray-500">{row.updated_at ? new Date(row.updated_at.endsWith("Z") ? row.updated_at : `${row.updated_at}Z`).toLocaleDateString() : "–"}</td>
      <td className="px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-center gap-3 text-brand-600">
          {row.latest_path ? (
            <a href={apiUrl(`/projects/${projectId}/logs/file?path=${encodeURIComponent(row.latest_path)}#page=${row.latest_page}`)} target="_blank" rel="noreferrer" title={`View ${row.latest_revision}`} className="hover:text-brand-800"><EyeIcon /></a>
          ) : row.candidates.length > 0 ? (
            <span className="text-gray-300" title={`${row.candidates.join(", ")}: detected, not confirmed submitted`}><EyeIcon /></span>
          ) : (
            <span className="text-gray-300" title="Nothing submitted yet"><EyeIcon /></span>
          )}
          <button type="button" onClick={onFolder} title={row.latest_path ? "Open the folder of the latest submission" : "Open the shop drawings folder"} className="hover:text-brand-800"><FolderIcon /></button>
          {clickable && (
            <button type="button" onClick={onOpen} className="rounded px-1.5 text-lg leading-none text-gray-500 hover:bg-gray-100" aria-label={`Details of ${row.reference}`}>⋯</button>
          )}
        </div>
      </td>
    </tr>
  );
}

function Tile({ label, value, icon, active, onClick, tone }: { label: string; value: number; icon?: React.ReactNode; active?: boolean; onClick?: () => void; tone?: "rose" }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={active} className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-left ${active ? "border-brand-500 bg-brand-50" : "border-gray-200 bg-white hover:bg-gray-50"}`}>
      {icon}
      <div className="min-w-0">
        <div className={`text-lg font-bold leading-tight ${tone === "rose" && value > 0 ? "text-rose-700" : "text-navy-900"}`}>{value}</div>
        <div className="truncate text-[11px] text-gray-500">{label}</div>
      </div>
    </button>
  );
}
