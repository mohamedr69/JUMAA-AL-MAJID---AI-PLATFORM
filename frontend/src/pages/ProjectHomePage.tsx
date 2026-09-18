import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { JobProgress } from "../components/JobProgress";
import { ReadinessPanel } from "../components/ReadinessPanel";
import { SyncDocumentsCard } from "../components/SyncDocumentsCard";
import { useAuth } from "../context/AuthContext";
import { ApiError, api, apiUrl } from "../lib/api";
import { formatApiDate } from "../lib/format";
import { directoryRevision, groupRevisions, type LogDocument } from "../lib/projectLog";
import { useJob } from "../lib/useJob";
import {
  PROJECT_EDITOR_ROLES,
  type DocumentStatus,
  type ProjectLogDrawing,
  type ProjectLogs,
  type ProposedMaterials,
  type Readiness,
  type SubmittalMap,
} from "../lib/types";
import { useProject } from "./ProjectWorkspace";

/** The project at a glance: what is submitted and where it stands, per
 * document kind and per system, with what is still open.
 *
 * Everything here is read from the database -- the document index (the
 * logs), the register, the readiness checks -- so opening the project
 * costs no scan of the folder and no model call. The folder is read only
 * by "Sync documents", in the header.
 */
export function ProjectHomePage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);
  const { state } = useLocation();
  const justCreated = Boolean((state as { justCreated?: boolean } | null)?.justCreated);

  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [readinessError, setReadinessError] = useState<string | null>(null);
  const [logs, setLogs] = useState<ProjectLogs | null>(null);
  const [systems, setSystems] = useState<ProposedMaterials["systems"] | null>(null);
  const [submittalMap, setSubmittalMap] = useState<SubmittalMap | null>(null);
  const [documents, setDocuments] = useState<DocumentStatus | null>(null);
  const [tab, setTab] = useState<Kind>("submittals");

  const loadReadiness = useCallback(() => {
    api
      .get<Readiness>(`/projects/${project.id}/readiness`)
      .then((r) => {
        setReadiness(r);
        setReadinessError(null);
      })
      .catch((err) => setReadinessError(err instanceof ApiError ? err.message : "Could not load readiness"));
  }, [project.id]);

  const loadBoard = useCallback(() => {
    api.get<ProjectLogs>(`/projects/${project.id}/logs`).then(setLogs).catch(() => setLogs(null));
    api.get<ProposedMaterials>(`/projects/${project.id}/materials`).then((m) => setSystems(m.systems)).catch(() => setSystems(null));
    api.get<SubmittalMap>(`/projects/${project.id}/submittals/map`).then(setSubmittalMap).catch(() => setSubmittalMap(null));
  }, [project.id]);

  useEffect(() => {
    loadReadiness();
    loadBoard();
  }, [loadReadiness, loadBoard]);

  const intake = useJob(project.id, "documents_intake", `/projects/${project.id}/jobs/documents-intake`, () => loadReadiness());

  // The same grouping the Logs tab uses, so the two never disagree about
  // how many documents are on file.
  const integrated = project.voice_evacuation_integrated;
  const submittals = useMemo(() => summarise(logs?.material_submittals, integrated), [logs, integrated]);
  const drawings = useMemo(() => summarise(logs?.drawings, integrated), [logs, integrated]);
  const samples = useMemo(() => summarise(logs?.samples, integrated), [logs, integrated]);
  const kinds = useMemo<Record<Kind, Summary>>(() => ({ submittals, drawings, samples }), [submittals, drawings, samples]);
  const overall = submittals.total + drawings.total + samples.total;
  const approved = submittals.approved + drawings.approved + samples.approved;

  const rows = useMemo(() => systemRows(systems, kinds, project.system_codes), [systems, kinds, project.system_codes]);
  const actions = useMemo(
    () => openActions(readiness, submittalMap, documents),
    [readiness, submittalMap, documents],
  );

  return (
    <div>
      {justCreated && (
        <div className="mb-3 flex items-center gap-2 text-sm text-green-700">
          <span className="h-2 w-2 rounded-full bg-green-500" />
          Project created successfully
        </div>
      )}

      {/* The project, and the one action that reads the folder. */}
      <section className="rounded-xl border border-gray-200 bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-2xl font-bold text-navy-900">EP-{project.ep_number}</h1>
              <span
                className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                  project.status === "active"
                    ? "bg-green-50 text-green-700"
                    : project.status === "archived"
                      ? "bg-gray-100 text-gray-600"
                      : "bg-amber-50 text-amber-800"
                }`}
              >
                {project.status === "active" ? "Active" : project.status === "archived" ? "Archived" : "Draft"}
              </span>
            </div>
            {project.project_name && <div className="mt-0.5 text-xl font-semibold text-navy-900">{project.project_name}</div>}
          </div>
          <SyncDocumentsCard
            projectId={project.id}
            canEdit={canEdit}
            autoStart
            header
            onStatus={setDocuments}
            onSynced={() => {
              loadReadiness();
              loadBoard();
            }}
          />
        </div>
        <dl className="mt-4 grid gap-4 border-t border-gray-100 pt-4 sm:grid-cols-2 lg:grid-cols-4">
          <Fact label="Client" value={project.client} />
          <Fact label="Consultant" value={project.consultant} />
          <Fact label="Contractor" value={project.contractor} />
          <Fact label="Scope" value={project.scope_of_work} />
        </dl>
      </section>

      {/* What is submitted, and where it stands. */}
      <div className="mt-4 grid gap-4 lg:grid-cols-4">
        <section className="rounded-xl border border-gray-200 bg-white p-5">
          <h2 className="text-sm font-bold text-navy-900">Overall progress</h2>
          <div className="mt-3 flex items-center gap-5">
            <Donut value={share(approved, overall)} />
            <div className="min-w-0 flex-1 space-y-2">
              {KINDS.map(({ key, label, tint }) => (
                <Bar key={key} label={label} tint={tint} approved={kinds[key].approved} total={kinds[key].total} />
              ))}
            </div>
          </div>
          <p className="mt-3 text-xs text-gray-500">
            {overall > 0 ? `${approved} of ${overall} documents approved` : "Nothing filed yet: sync the documents."}
          </p>
        </section>

        {KINDS.map(({ key, label, tint }) => (
          <StatCard key={key} label={label} tint={tint} summary={kinds[key]} onOpen={() => setTab(key)} />
        ))}
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        {/* Per system: how much of what is on file is approved. */}
        <section className="rounded-xl border border-gray-200 bg-white">
          <h2 className="border-b border-gray-100 px-5 py-3 text-sm font-bold text-navy-900">System progress</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-4 py-2 font-semibold">System</th>
                  {KINDS.map((k) => (
                    <th key={k.key} className="px-4 py-2 font-semibold">{k.short}</th>
                  ))}
                  <th className="px-4 py-2 text-right font-semibold">Overall</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr><td colSpan={5} className="px-4 py-6 text-center text-gray-400">No system on this project yet.</td></tr>
                )}
                {rows.map((row) => (
                  <tr key={row.code} className="border-t border-gray-100">
                    <td className="px-4 py-2.5 font-medium text-navy-900">
                      {row.title}
                      {row.brand && <span className="ml-2 text-xs font-normal text-gray-500">{row.brand}</span>}
                    </td>
                    {KINDS.map((k) => (
                      <td key={k.key} className="px-4 py-2.5">
                        <Bar label="" tint={k.tint} approved={row[k.key].approved} total={row[k.key].total} compact />
                      </td>
                    ))}
                    <td className="px-4 py-2.5 text-right font-semibold tabular-nums text-navy-900">
                      {row.total > 0 ? `${share(row.approved, row.total)}%` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* The documents the project stands on. */}
        <section className="rounded-xl border border-gray-200 bg-white">
          <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
            <h2 className="text-sm font-bold text-navy-900">Key documents</h2>
            <Link to="documents" className="text-xs font-semibold text-brand-600 hover:underline">Documents →</Link>
          </div>
          <ul className="divide-y divide-gray-100 text-sm">
            {keyDocuments(project, logs, documents).map((doc) => (
              <li key={doc.label} className="flex items-center justify-between gap-3 px-5 py-2.5">
                <span className="min-w-0">
                  <span className="block font-medium text-navy-900">{doc.label}</span>
                  {doc.detail && <span className="block truncate text-xs text-gray-500">{doc.detail}</span>}
                </span>
                <span
                  className={`shrink-0 rounded-md px-2 py-0.5 text-xs font-semibold ${
                    doc.found ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-600"
                  }`}
                >
                  {doc.found ? "Found" : doc.missing}
                </span>
              </li>
            ))}
          </ul>
        </section>
      </div>

      {/* The latest of each kind, and what is still open. */}
      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <section className="rounded-xl border border-gray-200 bg-white">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-4 py-2.5">
            <div className="flex flex-wrap gap-1">
              {KINDS.map((k) => (
                <button
                  key={k.key}
                  onClick={() => setTab(k.key)}
                  className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${
                    tab === k.key ? "bg-brand-600 text-white" : "text-navy-900 hover:bg-gray-100"
                  }`}
                >
                  {k.label}
                </button>
              ))}
            </div>
            <Link to="logs" className="text-xs font-semibold text-brand-600 hover:underline">View all →</Link>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  {["Reference", "Description", "System", "Rev.", "Status", "Last update", ""].map((label) => (
                    <th key={label} className="px-4 py-2 font-semibold">{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {kinds[tab].documents.length === 0 && (
                  <tr><td colSpan={7} className="px-4 py-6 text-center text-gray-400">Nothing filed under this kind yet.</td></tr>
                )}
                {kinds[tab].documents.slice(0, 6).map((doc) => {
                  const latest = doc.revisions[0];
                  return (
                    <tr key={doc.key} className="border-t border-gray-100">
                      <td className="px-4 py-2 font-medium text-navy-900">{doc.reference}</td>
                      <td className="max-w-64 truncate px-4 py-2 text-gray-700" title={doc.title}>{doc.title}</td>
                      <td className="px-4 py-2 text-gray-600">{latest.system ?? "—"}</td>
                      <td className="px-4 py-2 text-gray-600">{latest.revision}</td>
                      <td className="px-4 py-2"><StatusPill status={latest.status} /></td>
                      <td className="px-4 py-2 text-gray-500">{formatApiDate(latest.updated, "short")}</td>
                      <td className="px-4 py-2 text-right">
                        {latest.path && (
                          <a
                            className="text-xs font-semibold text-brand-600 hover:underline"
                            href={apiUrl(`/projects/${project.id}/logs/file?path=${encodeURIComponent(latest.path)}#page=${latest.page ?? 1}`)}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Open
                          </a>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        <section className="rounded-xl border border-gray-200 bg-white">
          <h2 className="border-b border-gray-100 px-5 py-3 text-sm font-bold text-navy-900">
            Open actions{actions.length > 0 && <span className="ml-2 rounded-full bg-amber-50 px-2 py-0.5 text-xs text-amber-800">{actions.length}</span>}
          </h2>
          {actions.length === 0 ? (
            <p className="px-5 py-6 text-sm text-gray-400">Nothing is waiting: every check passed.</p>
          ) : (
            <ul className="divide-y divide-gray-100">
              {actions.map((action) => (
                <li key={`${action.kind}:${action.text}`} className="px-5 py-2.5 text-sm">
                  <div className="flex items-start gap-2">
                    <span
                      className={`mt-1 h-2 w-2 shrink-0 rounded-full ${action.severity === "blocked" ? "bg-red-500" : "bg-amber-500"}`}
                      aria-hidden="true"
                    />
                    <span className="min-w-0">
                      <span className="block text-navy-900">{action.text}</span>
                      <span className="block text-xs text-gray-500">
                        {action.kind}
                        {action.link && (
                          <>
                            {" · "}
                            <Link to={action.link} className="font-semibold text-brand-600 hover:underline">Open</Link>
                          </>
                        )}
                      </span>
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {/* Where the work is done. */}
      <div className="mt-4 flex flex-wrap gap-2">
        <QuickAction to="submittal" label="Material submittals" />
        <QuickAction to="materials" label="Proposed materials" />
        <QuickAction to="boq" label="Open BOQ" />
        <QuickAction to="calculations/battery" label="Battery calculation" />
        <QuickAction to="compliance" label="Compliance" />
        <QuickAction to="logs" label="View logs" />
      </div>

      {/* The detail behind the open actions: every readiness check, and the
          document check that fills the DRF and the Design Sheets. */}
      <div className="mt-4">
        {readiness ? (
          <>
            <ReadinessPanel
              readiness={readiness}
              onCheckDocuments={canEdit ? () => intake.start() : undefined}
              checking={intake.active}
            />
            {intake.job && intake.active && <JobProgress job={intake.job} what="the document check" onCancel={intake.cancel} />}
          </>
        ) : readinessError ? (
          <div role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{readinessError}</div>
        ) : (
          <div className="rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-400">Checking readiness...</div>
        )}
      </div>
    </div>
  );
}

// --- what the board counts ------------------------------------------------------------

type Kind = "submittals" | "drawings" | "samples";

const KINDS: { key: Kind; label: string; short: string; tint: string }[] = [
  { key: "submittals", label: "Material submittals", short: "Submittals", tint: "bg-green-500" },
  { key: "drawings", label: "Shop drawings", short: "Drawings", tint: "bg-brand-500" },
  { key: "samples", label: "Sample boards", short: "Samples", tint: "bg-amber-500" },
];

interface Counts {
  total: number;
  approved: number;
  review: number;
  rejected: number;
}

interface Summary extends Counts {
  documents: LogDocument[];
  bySystem: Map<string, Counts>;
}

/** Which of the three states a document is in, from the status its latest
 * revision carries (app.services.document_control): A and ANN are approved,
 * RR and REJ returned, everything else still under review. */
function bucket(status: string): keyof Omit<Counts, "total"> {
  const value = (status || "").trim().toUpperCase();
  if (value === "A" || value === "ANN" || value.startsWith("APPROVED")) return "approved";
  if (value === "RR" || value === "REJ" || value.startsWith("REJECT")) return "rejected";
  return "review";
}

/** One row per document (its revisions collapsed), counted by where its
 * latest revision stands -- the same grouping the Logs tab shows. */
function summarise(rows: ProjectLogDrawing[] | undefined, voiceEvacuationIntegrated = false): Summary {
  const documents = groupRevisions((rows ?? []).map(directoryRevision), voiceEvacuationIntegrated);
  const summary: Summary = { documents, total: 0, approved: 0, review: 0, rejected: 0, bySystem: new Map() };
  for (const doc of documents) {
    const latest = doc.revisions[0];
    if (!latest) continue;
    const state = bucket(latest.status);
    summary.total += 1;
    summary[state] += 1;
    const code = latest.system ?? "";
    const system = summary.bySystem.get(code) ?? { total: 0, approved: 0, review: 0, rejected: 0 };
    system.total += 1;
    system[state] += 1;
    summary.bySystem.set(code, system);
  }
  return summary;
}

const NOTHING: Counts = { total: 0, approved: 0, review: 0, rejected: 0 };

interface SystemRow extends Counts {
  code: string;
  title: string;
  brand: string | null;
  submittals: Counts;
  drawings: Counts;
  samples: Counts;
}

/** A row per system the project has -- named as the platform names it --
 * plus any system the documents themselves carry. */
function systemRows(
  systems: ProposedMaterials["systems"] | null,
  kinds: Record<Kind, Summary>,
  codes: string[],
): SystemRow[] {
  const known = new Map((systems ?? []).map((s) => [s.code, s]));
  for (const code of codes) if (!known.has(code)) known.set(code, { code, title: code, brand: null });
  for (const kind of KINDS) {
    for (const code of kinds[kind.key].bySystem.keys()) {
      if (code && !known.has(code)) known.set(code, { code, title: code, brand: null });
    }
  }
  return [...known.values()].map((system) => {
    const per = Object.fromEntries(
      KINDS.map((k) => [k.key, kinds[k.key].bySystem.get(system.code) ?? NOTHING]),
    ) as Record<Kind, Counts>;
    const total = KINDS.reduce((sum, k) => sum + per[k.key].total, 0);
    const approved = KINDS.reduce((sum, k) => sum + per[k.key].approved, 0);
    return {
      code: system.code,
      title: system.title,
      brand: system.brand,
      ...per,
      total,
      approved,
      review: KINDS.reduce((sum, k) => sum + per[k.key].review, 0),
      rejected: KINDS.reduce((sum, k) => sum + per[k.key].rejected, 0),
    };
  });
}

/** The documents a project is built on, and whether the platform has them. */
function keyDocuments(
  project: ReturnType<typeof useProject>["project"],
  logs: ProjectLogs | null,
  documents: DocumentStatus | null,
) {
  const sheets = project.design_sheets;
  return [
    {
      label: "Design Sheet",
      detail: sheets.length > 0 ? sheets.map((s) => s.system_code ?? "?").join(", ") : null,
      found: sheets.length > 0,
      missing: "Not found",
    },
    { label: "DRF", detail: null, found: Boolean(project.drf_document_path), missing: "Not found" },
    {
      label: "Material submittals",
      detail: logs ? `${logs.material_submittals.length} filed` : null,
      found: (logs?.material_submittals.length ?? 0) > 0,
      missing: "None filed",
    },
    {
      label: "Shop drawings",
      detail: logs ? `${logs.drawings.length} filed` : null,
      found: (logs?.drawings.length ?? 0) > 0,
      missing: "None filed",
    },
    {
      label: "Sample boards",
      detail: logs ? `${logs.samples.length} filed` : null,
      found: (logs?.samples.length ?? 0) > 0,
      missing: "None filed",
    },
    {
      label: "Document index",
      detail: documents?.synced_at ? `${documents.documents} documents · synced ${formatApiDate(documents.synced_at, "short")}` : null,
      found: Boolean(documents?.synced_at),
      missing: "Not synced",
    },
  ];
}

interface OpenAction {
  kind: string;
  text: string;
  severity: "blocked" | "warning";
  link: string | null;
}

/** What is waiting: a readiness check that is not clear, a submittal the
 * map says is required, a document that changed under something built from
 * it, a file that could not be read. */
function openActions(
  readiness: Readiness | null,
  map: SubmittalMap | null,
  documents: DocumentStatus | null,
): OpenAction[] {
  const actions: OpenAction[] = [];
  for (const action of map?.actions ?? []) {
    actions.push({ kind: "Material submittal", text: action, severity: "warning", link: "submittal" });
  }
  for (const check of readiness?.checks ?? []) {
    if (check.status === "ok") continue;
    actions.push({
      kind: check.label,
      text: check.summary,
      severity: check.status === "blocked" ? "blocked" : "warning",
      link: check.link,
    });
  }
  for (const stale of documents?.stale ?? []) {
    actions.push({ kind: "Source changed", text: `${stale.reason} (${stale.source})`, severity: "warning", link: null });
  }
  for (const failed of documents?.failed ?? []) {
    actions.push({ kind: "Could not be read", text: `${failed.path}: ${failed.error ?? "unknown error"}`, severity: "warning", link: null });
  }
  return actions;
}

function share(part: number, whole: number): number {
  return whole > 0 ? Math.round((part / whole) * 100) : 0;
}

// --- the pieces -----------------------------------------------------------------------

function Fact({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-gray-400">{label}</dt>
      <dd className="mt-0.5 font-semibold text-navy-900">{value || "—"}</dd>
    </div>
  );
}

function Donut({ value, size = 104 }: { value: number; size?: number }) {
  const stroke = 12;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${value}% approved`}>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#e5e7eb" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="#16a34a"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${(circumference * value) / 100} ${circumference}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center text-xl font-bold text-navy-900">{value}%</span>
    </div>
  );
}

function Bar({
  label,
  approved,
  total,
  tint,
  compact = false,
}: {
  label: string;
  approved: number;
  total: number;
  tint: string;
  compact?: boolean;
}) {
  const percent = share(approved, total);
  return (
    <div>
      <div className={`flex items-center justify-between gap-2 ${compact ? "text-xs" : "text-xs"}`}>
        {label && <span className="truncate text-gray-600">{label}</span>}
        <span className="tabular-nums font-semibold text-navy-900">{total > 0 ? `${percent}%` : "—"}</span>
      </div>
      <div className="mt-1 h-1.5 w-full rounded-full bg-gray-100">
        <div className={`h-1.5 rounded-full ${tint}`} style={{ width: `${percent}%` }} />
      </div>
      {!compact && <div className="mt-0.5 text-[11px] text-gray-400">{approved} of {total}</div>}
    </div>
  );
}

function StatCard({ label, tint, summary, onOpen }: { label: string; tint: string; summary: Summary; onOpen: () => void }) {
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-5">
      <div className="flex items-start justify-between gap-2">
        <h2 className="text-sm font-bold text-navy-900">{label}</h2>
        <button onClick={onOpen} className="text-xs font-semibold text-brand-600 hover:underline">Recent →</button>
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="text-3xl font-bold text-navy-900">{summary.total}</span>
        <span className="text-xs text-gray-500">on file</span>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <Count tint="bg-green-500" value={summary.approved} label="Approved" />
        <Count tint="bg-amber-500" value={summary.review} label="Under review" />
        <Count tint="bg-red-500" value={summary.rejected} label="Returned" />
      </div>
      <div className="mt-3 h-1.5 w-full rounded-full bg-gray-100">
        <div className={`h-1.5 rounded-full ${tint}`} style={{ width: `${share(summary.approved, summary.total)}%` }} />
      </div>
    </section>
  );
}

function Count({ tint, value, label }: { tint: string; value: number; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className={`h-2 w-2 rounded-full ${tint}`} aria-hidden="true" />
      <span className="font-semibold tabular-nums text-navy-900">{value}</span>
      <span className="text-gray-500">{label}</span>
    </span>
  );
}

function StatusPill({ status }: { status: string }) {
  const state = bucket(status);
  const tint =
    state === "approved"
      ? "bg-green-100 text-green-700"
      : state === "rejected"
        ? "bg-rose-100 text-rose-700"
        : "bg-amber-100 text-amber-800";
  return <span className={`rounded-md px-2 py-0.5 text-xs font-semibold ${tint}`}>{status || "UR"}</span>;
}

function QuickAction({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-semibold text-navy-900 hover:border-brand-300 hover:text-brand-700"
    >
      {label}
    </Link>
  );
}
