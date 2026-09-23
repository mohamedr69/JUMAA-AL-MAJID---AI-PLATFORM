import { Fragment, useEffect, useState } from "react";
import { ApiError, api, apiUrl } from "../lib/api";
import type { ProjectLogs, SampleBoardCheck, SubmittalRegister } from "../lib/types";
import { directoryRevision, registerRevision, groupRevisions, systemGroup, type LogDocument, type LogRevision } from "../lib/projectLog";
import { useAuth } from "../context/AuthContext";
import { PROJECT_EDITOR_ROLES } from "../lib/types";
import { SyncDocumentsCard } from "../components/SyncDocumentsCard";
import { DeleteSubmittalDialog } from "../components/DeleteSubmittalDialog";
import type { SubmittalDeleted } from "../lib/types";
import { useProject } from "./ProjectWorkspace";

const ALL = "__all__";
type ChildTab = "submittals" | "drawings" | "samples";

function systemLabel(code: string): string {
  return code === "FRC" ? "Fire Rated Cable" : code;
}

function when(value: string): string {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`).toLocaleString();
}

export function ProjectLogsPage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [refresh, setRefresh] = useState(0);
  const [logs, setLogs] = useState<ProjectLogs | null>(null);
  const [submittals, setSubmittals] = useState<SubmittalRegister | null>(null);
  const [system, setSystem] = useState(ALL);
  const [child, setChild] = useState<ChildTab>("submittals");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLogs(null);
    setSubmittals(null);
    setError(null);
    // From the index only: opening the logs reads the database. The folder
    // is read by "Sync documents", which reloads this when it ends.
    Promise.all([
      api.get<ProjectLogs>(`/projects/${project.id}/logs`),
      api.get<SubmittalRegister>(`/projects/${project.id}/submittals`),
    ])
      .then(([directory, register]) => {
        if (cancelled) return;
        setLogs(directory);
        setSubmittals(register);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load project logs");
      });
    return () => {
      cancelled = true;
    };
  }, [project.id, project.source_folder_path, refresh]);

  useEffect(() => {
    if (!logs?.scanning) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api.get<ProjectLogs>(`/projects/${project.id}/logs`);
        if (cancelled) return;
        setLogs(result);
        if (result.scanning) timer = setTimeout(poll, 3000);
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not check scan progress");
      }
    }
    timer = setTimeout(poll, 3000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [project.id, logs?.scanning]);

  useEffect(() => { setSystem(ALL); }, [project.id]);

  // An Edwards fire alarm carries voice evacuation and fire telephone (backend system rules).
  const integrated = project.voice_evacuation_integrated;
  const group = (value: string | null): string => systemGroup(value, integrated);
  const fullPackage = /full[ _-]*package/i.test(project.scope_of_work ?? "");
  // The project's systems under their effective codes, not the DRF's row
  // names: an Edwards panel's voice evacuation, fire telephone and smoke
  // management are the fire alarm, and listing the rows gave a tab each.
  const systems = [ALL, ...Array.from(new Set([
    ...project.system_codes,
    ...(logs?.systems ?? []), ...(submittals?.systems ?? []),
    ...(fullPackage ? ["FRC"] : []),
  ].map(group).filter(Boolean)))];
  const selectedSystem = systems.includes(system) ? system : ALL;
  const matches = (code: string | null) => selectedSystem === ALL || group(code) === selectedSystem;
  // Fire-rated cable has no drawings of its own, but it does have a sample board.
  // Fire-rated cable has no drawings of its own, and neither has a
  // project we do not draw: the tab is gone, so it cannot stay selected.
  const noDrawings = selectedSystem === "FRC" || project.drawings_in_scope === false;
  const activeChild = noDrawings && child === "drawings" ? "submittals" : child;
  const drawings = (logs?.drawings ?? []).filter((drawing) => matches(drawing.system_code));
  const directoryReferences = new Set((logs?.material_submittals ?? []).map((item) => item.reference?.replace(/-R\d+$/i, "").toUpperCase()));
  const items = (submittals?.items ?? []).filter((item) => matches(item.system_code) && !directoryReferences.has(item.reference?.replace(/-R\d+$/i, "").toUpperCase()));
  const samples = (logs?.samples ?? []).filter((file) => matches(file.system_code));
  const boardChecks = (logs?.sample_boards ?? []).filter((check) => matches(check.system_code));
  const materials = (logs?.material_submittals ?? []).filter((item) => matches(item.system_code));
  const rows = activeChild === "submittals" ? [...items.map(registerRevision), ...materials.map(directoryRevision)]
    : activeChild === "samples"
      // A transmittal's sample is filed as "Sample Board" per system: the
      // system goes in the title so the ALL view tells them apart.
      ? samples.map((file) => ({ ...directoryRevision(file), title: file.source === "transmittal" ? `${file.name} / ${systemLabel(group(file.system_code))}` : file.name }))
      : drawings.filter((file) => group(file.system_code) !== "FRC").map(directoryRevision);
  const documents = groupRevisions(rows, integrated);
  // The material submittals the log lists, by reference: these can be
  // deleted for good from here (the files included), after the warning.
  const materialReferences = new Set((logs?.material_submittals ?? []).map((item) => (item.reference ?? "").toUpperCase()));
  const [deleting, setDeleting] = useState<LogDocument | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function confirmDelete() {
    if (!deleting) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await api.post<SubmittalDeleted>(`/projects/${project.id}/submittals/delete`, { reference: deleting.reference });
      setDeleting(null);
      setRefresh((v) => v + 1);
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : "Could not delete the submittal");
    } finally {
      setDeleteBusy(false);
    }
  }
  const visible = documents.filter((doc) => `${doc.title} ${doc.reference}`.toLowerCase().includes(search.toLowerCase()) && (!statusFilter || doc.revisions[0].status === statusFilter));
  const revisionColumns = [...new Set(["R0", ...documents.flatMap((doc) => doc.revisions.map((r) => r.revision))])].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  const view = (row: LogRevision) => row.path ? <a className="font-medium text-brand-600 hover:underline" href={apiUrl(`/projects/${project.id}/logs/file?path=${encodeURIComponent(row.path)}#page=${row.page ?? 1}`)} target="_blank" rel="noreferrer">{row.source === "drawing schedule" ? "View schedule" : "View file"}</a> : <span className="text-gray-400">No file</span>;
  const badge = (value: string) => <span className={`inline-block rounded-md px-3 py-1 text-xs font-semibold ${value === "ANN" ? "bg-cyan-100 text-cyan-800" : value === "rejected" ? "bg-rose-100 text-rose-700" : value === "approved" ? "bg-green-100 text-green-700" : value === "UR" ? "bg-amber-100 text-amber-800" : "bg-gray-100 text-gray-600"}`}>{value}</span>;
  function exportLog() {
    const data = [["Document", "Reference number", "System", "Revision", "Status", "File", "Updated"], ...visible.flatMap((doc) => doc.revisions.map((r) => [doc.title, doc.reference, group(r.system), r.revision, r.status, r.path ?? "", r.updated]))];
    const csv = data.map((row) => row.map((value) => `"${(/^[=+@-]/.test(value) ? "'" : "") + value.replaceAll('"', '""')}"`).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = `EP-${project.ep_number}-${activeChild}-log.csv`; link.click(); URL.revokeObjectURL(url);
  }

  // The log is only true once every document has been read. A half-finished
  // scan shows a drawing with no later revision because that file has not
  // been opened yet, and shows a submittal as UR because the page carrying
  // the consultant's stamp is still ahead of it -- both read as fact and
  // neither is one. So nothing is shown until the scan has finished.
  const ready = Boolean(logs && submittals && !logs.scanning);
  const total = logs?.total_files ?? 0;
  const done = Math.min(logs?.processed_files ?? 0, total || (logs?.processed_files ?? 0));
  const percent = total ? Math.round((done / total) * 100) : 0;

  return (
    <div className="text-navy-900">
      <div className="text-xs text-gray-500">EP-{project.ep_number} / {selectedSystem === ALL ? "ALL" : selectedSystem} / Logs</div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-4">
        <div><h1 className="text-3xl font-bold">Project Logs</h1><p className="mt-2 text-sm text-gray-500">Track submissions, drawings and samples across every revision.</p></div>
        <div className="flex gap-3"><button onClick={exportLog} disabled={!visible.length} className="rounded-lg bg-brand-600 px-5 py-2 font-semibold text-white disabled:opacity-40">Export log</button></div>
      </div>
      <div className="mt-4">
        <SyncDocumentsCard projectId={project.id} canEdit={canEdit} compact onSynced={() => setRefresh((v) => v + 1)} />
      </div>
      {deleting && (
        <DeleteSubmittalDialog
          title={deleting.title}
          reference={deleting.reference}
          revision={deleting.revisions[0]?.revision ?? null}
          files={deleting.revisions.map((r) => r.path).filter((p): p is string => Boolean(p))}
          busy={deleteBusy}
          error={deleteError}
          onConfirm={() => void confirmDelete()}
          onCancel={() => setDeleting(null)}
        />
      )}
      {error && <div role="alert" className="mt-4 rounded-lg bg-red-50 p-3 text-red-700">{error}</div>}
      {/* Scan warnings are deliberately not shown. They are notes about the
          read itself -- a file still in the cloud, a long document only
          partly checked -- and the page is the log, not a report on how it
          was produced. The API still returns them (GET /projects/{id}/logs,
          `warnings`) for diagnosing a scan. */}
      {!ready && !error && (
        <div role="status" aria-live="polite" className="mt-6 rounded-xl border border-gray-200 bg-white p-8">
          <p className="text-lg font-semibold">Checking project documents</p>
          <div className="mt-5 h-2.5 w-full overflow-hidden rounded-full bg-gray-100">
            <div
              className="h-full rounded-full bg-brand-600 transition-[width] duration-500 ease-out"
              style={{ width: total ? `${percent}%` : "12%" }}
            />
          </div>
          <p className="mt-3 text-sm font-medium text-brand-700">
            {total ? `${done} of ${total} documents checked (${percent}%)` : "Listing project documents..."}
          </p>
        </div>
      )}
      {ready && <>
      <div className="mt-7 flex flex-wrap gap-1">{systems.map((value) => <button key={value} onClick={() => { setSystem(value); setStatusFilter(""); if (value === "FRC") setChild("submittals"); }} className={`rounded-t-lg border border-gray-200 px-7 py-3 font-semibold ${selectedSystem === value ? "bg-brand-600 text-white" : "bg-gray-50 text-gray-500"}`}>{value === ALL ? "ALL" : systemLabel(value)}</button>)}</div>
      <div className="rounded-b-xl rounded-tr-xl border border-gray-200 bg-white p-4">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
          <div className="flex gap-4">{((selectedSystem === "FRC" || project.drawings_in_scope === false ? ["submittals", "samples"] : ["submittals", "drawings", "samples"]) as ChildTab[]).map((value) => <button key={value} onClick={() => { setChild(value); setStatusFilter(""); }} className={`border-b-2 px-2 py-3 text-sm font-semibold ${activeChild === value ? "border-brand-600 text-brand-600" : "border-transparent text-gray-500"}`}>{value === "submittals" ? "Material Submittals" : value === "drawings" ? "Drawings" : "Samples"}</button>)}</div>
          <div className="flex flex-wrap items-center gap-3"><input aria-label="Search documents" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search documents..." className="input w-60" /><label className="flex items-center gap-2 text-sm">Status<select className="input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}><option value="">All statuses</option>{[...new Set(documents.map((doc) => doc.revisions[0].status))].sort().map((value) => <option key={value}>{value}</option>)}</select></label></div>
        </div>
        {activeChild === "samples" && <SampleBoardChecks checks={boardChecks} projectId={project.id} synced={Boolean(logs?.synced_at)} />}
        {(() => {
          // Two shapes, following the platform owner's design. A drawing is
          // one row per floor with a column per revision, because what is
          // asked of it is "which floors are issued, and where has each got
          // to". A submittal is one row per reference showing only its latest
          // revision and status, with the earlier ones as a history trail --
          // a submittal has no floor and its older revisions are superseded,
          // so a column each would be mostly empty cells.
          const columns = activeChild === "drawings"
            ? ["Drawing title", "Reference number", "Floor", ...revisionColumns, "Latest rev.", "Action"]
            : ["Document", "Reference number", "Latest revision", "Status", "Revision history", "Action"];
          return <div className="overflow-x-auto rounded-lg border border-gray-200"><table className="w-full text-left text-sm">
          <thead className="bg-gray-50"><tr>{columns.map((label) => <th key={label} className="whitespace-nowrap border-b border-gray-200 px-4 py-4 font-semibold">{label}</th>)}</tr></thead>
          <tbody>{visible.map((doc) => { const latest = doc.revisions[0]; return <Fragment key={doc.key}>
            <tr className="border-b border-gray-100"><td className="min-w-60 px-4 py-4"><button aria-expanded={expanded.has(doc.key)} onClick={() => setExpanded((prev) => { const next = new Set(prev); if (next.has(doc.key)) next.delete(doc.key); else next.add(doc.key); return next; })} className="flex gap-3 text-left font-medium"><span aria-hidden="true">{expanded.has(doc.key) ? "⌄" : "›"}</span>{doc.title}</button></td><td className="px-4 py-4">{activeChild === "samples" ? latest.reference : doc.reference}{canEdit && materialReferences.has(doc.reference.toUpperCase()) && <button onClick={() => { setDeleteError(null); setDeleting(doc); }} title="Delete this material submittal permanently" className="ml-3 text-xs font-semibold text-red-600 hover:underline">Delete</button>}</td>
            {activeChild === "drawings" ? <>
              <td className="px-4 py-4">{latest.floor}</td>
              {revisionColumns.map((rev) => <td key={rev} className="px-4 py-4">{doc.revisions.some((r) => r.revision === rev) ? doc.revisions.filter((r) => r.revision === rev).map((r, i) => <div key={i} title={r.evidence ?? "No consultant decision recorded"}>{badge(r.status)}<div className="mt-1 text-xs">{view(r)}</div></div>) : <span className="text-gray-400">&mdash;</span>}</td>)}
              <td className="px-4 py-4">{latest.revision}</td>
            </> : <>
              <td className="px-4 py-4">{latest.revision}</td>
              <td className="px-4 py-4" title={latest.evidence ?? "No consultant decision recorded"}>{badge(latest.status)}</td>
              <td className="px-4 py-4"><div className="flex flex-wrap items-center gap-2">{[...doc.revisions].reverse().map((r, i) => <Fragment key={i}>{i > 0 && <span aria-hidden="true" className="text-gray-400">&rarr;</span>}<span className="inline-flex items-center gap-1.5 whitespace-nowrap"><span className="text-xs font-semibold text-gray-500">{r.revision}</span>{badge(r.status)}</span></Fragment>)}</div></td>
            </>}
            <td className="whitespace-nowrap px-4 py-4">{view(latest)}</td></tr>
            {expanded.has(doc.key) && <tr className="bg-brand-50/40"><td colSpan={columns.length} className="p-4"><table className="w-full bg-white text-sm"><thead className="bg-gray-50"><tr>{["Revision", "Status", "Reference number", "Updated", "Consultant reply", "File"].map((label) => <th key={label} className="border border-gray-200 p-3">{label}</th>)}</tr></thead><tbody>{doc.revisions.map((r, i) => <tr key={i}><td className="border border-gray-200 p-3">{r.revision} {i === 0 && r.revision !== "Not recorded" && <span className="ml-2 text-xs text-brand-600">Latest</span>}</td><td className="border border-gray-200 p-3">{badge(r.status)}</td><td className="border border-gray-200 p-3">{r.reference}</td><td className="border border-gray-200 p-3">{when(r.updated)}</td><td className="border border-gray-200 p-3">{r.evidence ?? "No reply found"}</td><td className="border border-gray-200 p-3">{view(r)}</td></tr>)}</tbody></table></td></tr>}
          </Fragment>; })}</tbody>
        </table>{visible.length === 0 && <p className="p-10 text-center text-gray-500">No documents match this view.</p>}</div>;
        })()}
      </div>
      </>}
    </div>
  );
}

