import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";

export interface Job {
  id: number;
  project_id: number | null;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  progress: { done?: number; total?: number; message?: string };
  cancel_requested: boolean;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

const POLL_MS = 1500;

/** Start, follow and stop one kind of server job for a project. A job that
 * is already running when the page opens (started before a refresh, or by a
 * colleague) is picked up and followed. `onFinished` runs once per job that
 * ends while the page is open. */
export function useJob(projectId: number, kind: string, startPath: string, onFinished?: (job: Job) => void) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const finished = useRef<Set<number>>(new Set());
  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;

  const active = job !== null && (job.status === "queued" || job.status === "running");

  // Pick up a job already running for this project.
  useEffect(() => {
    let cancelled = false;
    api
      .get<Job[]>(`/projects/${projectId}/jobs`)
      .then((jobs) => {
        const running = jobs.find((j) => j.kind === kind && (j.status === "queued" || j.status === "running"));
        if (!cancelled && running) setJob(running);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [projectId, kind]);

  useEffect(() => {
    if (!active || job === null) return;
    const timer = window.setInterval(() => {
      api
        .get<Job>(`/jobs/${job.id}`)
        .then((next) => {
          setJob(next);
          if (next.status !== "queued" && next.status !== "running" && !finished.current.has(next.id)) {
            finished.current.add(next.id);
            onFinishedRef.current?.(next);
          }
        })
        .catch((err) => setError(err instanceof ApiError ? err.message : "Lost track of the job"));
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, job]);

  const start = useCallback(async () => {
    setError(null);
    try {
      const started = await api.post<Job>(startPath);
      setJob(started);
      if (started.status !== "queued" && started.status !== "running") {
        finished.current.add(started.id);
        onFinishedRef.current?.(started);
      }
    } catch (err) {
      if (err instanceof ApiError && err.code === "job_running" && typeof err.detail?.job_id === "number") {
        setJob(await api.get<Job>(`/jobs/${err.detail.job_id}`));
        return;
      }
      setError(err instanceof ApiError ? err.message : "The job could not be started");
    }
  }, [startPath]);

  const cancel = useCallback(async () => {
    if (!job) return;
    try {
      setJob(await api.post<Job>(`/jobs/${job.id}/cancel`));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not stop the job");
    }
  }, [job]);

  /** Follow a job started some other way (the page's automatic check). */
  const follow = useCallback((started: Job) => setJob(started), []);

  return { job, active, error, start, cancel, follow };
}
