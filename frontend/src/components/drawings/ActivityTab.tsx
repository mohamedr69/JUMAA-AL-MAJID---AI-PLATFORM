import { useCallback, useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useOnProjectChange } from "../../lib/projectChanges";
import type { DrawingEvent } from "./types";
import { when } from "./types";

const KIND_LABEL: Record<string, string> = {
  "drawing.found": "Drawing found",
  "revision.found": "Revision on file",
  "revision.detected": "Revision detected",
  "revision.confirmed": "Revision confirmed",
  "revision.ignored": "Revision ignored",
  "reply.received": "Consultant reply",
  "status.changed": "Status set",
  "reference.corrected": "Drawing corrected",
  "source.missing": "File missing",
  "requirement.requested": "Requested",
  "issue.resolved": "Issue resolved",
};

/** Activity / History: what happened to the system's shop drawings, in
 *  order -- the sync's findings and the engineers' decisions alike. */
export function ActivityTab({ projectId, system }: { projectId: number; system: string }) {
  const [events, setEvents] = useState<DrawingEvent[] | null>(null);
  const [all, setAll] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");

  const load = useCallback(() => {
    const path = all ? `/projects/${projectId}/drawings/activity?all_systems=true` : `/projects/${projectId}/drawings/activity?system=${encodeURIComponent(system)}`;
    api
      .get<{ events: DrawingEvent[] }>(path)
      .then((d) => {
        setEvents(d.events);
        setError("");
      })
      .catch((e) => setError(`The activity could not be loaded: ${e.message}`));
  }, [projectId, system, all]);

  useEffect(() => {
    load();
  }, [load]);
  useOnProjectChange(["documents", "drawing"], load);

  const q = query.trim().toLowerCase();
  const shown = (events ?? []).filter((e) => !q || e.text.toLowerCase().includes(q) || (KIND_LABEL[e.kind] ?? e.kind).toLowerCase().includes(q));

  return (
    <div className="rounded-xl border border-gray-200 bg-white">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-gray-100 p-5">
        <div>
          <h2 className="text-xl font-bold">Activity / History {all ? "— all systems" : `— ${system}`}</h2>
          <p className="mt-1 text-sm text-gray-500">Revisions found, confirmed and ignored; consultant replies; statuses set and references corrected; requests sent.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search…" aria-label="Search activity" className="w-48 rounded-lg border border-gray-300 px-3 py-2 text-sm" />
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} className="h-4 w-4" /> All systems
          </label>
        </div>
      </div>
      {error && <div className="m-5 rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm text-rose-800">{error}</div>}
      {events === null ? (
        <div className="p-6 text-sm text-gray-500">Loading…</div>
      ) : shown.length === 0 ? (
        <div className="p-6 text-sm text-gray-500">Nothing recorded yet{q ? " that matches the search" : ""}.</div>
      ) : (
        <ol className="divide-y divide-gray-100">
          {shown.map((e) => (
            <li key={e.id} className="flex flex-wrap items-start gap-3 px-5 py-3 text-sm">
              <span className="w-36 shrink-0 text-xs text-gray-500">{when(e.at)}</span>
              <span className="w-32 shrink-0 rounded-md bg-gray-100 px-2 py-0.5 text-center text-xs font-medium text-gray-700">{KIND_LABEL[e.kind] ?? e.kind}</span>
              {all && e.system && <span className="w-12 shrink-0 text-xs font-semibold text-gray-500">{e.system}</span>}
              <span className="min-w-0 flex-1 text-gray-800">{e.text}</span>
              <span className="text-xs text-gray-400">{e.user_id ? "engineer" : "sync"}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
