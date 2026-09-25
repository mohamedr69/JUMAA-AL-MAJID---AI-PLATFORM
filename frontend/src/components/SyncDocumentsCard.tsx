import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../lib/api";
import { formatApiDate } from "../lib/format";
import type { DocumentStatus } from "../lib/types";
import { useJob } from "../lib/useJob";
import { JobProgress } from "./JobProgress";

const DEPENDENT: Record<string, string> = {
  boq: "BOQ (read from a Design Sheet that changed) — re-read the sheets from BOQ → Re-read",
  details: "Project Info (read from the DRF that changed) — run the AI check on Project Info",
  compliance: "Compliance specifications — the page searches the folder again on its next open",
  submittal: "Material submittal register row",
  log: "Log entry",
};

/** The project's document index: when the folder was last synced, what is
 * stale because a source document changed, what failed to read -- from the
 * database -- and the one action that reads the folder: Sync documents.
 * Opening a page never syncs; the first sync of a project can be started
 * from here by itself (`autoStart`) as its initial processing. */
export function SyncDocumentsCard({
  projectId,
  canEdit,
  autoStart = false,
  onSynced,
  onStatus,
  compact = false,
  header = false,
}: {
  projectId: number;
  canEdit: boolean;
  autoStart?: boolean;
  onSynced?: () => void;
  /** The index as it stands, for a page that shows the stale and failed
   * documents in its own words (the project board does). */
  onStatus?: (status: DocumentStatus) => void;
  compact?: boolean;
  /** In a page header: the last sync and the button on one line, the
   * lists left to the page. */
  header?: boolean;
}) {
  const [status, setStatus] = useState<DocumentStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const onSyncedRef = useRef(onSynced);
  onSyncedRef.current = onSynced;
  const onStatusRef = useRef(onStatus);
  onStatusRef.current = onStatus;
  const started = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const got = await api.get<DocumentStatus>(`/projects/${projectId}/documents/status`);
      setStatus(got);
      onStatusRef.current?.(got);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not read the document index");
    }
  }, [projectId]);

  const sync = useJob(projectId, "sync_documents", `/projects/${projectId}/jobs/sync-documents`, () => {
    void load();
    onSyncedRef.current?.();
  });
  const { active, start, follow } = sync;

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (status?.job && !active) follow(status.job);
  }, [status, active, follow]);

  // While the sync waits in the queue, look again now and then: whether a
  // worker is running to take it can change (start.bat run, a window closed).
  const queued = sync.job?.status === "queued";
  useEffect(() => {
    if (!queued) return;
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [queued, load]);

  // The first sync is the project's initial processing: started once, by
  // itself, when the project has never been synced. Never again on an open.
  useEffect(() => {
    if (!autoStart || !canEdit || !status || status.synced_at || status.syncing || !status.folder_reachable) return;
    if (started.current === projectId) return;
    started.current = projectId;
    void start();
  }, [autoStart, canEdit, status, projectId, start]);

  if (!status) return error ? <div className="text-xs text-red-700">{error}</div> : null;
  const stale = status.stale;
  // The sync runs in the worker process: the pages keep working from the
  // index as it stands, and are refreshed when it finishes.
  const hint = queued && !status.worker_running
    ? "Waiting: the background worker is not running on this PC. Start the platform with start.bat."
    : "Syncing in the background: keep working, the pages show the current index until it finishes.";

  if (header) {
    return (
      <div className="text-right">
        <div className="flex flex-wrap items-center justify-end gap-3">
          <span className="text-xs text-gray-500">
            {status.synced_at
              ? `Last sync ${formatApiDate(status.synced_at, "short")}`
              : status.folder_reachable
                ? "Not synced yet"
                : "The project folder is not reachable on this PC"}
          </span>
          {canEdit && (
            <button
              onClick={() => void start()}
              disabled={active || !status.folder_reachable}
              className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
              title="Check every file's size and time against the index; read only new or changed files"
            >
              {active ? "Syncing..." : "Sync documents"}
            </button>
          )}
        </div>
        {sync.job && (active || sync.job.status === "failed") && (
          <div className="mt-2 text-left">
            <JobProgress job={sync.job} onCancel={sync.cancel} what="the document sync" hint={hint} />
          </div>
        )}
        {sync.error && <div className="mt-1 text-xs text-red-700">{sync.error}</div>}
      </div>
    );
  }
  return (
    <section aria-label="Documents" className={`rounded-xl border bg-white ${compact ? "p-3" : "p-4"} ${stale.length || status.failed.length ? "border-amber-200" : "border-gray-200"}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-navy-900">Project documents</div>
          <div className="text-xs text-gray-500">
            {status.synced_at
              ? `${status.documents} document${status.documents === 1 ? "" : "s"} indexed · synced ${formatApiDate(status.synced_at, "short")}`
              : status.folder_reachable
                ? "Not synced yet: the folder has not been read into the index"
                : "The project folder is not reachable on this PC"}
          </div>
        </div>
        {canEdit && (
          <button
            onClick={() => void start()}
            disabled={active || !status.folder_reachable}
            className="rounded-lg border border-brand-300 bg-white px-3 py-1.5 text-xs font-semibold text-brand-700 hover:bg-brand-50 disabled:opacity-60"
            title="Check every file's size and time against the index; read only new or changed files"
          >
            {active ? "Syncing..." : "Sync documents"}
          </button>
        )}
      </div>
      {sync.job && (active || sync.job.status === "failed") && <JobProgress job={sync.job} onCancel={sync.cancel} what="the document sync" hint={hint} />}
      {sync.error && <div className="mt-1 text-xs text-red-700">{sync.error}</div>}
      {stale.length > 0 && (
        <div className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <div className="font-semibold">Source documents changed since these were built</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {stale.map((s) => (
              <li key={`${s.dependent_type}:${s.dependent_id}:${s.source}`}>
                {DEPENDENT[s.dependent_type] ?? s.dependent_type}
                {s.dependent_type === "submittal" || s.dependent_type === "log" ? ` ${s.dependent_id}` : ""} — {s.source}
                {s.source_state === "removed" ? " (removed from the folder)" : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
      {status.failed.length > 0 && (
        <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-800">
          <div className="font-semibold">Could not be read (the previous reading, if any, is kept)</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {status.failed.map((f) => (
              <li key={f.path}>
                {f.path}: {f.error}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
