import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useAuth } from "../context/AuthContext";
import { ApiError, api } from "../lib/api";
import { formatApiDate } from "../lib/format";
import type { FileSyncFile, FileSyncStatus, FileSyncSummary } from "../lib/types";
import { PROJECT_EDITOR_ROLES } from "../lib/types";
import { useJob, type Job } from "../lib/useJob";
import { useProject } from "./ProjectWorkspace";

/** File Sync: every file of the project, read from its OneDrive folder, and
 * what the last sync did with each. The page stays a summary -- counts per
 * status -- and says which files and why only when a category is opened.
 * Warnings about files live here and nowhere else. */

const STATUSES: FileSyncStatus[] = ["processed", "unchanged", "partial", "unavailable", "failed"];

const STYLE: Record<FileSyncStatus, {
  label: string; title: string; description: string; text: string; tile: string; chip: string; hint?: string;
}> = {
  processed: {
    label: "Processed", title: "Processed files", hint: "New/Updated",
    description: "Files successfully processed and added/updated in the platform.",
    text: "text-green-700", tile: "bg-green-50 text-green-700", chip: "bg-green-50 text-green-700 ring-green-200",
  },
  unchanged: {
    label: "Unchanged", title: "Unchanged files", hint: "No changes",
    description: "Files already up to date, no changes detected.",
    text: "text-gray-700", tile: "bg-gray-100 text-gray-500", chip: "bg-gray-100 text-gray-600 ring-gray-200",
  },
  partial: {
    label: "Partial", title: "Partially processed files",
    description: "Only part of the document was analyzed (e.g. specific pages for consultant replies).",
    text: "text-amber-500", tile: "bg-amber-50 text-amber-500", chip: "bg-amber-50 text-amber-700 ring-amber-200",
  },
  unavailable: {
    label: "Unavailable", title: "Unavailable files",
    description: "These files are online-only in OneDrive and could not be processed. Make the project folder available offline, then retry.",
    text: "text-red-600", tile: "bg-red-50 text-red-600", chip: "bg-red-50 text-red-700 ring-red-200",
  },
  failed: {
    label: "Failed", title: "Failed files",
    description: "Processing produced an error. The previous reading, if any, is kept.",
    text: "text-red-700", tile: "bg-red-50 text-red-700", chip: "bg-red-100 text-red-800 ring-red-300",
  },
};

