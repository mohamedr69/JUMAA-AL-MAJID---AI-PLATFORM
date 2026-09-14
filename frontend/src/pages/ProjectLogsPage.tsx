import { Fragment, useEffect, useState } from "react";
import { ApiError, api, apiUrl } from "../lib/api";
import type { ProjectLogs, SubmittalRegister } from "../lib/types";
import { directoryRevision, registerRevision, groupRevisions, type LogRevision } from "../lib/projectLog";
import { useProject } from "./ProjectWorkspace";

const ALL = "__all__";
type ChildTab = "submittals" | "drawings" | "samples";

function when(value: string): string {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`).toLocaleString();
}

export function ProjectLogsPage() {
  const { project } = useProject();
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
    Promise.all([
      api.get<ProjectLogs>(`/projects/${project.id}/logs?refresh=true`),
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
  const group = (value: string | null): string => {
    const code = (value ?? "").trim().toUpperCase().replace(/[_-]+/g, " ");
    if (["FAS", "FA", "FIRE ALARM"].includes(code)) return "FAS";
    if (["VE", "VES", "VOICE EVACUATION", "FT", "FIRE TELEPHONE"].includes(code)) {
      if (["FT", "FIRE TELEPHONE"].includes(code)) return "FAS";
      return integrated ? "FAS" : "VE";
    }
    // Emergency lighting is one system: ELS, CBS and EML alike.
    if (["ELS", "EL", "EML", "ELM", "CBS", "EMERGENCY LIGHTING", "CENTRAL BATTERY SYSTEM", "EMERGENCY LIGHT MONITORING", "MONITORED SELF CONTAINED", "MONITORED SELF CONTAINED SYSTEM", "MONITORED SELF CONTAINED EMERGENCY LIGHTING", "EMERGENCY LIGHTING MONITORING"].includes(code)) return "ELS";
    if (["FRC", "FIRE RATED CABLE", "FIRE RESISTANT CABLE"].includes(code)) return "FRC";
    return value?.trim() ?? "";
  };
  const fullPackage = /full[ _-]*package/i.test(project.scope_of_work ?? "");
  const systems = [ALL, ...Array.from(new Set([
    ...project.systems.map((entry) => entry.name),
    ...(logs?.systems ?? []), ...(submittals?.systems ?? []),
    ...(fullPackage ? ["FRC"] : []),
  ].map(group).filter(Boolean)))];
  const selectedSystem = systems.includes(system) ? system : ALL;
  const matches = (code: string | null) => selectedSystem === ALL || group(code) === selectedSystem;
  const activeChild = selectedSystem === "FRC" ? "submittals" : child;
  const drawings = (logs?.drawings ?? []).filter((drawing) => matches(drawing.system_code));
  const directoryReferences = new Set((logs?.material_submittals ?? []).map((item) => item.reference?.replace(/-R\d+$/i, "").toUpperCase()));
  const items = (submittals?.items ?? []).filter((item) => matches(item.system_code) && !directoryReferences.has(item.reference?.replace(/-R\d+$/i, "").toUpperCase()));
  const samples = (logs?.samples ?? []).filter((file) => matches(file.system_code) && group(file.system_code) !== "FRC");
  const materials = (logs?.material_submittals ?? []).filter((item) => matches(item.system_code));
  const rows = activeChild === "submittals" ? [...items.map(registerRevision), ...materials.map(directoryRevision)]
    : (activeChild === "samples" ? samples : drawings.filter((file) => group(file.system_code) !== "FRC")).map(directoryRevision);
  const documents = groupRevisions(rows);
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
        <div className="flex gap-3"><button disabled={(!logs && !error) || logs?.scanning} onClick={() => setRefresh((v) => v + 1)} className="rounded-lg border border-brand-600 px-5 py-2 text-brand-600">Refresh</button><button onClick={exportLog} disabled={!visible.length} className="rounded-lg bg-brand-600 px-5 py-2 font-semibold text-white disabled:opacity-40">Export log</button></div>
      </div>
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
      <div className="mt-7 flex flex-wrap gap-1">{systems.map((value) => <button key={value} onClick={() => { setSystem(value); setStatusFilter(""); if (value === "FRC") setChild("submittals"); }} className={`rounded-t-lg border border-gray-200 px-7 py-3 font-semibold ${selectedSystem === value ? "bg-brand-600 text-white" : "bg-gray-50 text-gray-500"}`}>{value === ALL ? "ALL" : value === "FRC" ? "Fire Rated Cable" : value}</button>)}</div>
      <div className="rounded-b-xl rounded-tr-xl border border-gray-200 bg-white p-4">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
          <div className="flex gap-4">{((selectedSystem === "FRC" ? ["submittals"] : ["submittals", "drawings", "samples"]) as ChildTab[]).map((value) => <button key={value} onClick={() => { setChild(value); setStatusFilter(""); }} className={`border-b-2 px-2 py-3 text-sm font-semibold ${activeChild === value ? "border-brand-600 text-brand-600" : "border-transparent text-gray-500"}`}>{value === "submittals" ? "Material Submittals" : value === "drawings" ? "Drawings" : "Samples"}</button>)}</div>
          <div className="flex flex-wrap items-center gap-3"><input aria-label="Search documents" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search documents..." className="input w-60" /><label className="flex items-center gap-2 text-sm">Status<select className="input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}><option value="">All statuses</option>{[...new Set(documents.map((doc) => doc.revisions[0].status))].sort().map((value) => <option key={value}>{value}</option>)}</select></label></div>
        </div>
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
            <tr className="border-b border-gray-100"><td className="min-w-60 px-4 py-4"><button aria-expanded={expanded.has(doc.key)} onClick={() => setExpanded((prev) => { const next = new Set(prev); if (next.has(doc.key)) next.delete(doc.key); else next.add(doc.key); return next; })} className="flex gap-3 text-left font-medium"><span aria-hidden="true">{expanded.has(doc.key) ? "⌄" : "›"}</span>{doc.title}</button></td><td className="px-4 py-4">{doc.reference}</td>
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
