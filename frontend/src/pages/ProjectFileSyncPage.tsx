import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useAuth } from "../context/AuthContext";
import { ApiError, api, apiUrl } from "../lib/api";
import { formatApiDate } from "../lib/format";
import type {
  ClassificationMetrics, ClassificationRow, ClassificationStage, FileSyncFile, FileSyncProcessing, FileSyncStatus, FileSyncSummary,
} from "../lib/types";
import { PROJECT_EDITOR_ROLES } from "../lib/types";
import { useJob, type Job } from "../lib/useJob";
import { useProject } from "./ProjectWorkspace";

/** File Sync: two different things, shown as two.
 *
 *  1. The file sync -- the index. Every file of the project's OneDrive
 *     folder is found and recorded (a stat each, nothing opened): seconds.
 *     Once it has run, the platform knows what exists and the project is
 *     synced, whatever is still to be read.
 *  2. Document processing -- the reading. New and changed documents are
 *     read in the background by the document worker, one at a time as
 *     each finishes, and the pages fill in progressively. The engineer
 *     keeps working meanwhile; leaving this page interrupts nothing.
 *
 * The page stays a summary -- counts per status -- and says which files and
 * why only when a category is opened. Warnings about files live here and
 * nowhere else. */

const STATUSES: FileSyncStatus[] = ["processed", "unchanged", "pending", "partial", "unavailable", "failed"];

