import type { Job } from "../lib/useJob";

function capitalised(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

/** A job's progress as a bar with its sentence, and a way to stop it.
 * `hint` is a line under the heading while the job is active. */
export function JobProgress({ job, onCancel, what, hint }: { job: Job; onCancel?: () => void; what: string; hint?: string }) {
  const total = job.progress.total ?? 0;
  const done = job.progress.done ?? 0;
  const percent = total > 0 ? Math.min(100, Math.round((100 * done) / total)) : null;
  const active = job.status === "queued" || job.status === "running";
  const heading = active
    ? job.cancel_requested
      ? `Stopping ${what}...`
      : job.status === "queued"
        ? `${capitalised(what)} queued`
        : `${capitalised(what)} in progress`
    : job.status === "succeeded"
      ? `${capitalised(what)} finished`
      : job.status === "cancelled"
        ? `${capitalised(what)} stopped`
        : `${capitalised(what)} failed`;
  return (
    <div
      className={`mt-3 rounded-lg border p-3 ${job.status === "failed" ? "border-red-200 bg-red-50" : "border-gray-200 bg-white"}`}
      aria-live="polite"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="font-medium text-navy-900">{heading}</span>
        {active && onCancel && !job.cancel_requested && (
          <button
            onClick={onCancel}
            className="rounded-lg border border-gray-300 bg-white px-3 py-1 text-xs font-semibold text-navy-900 hover:bg-gray-50"
          >
            Stop
          </button>
        )}
      </div>
      {active && hint && <div className="mt-1 text-xs text-gray-600">{hint}</div>}
      {active && (
        <div
          className="mt-2 h-2 overflow-hidden rounded-full bg-gray-100"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent ?? undefined}
          aria-label={`${what} progress`}
        >
          <div
            className={`h-full bg-brand-600 transition-all ${percent === null ? "w-1/3 animate-pulse" : ""}`}
            style={percent === null ? undefined : { width: `${percent}%` }}
          />
        </div>
      )}
      <div className={`mt-1 text-xs ${job.status === "failed" ? "text-red-700" : "text-gray-500"}`}>{job.error ?? job.progress.message}</div>
    </div>
  );
}
