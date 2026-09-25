import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, apiUrl } from "../lib/api";
import { useAuth } from "../context/AuthContext";
import { PROJECT_EDITOR_ROLES } from "../lib/types";
import { SyncDocumentsCard } from "../components/SyncDocumentsCard";
import { RequiredDrawingsTab } from "../components/drawings/RequiredDrawingsTab";
import { UnplacedDrawingsTable, type UnplacedDrawing } from "../components/UnplacedDrawingsTable";
import { useProject } from "./ProjectWorkspace";
import { useOnProjectChange } from "../lib/projectChanges";

type Status =
  | "approved"
  | "approved_as_noted"
  | "under_review"
  | "not_approved"
  | "not_submitted";

interface LogCell {
  status: Status;
  label: string;
  reference?: string | null;
  path?: string | null;
  page?: number;
  floor_named?: string | null;
  remarks?: string | null;
  modified?: string;
}

interface LogRow {
  key: string;
  sheet: string;
  drawing: string;
  ifc_revision: string;
  floor: string;
  title: string;
  floors: number;
  cells: Record<string, LogCell>;
  latest_revision: string | null;
  latest_status: Status;
  remarks: string;
  latest_path: string | null;
  latest_page: number;
}

interface DrawingsLog {
  revisions: string[];
  rows: LogRow[];
  counts: Partial<Record<Status, number>>;
  unplaced: UnplacedDrawing[];
  submissions: number;
  system: string;
  systems: string[];
  ifc: { id: number; filename: string; revision: string }[];
  synced_at: string | null;
  folder: string | null;
  warnings: string[];
}

/** How each status reads: an icon and a label, colour third, so it reads without colour. */
const STATUS: Record<
  Status,
  { label: string; help: string; chip: string; dot: string }
> = {
  approved: {
    label: "Approved",
    help: "Approved by consultant",
    chip: "bg-emerald-50 text-emerald-800 ring-emerald-200",
    dot: "bg-emerald-600",
  },
  approved_as_noted: {
    label: "Approved as Noted",
    help: "Approved with comments to incorporate",
    chip: "bg-cyan-50 text-cyan-800 ring-cyan-200",
    dot: "bg-cyan-600",
  },
  under_review: {
    label: "Under Review",
    help: "Submitted and under review",
    chip: "bg-amber-50 text-amber-800 ring-amber-200",
    dot: "bg-amber-500",
  },
  not_approved: {
    label: "Not Approved",
    help: "Returned with comments",
    chip: "bg-rose-50 text-rose-700 ring-rose-200",
    dot: "bg-rose-600",
  },
  not_submitted: {
    label: "Not Submitted",
    help: "Drawing not yet submitted",
    chip: "bg-slate-100 text-slate-600 ring-slate-200",
    dot: "bg-slate-400",
  },
};

function StatusIcon({
  status,
  className = "h-4 w-4",
}: {
  status: Status;
  className?: string;
}) {
  const common = {
    className,
    viewBox: "0 0 20 20",
    "aria-hidden": true,
  } as const;
  if (status === "approved" || status === "approved_as_noted")
    return (
      <svg {...common}>
        <circle
          cx="10"
          cy="10"
          r="9"
          className={
            status === "approved" ? "fill-emerald-600" : "fill-cyan-600"
          }
        />
        <path
          d="M6 10.5l2.5 2.5L14 7.5"
          fill="none"
          stroke="white"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    );
  if (status === "not_approved")
    return (
      <svg {...common}>
        <circle cx="10" cy="10" r="9" className="fill-rose-600" />
        <path
          d="M7 7l6 6M13 7l-6 6"
          stroke="white"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
    );
  if (status === "under_review")
    return (
      <svg {...common}>
        <circle
          cx="10"
          cy="10"
          r="8"
          fill="none"
          className="stroke-amber-500"
          strokeWidth="2"
        />
        <path
          d="M10 5.5V10l3 2"
          fill="none"
          className="stroke-amber-500"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
    );
  return (
    <svg {...common}>
      <circle cx="10" cy="10" r="6" className="fill-slate-400" />
    </svg>
  );
}