export function ProjectFileSyncPage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);
  const [summary, setSummary] = useState<FileSyncSummary | null>(null);
  const [lastJob, setLastJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cardOpen, setCardOpen] = useState(true);
  const [openCategory, setOpenCategory] = useState<FileSyncStatus | null>(null);
  const [epoch, setEpoch] = useState(0);
  const categoriesRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const [next, recent] = await Promise.all([
        api.get<FileSyncSummary>(`/projects/${project.id}/documents/sync-summary`),
        api.get<Job[]>(`/projects/${project.id}/jobs`),
      ]);
      setSummary(next);
      setLastJob(recent.find((job) => job.kind === "sync_documents") ?? null);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not read the sync status");
    }
  }, [project.id]);

  const sync = useJob(project.id, "sync_documents", `/projects/${project.id}/jobs/sync-documents`, () => {
    setEpoch((n) => n + 1);
    void load();
  });
  const { active, follow } = sync;

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (summary?.job && !active) follow(summary.job);
  }, [summary, active, follow]);

  // While a sync waits for the worker, look again now and then: whether a
  // worker is running to take it can change.
  const queued = sync.job?.status === "queued";
  useEffect(() => {
    if (!queued) return;
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [queued, load]);

  const showCategory = (status: FileSyncStatus) => {
    setCardOpen(true);
    setOpenCategory(status);
    window.setTimeout(() => categoriesRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };

  const job = sync.job ?? lastJob;
  // The last sync failed or was stopped after the last one that finished.
  const lastEndedBadly = job && (job.status === "failed" || job.status === "cancelled") &&
    (!summary?.synced_at || (job.finished_at ?? "") > summary.synced_at);
  const reachable = Boolean(summary?.folder);

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-navy-900">File Sync</h1>
          <p className="mt-1 text-sm text-gray-500">
            Sync project files from OneDrive and process all documents for use across the platform.
          </p>
        </div>
        {canEdit && (
          <button
            type="button"
            onClick={() => void sync.start()}
            disabled={active || !reachable}
            className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 disabled:opacity-60"
            title={reachable ? "Read new and changed files; unchanged files are skipped" : "The project has no folder to sync"}
          >
            <SyncIcon spinning={active} />
            {active ? "Syncing..." : "Sync Files"}
          </button>
        )}
      </header>

      {error && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {sync.error && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{sync.error}</div>}

      {!summary ? (
        !error && <div className="text-sm text-gray-400">Loading...</div>
      ) : (
        <>
          <SummaryStrip summary={summary} />

          {active && sync.job ? (
            <RunningCard job={sync.job} workerRunning={summary.worker_running} onStop={canEdit ? sync.cancel : undefined} />
          ) : lastEndedBadly && job ? (
            <EndedCard job={job} />
          ) : null}

          {summary.synced_at ? (
            <section className="overflow-hidden rounded-xl border border-green-200 bg-white">
              <div className="flex flex-wrap items-center gap-4 bg-green-50/60 p-5">
                <div className="flex min-w-0 flex-1 items-center gap-4">
                  <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-green-600 text-white">
                    <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true">
                      <path d="M5 12.5l4.5 4.5L19 7.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </span>
                  <div className="min-w-0">
                    <div className="text-lg font-bold text-green-800">Sync completed</div>
                    <div className="text-sm text-gray-600">Project files have been synchronized and processed.</div>
                  </div>
                </div>
                <div className="grid w-full grid-cols-2 gap-y-3 sm:flex sm:w-auto sm:items-stretch sm:divide-x sm:divide-gray-200">
                  {STATUSES.filter((s) => s !== "failed" || summary.counts.failed > 0).map((status) => (
                    <Counter key={status} status={status} count={summary.counts[status]}
                      onDetails={status === "processed" || status === "unchanged" ? undefined : () => showCategory(status)} />
                  ))}
                </div>
                <button
                  type="button"
                  onClick={() => setCardOpen((open) => !open)}
                  aria-expanded={cardOpen}
                  aria-label={cardOpen ? "Collapse the sync result" : "Expand the sync result"}
                  className="rounded-lg border border-gray-200 bg-white p-2 text-gray-500 hover:bg-gray-50"
                >
                  <Chevron up={cardOpen} />
                </button>
              </div>
              {cardOpen && (
                <div ref={categoriesRef} className="space-y-2 border-t border-green-100 p-4">
                  {STATUSES.filter((s) => s !== "failed" || summary.counts.failed > 0).map((status) => (
                    <Category
                      key={status}
                      projectId={project.id}
                      status={status}
                      count={summary.counts[status]}
                      open={openCategory === status}
                      epoch={epoch}
                      onToggle={() => setOpenCategory((current) => (current === status ? null : status))}
                    />
                  ))}
                </div>
              )}
            </section>
          ) : (
            !active && (
              <section className="rounded-xl border border-gray-200 bg-white p-6 text-center">
                <div className="font-semibold text-navy-900">Not synced yet</div>
                <p className="mt-1 text-sm text-gray-500">
                  {reachable
                    ? "The project folder has not been read yet. Sync Files reads every document in it."
                    : "The project has no folder to sync: set it on Project Info."}
                </p>
              </section>
            )
          )}

          {summary.synced_at && <SyncInformation summary={summary} />}
          {summary.synced_at && <FileDetails projectId={project.id} total={summary.total} epoch={epoch} />}
        </>
      )}
    </div>
  );
}

// --- the summary --------------------------------------------------------------------------

function duration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds} sec`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${seconds % 60} sec`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function byWhom(summary: FileSyncSummary): string | null {
  if (summary.started_by) return `by ${summary.started_by}`;
  return summary.automatic ? "Automatic" : null;
}

function SummaryStrip({ summary }: { summary: FileSyncSummary }) {
  return (
    <section className="grid grid-cols-1 divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-4 lg:divide-x">
      <Fact label="Source" icon={<CloudIcon />} value={summary.source}
        sub={summary.folder_display ?? "No folder set"} title={summary.folder ?? undefined} />
      <Fact label="Last Sync" icon={<CalendarIcon />}
        value={summary.synced_at ? formatApiDate(summary.synced_at, "medium") : "Never"} sub={byWhom(summary)} />
      <Fact label="Duration" icon={<ClockIcon />} value={duration(summary.duration_s)} />
      <Fact label="Total Files Found" icon={<FileIcon />} value={String(summary.total)} sub="All project documents" />
    </section>
  );
}

