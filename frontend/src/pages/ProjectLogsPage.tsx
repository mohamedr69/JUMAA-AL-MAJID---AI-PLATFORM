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

  useEffect(() => { setSystem(ALL); }, [project.id]);

  const edwards = project.systems.some((entry) => /edwards/i.test(entry.brand ?? "")) ||
    (submittals?.items ?? []).some((entry) => /edwards/i.test(entry.manufacturer ?? ""));
  const group = (value: string | null): string => {
    const code = (value ?? "").trim().toUpperCase().replace(/[_-]+/g, " ");
    if (["FAS", "FA", "FIRE ALARM"].includes(code)) return "FAS";
    if (["VE", "VES", "VOICE EVACUATION", "FT", "FIRE TELEPHONE"].includes(code)) {
      return edwards ? "FAS" : (["FT", "FIRE TELEPHONE"].includes(code) ? "FT" : "VE");
    }
    if (["EML", "ELS", "EL", "EMERGENCY LIGHTING", "EMERGENCY LIGHT MONITORING", "MONITORED SELF CONTAINED", "MONITORED SELF CONTAINED SYSTEM", "MONITORED SELF CONTAINED EMERGENCY LIGHTING", "EMERGENCY LIGHTING MONITORING"].includes(code)) return "EML";
    if (["FRC", "FIRE RATED CABLE", "FIRE RESISTANT CABLE"].includes(code)) return "FRC";
    if (code === "CENTRAL BATTERY SYSTEM") return "CBS";
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
  const items = (submittals?.items ?? []).filter((item) => matches(item.system_code) && Boolean(item.reply_code?.trim()));
  const samples = (logs?.samples ?? []).filter((file) => matches(file.system_code) && group(file.system_code) !== "FRC");
  const rows = activeChild === "submittals" ? items.map(registerRevision)
    : (activeChild === "samples" ? samples : drawings.filter((file) => group(file.system_code) !== "FRC")).map(directoryRevision);
  const documents = groupRevisions(rows, group);
  const visible = documents.filter((doc) => `${doc.title} ${doc.reference}`.toLowerCase().includes(search.toLowerCase()) && (!statusFilter || doc.revisions[0].status === statusFilter));
  const revisionColumns = [...new Set(documents.flatMap((doc) => doc.revisions.map((r) => r.revision)))].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  const view = (row: LogRevision) => row.path ? <a className="font-medium text-brand-600 hover:underline" href={apiUrl(`/projects/${project.id}/logs/file?path=${encodeURIComponent(row.path)}`)} target="_blank" rel="noreferrer">View file</a> : <span className="text-gray-400">No file</span>;
  const badge = (value: string) => <span className={`inline-block rounded-md px-3 py-1 text-xs font-semibold ${value === "ANN" ? "bg-cyan-100 text-cyan-800" : value === "rejected" ? "bg-rose-100 text-rose-700" : value === "approved" ? "bg-green-100 text-green-700" : value === "under review" ? "bg-amber-100 text-amber-800" : "bg-gray-100 text-gray-600"}`}>{value}</span>;
  function exportLog() {
    const data = [["Document", "Reference number", "System", "Revision", "Status", "File", "Updated"], ...visible.flatMap((doc) => doc.revisions.map((r) => [doc.title, doc.reference, group(r.system), r.revision, r.status, r.path ?? "", r.updated]))];
    const csv = data.map((row) => row.map((value) => `"${(/^[=+@-]/.test(value) ? "'" : "") + value.replaceAll('"', '""')}"`).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = `EP-${project.ep_number}-${activeChild}-log.csv`; link.click(); URL.revokeObjectURL(url);
  }

  return (
    <div className="text-navy-900">
      <div className="text-xs text-gray-500">EP-{project.ep_number} / {selectedSystem === ALL ? "ALL" : selectedSystem} / Logs</div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-4">
        <div><h1 className="text-3xl font-bold">Project Logs</h1><p className="mt-2 text-sm text-gray-500">Track submissions, drawings and samples across every revision.</p></div>
        <div className="flex gap-3"><button onClick={() => setRefresh((v) => v + 1)} className="rounded-lg border border-brand-600 px-5 py-2 text-brand-600">Refresh</button><button onClick={exportLog} disabled={!visible.length} className="rounded-lg bg-brand-600 px-5 py-2 font-semibold text-white disabled:opacity-40">Export log</button></div>
      </div>
      {error && <div role="alert" className="mt-4 rounded-lg bg-red-50 p-3 text-red-700">{error}</div>}
      {logs?.warnings.map((warning) => <p key={warning} className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">{warning}</p>)}
      <div className="mt-7 flex flex-wrap gap-1">{systems.map((value) => <button key={value} onClick={() => { setSystem(value); setStatusFilter(""); if (value === "FRC") setChild("submittals"); }} className={`rounded-t-lg border border-gray-200 px-7 py-3 font-semibold ${selectedSystem === value ? "bg-brand-600 text-white" : "bg-gray-50 text-gray-500"}`}>{value === ALL ? "ALL" : value === "FRC" ? "Fire Rated Cable" : value}</button>)}</div>
      <div className="rounded-b-xl rounded-tr-xl border border-gray-200 bg-white p-4">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
          <div className="flex gap-4">{((selectedSystem === "FRC" ? ["submittals"] : ["submittals", "drawings", "samples"]) as ChildTab[]).map((value) => <button key={value} onClick={() => { setChild(value); setStatusFilter(""); }} className={`border-b-2 px-2 py-3 text-sm font-semibold ${activeChild === value ? "border-brand-600 text-brand-600" : "border-transparent text-gray-500"}`}>{value === "submittals" ? "Material Submittals" : value === "drawings" ? "Drawings" : "Samples"}</button>)}</div>
          <div className="flex flex-wrap items-center gap-3"><input aria-label="Search documents" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search documents..." className="input w-60" /><label className="flex items-center gap-2 text-sm">Status<select className="input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}><option value="">All statuses</option>{[...new Set(documents.map((doc) => doc.revisions[0].status))].sort().map((value) => <option key={value}>{value}</option>)}</select></label></div>
        </div>
        {activeChild === "submittals" && <p className="mb-4 text-sm text-gray-500">Only submittals with a recorded consultant response are included.</p>}
        {!logs || !submittals ? <p className="p-6 text-gray-500">{error ? "Logs could not be loaded. Use Refresh to retry." : "Reading project directory..."}</p> : <div className="overflow-x-auto rounded-lg border border-gray-200"><table className="w-full text-left text-sm">
          <thead className="bg-gray-50"><tr>{[activeChild === "drawings" ? "Drawing title" : "Document", "Reference number", ...(activeChild === "drawings" ? ["Floor", ...revisionColumns, "Latest rev."] : ["Latest revision", "Status", "Revision history"]), "Action"].map((label) => <th key={label} className="whitespace-nowrap border-b border-gray-200 px-4 py-4 font-semibold">{label}</th>)}</tr></thead>
          <tbody>{visible.map((doc) => { const latest = doc.revisions[0]; return <Fragment key={doc.key}>
            <tr className="border-b border-gray-100"><td className="min-w-60 px-4 py-4"><button aria-expanded={expanded.has(doc.key)} onClick={() => setExpanded((prev) => { const next = new Set(prev); if (next.has(doc.key)) next.delete(doc.key); else next.add(doc.key); return next; })} className="flex gap-3 text-left font-medium"><span>{expanded.has(doc.key) ? "?" : "+"}</span>{doc.title}</button></td><td className="px-4 py-4">{doc.reference}</td>
            {activeChild === "drawings" ? <><td className="px-4 py-4">{latest.floor}</td>{revisionColumns.map((rev) => <td key={rev} className="px-4 py-4">{doc.revisions.filter((r) => r.revision === rev).map((r, i) => <div key={i}>{badge(r.status)}<div className="mt-1 text-xs">{view(r)}</div></div>)}</td>)}<td className="px-4 py-4">{latest.revision}</td></> : <><td className="px-4 py-4">{latest.revision}</td><td className="px-4 py-4">{badge(latest.status)}</td><td className="px-4 py-4"><div className="flex flex-wrap gap-2">{[...doc.revisions].reverse().map((r, i) => <span key={i} className="whitespace-nowrap">{i > 0 && " ? "}{r.revision} {badge(r.status)}</span>)}</div></td></>}
            <td className="whitespace-nowrap px-4 py-4">{view(latest)}</td></tr>
            {expanded.has(doc.key) && <tr className="bg-brand-50/40"><td colSpan={activeChild === "drawings" ? revisionColumns.length + 5 : 6} className="p-4"><table className="w-full bg-white text-sm"><thead className="bg-gray-50"><tr>{["Revision", "Status", "Reference number", "Updated", "File"].map((label) => <th key={label} className="border border-gray-200 p-3">{label}</th>)}</tr></thead><tbody>{doc.revisions.map((r, i) => <tr key={i}><td className="border border-gray-200 p-3">{r.revision} {i === 0 && r.revision !== "Not recorded" && <span className="ml-2 text-xs text-brand-600">Latest</span>}</td><td className="border border-gray-200 p-3">{badge(r.status)}</td><td className="border border-gray-200 p-3">{r.reference}</td><td className="border border-gray-200 p-3">{when(r.updated)}</td><td className="border border-gray-200 p-3">{view(r)}</td></tr>)}</tbody></table></td></tr>}
          </Fragment>; })}</tbody>
        </table>{visible.length === 0 && <p className="p-10 text-center text-gray-500">No documents match this view.</p>}</div>}
        <p className="mt-4 text-xs text-gray-500">Expand a document to view its revision files. Directory filenames supply references and revisions where available; unrecorded review statuses are shown explicitly.</p>
      </div>
    </div>
  );
}