function Chip({ cell }: { cell: LogCell }) {
  const s = STATUS[cell.status];
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${s.chip}`}
      title={
        [
          cell.reference,
          cell.floor_named && `Floor on the drawing: ${cell.floor_named}`,
          cell.remarks,
        ]
          .filter(Boolean)
          .join("\n") || s.help
      }
    >
      <StatusIcon status={cell.status} className="h-3.5 w-3.5" />
      {s.label}
    </span>
  );
}

const EyeIcon = () => (
  <svg
    viewBox="0 0 24 24"
    className="h-5 w-5"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    aria-hidden="true"
  >
    <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);
const FolderIcon = () => (
  <svg
    viewBox="0 0 24 24"
    className="h-5 w-5"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    aria-hidden="true"
  >
    <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
  </svg>
);

const TABS = [
  { key: "log", label: "Drawings Log" },
  { key: "required", label: "Actions Required" },
];

/** The Drawings page. Its first tab, the Drawings Log: every floor plan of
 *  the IFC drawings in force (BOQ > As per IFC Drawings), and where its shop
 *  drawing stands at each revision, read from the submissions and consultant
 *  replies in the project folder (Sync documents). */
export function ProjectDrawingsPage() {
  const { project } = useProject();
  // Reachable by its address even while the tab is locked, so it says the
  // same thing here rather than loading a log of nothing.
  if (project.drawings_in_scope === false) return <NotOurScope />;
  return <DrawingsWorkspace />;
}

/** The DRF's Systems table says no drawing is required on any of this
 * project's systems: we supply and commission it, someone else draws it.
 * Said plainly, because an empty Drawings Log reads as work not started
 * rather than work that is not ours. */
function NotOurScope() {
  return (
    <div className="mx-auto max-w-xl rounded-xl border border-gray-200 bg-white p-6 text-center">
      <h1 className="text-lg font-bold text-navy-900">Drawings are not in our scope</h1>
      <p className="mt-2 text-sm text-gray-600">
        The DRF marks no drawing against any system on this project, so the shop drawings are not ours to produce and
        no drawing folders are made for it.
      </p>
      <p className="mt-3 text-xs text-gray-400">
        If that is wrong, tick the drawing column for the system on Project Info and this tab opens.
      </p>
    </div>
  );
}

function DrawingsWorkspace() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);
  const [log, setLog] = useState<DrawingsLog | null>(null);
  const [error, setError] = useState("");
  const [floor, setFloor] = useState("");
  const [status, setStatus] = useState<Status | "">("");
  const [query, setQuery] = useState("");
  const [notice, setNotice] = useState("");
  // The Drawings Log is the fire alarm's; Actions Required shows the system chosen there.
  const [requiredSystem, setRequiredSystem] = useState("FAS");
  const [tab, setTab] = useState<string>(() => {
    try {
      const saved = localStorage.getItem("drawings.tab");
      return TABS.some((t) => t.key === saved) ? saved! : "log";
    } catch {
      return "log";
    }
  });
  const choose = (key: string) => {
    setTab(key);
    try {
      localStorage.setItem("drawings.tab", key);
    } catch {
      /* storage blocked: the tab is not remembered */
    }
  };

  // Which of the project's systems is on show. The log was fixed to the
  // fire alarm, so a project's emergency lighting drawings were read,
  // indexed, and then never shown anywhere.
  const [system, setSystem] = useState<string | null>(null);

  const load = useCallback(() => {
    setError("");
    const asked = system ? `?system=${encodeURIComponent(system)}` : "";
    api
      .get<DrawingsLog>(`/projects/${project.id}/drawings/log${asked}`)
      .then(setLog)
      .catch((e) =>
        setError(`The drawings log could not be loaded: ${e.message}`),
      );
  }, [project.id, system]);

  useEffect(() => {
    load();
  }, [load]);

  // The shop drawings are read from the document index: a folder sync --
  // by this page, another, or the worker -- reloads the log here.
  useOnProjectChange(["documents", "drawing"], load);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (log?.rows ?? []).filter(
      (r) =>
        (!floor || r.key === floor) &&
        (!status || r.latest_status === status) &&
        (!q ||
          r.floor.toLowerCase().includes(q) ||
          r.sheet.toLowerCase().includes(q) ||
          r.remarks.toLowerCase().includes(q)),
    );
  }, [log, floor, status, query]);

  const openFolder = async (path: string | null) => {
    setNotice("");
    try {
      await api.post(`/projects/${project.id}/drawings/open-folder`, { path });
    } catch (e) {
      setNotice((e as Error).message);
    }
  };

  const shownSystem = log?.system ?? "FAS";

  const exportLog = () =>
    api
      .download(
        `/projects/${project.id}/drawings/log/export.xlsx?system=${encodeURIComponent(shownSystem)}`,
        `EP-${project.ep_number} Drawings Log ${shownSystem}.xlsx`,
      )
      .catch((e) => setError(e.message));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">Drawings</h1>
          <p className="mt-2 text-sm text-gray-500">
            Manage project drawings and track revision status per floor.
          </p>
        </div>
        <div className="grid grid-cols-3 divide-x divide-gray-200 rounded-xl border border-gray-200 bg-white text-sm">
          <div className="px-5 py-3">
            <div className="text-gray-500">Project</div>
            <div className="mt-1 font-semibold text-navy-900">
              {project.project_name}
            </div>
          </div>
          <div className="px-5 py-3">
            <div className="text-gray-500">Project No.</div>
            <div className="mt-1 font-semibold text-navy-900">
              EP-{project.ep_number}
            </div>
          </div>
          <div className="px-5 py-3">
            <div className="text-gray-500">System</div>
            <div
              className="mt-1 font-semibold text-navy-900"
              title="Fire alarm, as the BOQ as per IFC. Emergency lighting comes with it."
            >
              {tab === "required" ? requiredSystem : (log?.system ?? "FAS")}
            </div>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap gap-1 rounded-xl border border-gray-200 bg-white p-1">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => choose(t.key)}
            aria-pressed={t.key === tab}
            className={`flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold ${
              t.key === tab ? "bg-brand-50 text-brand-700" : "text-gray-600 hover:bg-gray-50"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "required" ? (
        <RequiredDrawingsTab projectId={project.id} canEdit={canEdit} onSystem={setRequiredSystem} />
      ) : (
        <>
          <SyncDocumentsCard
            projectId={project.id}
            canEdit={canEdit}
            compact
            onSynced={load}
          />

          {error && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm text-rose-800">
              {error}
            </div>
          )}
          {notice && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-900">
              {notice}
            </div>
          )}
          {/* The notes about files the sync could not read in full are File
              Sync's (the sync card above links there), not a banner each here. */}

          <div className="rounded-xl border border-gray-200 bg-white">
            <div className="flex flex-wrap items-end justify-between gap-3 border-b border-gray-100 p-5">
              <div>
                <h2 className="text-xl font-bold">Drawings Log</h2>
                <p className="mt-1 text-sm text-gray-500">
                  Revision status per floor (lowest floor first). Floors from{" "}
                  {log?.ifc.length ? (
                    log.ifc.map((d) => `${d.filename} ${d.revision}`).join(", ")
                  ) : (
                    <>the BOQ as per IFC drawings</>
                  )}
                  ; statuses from the shop drawings in{" "}
                  {log?.folder ?? "the project folder"}.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={floor}
                  onChange={(e) => setFloor(e.target.value)}
                  className="rounded-lg border border-gray-300 px-3 py-2 text-sm"
                  aria-label="Floor"
                >
                  <option value="">All Floors</option>
                  {(log?.rows ?? []).map((r) => (
                    <option key={r.key} value={r.key}>
                      {r.floor}
                    </option>
                  ))}
                </select>
                <select
                  value={status}
                  onChange={(e) => setStatus(e.target.value as Status | "")}
                  className="rounded-lg border border-gray-300 px-3 py-2 text-sm"
                  aria-label="Status"
                >
                  <option value="">All Status</option>
                  {(Object.keys(STATUS) as Status[]).map((s) => (
                    <option key={s} value={s}>
                      {STATUS[s].label}
                      {log?.counts[s] ? ` (${log.counts[s]})` : ""}
                    </option>
                  ))}
                </select>
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search floor…"
                  className="w-48 rounded-lg border border-gray-300 px-3 py-2 text-sm"
                  aria-label="Search floor"
                />
                <button
                  type="button"
                  onClick={exportLog}
                  disabled={!log?.rows.length}
                  className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-50"
                >
                  Export
                </button>
              </div>
            </div>

            {log === null ? (
              <div className="p-6 text-sm text-gray-500">
                {error ? "" : "Loading the drawings log…"}
              </div>
            ) : log.rows.length === 0 ? (
              <div className="p-6 text-sm text-gray-600">
                The floors come from the IFC drawings: import the fire alarm IFC
                drawing on{" "}
                <Link
                  to={`/projects/${project.id}/boq`}
                  className="font-medium text-brand-700 hover:underline"
                >
                  BOQ &gt; As per IFC Drawings
                </Link>{" "}
                first.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead className="border-b border-gray-200 bg-gray-50 text-left text-sm font-semibold text-navy-900">
                    <tr>
                      <th className="px-4 py-3">#</th>
                      <th className="px-4 py-3">Floor</th>
                      {log.revisions.map((r) => (
                        <th key={r} className="px-4 py-3 text-center">
                          {r}
                        </th>
                      ))}
                      <th className="px-4 py-3 text-center">Latest Revision</th>
                      <th className="px-4 py-3">Remarks / Notes</th>
                      <th className="px-4 py-3 text-center">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {rows.map((r) => (
                      <tr key={r.key} className="hover:bg-gray-50">
                        <td className="px-4 py-2.5 text-gray-500">
                          {log.rows.indexOf(r) + 1}
                        </td>
                        <td className="px-4 py-2.5">
                          <div className="font-medium text-navy-900">
                            {r.floor}
                          </div>
                          <div className="text-xs text-gray-500">
                            {r.sheet}
                            {r.floors > 1 && ` · ${r.floors} floors`}
                          </div>
                        </td>
                        {log.revisions.map((rev) => (
                          <td key={rev} className="px-3 py-2.5 text-center">
                            <Chip cell={r.cells[rev]} />
                          </td>
                        ))}
                        <td className="px-4 py-2.5 text-center font-medium">
                          {r.latest_revision ?? "–"}
                        </td>
                        <td className="max-w-xs px-4 py-2.5 text-gray-700">
                          <div className="line-clamp-2" title={r.remarks}>
                            {r.remarks || "–"}
                          </div>
                        </td>
                        <td className="px-4 py-2.5">
                          <div className="flex items-center justify-center gap-3 text-brand-600">
                            {r.latest_path ? (
                              <a
                                href={apiUrl(
                                  `/projects/${project.id}/logs/file?path=${encodeURIComponent(r.latest_path)}#page=${r.latest_page}`,
                                )}
                                target="_blank"
                                rel="noreferrer"
                                title={`View ${r.latest_revision} drawing`}
                                className="hover:text-brand-800"
                              >
                                <EyeIcon />
                              </a>
                            ) : (
                              <span
                                className="text-gray-300"
                                title="Nothing submitted yet"
                              >
                                <EyeIcon />
                              </span>
                            )}
                            <button
                              type="button"
                              onClick={() => openFolder(r.latest_path)}
                              disabled={!log.folder}
                              title={
                                r.latest_path
                                  ? "Open the folder of the latest submission"
                                  : `Open ${log.folder ?? "the shop drawings folder"}`
                              }
                              className="hover:text-brand-800 disabled:text-gray-300"
                            >
                              <FolderIcon />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                    {rows.length === 0 && (
                      <tr>
                        <td
                          colSpan={log.revisions.length + 5}
                          className="px-4 py-6 text-center text-gray-500"
                        >
                          No floor matches the filter.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
                <div className="border-t border-gray-100 px-5 py-3 text-sm text-gray-500">
                  Showing {rows.length} of {log.rows.length} floor plans
                  {log.submissions === 0 &&
                    " · no fire alarm shop drawing has been submitted yet"}
                </div>
              </div>
            )}

            {log && log.systems.length > 1 && (
              <div className="flex flex-wrap items-center gap-2 border-t border-gray-100 px-5 pt-4">
                <span className="text-xs font-medium text-gray-500">System</span>
                {log.systems.map((code) => (
                  <button
                    key={code}
                    type="button"
                    onClick={() => setSystem(code)}
                    aria-pressed={code === log.system}
                    className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${
                      code === log.system
                        ? "bg-brand-600 text-white"
                        : "border border-gray-300 bg-white text-navy-900 hover:bg-gray-50"
                    }`}
                  >
                    {code}
                  </button>
                ))}
              </div>
            )}

            {log && log.unplaced.length > 0 && (
              <UnplacedDrawingsTable
                rows={log.unplaced}
                fileHref={(path, page) =>
                  apiUrl(
                    `/projects/${project.id}/logs/file?path=${encodeURIComponent(path)}#page=${page}`,
                  )
                }
              />
            )}

            <div className="flex flex-wrap items-center gap-x-8 gap-y-3 border-t border-gray-200 bg-gray-50/60 px-5 py-4 text-sm">
              {(Object.keys(STATUS) as Status[]).map((s) => (
                <div key={s} className="flex items-center gap-2">
                  <StatusIcon status={s} className="h-6 w-6" />
                  <div>
                    <div className="font-medium text-navy-900">
                      {STATUS[s].label}
                    </div>
                    <div className="text-xs text-gray-500">
                      {STATUS[s].help}
                    </div>
                  </div>
                </div>
              ))}
              <div className="ml-auto flex items-center gap-5 text-brand-600">
                <span className="flex items-center gap-1.5">
                  <EyeIcon />{" "}
                  <span className="text-gray-600">View Drawing</span>
                </span>
                <span className="flex items-center gap-1.5">
                  <FolderIcon />{" "}
                  <span className="text-gray-600">Open Folder</span>
                </span>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
