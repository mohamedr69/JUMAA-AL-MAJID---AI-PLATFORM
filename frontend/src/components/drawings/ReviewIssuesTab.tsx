import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../../lib/api";
import { useOnProjectChange } from "../../lib/projectChanges";
import type { Issue, Issues } from "./types";

const SEVERITY: Record<Issue["severity"], { mark: string; tone: string; label: string }> = {
  error: { mark: "✕", tone: "border-rose-200 bg-rose-50 text-rose-800", label: "Error" },
  warning: { mark: "⚠", tone: "border-amber-200 bg-amber-50 text-amber-900", label: "Warning" },
  info: { mark: "ⓘ", tone: "border-slate-200 bg-slate-50 text-slate-700", label: "Info" },
};

/** Review & Issues: exceptions only. What the rules found (System Check)
 *  kept apart from what the AI suggested (AI Review), so a fact is never
 *  read as a guess. */
interface MergeReview {
  issue: Issue;
  alias_key: string;
  canonical_key: string;
  alias_label: string;
  canonical_label: string;
  conflicts: {
    system: string;
    alias: { id: number; reference: string; revisions: { revision: string; status: string; label: string }[] }[];
    canonical: { id: number; reference: string; revisions: { revision: string; status: string; label: string }[] }[];
  }[];
}