function Fact({ label, icon, value, sub, title }: {
  label: string; icon: ReactNode; value: string; sub?: string | null; title?: string;
}) {
  return (
    <div className="min-w-0 p-4">
      <div className="text-xs font-medium text-gray-500">{label}</div>
      <div className="mt-1.5 flex items-start gap-3">
        <span className="mt-0.5 shrink-0 text-gray-400">{icon}</span>
        <div className="min-w-0">
          <div className="font-semibold text-navy-900">{value}</div>
          {sub && <div className="truncate text-xs text-gray-500" title={title ?? sub}>{sub}</div>}
        </div>
      </div>
    </div>
  );
}

function Counter({ status, count, onDetails }: { status: FileSyncStatus; count: number; onDetails?: () => void }) {
  const style = STYLE[status];
  const quiet = status === "processed" || status === "unchanged";
  return (
    <div className="min-w-24 px-4 py-1 text-center">
      <div className={`text-2xl font-bold ${style.text}`}>{count}</div>
      <div className={`text-sm font-semibold ${style.text}`}>{style.label}</div>
      {quiet ? (
        <div className="text-xs text-gray-500">{style.hint}</div>
      ) : count > 0 && onDetails ? (
        <button type="button" onClick={onDetails} className={`text-xs font-medium hover:underline ${style.text}`}>
          See details
        </button>
      ) : (
        <div className="text-xs text-gray-400">None</div>
      )}
    </div>
  );
}