/** Every system shall have a sample board. One card a system: the board
 * sent (its transmittal, date and where it stands), loose sample material
 * only, or none found in the Transmittal folder. */
function SampleBoardChecks({ checks, projectId, synced }: { checks: SampleBoardCheck[]; projectId: number; synced: boolean }) {
  if (!synced || checks.length === 0) return null;
  const missing = checks.filter((check) => check.state !== "submitted").length;
  const tone = { submitted: "border-green-200 bg-green-50", material_only: "border-amber-200 bg-amber-50", missing: "border-rose-200 bg-rose-50" };
  const heading = { submitted: "Sample board submitted", material_only: "Sample material only — no board", missing: "No sample board found" };
  return (
    <section aria-label="Sample board per system" className="mb-5">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Sample board per system</h2>
        <p className="text-xs text-gray-500">{missing ? `${missing} of ${checks.length} system${checks.length === 1 ? "" : "s"} still need a sample board` : "Every system has a sample board"} &middot; read from the transmittals in the Transmittal folder</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {checks.map((check) => (
          <div key={check.system_code} className={`rounded-lg border p-3 ${tone[check.state]}`}>
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold">{check.system_name}</span>
              <span className="text-xs font-semibold text-gray-500">{systemLabel(check.system_code)}</span>
            </div>
            <p className={`mt-1 text-sm font-medium ${check.state === "submitted" ? "text-green-800" : check.state === "missing" ? "text-rose-700" : "text-amber-800"}`}>{heading[check.state]}</p>
            {check.reference && (
              <p className="mt-1 text-xs text-gray-600">
                {check.reference}{check.revision ? ` · ${check.revision}` : ""}{check.submitted_on ? ` · ${new Date(check.submitted_on).toLocaleDateString()}` : ""}
                {check.path && <> · <a className="font-medium text-brand-600 hover:underline" href={apiUrl(`/projects/${projectId}/logs/file?path=${encodeURIComponent(check.path)}`)} target="_blank" rel="noreferrer">View transmittal</a></>}
              </p>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
