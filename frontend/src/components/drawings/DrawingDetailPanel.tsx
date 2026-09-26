import { useCallback, useEffect, useState } from "react";
import { api, apiUrl } from "../../lib/api";
import { Chip, HintBadge, StatusBadge } from "./StatusChip";
import type { Candidate, DrawingDetail, Status, SystemSummary } from "./types";
import { OFFICIAL_STATUSES, STATUS_LABEL, day, when } from "./types";

const SECTIONS = ["Overview", "Revisions", "Files", "Review", "Activity"] as const;
type Section = (typeof SECTIONS)[number];

const KIND_LABEL: Record<string, string> = {
  "drawing.found": "Drawing found", "revision.found": "Revision on file", "revision.detected": "Revision detected",
  "revision.confirmed": "Revision confirmed", "revision.ignored": "Revision ignored", "reply.received": "Consultant reply",
  "status.changed": "Status set", "reference.corrected": "Drawing corrected", "source.missing": "File missing",
  "issue.resolved": "Issue resolved",
};

/** One shop drawing: what it is, every official revision with its file
 *  and answer, the files found that are not yet revisions, what needs
 *  review, and what happened to it. */
export function DrawingDetailPanel({ projectId, drawingId, canEdit, summary, onClose, onChanged }: {
  projectId: number; drawingId: number; canEdit: boolean; summary: SystemSummary | null; onClose: () => void; onChanged: () => void;
}) {
  const [d, setD] = useState<DrawingDetail | null>(null);
  const [section, setSection] = useState<Section>("Overview");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<{ reference: string; remarks: string } | null>(null);
  const [setting, setSetting] = useState<{ revision: string; status: Status; note: string } | null>(null);

  const load = useCallback(() => {
    api.get<DrawingDetail>(`/projects/${projectId}/drawings/sd/${drawingId}`).then((x) => { setD(x); setError(""); }).catch((e) => setError(e.message));
  }, [projectId, drawingId]);
  useEffect(() => { load(); }, [load]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      const result = await fn();
      if (result && typeof result === "object" && "reference" in (result as object)) setD(result as DrawingDetail);
      else load();
      onChanged();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const confirm = (c: Candidate) => run(() => api.post(`/projects/${projectId}/drawings/candidates/${c.id}/confirm`, {}));
  const ignore = (c: Candidate) => {
    const reason = window.prompt("Why is it not a submission? (optional)", "") ?? "";
    return run(() => api.post(`/projects/${projectId}/drawings/candidates/${c.id}/ignore`, { reason }));
  };
  const saveEdit = () => editing && run(() => api.patch(`/projects/${projectId}/drawings/sd/${drawingId}`, { drawing_reference: editing.reference, remarks: editing.remarks })).then((ok) => ok && setEditing(null));
  const saveStatus = () => setting && run(() => api.put(`/projects/${projectId}/drawings/sd/${drawingId}/revisions/${setting.revision}`, { status: setting.status, note: setting.note })).then((ok) => ok && setSetting(null));

  const fileLink = (path: string | null | undefined, page = 1) =>
    path ? <a href={apiUrl(`/projects/${projectId}/logs/file?path=${encodeURIComponent(path)}#page=${page}`)} target="_blank" rel="noreferrer" className="font-medium text-brand-700 hover:underline">View</a> : <span className="text-gray-400">—</span>;

  const available = d?.candidates.filter((c) => c.status === "available") ?? [];

  return (
    <aside className="w-full shrink-0 rounded-xl border border-gray-200 bg-white xl:w-[420px]" aria-label="Drawing details">
      <div className="flex items-start justify-between gap-2 border-b border-gray-100 px-4 py-3">
        <div className="min-w-0">
          <div className="truncate text-base font-bold text-navy-900">{d ? `${d.floor} — ${d.reference}` : "Loading…"}</div>
          {d?.floor_secondary && <div className="truncate text-xs text-gray-500">{d.floor_secondary}</div>}
          {d && (
            <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
              <div><div className="text-gray-500">Latest Revision</div><div className="font-semibold text-navy-900">{d.latest_revision ?? "–"}</div></div>
              <div><div className="text-gray-500">Latest Status</div><div className="mt-0.5"><StatusBadge status={d.latest_status} /></div></div>
              <div><div className="text-gray-500">Review</div><div className="mt-0.5">{d.hints.length ? <HintBadge hint={d.hints[0]} /> : <span className="text-emerald-700">✓ Nothing</span>}</div></div>
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {canEdit && d && !editing && (
            <button type="button" onClick={() => setEditing({ reference: d.reference ?? "", remarks: d.remarks })} className="rounded-lg border border-gray-300 px-2.5 py-1 text-xs font-medium text-navy-900 hover:bg-gray-50">✎ Edit</button>
          )}
          <button type="button" onClick={onClose} className="rounded px-2 py-1 text-lg leading-none text-gray-500 hover:bg-gray-100" aria-label="Close">×</button>
        </div>
      </div>
      <div className="flex gap-1 border-b border-gray-100 px-2">
        {SECTIONS.map((s) => (
          <button key={s} type="button" onClick={() => setSection(s)} aria-pressed={s === section} className={`-mb-px border-b-2 px-3 py-2 text-xs font-semibold ${s === section ? "border-brand-600 text-brand-700" : "border-transparent text-gray-500 hover:text-gray-700"}`}>
            {s}
            {s === "Revisions" && d && ` (${d.revision_history.length})`}
            {s === "Review" && d && d.hints.length > 0 && ` (${d.hints.length})`}
            {s === "Activity" && d && ` (${d.events.length})`}
          </button>
        ))}
      </div>
      {error && <div className="m-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">{error}</div>}
      {!d ? null : (
        <div className="space-y-3 p-4 text-sm">
          {editing && (
            <div className="space-y-2 rounded-lg border border-brand-200 bg-brand-50 p-3">
              <label className="block text-xs font-medium text-gray-700">Drawing reference
                <input value={editing.reference} onChange={(e) => setEditing({ ...editing, reference: e.target.value })} className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1 font-mono text-xs" />
              </label>
              <label className="block text-xs font-medium text-gray-700">Remarks
                <textarea value={editing.remarks} onChange={(e) => setEditing({ ...editing, remarks: e.target.value })} rows={2} className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1 text-xs" />
              </label>
              <div className="flex gap-2">
                <button type="button" disabled={busy} onClick={saveEdit} className="rounded-md bg-brand-600 px-3 py-1 text-xs font-semibold text-white disabled:opacity-50">Save</button>
                <button type="button" onClick={() => setEditing(null)} className="rounded-md px-3 py-1 text-xs text-gray-600">Cancel</button>
              </div>
              <p className="text-[11px] text-gray-500">A correction is the engineer's: the sync does not change it back.</p>
            </div>
          )}

          {section === "Overview" && (
            <>
              <Block title="Floor Information">
                <Row k="Floor" v={d.floor} />
                <Row k="Applicable" v={d.floor_keys.every((k) => d.floor_source[k]?.active !== false) ? "✓ Yes" : "Not in the latest IFC"} />
                <Row k="Source" v={d.floor_keys.map((k) => d.floor_source[k]?.source === "ifc" ? "IFC Drawings" : d.floor_source[k]?.source === "shop_drawing" ? "Shop drawing" : "Engineer").filter((v, i, a) => a.indexOf(v) === i).join(", ") || "—"} />
                {d.floor_keys.some((k) => d.floor_source[k]?.ifc_sheet) && <Row k="IFC sheet" v={d.floor_keys.map((k) => d.floor_source[k]?.ifc_sheet).filter(Boolean).join("; ")} small />}
                {d.floors > 1 && <Row k="Floors" v={`${d.floors} (${d.floor_keys.join(", ")})`} />}
              </Block>
              <Block title="Drawing Information">
                <Row k="Drawing Reference" v={<span className="font-mono text-xs">{d.reference}</span>} />
                <Row k="System" v={`${d.system_name} (${d.system})`} />
                <Row k="Latest Revision" v={d.latest_revision ?? "–"} />
                <Row k="Latest Status" v={STATUS_LABEL[d.latest_status]} />
                <Row k="Remarks" v={d.remarks || "—"} />
              </Block>
              {summary && <p className="text-[11px] text-gray-400">{summary.name}: {summary.approved_total} of {summary.floors} floors approved.</p>}
            </>
          )}

          {section === "Revisions" && (
            <>
              <Block title="Revision History">
                <table className="w-full text-xs">
                  <thead className="text-left text-gray-500"><tr><th className="py-1">Revision</th><th>Status</th><th>Submitted</th><th>Reply</th><th>File</th><th /></tr></thead>
                  <tbody className="divide-y divide-gray-100">
                    {d.revision_history.map((r) => (
                      <tr key={r.revision}>
                        <td className="py-1.5 font-semibold">{r.revision}</td>
                        <td><Chip cell={r} /></td>
                        <td>{day(r.submitted_at)}</td>
                        <td>{day(r.reply_at)}</td>
                        <td>{fileLink(r.path, r.page)}</td>
                        <td className="text-right">
                          {canEdit && <button type="button" onClick={() => setSetting({ revision: r.revision!, status: r.status, note: "" })} className="text-brand-700 hover:underline">Set</button>}
                        </td>
                      </tr>
                    ))}
                    {available.map((c) => (
                      <tr key={c.id} className="text-indigo-800">
                        <td className="py-1.5 font-semibold">{c.revision}</td>
                        <td colSpan={3}><span className="rounded border border-dashed border-indigo-300 px-2 py-0.5 text-[11px]">Candidate — not submitted</span></td>
                        <td>{fileLink(c.path, c.page)}</td>
                        <td />
                      </tr>
                    ))}
                    {d.revision_history.length === 0 && available.length === 0 && <tr><td colSpan={6} className="py-2 text-gray-500">No revision on record.</td></tr>}
                  </tbody>
                </table>
                {d.revision_history.some((r) => r.note) && (
                  <ul className="mt-2 space-y-1 text-[11px] text-gray-500">
                    {d.revision_history.filter((r) => r.note).map((r) => <li key={r.revision}><span className="font-semibold">{r.revision}:</span> {r.note}</li>)}
                  </ul>
                )}
              </Block>
              {setting && (
                <div className="space-y-2 rounded-lg border border-brand-200 bg-brand-50 p-3">
                  <div className="text-xs font-semibold text-navy-900">Set {setting.revision}'s status — your word stands over the folder and the AI</div>
                  <select value={setting.status} onChange={(e) => setSetting({ ...setting, status: e.target.value as Status })} className="w-full rounded-md border border-gray-300 px-2 py-1 text-xs">
                    {OFFICIAL_STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
                  </select>
                  <input value={setting.note} onChange={(e) => setSetting({ ...setting, note: e.target.value })} placeholder="Why (optional): reply received by email, …" className="w-full rounded-md border border-gray-300 px-2 py-1 text-xs" />
                  <div className="flex gap-2">
                    <button type="button" disabled={busy} onClick={saveStatus} className="rounded-md bg-brand-600 px-3 py-1 text-xs font-semibold text-white disabled:opacity-50">Save</button>
                    <button type="button" onClick={() => setSetting(null)} className="rounded-md px-3 py-1 text-xs text-gray-600">Cancel</button>
                  </div>
                </div>
              )}
              {canEdit && !setting && (
                <button type="button" onClick={() => setSetting({ revision: `R${d.revision_history.length}`, status: "under_review", note: "" })} className="text-xs font-medium text-brand-700 hover:underline">
                  + Record a revision the folder does not show
                </button>
              )}
              <Block title="Detected New Revisions">
                {available.length === 0 ? (
                  <p className="text-xs text-gray-500">No file found at a newer revision than the official latest.</p>
                ) : (
                  <table className="w-full text-xs">
                    <thead className="text-left text-gray-500"><tr><th className="py-1">Revision</th><th>File</th><th>Detected</th><th>Status</th><th /></tr></thead>
                    <tbody className="divide-y divide-gray-100">
                      {available.map((c) => (
                        <tr key={c.id}>
                          <td className="py-1.5 font-semibold">{c.revision}</td>
                          <td className="max-w-[120px] truncate" title={c.path ?? ""}>{c.path?.split("/").pop()}</td>
                          <td>{day(c.detected_at)}</td>
                          <td><span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[11px] font-medium text-indigo-700">Available</span></td>
                          <td className="whitespace-nowrap text-right">
                            {fileLink(c.path, c.page)}
                            {canEdit && (
                              <>
                                {" "}<button type="button" disabled={busy} onClick={() => confirm(c)} className="ml-1 rounded-md bg-brand-600 px-2 py-0.5 text-[11px] font-semibold text-white disabled:opacity-50">Confirm</button>
                                {" "}<button type="button" disabled={busy} onClick={() => ignore(c)} className="rounded-md border border-gray-300 px-2 py-0.5 text-[11px] font-medium disabled:opacity-50">Ignore</button>
                              </>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {available.length > 0 && (
                  <p className="mt-2 text-[11px] text-gray-500">
                    Official latest: {d.latest_revision ?? "none"} — {STATUS_LABEL[d.latest_status]}. No confirmed submission evidence was found for the detected file. Confirm it if it was submitted; ignore it if it was not.
                  </p>
                )}
              </Block>
            </>
          )}

          {section === "Files" && (
            <Block title="Files">
              <ul className="space-y-1 text-xs">
                {d.revision_history.filter((r) => r.path).map((r) => (
                  <li key={r.revision} className="flex items-center justify-between gap-2">
                    <span className="min-w-0 truncate" title={r.path ?? ""}>{r.revision} · {r.path?.split("/").pop()}{r.source_missing ? " (missing)" : ""}</span>
                    {fileLink(r.path, r.page)}
                  </li>
                ))}
                {d.candidates.filter((c) => c.path).map((c) => (
                  <li key={`c${c.id}`} className="flex items-center justify-between gap-2 text-indigo-800">
                    <span className="min-w-0 truncate" title={c.path ?? ""}>{c.revision} · {c.path?.split("/").pop()} ({c.status})</span>
                    {fileLink(c.path, c.page)}
                  </li>
                ))}
                {d.revision_history.every((r) => !r.path) && d.candidates.length === 0 && <li className="text-gray-500">No file on record.</li>}
              </ul>
            </Block>
          )}

          {section === "Review" && (
            <Block title="Review">
              {d.issues_list.length === 0 && d.hints.length === 0 ? (
                <p className="text-xs text-emerald-700">✓ Nothing to review on this drawing.</p>
              ) : (
                <ul className="space-y-2 text-xs">
                  {d.issues_list.map((i) => (
                    <li key={i.id} className="rounded-lg border border-gray-200 p-2">
                      <div className="flex items-center gap-2"><HintBadge hint={{ kind: i.kind, label: i.label, severity: i.severity, source: i.source }} /><span className="text-gray-500">{i.source === "ai" ? "AI Review" : "System Check"}</span></div>
                      <p className="mt-1 text-gray-700">{i.text}</p>
                      {i.ai?.confidence != null && <p className="text-[11px] text-gray-500">Confidence {Math.round(i.ai.confidence * 100)}%{i.ai.reason_code ? ` · ${i.ai.reason_code.toLowerCase().replace(/_/g, " ")}` : ""}</p>}
                    </li>
                  ))}
                </ul>
              )}
            </Block>
          )}

          {section === "Activity" && (
            <Block title="Activity">
              <ol className="space-y-1.5 text-xs">
                {d.events.map((e) => (
                  <li key={e.id} className="flex gap-2"><span className="w-28 shrink-0 text-gray-500">{when(e.at)}</span><span className="w-24 shrink-0 font-medium text-gray-700">{KIND_LABEL[e.kind] ?? e.kind}</span><span className="min-w-0 flex-1">{e.text}</span></li>
                ))}
                {d.events.length === 0 && <li className="text-gray-500">Nothing recorded yet.</li>}
              </ol>
            </Block>
          )}
        </div>
      )}
    </aside>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-gray-200">
      <div className="border-b border-gray-100 bg-gray-50 px-3 py-1.5 text-xs font-semibold text-navy-900">{title}</div>
      <div className="px-3 py-2">{children}</div>
    </section>
  );
}

function Row({ k, v, small }: { k: string; v: React.ReactNode; small?: boolean }) {
  return (
    <div className={`flex justify-between gap-3 py-1 ${small ? "text-[11px]" : "text-xs"}`}>
      <span className="text-gray-500">{k}</span>
      <span className="text-right text-navy-900">{v}</span>
    </div>
  );
}
