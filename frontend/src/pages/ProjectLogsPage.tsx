import { Fragment, useEffect, useMemo, useState } from "react";
import { ApiError, api, apiUrl } from "../lib/api";
import type { LogsSystem, LogsSystems, ProjectLogs, SampleBoardCheck, SubmittalDeleted } from "../lib/types";
import { PROJECT_EDITOR_ROLES } from "../lib/types";
import { directoryRevision, groupRevisions, submittalDocuments, systemGroup, type LogDocument, type LogRevision } from "../lib/projectLog";
import { useOnProjectChange } from "../lib/projectChanges";
import { useAuth } from "../context/AuthContext";
import { SyncDocumentsCard } from "../components/SyncDocumentsCard";
import { DeleteSubmittalDialog } from "../components/DeleteSubmittalDialog";
import { ALL_SYSTEMS, DrawingsRegister } from "../components/registers/DrawingsRegister";
import { useProject } from "./ProjectWorkspace";

type Register = "submittals" | "drawings" | "samples";
const REGISTERS: { key: Register; label: string; field: keyof Pick<LogsSystem, "material_submittals" | "drawings" | "samples"> }[] = [
  { key: "submittals", label: "Material Submittals", field: "material_submittals" },
  { key: "drawings", label: "Drawings", field: "drawings" },
  { key: "samples", label: "Samples", field: "samples" },
];
// Systems shown as tabs before the rest go under "More".
const INLINE_SYSTEMS = 3;