const STYLE: Record<FileSyncStatus, {
  label: string; title: string; description: string; text: string; tile: string; chip: string; hint?: string;
}> = {
  processed: {
    label: "Processed", title: "Processed files", hint: "Read since the last sync",
    description: "Documents read since the last file sync and added/updated in the platform.",
    text: "text-green-700", tile: "bg-green-50 text-green-700", chip: "bg-green-50 text-green-700 ring-green-200",
  },
  unchanged: {
    label: "Unchanged", title: "Unchanged files", hint: "No changes",
    description: "Files already up to date, no changes detected.",
    text: "text-gray-700", tile: "bg-gray-100 text-gray-500", chip: "bg-gray-100 text-gray-600 ring-gray-200",
  },
  pending: {
    label: "Pending", title: "Pending files", hint: "Waiting to be read",
    description: "Discovered by the file sync; their content is still to be read by document processing.",
    text: "text-sky-700", tile: "bg-sky-50 text-sky-700", chip: "bg-sky-50 text-sky-700 ring-sky-200",
  },
  partial: {
    label: "Partial", title: "Partially processed files",
    description: "Only part of the document was analyzed (e.g. specific pages for consultant replies).",
    text: "text-amber-500", tile: "bg-amber-50 text-amber-500", chip: "bg-amber-50 text-amber-700 ring-amber-200",
  },
  unavailable: {
    label: "Unavailable", title: "Unavailable files",
    description: "These files are online-only in OneDrive and could not be processed. Make the project folder available offline, then process the remaining documents.",
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
  const [lastSyncJob, setLastSyncJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
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
      setLastSyncJob(recent.find((job) => job.kind === "sync_documents") ?? null);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not read the sync status");
    }
  }, [project.id]);

  const refresh = useCallback(() => {
    setEpoch((n) => n + 1);
    void load();
  }, [load]);

  const sync = useJob(project.id, "sync_documents", `/projects/${project.id}/jobs/sync-documents`, refresh);
  const processing = useJob(project.id, "process_documents", `/projects/${project.id}/jobs/process-documents`, refresh);
  const { active: syncActive, follow: followSync } = sync;
  const { active: processingActive, follow: followProcessing } = processing;

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (summary?.job && !syncActive) followSync(summary.job);
  }, [summary, syncActive, followSync]);
  useEffect(() => {
    if (summary?.processing.job && !processingActive) followProcessing(summary.processing.job);
  }, [summary, processingActive, followProcessing]);

  // While a job waits for its worker, or documents are being read, look at
  // the summary again now and then: the counts move file by file, and
  // whether a worker is running to take a queued job can change.
  const busy = syncActive || processingActive;
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [busy, load]);

  const showCategory = (status: FileSyncStatus) => {
    setCardOpen(true);
    setOpenCategory(status);
    window.setTimeout(() => categoriesRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };

  const processRemaining = async (retryFailed: boolean) => {
    setNotice(null);
    try {
      const job = await api.post<Job>(`/projects/${project.id}/jobs/process-documents`, { retry_failed: retryFailed });
      processing.follow(job);
      void load();
    } catch (err) {
      if (err instanceof ApiError && err.code === "job_running" && typeof err.detail?.job_id === "number") {
        processing.follow(await api.get<Job>(`/jobs/${err.detail.job_id}`));
        return;
      }
      setNotice(err instanceof ApiError ? err.message : "Document processing could not be started");
    }
  };

  const syncJob = sync.job ?? lastSyncJob;
  // The last file sync failed or was stopped after the last one that finished.
  const syncEndedBadly = syncJob && (syncJob.status === "failed" || syncJob.status === "cancelled") &&
    (!summary?.synced_at || (syncJob.finished_at ?? "") > summary.synced_at);
  const reachable = Boolean(summary?.folder);

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-navy-900">File Sync</h1>
          <p className="mt-1 text-sm text-gray-500">
            Find the project's files on OneDrive in seconds; their documents are then read in the background while you work.
          </p>
        </div>
        {canEdit && (
          <div className="flex flex-wrap items-center gap-2">
            {summary && summary.processing.pending > 0 && !processing.active && (
              <button
                type="button"
                onClick={() => void processRemaining(false)}
                className="inline-flex items-center gap-2 rounded-lg border border-brand-300 bg-white px-4 py-2.5 text-sm font-semibold text-brand-700 shadow-sm hover:bg-brand-50"
                title="Read the documents the file sync found that have not been read yet"
              >
                Process remaining ({summary.processing.pending})
              </button>
            )}
            <button
              type="button"
              onClick={() => void sync.start()}
              disabled={sync.active || !reachable}
              className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 disabled:opacity-60"
              title={reachable ? "Find new, changed and removed files; unchanged files are skipped" : "The project has no folder to sync"}
            >
              <SyncIcon spinning={sync.active} />
              {sync.active ? "Syncing files..." : "Sync Files"}
            </button>
          </div>
        )}
      </header>

      {error && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {sync.error && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{sync.error}</div>}
      {processing.error && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{processing.error}</div>}
      {notice && <div className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">{notice}</div>}

      {!summary ? (
        !error && <div className="text-sm text-gray-400">Loading...</div>
      ) : (
        <>
          <SummaryStrip summary={summary} processingJob={processing.job} />

          {/* The file sync: running, or how the last one ended. */}
          {sync.active && sync.job ? (
            <RunningCard
              job={sync.job}
              what="file sync"
              heading={sync.job.cancel_requested ? "Stopping the file sync..." : sync.job.status === "queued" ? "File sync queued" : "Finding files"}
              waitingFor={summary.worker_running ? null : "the background worker"}
              hint="Every file's size and time is checked against the index. Nothing is opened: this takes seconds, and every page keeps working."
              stopLabel="Stop file sync"
              onStop={canEdit ? sync.cancel : undefined}
            />
          ) : syncEndedBadly && syncJob ? (
            <EndedCard job={syncJob} what="file sync" />
          ) : summary.synced_at ? (
            <FileSyncCard summary={summary} />
          ) : (
            <section className="rounded-xl border border-gray-200 bg-white p-6 text-center">
              <div className="font-semibold text-navy-900">Not synced yet</div>
              <p className="mt-1 text-sm text-gray-500">
                {reachable
                  ? "The project folder has not been read yet. Sync Files finds every document in it; they are then read in the background."
                  : "The project has no folder to sync: set it on Project Info."}
              </p>
            </section>
          )}

          {/* Document processing: what the index found being read. */}
          {summary.synced_at && (
            <ProcessingCard
              summary={summary}
              job={processing.active ? processing.job : null}
              canEdit={canEdit}
              onStop={processing.cancel}
              onResume={() => void processRemaining(false)}
              onRetryFailed={() => void processRemaining(true)}
            />
          )}

          {summary.synced_at && (
            <section className="overflow-hidden rounded-xl border border-gray-200 bg-white">
              <div className="flex flex-wrap items-center gap-4 p-5">
                <div className="min-w-0 flex-1">
                  <div className="text-lg font-bold text-navy-900">Files by status</div>
                  <div className="text-sm text-gray-600">{summary.total} file{summary.total === 1 ? "" : "s"} discovered in the project folder.</div>
                </div>
                <div className="grid w-full grid-cols-3 gap-y-3 sm:flex sm:w-auto sm:items-stretch sm:divide-x sm:divide-gray-200">
                  {STATUSES.filter((s) => (s !== "failed" && s !== "pending") || summary.counts[s] > 0).map((status) => (
                    <Counter key={status} status={status} count={summary.counts[status]}
                      onDetails={status === "processed" || status === "unchanged" ? undefined : () => showCategory(status)} />
                  ))}
                </div>
                <button
                  type="button"
                  onClick={() => setCardOpen((open) => !open)}
                  aria-expanded={cardOpen}
                  aria-label={cardOpen ? "Collapse the file categories" : "Expand the file categories"}
                  className="rounded-lg border border-gray-200 bg-white p-2 text-gray-500 hover:bg-gray-50"
                >
                  <Chevron up={cardOpen} />
                </button>
              </div>
              {cardOpen && (
                <div ref={categoriesRef} className="space-y-2 border-t border-gray-100 p-4">
                  {STATUSES.filter((s) => (s !== "failed" && s !== "pending") || summary.counts[s] > 0).map((status) => (
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
          )}

          {summary.synced_at && <SyncInformation summary={summary} />}
          {summary.synced_at && <FileDetails projectId={project.id} total={summary.total} epoch={epoch} />}
          {summary.synced_at && <ClassificationInspector projectId={project.id} epoch={epoch} />}
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

function processingLine(processing: FileSyncProcessing, job: Job | null): { value: string; sub: string | null } {
  const live = job ?? processing.job;
  const done = live?.progress.done ?? processing.completed;
  const total = live?.progress.total ?? processing.total;
  switch (processing.status) {
    case "running":
      return { value: `${done} / ${total} completed`, sub: "Processing in background" };
    case "queued":
      return { value: `${processing.pending} waiting`, sub: processing.worker_running ? "Queued" : "Queued: no processing worker is running" };
    case "complete":
      return { value: `${processing.processed} processed`, sub: processing.failed ? `${processing.failed} failed` : "Complete" };
    case "stopped":
      return { value: `${processing.pending} remaining`, sub: "Stopped" };
    case "failed":
      return { value: `${processing.pending} remaining`, sub: "The last run failed" };
    default:
      return processing.pending > 0
        ? { value: `${processing.pending} waiting`, sub: "Not started" }
        : { value: processing.processed > 0 ? `${processing.processed} processed` : "—", sub: processing.processed > 0 ? "Up to date" : "Nothing to read yet" };
  }
}

function SummaryStrip({ summary, processingJob }: { summary: FileSyncSummary; processingJob: Job | null }) {
  const line = processingLine(summary.processing, processingJob);
  return (
    <section className="grid grid-cols-1 divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-4 lg:divide-x">
      <Fact label="Source" icon={<CloudIcon />} value={summary.source}
        sub={summary.folder_display ?? "No folder set"} title={summary.folder ?? undefined} />
      <Fact label="Last File Sync" icon={<CalendarIcon />}
        value={summary.synced_at ? formatApiDate(summary.synced_at, "medium") : "Never"} sub={summary.synced_at ? byWhom(summary) : null} />
      <Fact label="Files Discovered" icon={<FileIcon />} value={String(summary.discovered)} sub="In the project folder" />
      <Fact label="Document Processing" icon={<GearIcon spinning={summary.processing.status === "running"} />}
        value={line.value} sub={line.sub} />
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

// --- the file sync ---------------------------------------------------------------------------

function FileSyncCard({ summary }: { summary: FileSyncSummary }) {
  const last = summary.last_sync;
  const facts: [string, ReactNode][] = last
    ? [
        ["Files discovered", last.files],
        ["New", last.new],
        ["Changed", last.changed],
        ["Already indexed", last.unchanged],
        ["Removed", last.removed],
        ["Duration", duration(last.duration_s)],
      ]
    : [["Files discovered", summary.discovered]];
  return (
    <section className="rounded-xl border border-green-200 bg-white">
      <div className="flex flex-wrap items-center gap-4 bg-green-50/60 p-5">
        <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-green-600 text-white">
          <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true">
            <path d="M5 12.5l4.5 4.5L19 7.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-xs font-semibold uppercase tracking-wide text-green-700">File sync</div>
          <div className="text-lg font-bold text-green-800">File discovery complete</div>
          <div className="text-sm text-gray-600">
            {summary.discovered} file{summary.discovered === 1 ? "" : "s"} discovered
            {summary.synced_at && <> · {formatApiDate(summary.synced_at, "medium")}</>}
            {byWhom(summary) && <> · {byWhom(summary)}</>}
          </div>
        </div>
        <dl className="grid grid-cols-3 gap-x-6 gap-y-2 sm:grid-cols-6">
          {facts.map(([label, value]) => (
            <div key={label} className="min-w-0 text-center">
              <dt className="text-xs text-gray-500">{label}</dt>
              <dd className="text-sm font-semibold text-navy-900">{value}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}

// --- document processing ---------------------------------------------------------------------

function ProcessingCard({ summary, job, canEdit, onStop, onResume, onRetryFailed }: {
  summary: FileSyncSummary; job: Job | null; canEdit: boolean;
  onStop: () => void; onResume: () => void; onRetryFailed: () => void;
}) {
  const p = summary.processing;
  const live = job ?? p.job;
  const active = live !== null && (live.status === "queued" || live.status === "running");
  const done = live?.progress.done ?? p.completed;
  const total = live?.progress.total ?? p.total;
  const percent = total > 0 ? Math.min(100, Math.round((100 * done) / total)) : null;
  const current = (live?.progress as { current?: string } | undefined)?.current ?? p.current;

  if (active && live) {
    const waiting = live.status === "queued";
    return (
      <section className="rounded-xl border border-brand-200 bg-brand-50/50 p-5" aria-live="polite">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="text-brand-600"><GearIcon spinning /></span>
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-brand-700">Document processing</div>
              <div className="font-bold text-navy-900">
                {live.cancel_requested ? "Stopping document processing..." : waiting ? "Document processing queued" : "Processing in background"}
              </div>
              <div className="text-sm text-gray-600">
                {waiting && !p.worker_running
                  ? "Waiting: the document processing worker is not running on this PC. Start the platform with start.bat."
                  : "The files are already discovered and the platform is usable. Each document is added to the logs and registers as soon as it is read."}
              </div>
            </div>
          </div>
          {canEdit && !live.cancel_requested && (
            <button type="button" onClick={onStop}
              className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-navy-900 hover:bg-gray-50">
              Stop processing
            </button>
          )}
        </div>
        {!waiting && (
          <>
            <div className="mt-3 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
              <span className="text-lg font-bold text-navy-900">{done} / {total} completed</span>
              {p.failed > 0 && <span className="text-red-700">{p.failed} failed</span>}
              {p.unavailable > 0 && <span className="text-red-600">{p.unavailable} unavailable</span>}
              <span className="text-gray-600">{Math.max(total - done, 0)} remaining</span>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-white" role="progressbar" aria-valuemin={0} aria-valuemax={100}
              aria-valuenow={percent ?? undefined} aria-label="Document processing progress">
              <div className={`h-full bg-brand-600 transition-all ${percent === null ? "w-1/3 animate-pulse" : ""}`}
                style={percent === null ? undefined : { width: `${percent}%` }} />
            </div>
          </>
        )}
        <div className="mt-1.5 truncate text-xs text-gray-500" title={current ?? undefined}>
          {current ? <>Current: {current}</> : live.progress.message}
        </div>
      </section>
    );
  }

  const stopped = p.status === "stopped";
  const failedRun = p.status === "failed";
  const remaining = p.pending;
  const tone = failedRun ? "border-red-200 bg-red-50/60" : stopped || remaining > 0 ? "border-amber-200 bg-amber-50/60" : "border-green-200 bg-green-50/60";
  return (
    <section className={`rounded-xl border p-5 ${tone}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-600">Document processing</div>
          <div className="font-bold text-navy-900">
            {failedRun ? "The last processing run failed" : stopped ? "Processing stopped" : remaining > 0 ? "Documents waiting to be processed" : p.processed > 0 || p.total > 0 ? "Processing complete" : "Nothing to process yet"}
          </div>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm text-gray-700">
            <span><b>{p.processed}</b> processed</span>
            {p.failed > 0 && <span className="text-red-700"><b>{p.failed}</b> failed</span>}
            {p.unavailable > 0 && <span className="text-red-600"><b>{p.unavailable}</b> unavailable</span>}
            {p.partial > 0 && <span className="text-amber-600"><b>{p.partial}</b> partial</span>}
            {remaining > 0 && <span className="text-sky-700"><b>{remaining}</b> remaining</span>}
            {p.duration_s !== null && <span className="text-gray-500">Duration: {duration(p.duration_s)}</span>}
            {p.finished_at && <span className="text-gray-500">Finished {formatApiDate(p.finished_at, "medium")}</span>}
          </div>
          {failedRun && p.last_job?.error && <div className="mt-1 text-xs text-red-700">{p.last_job.error}</div>}
        </div>
        {canEdit && (
          <div className="flex flex-wrap gap-2">
            {remaining > 0 && (
              <button type="button" onClick={onResume}
                className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700">
                {stopped ? "Resume processing" : "Process remaining"}
              </button>
            )}
            {p.failed > 0 && (
              <button type="button" onClick={onRetryFailed}
                className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-navy-900 hover:bg-gray-50"
                title="Read the failed documents again">
                Retry failed
              </button>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

// --- a job in progress, or one that did not finish -----------------------------------------

function RunningCard({ job, what, heading, waitingFor, hint, stopLabel, onStop }: {
  job: Job; what: string; heading: string; waitingFor: string | null; hint: string; stopLabel: string; onStop?: () => void;
}) {
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
            <div className="text-xs font-semibold uppercase tracking-wide text-brand-700">{what}</div>
            <div className="font-bold text-navy-900">{heading}</div>
            <div className="text-sm text-gray-600">
              {waiting && waitingFor
                ? `Waiting: ${waitingFor} is not running on this PC. Start the platform with start.bat.`
                : hint}
            </div>
          </div>
        </div>
        {onStop && !job.cancel_requested && (
          <button type="button" onClick={onStop}
            className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-navy-900 hover:bg-gray-50">
            {stopLabel}
          </button>
        )}
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-white" role="progressbar" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={percent ?? undefined} aria-label={`${what} progress`}>
        <div className={`h-full bg-brand-600 transition-all ${percent === null ? "w-1/3 animate-pulse" : ""}`}
          style={percent === null ? undefined : { width: `${percent}%` }} />
      </div>
      <div className="mt-1.5 truncate text-xs text-gray-500">{job.progress.message}</div>
    </section>
  );
}

function EndedCard({ job, what }: { job: Job; what: string }) {
  const stopped = job.status === "cancelled";
  return (
    <section className={`rounded-xl border p-4 text-sm ${stopped ? "border-gray-200 bg-gray-50 text-gray-700" : "border-red-200 bg-red-50 text-red-800"}`}>
      <span className="font-semibold">{stopped ? `The last ${what} was stopped` : `The last ${what} failed`}</span>
      {job.finished_at && <span> on {formatApiDate(job.finished_at, "medium")}</span>}
      {!stopped && job.error && <span>: {job.error}</span>}
      <span>. The results below are from the last {what} that finished.</span>
    </section>
  );
}

function SyncInformation({ summary }: { summary: FileSyncSummary }) {
  const p = summary.processing;
  const cells: [string, ReactNode][] = [
    ["Source", <span key="s" title={summary.folder ?? undefined}>{summary.source}<span className="block truncate text-xs font-normal text-gray-500">{summary.folder_display}</span></span>],
    ["Last File Sync", <span key="l">{formatApiDate(summary.synced_at, "medium")}<span className="block text-xs font-normal text-gray-500">{byWhom(summary)}</span></span>],
    ["Sync Duration", duration(summary.duration_s)],
    ["Files Discovered", summary.discovered],
    ["Files Processed", summary.counts.processed],
    ["Files Unchanged", summary.counts.unchanged],
    ["Files Pending", <span key="q" className="text-sky-700">{summary.counts.pending}</span>],
    ["Files Partial", <span key="p" className="text-amber-500">{summary.counts.partial}</span>],
    ["Files Unavailable", <span key="u" className="text-red-600">{summary.counts.unavailable}</span>],
  ];
  if (summary.counts.failed > 0) cells.push(["Files Failed", <span key="f" className="text-red-700">{summary.counts.failed}</span>]);
  if (summary.removed > 0) cells.push(["Removed from folder", summary.removed]);
  if (p.duration_s !== null) cells.push(["Processing Duration", duration(p.duration_s)]);
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

// --- Document Classification V2: a read-only inspector ----------------------------------------
//
// What each file appears to contain, at what stage of evidence, on what
// basis and why (GET /projects/{id}/documents/classification). Diagnostic
// only: nothing here edits a role, a status, a revision or a register, and
// nothing routes from it. Empty until the feature is on and a backfill or
// a processing run has stored assessments.

type ClassificationFilter = "all" | ClassificationStage | "stale" | "review" | "flagged" | "errors" | "outdated" | "unassessed";

const CLASSIFICATION_FILTERS: { value: ClassificationFilter; label: string }[] = [
  { value: "all", label: "All" }, { value: "supported", label: "Supported" }, { value: "hint", label: "Hint" },
  { value: "ambiguous", label: "Ambiguous" }, { value: "unknown", label: "Unknown" }, { value: "stale", label: "Stale" },
  { value: "review", label: "Needs review" }, { value: "flagged", label: "Source flags" }, { value: "errors", label: "Processing errors" },
  { value: "outdated", label: "Outdated reading" }, { value: "unassessed", label: "Unassessed" },
];

const STAGE_CHIP: Record<ClassificationStage, string> = {
  supported: "bg-green-50 text-green-700 ring-green-200",
  hint: "bg-sky-50 text-sky-700 ring-sky-200",
  ambiguous: "bg-amber-50 text-amber-700 ring-amber-200",
  unknown: "bg-gray-100 text-gray-600 ring-gray-200",
};

const FRESHNESS_LABEL: Record<string, string> = {
  current: "Current", source_changed: "Source changed", context_changed: "Context changed", rules_changed: "Earlier rules",
};

function typeLabel(value: string): string {
  return value.toLowerCase().replace(/_/g, " ");
}

function ClassificationInspector({ projectId, epoch }: { projectId: number; epoch: number }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<ClassificationRow[] | null>(null);
  const [metrics, setMetrics] = useState<ClassificationMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<ClassificationFilter>("all");
  const [type, setType] = useState("all");
  const [conflictingOnly, setConflictingOnly] = useState(false);
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    Promise.all([
      api.get<ClassificationRow[]>(`/projects/${projectId}/documents/classification`),
      api.get<ClassificationMetrics>(`/projects/${projectId}/documents/classification/metrics`),
    ])
      .then(([list, counts]) => {
        if (cancelled) return;
        setRows(list);
        setMetrics(counts);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not read the classifications");
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, open, epoch]);
  const types = useMemo(() => {
    const seen = new Set<string>();
    for (const row of rows ?? []) if (row.classification) seen.add(row.classification.primary_type);
    return Array.from(seen).sort();
  }, [rows]);
  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (rows ?? []).filter((row) => {
      const c = row.classification;
      const named = !needle || `${row.name} ${row.path} ${row.role}`.toLowerCase().includes(needle);
      if (filter === "errors") return !!row.extracted?.error && named;
      if (filter === "outdated") return row.extracted !== null && !row.extracted.parser_current && named;
      if (filter === "unassessed" ? c !== null : c === null && filter !== "all") return false;
      if (c) {
        if (filter === "stale" && c.freshness === "current") return false;
        if (filter === "review" && !c.needs_review) return false;
        if (filter === "flagged" && c.flags.length === 0) return false;
        if ((filter === "supported" || filter === "hint" || filter === "ambiguous" || filter === "unknown") && c.stage !== filter) return false;
        if (type !== "all" && c.primary_type !== type) return false;
        if (conflictingOnly && c.evidence_strength !== "conflicting") return false;
      } else if (type !== "all" || conflictingOnly) {
        return false;
      }
      return !needle || `${row.name} ${row.path} ${row.role}`.toLowerCase().includes(needle);
    });
  }, [rows, filter, type, conflictingOnly, search]);
  const summaryLine = metrics && metrics.assessed > 0
    ? `${metrics.assessed} of ${metrics.eligible} assessed · ${metrics.by_stage.supported ?? 0} supported · ${metrics.by_stage.hint ?? 0} hint · `
      + `${metrics.ambiguous} ambiguous · ${metrics.by_stage.unknown ?? 0} unknown · ${metrics.stale} not current · `
      + `${metrics.needs_review} need review · ${metrics.flagged ?? 0} with source flags · ${metrics.metadata_only} from path and role only · rules ${metrics.rules_version}`
    : null;
  return (
    <section className="rounded-xl border border-gray-200 bg-white">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="flex w-full items-center gap-3 p-4 text-left hover:bg-gray-50">
        <span className="text-gray-400"><InfoIcon /></span>
        <span className="min-w-0 flex-1">
          <span className="block font-bold text-navy-900">Classification (diagnostic)</span>
          <span className="block text-sm text-gray-500">
            What each file appears to contain, on what evidence, and whether that answer is still current. Read-only: nothing here changes a role, a status or a register.
          </span>
        </span>
        <span className="rounded-md border border-gray-200 p-1.5 text-gray-500"><Chevron up={open} /></span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-gray-100 p-4">
          {error ? (
            <div className="text-sm text-red-700">{error}</div>
          ) : rows === null || metrics === null ? (
            <div className="text-sm text-gray-400">Loading...</div>
          ) : (
            <>
              {summaryLine ? (
                <div className="text-xs text-gray-600">{summaryLine}</div>
              ) : (
                <div className="rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-600">
                  No assessment is stored for this project yet. Classification V2 is off on this server, or its backfill has not run.
                </div>
              )}
              <div className="flex flex-wrap items-center gap-2">
                {CLASSIFICATION_FILTERS.map(({ value, label }) => (
                  <button key={value} type="button" onClick={() => setFilter(value)} aria-pressed={filter === value}
                    className={`rounded-full px-3 py-1 text-xs font-semibold ${filter === value ? "bg-brand-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}>
                    {label}
                  </button>
                ))}
                <select aria-label="Document type" value={type} onChange={(e) => setType(e.target.value)} className="input w-auto text-xs">
                  <option value="all">All types</option>
                  {types.map((value) => <option key={value} value={value}>{typeLabel(value)}</option>)}
                </select>
                <label className="flex items-center gap-1 text-xs text-gray-600">
                  <input type="checkbox" checked={conflictingOnly} onChange={(e) => setConflictingOnly(e.target.checked)} />
                  Conflicting evidence only
                </label>
                <input aria-label="Search classified files" value={search} onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search files..." className="input ml-auto w-full sm:w-64" />
              </div>
              {visible.length === 0 ? (
                <div className="text-sm text-gray-500">No files match.</div>
              ) : (
                <ClassificationTable rows={visible} expanded={expanded} onExpand={setExpanded} projectId={projectId} />
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}

function ClassificationTable({ rows, expanded, onExpand, projectId }: {
  rows: ClassificationRow[]; expanded: number | null; onExpand: (id: number | null) => void; projectId: number;
}) {
  const [limit, setLimit] = useState(100);
  const shown = rows.slice(0, limit);
  return (
    <div>
      <div className="max-h-[32rem] overflow-auto rounded-md border border-gray-100">
        <table className="w-full min-w-[72rem] text-left text-xs">
          <thead className="sticky top-0 bg-gray-50 text-gray-500">
            <tr>
              <th className="px-2 py-2 font-semibold">File</th>
              <th className="px-2 py-2 font-semibold">Legacy role</th>
              <th className="px-2 py-2 font-semibold">Extracted</th>
              <th className="px-2 py-2 font-semibold">Primary type</th>
              <th className="px-2 py-2 font-semibold">Also</th>
              <th className="px-2 py-2 font-semibold">Stage</th>
              <th className="px-2 py-2 font-semibold">Evidence</th>
              <th className="px-2 py-2 font-semibold">System</th>
              <th className="px-2 py-2 font-semibold">Reason</th>
              <th className="px-2 py-2 font-semibold">Freshness</th>
              <th className="px-2 py-2 font-semibold">Assessed</th>
              <th className="px-2 py-2 font-semibold">Review</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {shown.map((row) => {
              const c = row.classification;
              const isOpen = expanded === row.document_id;
              return [
                <tr key={row.document_id} onClick={() => onExpand(isOpen ? null : row.document_id)}
                  className={`cursor-pointer align-top hover:bg-gray-50 ${c?.needs_review ? "bg-amber-50/40" : ""}`}>
                  <td className="max-w-64 px-2 py-2">
                    <span className="block truncate font-medium text-navy-900" title={row.name}>{row.name}</span>
                    <span className="block truncate text-[11px] text-gray-500" title={row.path}>{row.path}</span>
                  </td>
                  <td className="px-2 py-2 text-gray-600">{row.role}<span className="block text-[11px] text-gray-400">{row.state}</span></td>
                  <td className="max-w-56 px-2 py-2 text-gray-600">
                    {row.extracted ? (
                      <>
                        <span className="block truncate" title={row.extracted.reference ?? ""}>
                          {row.extracted.reference ?? "\u2014"}{row.extracted.revision ? ` \u00b7 ${row.extracted.revision}` : ""}{row.extracted.status ? ` \u00b7 ${row.extracted.status}` : ""}
                        </span>
                        <span className="block text-[11px] text-gray-400">
                          {row.extracted.categories.length > 0 ? row.extracted.categories.join(", ") : "no record"}
                          {row.extracted.form_is_submittal === false ? " \u00b7 model: not a form" : row.extracted.form_is_submittal ? " \u00b7 form read" : ""}
                          {!row.extracted.parser_current ? " \u00b7 outdated reading" : ""}
                        </span>
                        {row.extracted.error && <span className="block truncate text-[11px] text-red-700" title={row.extracted.error}>error: {row.extracted.error}</span>}
                      </>
                    ) : <span className="text-gray-400">\u2014</span>}
                  </td>
                  {c ? (
                    <>
                      <td className="px-2 py-2 font-semibold text-navy-900">{typeLabel(c.primary_type)}</td>
                      <td className="px-2 py-2 text-gray-600">
                        {c.component_types.map((t) => (
                          <span key={t} className="block" title={`${t}: ${c.component_support[t] ?? "metadata"}`}>
                            {typeLabel(t)} <span className="text-gray-400">({c.component_support[t] ?? "metadata"})</span>
                          </span>
                        ))}
                      </td>
                      <td className="px-2 py-2">
                        <span className={`inline-flex rounded-full px-2 py-0.5 font-semibold ring-1 ${STAGE_CHIP[c.stage]}`}>{c.stage}</span>
                      </td>
                      <td className="px-2 py-2 text-gray-600">
                        {c.evidence_strength} · {c.basis.replace(/_/g, " ")}
                        <span className="block text-[11px] text-gray-400">{c.evidence_sources.join(", ")}</span>
                      </td>
                      <td className="px-2 py-2 text-gray-600">{c.system_code ?? "—"}{c.discipline ? ` · ${c.discipline}` : ""}</td>
                      <td className="max-w-80 px-2 py-2 text-gray-600" title={c.evidence.join("\n")}>{c.reason}</td>
                      <td className="px-2 py-2">
                        <span className={c.freshness === "current" ? "text-gray-600" : "font-semibold text-red-700"}>
                          {FRESHNESS_LABEL[c.freshness] ?? c.freshness}
                        </span>
                        <span className="block text-[11px] text-gray-400">{c.rules_version}</span>
                      </td>
                      <td className="px-2 py-2 text-gray-600">{formatApiDate(c.assessed_at, "medium")}<span className="block text-[11px] text-gray-400">{c.source}</span></td>
                      <td className="px-2 py-2">
                        {c.engineer_confirmed && <span className="block font-semibold text-green-700">confirmed</span>}
                        {c.needs_review ? <span className="font-semibold text-amber-700" title={c.review_reasons.join("; ")}>yes</span> : <span className="text-gray-400">—</span>}
                        {c.flags.length > 0 && <span className="block text-[11px] text-red-700" title={c.flags.join("; ")}>{c.flags.length} flag{c.flags.length === 1 ? "" : "s"}</span>}
                      </td>
                    </>
                  ) : (
                    <td colSpan={9} className="px-2 py-2 text-gray-400">Not assessed</td>
                  )}
                </tr>,
                isOpen && c ? (
                  <tr key={`${row.document_id}-evidence`} className="bg-gray-50/60">
                    <td colSpan={12} className="px-3 py-2">
                      <div className="flex items-center gap-3">
                        <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">Evidence</span>
                        <a href={apiUrl(`/projects/${projectId}/logs/file?path=${encodeURIComponent(row.path)}`)} target="_blank" rel="noreferrer"
                          className="text-[11px] font-semibold text-brand-700 hover:underline">Open the document</a>
                      </div>
                      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-gray-700">
                        {c.evidence.map((line, i) => <li key={i}>{line}</li>)}
                      </ul>
                      {c.evidence_pages.length > 0 && (
                        <div className="mt-2">
                          <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">Pages</span>
                          <ul className="mt-1 space-y-0.5 pl-5 text-gray-700">
                            {c.evidence_pages.map((p, i) => (
                              <li key={i}>
                                <a href={apiUrl(`/projects/${projectId}/logs/file?path=${encodeURIComponent(row.path)}#page=${p.page}`)} target="_blank" rel="noreferrer" className="font-medium text-brand-700 hover:underline">page {p.page}</a>
                                {" "}{p.kind.replace(/_/g, " ")}{p.rule && p.rule !== p.kind ? ` (${p.rule})` : ""} \u00b7 {p.method}: <span className="text-gray-500">\u201c{p.excerpt}\u201d</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {c.flags.length > 0 && (
                        <div className="mt-2 text-red-800">Source flags: {c.flags.join("; ")}</div>
                      )}
                      {c.review_reasons.length > 0 && (
                        <div className="mt-2 text-amber-800">Review: {c.review_reasons.join("; ")}</div>
                      )}
                      {row.extracted && row.extracted.notes.length > 0 && (
                        <div className="mt-2 text-gray-600">Reading notes: {row.extracted.notes.join("; ")}</div>
                      )}
                      <div className="mt-2 text-[11px] text-gray-400">
                        content {c.content_sha256 ? c.content_sha256.slice(0, 12) : "—"} · context {c.context_fingerprint.slice(0, 12)} · row state when assessed: {c.source_state ?? "—"}
                      </div>
                    </td>
                  </tr>
                ) : null,
              ];
            })}
          </tbody>
        </table>
      </div>
      {rows.length > limit && (
        <button type="button" onClick={() => setLimit((n) => n + 200)}
          className="mt-2 text-xs font-semibold text-brand-600 hover:underline">
          Show more ({rows.length - limit} not shown)
        </button>
      )}
    </div>
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

function GearIcon({ spinning = false }: { spinning?: boolean }) {
  return (
    <Svg className={`h-5 w-5 ${spinning ? "animate-spin" : ""}`}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1" />
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
    case "pending":
      return <Svg><circle cx="12" cy="12" r="9" /><path d="M12 7.5V12l3 2" /></Svg>;
    case "partial":
      return <Svg><path d="M12 3.5l9.5 16.5h-19z" /><path d="M12 10v4.5M12 17.5h.01" /></Svg>;
    case "unavailable":
      return <Svg><path d="M7 18h10.5a4 4 0 0 0 .6-7.95A5.5 5.5 0 0 0 7.6 8.6 4.7 4.7 0 0 0 7 18z" /><path d="M4 4l16 16" /></Svg>;
    case "failed":
      return <Svg><circle cx="12" cy="12" r="9" /><path d="M9 9l6 6M15 9l-6 6" /></Svg>;
  }
}
