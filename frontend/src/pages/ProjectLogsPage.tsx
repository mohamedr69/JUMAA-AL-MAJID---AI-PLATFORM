import { Fragment, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { ApiError, api, apiUrl } from "../lib/api";
import type { ProjectLogs, SubmittalRegister } from "../lib/types";
import { directoryRevision, registerRevision, groupRevisions, type LogDocument, type LogRevision } from "../lib/projectLog";
import { useProject } from "./ProjectWorkspace";

type ChildTab = "submittals" | "drawings" | "samples";

/** How the log names each system. Anything else a project carries keeps its
 * own code. */
const SYSTEM_LABELS: Record<string, string> = {
  FAS: "Fire Alarm",
  EML: "EML",
  FRC: "Fire Rated Cable",
  VE: "Voice Evacuation",
  FT: "Fire Telephone",
  CBS: "Central Battery System",
};

const CHILD_LABELS: Record<ChildTab, string> = {
  submittals: "Material Submittals",
  drawings: "Drawings",
  samples: "Samples",
};

/** UR is not a decision, it is the absence of one: a revision stays UR until a
 * consultant reply is read out of the document content itself. */
const STATUS_STYLES: Record<string, string> = {
  ANN: "bg-cyan-100 text-cyan-800",
  approved: "bg-green-100 text-green-700",
  rejected: "bg-rose-100 text-rose-700",
  UR: "bg-amber-100 text-amber-800",
};

const STATUS_LABELS: Record<string, string> = {
  ANN: "ANN",
  approved: "Approved",
  rejected: "Rejected",
  UR: "UR",
};

const STATUS_TITLES: Record<string, string> = {
  ANN: "Approved as noted",
  approved: "Approved",
  rejected: "Rejected - revise and resubmit",
  UR: "Under review - no consultant decision found in the document content",
};

function when(value: string): string {
  const date = new Date(/^\d{4}-\d\d-\d\d$/.test(value) ? `${value}T00:00:00Z` : /[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
  return date.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
}

export function ProjectLogsPage() {
  const { project } = useProject();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [refresh, setRefresh] = useState(0);
  const [logs, setLogs] = useState<ProjectLogs | null>(null);
  const [submittals, setSubmittals] = useState<SubmittalRegister | null>(null);
  const [system, setSystem] = useState<string | null>(null);
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

  useEffect(() => { setSystem(null); }, [project.id]);

  const edwards = project.systems.some((entry) => /edwards/i.test(entry.brand ?? "")) ||
    (submittals?.items ?? []).some((entry) => /edwards/i.test(entry.manufacturer ?? ""));
  const group = useMemo(() => (value: string | null): string => {
    const code = (value ?? "").trim().toUpperCase().replace(/[_-]+/g, " ");
    if (["FAS", "FA", "FIRE ALARM"].includes(code)) return "FAS";
    if (["VE", "VES", "VOICE EVACUATION", "FT", "FIRE TELEPHONE"].includes(code)) {
      return edwards ? "FAS" : (["FT", "FIRE TELEPHONE"].includes(code) ? "FT" : "VE");
    }
    if (["EML", "ELS", "EL", "EMERGENCY LIGHTING", "EMERGENCY LIGHT MONITORING", "MONITORED SELF CONTAINED", "MONITORED SELF CONTAINED SYSTEM", "MONITORED SELF CONTAINED EMERGENCY LIGHTING", "EMERGENCY LIGHTING MONITORING"].includes(code)) return "EML";
    if (["FRC", "FIRE RATED CABLE", "FIRE RESISTANT CABLE"].includes(code)) return "FRC";
    if (code === "CENTRAL BATTERY SYSTEM") return "CBS";
    return value?.trim() ?? "";
  }, [edwards]);

  const fullPackage = /full[ _-]*package/i.test(project.scope_of_work ?? "");
  const systems = Array.from(new Set([
    ...project.systems.map((entry) => entry.name),
    ...(logs?.systems ?? []), ...(submittals?.systems ?? []),
    ...(fullPackage ? ["FRC"] : []),
  ].map(group).filter(Boolean))).sort((a, b) => {
    const order = ["FAS", "EML", "FRC"];
    const rank = (code: string) => (order.includes(code) ? order.indexOf(code) : order.length);
    return rank(a) - rank(b) || a.localeCompare(b);
  });
  const selectedSystem = system && systems.includes(system) ? system : systems[0] ?? "";
  const matches = (code: string | null) => group(code) === selectedSystem;
  /** Cable is submitted as a material: it carries no drawings or samples. */
  const activeChild = selectedSystem === "FRC" ? "submittals" : child;

  const drawings = (logs?.drawings ?? []).filter((drawing) => matches(drawing.system_code));
  const directoryReferences = new Set((logs?.material_submittals ?? []).map((item) => item.reference?.replace(/-R\d+$/i, "").toUpperCase()));
  const items = (submittals?.items ?? []).filter((item) => matches(item.system_code) && !directoryReferences.has(item.reference?.replace(/-R\d+$/i, "").toUpperCase()));
  const samples = (logs?.samples ?? []).filter((file) => matches(file.system_code));
  const materials = (logs?.material_submittals ?? []).filter((item) => matches(item.system_code));
  const rows = activeChild === "submittals" ? [...items.map(registerRevision), ...materials.map(directoryRevision)]
    : (activeChild === "samples" ? samples : drawings).map(directoryRevision);
  const documents = groupRevisions(rows, group);
  const visible = documents.filter((doc) => `${doc.title} ${doc.reference}`.toLowerCase().includes(search.toLowerCase()) && (!statusFilter || doc.revisions[0].status === statusFilter));
  /** Drawings are logged revision by revision, the way the consultant returns them. */
  const revisionColumns = [...new Set(["R0", ...documents.flatMap((doc) => doc.revisions.map((r) => r.revision))])].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));

  const fileHref = (row: LogRevision) =>
    row.path ? apiUrl(`/projects/${project.id}/logs/file?path=${encodeURIComponent(row.path)}#page=${row.page ?? 1}`) : null;

  /** The status chip is the way into the document the status was read from. */
  function statusChip(row: LogRevision, withRevision?: string) {
    const href = fileHref(row);
    const title = `${STATUS_TITLES[row.status] ?? row.status}${row.evidence ? ` - "${row.evidence}"` : ""}${href ? "" : " (no file recorded)"}`;
    const chip = (
      <span className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-semibold ${STATUS_STYLES[row.status] ?? "bg-gray-100 text-gray-600"}`}>
        {withRevision && <span className="opacity-60">{withRevision}</span>}
        {STATUS_LABELS[row.status] ?? row.status}
      </span>
    );
    return href
      ? <a href={href} target="_blank" rel="noreferrer" title={title} className="hover:opacity-80">{chip}</a>
      : <span title={title}>{chip}</span>;
  }

  function exportLog() {
    const data = [["Document", "Reference number", "System", "Revision", "Status", "File", "Updated"], ...visible.flatMap((doc) => doc.revisions.map((r) => [doc.title, doc.reference, group(r.system), r.revision, r.status, r.path ?? "", r.updated]))];
    const csv = data.map((row) => row.map((value) => `"${(/^[=+@-]/.test(value) ? "'" : "") + value.replaceAll('"', '""')}"`).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `EP-${project.ep_number}-${selectedSystem}-${activeChild}-log.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  function toggle(key: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const columns = activeChild === "drawings"
    ? ["Drawing title", "Reference number", "Floor", ...revisionColumns, "Latest rev.", "Action"]
    : ["Document", "Reference number", "Latest revision", "Status", "Revision history", "Action"];

  return (
    <div className="text-navy-900">
      <div className="text-xs text-gray-500">EP-{project.ep_number} / {selectedSystem || "-"} / Logs</div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">Project Logs</h1>
          <p className="mt-2 text-sm text-gray-500">Track submissions, drawings and samples across every revision.</p>
        </div>
        <div className="flex gap-3">
          <button disabled={(!logs && !error) || logs?.scanning} onClick={() => setRefresh((v) => v + 1)} className="rounded-lg border border-brand-600 px-5 py-2 font-medium text-brand-600 disabled:opacity-40">Refresh</button>
          <button onClick={exportLog} disabled={!visible.length} className="rounded-lg bg-brand-600 px-5 py-2 font-semibold text-white disabled:opacity-40">Export log</button>
        </div>
      </div>
      {logs?.scanning && <div role="status" className="mt-4 rounded-lg bg-brand-50 p-3 text-sm text-brand-700">Reading project documents: {logs.processed_files} / {logs.total_files || "..."} files checked. Results update automatically.</div>}
      {error && <div role="alert" className="mt-4 rounded-lg bg-red-50 p-3 text-red-700">{error}</div>}
      {logs?.warnings.map((warning) => <p key={warning} className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">{warning}</p>)}

      <div className="mt-7 flex flex-wrap gap-1">
        {systems.map((code) => (
          <button
            key={code}
            onClick={() => { setSystem(code); setStatusFilter(""); if (code === "FRC") setChild("submittals"); }}
            className={`rounded-t-lg border border-gray-200 px-7 py-3 font-semibold ${selectedSystem === code ? "bg-brand-600 text-white" : "bg-gray-50 text-gray-500 hover:text-navy-900"}`}
          >
            {SYSTEM_LABELS[code] ?? code}
          </button>
        ))}
      </div>

      <div className="rounded-b-xl rounded-tr-xl border border-gray-200 bg-white p-4">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
          <div className="flex gap-4">
            {((selectedSystem === "FRC" ? ["submittals"] : ["submittals", "drawings", "samples"]) as ChildTab[]).map((value) => (
              <button
                key={value}
                onClick={() => { setChild(value); setStatusFilter(""); }}
                className={`border-b-2 px-2 py-3 text-sm font-semibold ${activeChild === value ? "border-brand-600 text-brand-600" : "border-transparent text-gray-500 hover:text-navy-900"}`}
              >
                {CHILD_LABELS[value]}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <input
              aria-label="Search documents"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={activeChild === "drawings" ? "Search drawings..." : "Search documents..."}
              className="input w-60"
            />
            <label className="flex items-center gap-2 text-sm">
              Status
              <select className="input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                <option value="">All statuses</option>
                {[...new Set(documents.map((doc) => doc.revisions[0].status))].sort().map((value) => <option key={value}>{value}</option>)}
              </select>
            </label>
          </div>
        </div>

        {!logs || !submittals ? (
          <p className="p-6 text-gray-500">{error ? "Logs could not be loaded. Use Refresh to retry." : "Reading project directory..."}</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table className="w-full text-left text-sm">
              <thead className="bg-gray-50">
                <tr>
                  {columns.map((label) => <th key={label} className="whitespace-nowrap border-b border-gray-200 px-4 py-4 font-semibold">{label}</th>)}
                </tr>
              </thead>
              <tbody>
                {visible.map((doc) => (
                  <Fragment key={doc.key}>
                    <DocumentRow
                      doc={doc}
                      drawings={activeChild === "drawings"}
                      revisionColumns={revisionColumns}
                      expanded={expanded.has(doc.key)}
                      onToggle={() => toggle(doc.key)}
                      statusChip={statusChip}
                      fileHref={fileHref}
                    />
                    {expanded.has(doc.key) && (
                      <tr className="bg-brand-50/40">
                        <td colSpan={columns.length} className="p-4">
                          <RevisionTable doc={doc} statusChip={statusChip} fileHref={fileHref} dateLabel={activeChild === "drawings" ? "Issued" : "Response"} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
            {visible.length === 0 && <p className="p-10 text-center text-gray-500">{logs?.scanning ? "Checking documents for this view..." : "No documents match this view."}</p>}
          </div>
        )}

        <p className="mt-4 text-xs text-gray-500">
          Select a revision status to open its document. Every status is read from the document content itself: a
          revision whose content carries no consultant decision stays UR, including a document prepared but not yet
          submitted. Drawing schedule rows cover the listed floors; grouped typical floors follow the source schedule.
        </p>
      </div>
    </div>
  );
}

function DocumentRow({ doc, drawings, revisionColumns, expanded, onToggle, statusChip, fileHref }: {
  doc: LogDocument;
  drawings: boolean;
  revisionColumns: string[];
  expanded: boolean;
  onToggle: () => void;
  statusChip: (row: LogRevision, withRevision?: string) => ReactNode;
  fileHref: (row: LogRevision) => string | null;
}) {
  const latest = doc.revisions[0];
  const href = fileHref(latest);
  /** Oldest first, the way a revision history reads. */
  const history = [...doc.revisions].reverse();
  return (
    <tr className="border-b border-gray-100 align-middle">
      <td className="min-w-60 px-4 py-4">
        <button aria-expanded={expanded} onClick={onToggle} className="flex items-center gap-2 text-left font-medium">
          <span className="text-gray-400">{expanded ? "▾" : "▸"}</span>
          {doc.title}
        </button>
      </td>
      <td className="px-4 py-4 text-gray-700">{doc.reference}</td>
      {drawings ? (
        <>
          <td className="px-4 py-4 text-gray-700">{latest.floor}</td>
          {revisionColumns.map((rev) => {
            const row = doc.revisions.find((r) => r.revision === rev);
            return <td key={rev} className="px-4 py-4">{row ? statusChip(row) : <span className="text-gray-300">{"—"}</span>}</td>;
          })}
          <td className="px-4 py-4 font-medium">{latest.revision}</td>
        </>
      ) : (
        <>
          <td className="px-4 py-4 font-medium">{latest.revision}</td>
          <td className="px-4 py-4">{statusChip(latest)}</td>
          <td className="px-4 py-4">
            <div className="flex flex-wrap items-center gap-2">
              {history.map((row, i) => (
                <Fragment key={`${row.revision}-${i}`}>
                  {i > 0 && <span className="text-gray-400">{"→"}</span>}
                  {statusChip(row, row.revision)}
                </Fragment>
              ))}
            </div>
          </td>
        </>
      )}
      <td className="whitespace-nowrap px-4 py-4">
        {href
          ? <a href={href} target="_blank" rel="noreferrer" className="rounded-lg border border-gray-300 px-4 py-1.5 text-sm font-semibold text-brand-600 hover:bg-gray-50">View</a>
          : <span className="text-xs text-gray-400">No file</span>}
      </td>
    </tr>
  );
}

function RevisionTable({ doc, statusChip, fileHref, dateLabel }: {
  doc: LogDocument;
  statusChip: (row: LogRevision) => ReactNode;
  fileHref: (row: LogRevision) => string | null;
  dateLabel: string;
}) {
  return (
    <table className="w-full bg-white text-sm">
      <thead className="bg-gray-50">
        <tr>
          {["Revision", "Status", "Reference number", dateLabel, "Consultant reply", "File"].map((label) => (
            <th key={label} className="border border-gray-200 p-3 text-left font-semibold">{label}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {doc.revisions.map((row, i) => {
          const href = fileHref(row);
          return (
            <tr key={`${row.revision}-${i}`}>
              <td className="border border-gray-200 p-3">
                {row.revision}
                {i === 0 && row.revision !== "Not recorded" && <span className="ml-2 rounded-md bg-gray-100 px-2 py-0.5 text-xs text-gray-600">Latest</span>}
                {row.note && <span title={row.note} className="ml-2 cursor-help text-amber-600">!</span>}
              </td>
              <td className="border border-gray-200 p-3">{statusChip(row)}</td>
              <td className="border border-gray-200 p-3 text-gray-700">{row.reference}</td>
              <td className="border border-gray-200 p-3 text-gray-700">{when(row.issued ?? row.updated)}</td>
              <td className="border border-gray-200 p-3 text-gray-600">{row.evidence ?? "No reply found in the document"}</td>
              <td className="border border-gray-200 p-3">
                {href
                  ? <a href={href} target="_blank" rel="noreferrer" className="font-medium text-brand-600 hover:underline">{row.source === "drawing schedule" ? "View schedule" : "View PDF"}</a>
                  : <span className="text-gray-400">No file</span>}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