function when(value: string): string {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`).toLocaleString();
}

function remembered(projectId: number): string {
  try {
    return localStorage.getItem(`logs.system.${projectId}`) ?? ALL_SYSTEMS;
  } catch {
    return ALL_SYSTEMS;
  }
}

/** Project > Logs: the consolidated registers -- material submittals,
 *  drawings, samples -- each read from the records its operational page
 *  edits. One system selector for the module (the project's own systems,
 *  under the names the project gives them), remembered per project, kept
 *  across the registers where a system takes part in each. */
export function ProjectLogsPage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);
  const [systems, setSystems] = useState<LogsSystems | null>(null);
  const [system, setSystemState] = useState<string>(() => remembered(project.id));
  const [register, setRegister] = useState<Register>("submittals");
  const [logs, setLogs] = useState<ProjectLogs | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const setSystem = (code: string) => {
    setSystemState(code);
    setStatusFilter("");
    try {
      localStorage.setItem(`logs.system.${project.id}`, code);
    } catch {
      /* storage blocked: the system is not remembered */
    }
  };

  useEffect(() => {
    setSystemState(remembered(project.id));
    api.get<LogsSystems>(`/projects/${project.id}/logs/systems`).then(setSystems).catch(() => setSystems(null));
  }, [project.id]);

  // The material submittals and samples: from the register and the document index.
  useEffect(() => {
    let cancelled = false;
    setLogs(null);
    setError(null);
    api.get<ProjectLogs>(`/projects/${project.id}/logs`)
      .then((directory) => { if (!cancelled) setLogs(directory); })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load project logs"); });
    return () => { cancelled = true; };
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

  // A submittal changed, or the folder was synced -- here, on another page,
  // or by the worker: read the registers again without clearing the table.
  useOnProjectChange(["submittal", "documents"], () => {
    api.get<ProjectLogs>(`/projects/${project.id}/logs`).then(setLogs).catch(() => undefined);
    api.get<LogsSystems>(`/projects/${project.id}/logs/systems`).then(setSystems).catch(() => undefined);
  });

  // The registers the project has: drawings only where they are ours to produce.
  const registers = REGISTERS.filter((r) => r.key !== "drawings" || (systems ? systems.drawings_in_scope : project.drawings_in_scope !== false));
  const activeRegister = registers.some((r) => r.key === register) ? register : "submittals";
  const registerField = REGISTERS.find((r) => r.key === activeRegister)!.field;
  const available: LogsSystem[] = systems?.systems ?? project.system_codes.map((code) => ({
    code, name: code, short_name: code, material_submittals: true, samples: true, drawings: code !== "FRC",
  }));
  const selected = system === ALL_SYSTEMS || available.some((s) => s.code === system) ? system : ALL_SYSTEMS;
  // The selection stays; a register the selected system takes no part in
  // (a cable's drawings) shows every system, and says so.
  const takesPart = selected === ALL_SYSTEMS || available.some((s) => s.code === selected && s[registerField]);
  const effective = takesPart ? selected : ALL_SYSTEMS;
  const selectedName = available.find((s) => s.code === selected)?.name ?? selected;
  const inline = available.slice(0, INLINE_SYSTEMS);
  const more = available.slice(INLINE_SYSTEMS);

  // --- the material submittals and samples, filtered by the selection ------------------------------
  const integrated = project.voice_evacuation_integrated;
  const group = (value: string | null): string => systemGroup(value, integrated);
  const matches = (code: string | null) => effective === ALL_SYSTEMS || group(code) === effective;
  const samples = (logs?.samples ?? []).filter((file) => matches(file.system_code));
  const boardChecks = (logs?.sample_boards ?? []).filter((check) => matches(check.system_code));
  const materials = (logs?.material_submittals ?? []).filter((item) => matches(item.system_code));
  const systemLabel = (code: string) => available.find((s) => s.code === code)?.short_name ?? code;
  const rows: LogRevision[] = activeRegister === "samples"
    // A transmittal's sample is filed as "Sample Board" per system: the
    // system goes in the title so the ALL view tells them apart.
    ? samples.map((file) => ({ ...directoryRevision(file), title: file.source === "transmittal" ? `${file.name} / ${systemLabel(group(file.system_code))}` : file.name }))
    : [];
  const documents = useMemo(
    () => (activeRegister === "submittals" ? submittalDocuments(materials) : groupRevisions(rows, integrated)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeRegister, logs, effective, integrated],
  );
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
  const view = (row: LogRevision) => row.path ? <a className="font-medium text-brand-600 hover:underline" href={apiUrl(`/projects/${project.id}/logs/file?path=${encodeURIComponent(row.path)}#page=${row.page ?? 1}`)} target="_blank" rel="noreferrer">View file</a> : <span className="text-gray-400">No file</span>;
  const badge = (value: string) => <span className={`inline-block rounded-md px-3 py-1 text-xs font-semibold ${value === "ANN" ? "bg-cyan-100 text-cyan-800" : value === "rejected" || value === "RR" || value === "REJ" ? "bg-rose-100 text-rose-700" : value === "approved" || value === "A" ? "bg-green-100 text-green-700" : value === "UR" ? "bg-amber-100 text-amber-800" : "bg-gray-100 text-gray-600"}`}>{value}</span>;
  function exportLog() {
    const data = [["Document", "Reference number", "System", "Revision", "Status", "File", "Updated"], ...visible.flatMap((doc) => doc.revisions.map((r) => [doc.title, doc.reference, group(r.system), r.revision, r.status, r.path ?? "", r.updated]))];
    const csv = data.map((row) => row.map((value) => `"${(/^[=+@-]/.test(value) ? "'" : "") + value.replaceAll('"', '""')}"`).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob(["﻿", csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = `EP-${project.ep_number}-${activeRegister}-log.csv`; link.click(); URL.revokeObjectURL(url);
  }

  // The log is only true once every document has been read: nothing is
  // shown until the scan has finished.
  const ready = Boolean(logs && !logs.scanning);
  const total = logs?.total_files ?? 0;
  const done = Math.min(logs?.processed_files ?? 0, total || (logs?.processed_files ?? 0));
  const percent = total ? Math.round((done / total) * 100) : 0;

  const systemTab = (code: string, label: string) => (
    <button key={code} onClick={() => setSystem(code)} aria-pressed={selected === code} className={`rounded-lg px-4 py-2 text-sm font-semibold ${selected === code ? "bg-brand-600 text-white" : "border border-gray-300 bg-white text-navy-900 hover:bg-gray-50"}`}>{label}</button>
  );

  return (
    <div className="text-navy-900">
      <div className="text-xs text-gray-500">EP-{project.ep_number} / {selected === ALL_SYSTEMS ? "All systems" : selectedName} / Logs</div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-4">
        <div><h1 className="text-3xl font-bold">Project Logs</h1><p className="mt-2 text-sm text-gray-500">The project's registers: material submittals, shop drawings and samples, each read from its own records.</p></div>
        {activeRegister !== "drawings" && (
          <div className="flex gap-3"><button onClick={exportLog} disabled={!visible.length} className="rounded-lg bg-brand-600 px-5 py-2 font-semibold text-white disabled:opacity-40">Export log</button></div>
        )}
      </div>
      <div className="mt-4">
        <SyncDocumentsCard projectId={project.id} canEdit={canEdit} compact onSynced={() => setRefresh((v) => v + 1)} />
      </div>

      {/* The system: the module's, not a register's. */}
      <div className="mt-5 flex flex-wrap items-center gap-2" role="group" aria-label="System">
        <span className="mr-1 text-xs font-medium uppercase tracking-wide text-gray-500">System</span>
        {systemTab(ALL_SYSTEMS, "All")}
        {inline.map((s) => systemTab(s.code, s.short_name))}
        {more.length > 0 && (
          <select value={more.some((s) => s.code === selected) ? selected : ""} onChange={(e) => e.target.value && setSystem(e.target.value)} className="rounded-lg border border-gray-300 px-3 py-2 text-sm font-semibold" aria-label="More systems">
            <option value="">More ▾</option>
            {more.map((s) => <option key={s.code} value={s.code}>{s.short_name}</option>)}
          </select>
        )}
        {selected !== ALL_SYSTEMS && <span className="ml-2 text-xs text-gray-500">{selectedName}</span>}
      </div>
      {/* The register. */}
      <div className="mt-3 flex flex-wrap gap-1" role="tablist" aria-label="Register">
        {registers.map((r) => (
          <button key={r.key} role="tab" aria-selected={activeRegister === r.key} onClick={() => { setRegister(r.key); setStatusFilter(""); setSearch(""); }} className={`rounded-t-lg border border-gray-200 px-6 py-3 text-sm font-semibold ${activeRegister === r.key ? "bg-brand-600 text-white" : "bg-gray-50 text-gray-500"}`}>{r.label}</button>
        ))}
      </div>
      {!takesPart && (
        <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900">
          {selectedName} has no {REGISTERS.find((r) => r.key === activeRegister)?.label.toLowerCase()} register on this project: showing all systems here. Your selection stays for the other registers.
        </div>
      )}

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

      {activeRegister === "drawings" ? (
        <div className="mt-4">
          <DrawingsRegister projectId={project.id} epNumber={project.ep_number} canEdit={canEdit} system={effective} systemName={effective === ALL_SYSTEMS ? "All systems" : selectedName} />
        </div>
      ) : (
        <>
          {!ready && !error && (
            <div role="status" aria-live="polite" className="mt-6 rounded-xl border border-gray-200 bg-white p-8">
              <p className="text-lg font-semibold">Checking project documents</p>
              <div className="mt-5 h-2.5 w-full overflow-hidden rounded-full bg-gray-100">
                <div className="h-full rounded-full bg-brand-600 transition-[width] duration-500 ease-out" style={{ width: total ? `${percent}%` : "12%" }} />
              </div>
              <p className="mt-3 text-sm font-medium text-brand-700">
                {total ? `${done} of ${total} documents checked (${percent}%)` : "Listing project documents..."}
              </p>
            </div>
          )}
          {ready && (
            <div className="rounded-b-xl rounded-tr-xl border border-gray-200 bg-white p-4">
              <div className="mb-4 flex flex-wrap items-center justify-end gap-3">
                <input aria-label="Search documents" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search documents..." className="input w-60" />
                <label className="flex items-center gap-2 text-sm">Status<select className="input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}><option value="">All statuses</option>{[...new Set(documents.map((doc) => doc.revisions[0].status))].sort().map((value) => <option key={value}>{value}</option>)}</select></label>
              </div>
              {activeRegister === "samples" && <SampleBoardChecks checks={boardChecks} projectId={project.id} synced={Boolean(logs?.synced_at)} />}
              {(() => {
                const columns = ["Document", "Reference number", "Latest revision", "Status", "Revision history", "Action"];
                return <div className="overflow-x-auto rounded-lg border border-gray-200"><table className="w-full text-left text-sm">
                  <thead className="bg-gray-50"><tr>{columns.map((label) => <th key={label} className="whitespace-nowrap border-b border-gray-200 px-4 py-4 font-semibold">{label}</th>)}</tr></thead>
                  <tbody>{visible.map((doc) => { const latest = doc.revisions[0]; return <Fragment key={doc.key}>
                    <tr className="border-b border-gray-100"><td className="min-w-60 px-4 py-4"><button aria-expanded={expanded.has(doc.key)} onClick={() => setExpanded((prev) => { const next = new Set(prev); if (next.has(doc.key)) next.delete(doc.key); else next.add(doc.key); return next; })} className="flex gap-3 text-left font-medium"><span aria-hidden="true">{expanded.has(doc.key) ? "⌄" : "›"}</span>{doc.title}</button></td><td className="px-4 py-4">{activeRegister === "samples" ? latest.reference : doc.reference}{canEdit && activeRegister === "submittals" && materialReferences.has(doc.reference.toUpperCase()) && <button onClick={() => { setDeleteError(null); setDeleting(doc); }} title="Delete this material submittal permanently" className="ml-3 text-xs font-semibold text-red-600 hover:underline">Delete</button>}</td>
                    <td className="px-4 py-4">{latest.revision}</td>
                    <td className="px-4 py-4" title={latest.evidence ?? "No consultant decision recorded"}>{badge(latest.status)}</td>
                    <td className="px-4 py-4"><div className="flex flex-wrap items-center gap-2">{[...doc.revisions].reverse().map((r, i) => <Fragment key={i}>{i > 0 && <span aria-hidden="true" className="text-gray-400">&rarr;</span>}<span className="inline-flex items-center gap-1.5 whitespace-nowrap"><span className="text-xs font-semibold text-gray-500">{r.revision}</span>{badge(r.status)}</span></Fragment>)}</div></td>
                    <td className="whitespace-nowrap px-4 py-4">{view(latest)}</td></tr>
                    {expanded.has(doc.key) && <tr className="bg-brand-50/40"><td colSpan={columns.length} className="p-4"><table className="w-full bg-white text-sm"><thead className="bg-gray-50"><tr>{["Revision", "Status", "Reference number", "Updated", "Consultant reply", "File"].map((label) => <th key={label} className="border border-gray-200 p-3">{label}</th>)}</tr></thead><tbody>{doc.revisions.map((r, i) => <tr key={i}><td className="border border-gray-200 p-3">{r.revision} {i === 0 && r.revision !== "Not recorded" && <span className="ml-2 text-xs text-brand-600">Latest</span>}</td><td className="border border-gray-200 p-3">{badge(r.status)}</td><td className="border border-gray-200 p-3">{r.reference}</td><td className="border border-gray-200 p-3">{when(r.updated)}</td><td className="border border-gray-200 p-3">{r.evidence ?? "No reply found"}</td><td className="border border-gray-200 p-3">{view(r)}</td></tr>)}</tbody></table></td></tr>}
                  </Fragment>; })}</tbody>
                </table>{visible.length === 0 && <p className="p-10 text-center text-gray-500">No documents match this view.</p>}</div>;
              })()}
            </div>
          )}
        </>
      )}
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
              <span className="text-xs font-semibold text-gray-500">{check.system_code}</span>
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