function SyncInformation({ summary }: { summary: FileSyncSummary }) {
  const cells: [string, ReactNode][] = [
    ["Source", <span key="s" title={summary.folder ?? undefined}>{summary.source}<span className="block truncate text-xs font-normal text-gray-500">{summary.folder_display}</span></span>],
    ["Last Sync", <span key="l">{formatApiDate(summary.synced_at, "medium")}<span className="block text-xs font-normal text-gray-500">{byWhom(summary)}</span></span>],
    ["Duration", duration(summary.duration_s)],
    ["Files Found", summary.total],
    ["Files Processed", summary.counts.processed],
    ["Files Unchanged", summary.counts.unchanged],
    ["Files Partial", <span key="p" className="text-amber-500">{summary.counts.partial}</span>],
    ["Files Unavailable", <span key="u" className="text-red-600">{summary.counts.unavailable}</span>],
  ];
  if (summary.counts.failed > 0) cells.push(["Files Failed", <span key="f" className="text-red-700">{summary.counts.failed}</span>]);
  if (summary.removed > 0) cells.push(["Removed from folder", summary.removed]);
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-4">
      <h2 className="flex items-center gap-2 text-sm font-bold text-navy-900">
        <InfoIcon /> Sync Information
      </h2>
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4 xl:grid-cols-8">
        {cells.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-xs text-gray-500">{label}</dt>
            <dd className="mt-0.5 text-sm font-semibold text-navy-900">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

// --- a sync in progress, or one that did not finish --------------------------------------

function RunningCard({ job, workerRunning, onStop }: { job: Job; workerRunning: boolean; onStop?: () => void }) {
  const total = job.progress.total ?? 0;
  const done = job.progress.done ?? 0;
  const percent = total > 0 ? Math.min(100, Math.round((100 * done) / total)) : null;
  const waiting = job.status === "queued";
  return (
    <section className="rounded-xl border border-brand-200 bg-brand-50/50 p-5" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="text-brand-600"><SyncIcon spinning /></span>
          <div>
            <div className="font-bold text-navy-900">
              {job.cancel_requested ? "Stopping the sync..." : waiting ? "Sync queued" : "Syncing in the background"}
            </div>
            <div className="text-sm text-gray-600">
              {waiting && !workerRunning
                ? "Waiting: the background worker is not running on this PC. Start the platform with start.bat."
                : "Keep working: every page shows the current files until the sync finishes."}
            </div>
          </div>
        </div>
        {onStop && !job.cancel_requested && (
          <button type="button" onClick={onStop}
            className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-navy-900 hover:bg-gray-50">
            Stop
          </button>
        )}
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-white" role="progressbar" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={percent ?? undefined} aria-label="Sync progress">
        <div className={`h-full bg-brand-600 transition-all ${percent === null ? "w-1/3 animate-pulse" : ""}`}
          style={percent === null ? undefined : { width: `${percent}%` }} />
      </div>
      <div className="mt-1.5 truncate text-xs text-gray-500">{job.progress.message}</div>
    </section>
  );
}

function EndedCard({ job }: { job: Job }) {
  const stopped = job.status === "cancelled";
  return (
    <section className={`rounded-xl border p-4 text-sm ${stopped ? "border-gray-200 bg-gray-50 text-gray-700" : "border-red-200 bg-red-50 text-red-800"}`}>
      <span className="font-semibold">{stopped ? "The last sync was stopped" : "The last sync failed"}</span>
      {job.finished_at && <span> on {formatApiDate(job.finished_at, "medium")}</span>}
      {!stopped && job.error && <span>: {job.error}</span>}
      <span>. The results below are from the last sync that finished.</span>
    </section>
  );
}

// --- the files ------------------------------------------------------------------------------

function useFiles(projectId: number, status: FileSyncStatus | null, enabled: boolean, epoch: number) {
  const [files, setFiles] = useState<FileSyncFile[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    api
      .get<FileSyncFile[]>(`/projects/${projectId}/documents/sync-files${status ? `?status=${status}` : ""}`)
      .then((got) => {
        if (!cancelled) setFiles(got);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not list the files");
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, status, enabled, epoch]);
  return { files, error };
}

function Category({ projectId, status, count, open, epoch, onToggle }: {
  projectId: number; status: FileSyncStatus; count: number; open: boolean; epoch: number; onToggle: () => void;
}) {
  const style = STYLE[status];
  const { files, error } = useFiles(projectId, status, open, epoch);
  return (
    <div className="rounded-lg border border-gray-200">
      <button type="button" onClick={onToggle} aria-expanded={open}
        className="flex w-full items-center gap-4 p-3 text-left hover:bg-gray-50">
        <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${style.tile}`}>
          <StatusIcon status={status} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block font-semibold text-navy-900">{style.title}</span>
          <span className="block text-sm text-gray-500">{style.description}</span>
        </span>
        <span className={`text-lg font-bold ${style.text}`}>{count}</span>
        <span className="rounded-md border border-gray-200 p-1.5 text-gray-500"><Chevron up={open} /></span>
      </button>
      {open && (
        <div className="border-t border-gray-100 p-3">
          {error ? (
            <div className="text-sm text-red-700">{error}</div>
          ) : files === null ? (
            <div className="text-sm text-gray-400">Loading...</div>
          ) : files.length === 0 ? (
            <div className="text-sm text-gray-500">No files.</div>
          ) : (
            <FileTable files={files} showStatus={false} />
          )}
        </div>
      )}
    </div>
  );
}

function FileTable({ files, showStatus }: { files: FileSyncFile[]; showStatus: boolean }) {
  const [limit, setLimit] = useState(100);
  const shown = files.slice(0, limit);
  return (
    <div>
      <div className="max-h-[28rem] overflow-auto rounded-md border border-gray-100">
        <table className="w-full min-w-[40rem] text-left text-sm">
          <thead className="sticky top-0 bg-gray-50 text-xs text-gray-500">
            <tr>
              <th className="px-3 py-2 font-semibold">File name</th>
              <th className="px-3 py-2 font-semibold">Path</th>
              <th className="px-3 py-2 font-semibold">Status</th>
              <th className="px-3 py-2 font-semibold">Reason</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {shown.map((file) => (
              <tr key={file.path}>
                <td className="max-w-72 truncate px-3 py-2 font-medium text-navy-900" title={file.name}>{file.name}</td>
                <td className="max-w-80 truncate px-3 py-2 text-xs text-gray-500" title={file.path}>{file.path}</td>
                <td className="px-3 py-2"><StatusChip status={file.status} /></td>
                <td className="px-3 py-2 text-xs text-gray-600">{file.reason ?? (showStatus ? "" : "—")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {files.length > limit && (
        <button type="button" onClick={() => setLimit((n) => n + 200)}
          className="mt-2 text-xs font-semibold text-brand-600 hover:underline">
          Show more ({files.length - limit} not shown)
        </button>
      )}
    </div>
  );
}

function StatusChip({ status }: { status: FileSyncStatus }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${STYLE[status].chip}`}>
      {STYLE[status].label}
    </span>
  );
}

function FileDetails({ projectId, total, epoch }: { projectId: number; total: number; epoch: number }) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<FileSyncStatus | "all">("all");
  const [search, setSearch] = useState("");
  const { files, error } = useFiles(projectId, null, open, epoch);
  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (files ?? []).filter((file) => (filter === "all" || file.status === filter) &&
      (!needle || `${file.name} ${file.path}`.toLowerCase().includes(needle)));
  }, [files, filter, search]);
  return (
    <section className="rounded-xl border border-gray-200 bg-white">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="flex w-full items-center gap-3 p-4 text-left hover:bg-gray-50">
        <span className="text-gray-400"><ListIcon /></span>
        <span className="min-w-0 flex-1">
          <span className="block font-bold text-navy-900">File Details ({total})</span>
          <span className="block text-sm text-gray-500">
            List of all files with their sync status. Expand a category above or use filters to view specific files.
          </span>
        </span>
        <span className="rounded-md border border-gray-200 p-1.5 text-gray-500"><Chevron up={open} /></span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-gray-100 p-4">
          <div className="flex flex-wrap items-center gap-2">
            {(["all", ...STATUSES] as const).map((value) => (
              <button key={value} type="button" onClick={() => setFilter(value)} aria-pressed={filter === value}
                className={`rounded-full px-3 py-1 text-xs font-semibold ${filter === value ? "bg-brand-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}>
                {value === "all" ? "All" : STYLE[value].label}
              </button>
            ))}
            <input aria-label="Search files" value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search files..." className="input ml-auto w-full sm:w-64" />
          </div>
          {error ? (
            <div className="text-sm text-red-700">{error}</div>
          ) : files === null ? (
            <div className="text-sm text-gray-400">Loading...</div>
          ) : visible.length === 0 ? (
            <div className="text-sm text-gray-500">No files match.</div>
          ) : (
            <FileTable files={visible} showStatus />
          )}
        </div>
      )}
    </section>
  );
}

// --- icons ------------------------------------------------------------------------------------

function Svg({ children, className = "h-5 w-5" }: { children: ReactNode; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  );
}

function SyncIcon({ spinning = false }: { spinning?: boolean }) {
  return (
    <Svg className={`h-5 w-5 ${spinning ? "animate-spin" : ""}`}>
      <path d="M20 11a8 8 0 0 0-14.3-4.9L4 8" /><path d="M4 4v4h4" />
      <path d="M4 13a8 8 0 0 0 14.3 4.9L20 16" /><path d="M20 20v-4h-4" />
    </Svg>
  );
}

function CloudIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-6 w-6 text-sky-500" fill="currentColor" aria-hidden="true">
      <path d="M7 18h10.5a4 4 0 0 0 .6-7.95A5.5 5.5 0 0 0 7.6 8.6 4.7 4.7 0 0 0 7 18z" />
    </svg>
  );
}

function CalendarIcon() {
  return <Svg><rect x="4" y="5" width="16" height="15" rx="2" /><path d="M8 3v4M16 3v4M4 10h16" /></Svg>;
}

function ClockIcon() {
  return <Svg><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></Svg>;
}

function FileIcon() {
  return <Svg><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></Svg>;
}

function InfoIcon() {
  return <span className="text-brand-600"><Svg><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></Svg></span>;
}

function ListIcon() {
  return <Svg><path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01" /></Svg>;
}

function Chevron({ up }: { up: boolean }) {
  return <Svg className="h-4 w-4"><path d={up ? "M6 15l6-6 6 6" : "M6 9l6 6 6-6"} /></Svg>;
}

function StatusIcon({ status }: { status: FileSyncStatus }) {
  switch (status) {
    case "processed":
      return <Svg><circle cx="12" cy="12" r="9" /><path d="M8 12.5l2.7 2.7L16 9.8" /></Svg>;
    case "unchanged":
      return <FileIcon />;
    case "partial":
      return <Svg><path d="M12 3.5l9.5 16.5h-19z" /><path d="M12 10v4.5M12 17.5h.01" /></Svg>;
    case "unavailable":
      return <Svg><path d="M7 18h10.5a4 4 0 0 0 .6-7.95A5.5 5.5 0 0 0 7.6 8.6 4.7 4.7 0 0 0 7 18z" /><path d="M4 4l16 16" /></Svg>;
    case "failed":
      return <Svg><circle cx="12" cy="12" r="9" /><path d="M9 9l6 6M15 9l-6 6" /></Svg>;
  }
}