export function ReviewIssuesTab({
  projectId,
  canEdit,
  system,
  onOpenDrawing,
  onChanged,
}: {
  projectId: number;
  canEdit: boolean;
  system: string;
  onOpenDrawing: (id: number) => void;
  onChanged: () => void;
}) {
  const [data, setData] = useState<Issues | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<number | null>(null);
  // A merge that brings two drawings together on one floor: shown for confirmation first.
  const [review, setReview] = useState<MergeReview | null>(null);

  const load = useCallback(() => {
    api
      .get<Issues>(`/projects/${projectId}/drawings/issues?system=${encodeURIComponent(system)}`)
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e) => setError(`The review items could not be loaded: ${e.message}`));
  }, [projectId, system]);

  useEffect(() => {
    load();
  }, [load]);
  useOnProjectChange(["documents", "drawing"], load);

  /** The engineer's word on two names: one floor (merged as the level,
   *  kept for every later IFC revision and sync) or two floors (not asked
   *  again). Where both floors already carry drawings, the merge stops and
   *  shows them until it is confirmed: nothing is overwritten either way. */
  const decideFloor = async (issue: Issue, decision: "merge" | "separate", confirm = false) => {
    const { alias_key, canonical_key } = issue.detail;
    if (!alias_key || !canonical_key) return;
    setBusy(issue.id);
    setError("");
    try {
      await api.post(`/projects/${projectId}/drawings/floors/${decision}`, { alias_key, canonical_key, confirm });
      setReview(null);
      load();
      onChanged();
    } catch (e) {
      if (e instanceof ApiError && e.code === "merge_review" && e.detail) {
        setReview({ issue, ...(e.detail as unknown as Omit<MergeReview, "issue">) });
      } else {
        setError((e as Error).message);
      }
    } finally {
      setBusy(null);
    }
  };

  const act = async (issue: Issue, action: "resolve" | "confirm" | "ignore") => {
    setBusy(issue.id);
    setError("");
    try {
      if (action === "resolve") {
        const resolution = window.prompt("How was it settled?", "Reviewed");
        if (resolution === null) return;
        await api.post(`/projects/${projectId}/drawings/issues/${issue.id}/resolve`, { resolution });
      } else {
        const id = issue.detail.candidate_id;
        if (!id) return;
        await api.post(`/projects/${projectId}/drawings/candidates/${id}/${action}`, action === "confirm" ? {} : { reason: "" });
      }
      load();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const section = (title: string, mark: string, items: Issue[], note: string) => (
    <section className="rounded-xl border border-gray-200 bg-white">
      <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
        <h3 className="text-sm font-bold uppercase tracking-wide text-navy-900">
          {mark} {title} <span className="ml-1 rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600">{items.length}</span>
        </h3>
        <span className="text-xs text-gray-500">{note}</span>
      </div>
      {items.length === 0 ? (
        <div className="px-5 py-4 text-sm text-gray-500">Nothing to review.</div>
      ) : (
        <ul className="divide-y divide-gray-100">
          {items.map((issue) => {
            const s = SEVERITY[issue.severity];
            const candidate = issue.kind === "revision_candidate" && issue.detail.candidate_id;
            const duplicate = issue.kind === "possible_duplicate_floor" && issue.detail.alias_key && issue.detail.canonical_key;
            return (
              <li key={issue.id} className="flex flex-wrap items-start gap-3 px-5 py-3">
                <span className={`mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs ${s.tone}`} title={s.label} aria-label={s.label}>{s.mark}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-semibold text-navy-900">{issue.floor_key ?? ""}{issue.floor_key ? " — " : ""}{issue.label}</span>
                    <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${s.tone}`}>{s.label}</span>
                  </div>
                  <p className="mt-0.5 text-sm text-gray-700">{issue.text}</p>
                  {issue.ai && (issue.ai.confidence != null || issue.ai.reason_code) && (
                    <p className="mt-0.5 text-xs text-gray-500">
                      {issue.kind === "possible_duplicate_floor" && <>AI: {issue.ai.possible_same_floor ? "possibly the same floor" : "possibly different floors"} · </>}
                      {issue.ai.confidence != null && <>Confidence {Math.round(issue.ai.confidence * 100)}% · </>}
                      {issue.kind !== "possible_duplicate_floor" && issue.ai.reason_code?.toLowerCase().replace(/_/g, " ")}
                      {issue.ai.evidence && <>{issue.ai.evidence}</>}
                      {issue.ai.validation_reason && <> · {issue.ai.validation_reason}</>}
                      {issue.kind === "possible_duplicate_floor" && <> · the AI merges nothing: your confirmation decides</>}
                    </p>
                  )}
                  {review && review.issue.id === issue.id && (
                    <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
                      <div className="font-semibold">Merge review: both floors already carry shop drawings</div>
                      <p className="mt-1">
                        Merging keeps every drawing, revision, status and consultant reply of both. The floor will then have two
                        drawing references, listed as a conflict for you to settle. Nothing is overwritten.
                      </p>
                      <ul className="mt-2 space-y-1">
                        {review.conflicts.map((c) => (
                          <li key={c.system}>
                            <span className="font-semibold">{c.system}:</span>{" "}
                            {c.alias.map((d) => `${d.reference} (${d.revisions.map((r) => `${r.revision} ${r.label}`).join(", ") || "no revision"})`).join("; ")}
                            {" "}on {review.alias_label} · {c.canonical.map((d) => `${d.reference} (${d.revisions.map((r) => `${r.revision} ${r.label}`).join(", ") || "no revision"})`).join("; ")}
                            {" "}on {review.canonical_label}
                          </li>
                        ))}
                      </ul>
                      <div className="mt-2 flex gap-2">
                        <button type="button" disabled={busy === issue.id} onClick={() => decideFloor(issue, "merge", true)} className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50">
                          Confirm merge as {review.canonical_label}
                        </button>
                        <button type="button" onClick={() => setReview(null)} className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-navy-900 hover:bg-gray-50">
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                  {issue.shop_drawing_id && (
                    <button type="button" onClick={() => onOpenDrawing(issue.shop_drawing_id!)} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-navy-900 hover:bg-gray-50">
                      Open drawing
                    </button>
                  )}
                  {canEdit && candidate && (
                    <>
                      <button type="button" disabled={busy === issue.id} onClick={() => act(issue, "confirm")} className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50">
                        Confirm submitted
                      </button>
                      <button type="button" disabled={busy === issue.id} onClick={() => act(issue, "ignore")} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-navy-900 hover:bg-gray-50 disabled:opacity-50">
                        Ignore
                      </button>
                    </>
                  )}
                  {canEdit && duplicate && (
                    <>
                      <button type="button" disabled={busy === issue.id} onClick={() => decideFloor(issue, "merge")} className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50">
                        Merge as {issue.detail.canonical_label ?? issue.detail.canonical_key}
                      </button>
                      <button type="button" disabled={busy === issue.id} onClick={() => decideFloor(issue, "separate")} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-navy-900 hover:bg-gray-50 disabled:opacity-50">
                        Keep Separate
                      </button>
                    </>
                  )}
                  {canEdit && !candidate && !duplicate && (
                    <button type="button" disabled={busy === issue.id} onClick={() => act(issue, "resolve")} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-navy-900 hover:bg-gray-50 disabled:opacity-50">
                      Resolve
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold">Review & Issues — {system}</h2>
          <p className="mt-1 text-sm text-gray-500">
            {data ? (data.total === 0 ? "Nothing needs attention." : `${data.total} ${data.total === 1 ? "item needs" : "items need"} attention.`) : "Loading…"}{" "}
            Only exceptions are listed: a floor whose drawing reads as the folder says it does is not here.
          </p>
        </div>
      </div>
      {error && <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm text-rose-800">{error}</div>}
      {data && (
        <>
          {section("System Check", "⚙", data.system_checks, "Found by the rules: facts, not guesses")}
          {section("AI Review", "✨", data.ai_review, "What the AI suggested about what the rules could not settle")}
        </>
      )}
    </div>
  );
}
