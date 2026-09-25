import { Fragment, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, apiUrl } from "../../lib/api";
import { useOnProjectChange } from "../../lib/projectChanges";
import { day } from "./types";

interface RequiredFile {
  path: string;
  name: string;
  modified: string;
}

interface RequiredItem {
  key: string;
  /** "approval": the system's material approved, from the material submittal
   * register -- nothing to request and no folder. "document": a file the
   * contractor hands over, received when it is in its folder. */
  kind: "approval" | "document";
  group: string;
  name: string;
  purpose: string;
  folder: string;
  format: string;
  received: boolean;
  status: "received" | "not_received" | "approved" | "not_approved";
  status_label: string;
  received_date: string | null;
  files: RequiredFile[];
  file_count: number;
  folder_exists: boolean;
  requested_at: string | null;
  request_count: number;
  remarks: string;
}

interface Required {
  system: string;
  system_name: string;
  systems: { code: string; name: string }[];
  groups: { key: string; name: string; items: RequiredItem[] }[];
  total: number;
  received: number;
  not_received: number;
  ready: number;
  readiness: string;
  reachable: boolean;
  contractor: string | null;
}

interface RequestText {
  subject: string;
  body: string;
  items: string[];
  system: string;
  keys: string[];
}

const VIEWABLE = /\.(pdf|dwg|dxf|doc|docx|xls|xlsx|zip)$/i;

const GroupIcon = ({ group }: { group: string }) =>
  group === "electrical" ? (
    <svg viewBox="0 0 24 24" className="h-5 w-5 fill-brand-600" aria-hidden="true"><path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z" /></svg>
  ) : group === "mechanical" ? (
    <svg viewBox="0 0 24 24" className="h-5 w-5 fill-none stroke-brand-600" strokeWidth="2.2" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1" strokeLinecap="round" />
    </svg>
  ) : group === "approvals" ? (
    <svg viewBox="0 0 24 24" className="h-5 w-5 fill-none stroke-brand-600" strokeWidth="2" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M8 12.5l2.5 2.5L16 9.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
  ) : (
    <svg viewBox="0 0 24 24" className="h-5 w-5 fill-none stroke-brand-600" strokeWidth="2" aria-hidden="true">
      <path d="M6 2h8l5 5v13a2 2 0 01-2 2H6a2 2 0 01-2-2V4a2 2 0 012-2z" /><path d="M9 12h6M9 16h6" strokeLinecap="round" />
    </svg>
  );

const SendIcon = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" strokeLinejoin="round" /></svg>
);
const FolderIcon = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" /></svg>
);

function StatusChip({ item }: { item: RequiredItem }) {
  const ok = item.received;
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${
      ok ? "bg-emerald-50 text-emerald-800 ring-emerald-200" : "bg-rose-50 text-rose-700 ring-rose-200"}`}>
      <span className={`h-2.5 w-2.5 rounded-full ${ok ? "bg-emerald-600" : "bg-rose-600"}`} /> {item.status_label}
    </span>
  );
}

/** Drawings > Actions Required: what the contractor must hand over before
 *  the selected system's shop drawings start. The material approval comes
 *  from the material submittal register (approved / not approved); each
 *  document is received when a file is in its folder of the project folder
 *  on OneDrive. Generating a request email records nothing; the request is
 *  noted against each item once the engineer says it was sent. */
export function RequiredDrawingsTab({ projectId, canEdit, system, onSystem }: {
  projectId: number; canEdit: boolean; system: string; onSystem: (code: string) => void;
}) {
  const [data, setData] = useState<Required | null>(null);
  const [error, setError] = useState("");
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [openFiles, setOpenFiles] = useState<string | null>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const [draft, setDraft] = useState<RequestText | null>(null);
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .get<Required>(`/projects/${projectId}/drawings/required?system=${encodeURIComponent(system)}`)
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e) => {
        // A system with no list of its own (PA/VA): the first that has one.
        if (e instanceof ApiError && e.status === 404) {
          api.get<Required>(`/projects/${projectId}/drawings/required`).then((d) => { setData(d); onSystem(d.system); }).catch((x) => setError(x.message));
          return;
        }
        setError(`The required documents could not be loaded: ${e.message}`);
      });
  }, [projectId, system, onSystem]);

  useEffect(() => {
    load();
    setDraft(null);
    setMenu(null);
    setOpenFiles(null);
  }, [load]);
  // The material approval is the register's and the files are the folder
  // sync's: when either changes, "missing" turns "received" here by itself.
  useOnProjectChange(["submittal", "documents", "drawing"], load);

  const openFolder = async (path: string) => {
    setNotice("");
    setMenu(null);
    try {
      await api.post(`/projects/${projectId}/drawings/open-folder`, { path, system });
    } catch (e) {
      setNotice((e as Error).message);
    }
  };

  /** Step one: the email, opened in the mail program and shown here. Nothing is recorded yet. */
  const generate = async (keys: string[]) => {
    setError("");
    setMenu(null);
    try {
      const text = await api.post<RequestText>(`/projects/${projectId}/drawings/required/request`, { system, keys });
      setDraft(text);
      const mail = document.createElement("a");
      mail.href = `mailto:?subject=${encodeURIComponent(text.subject)}&body=${encodeURIComponent(text.body)}`;
      mail.click();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  /** Step two: the engineer says it went: noted against each item, with the date. */
  const markSent = async () => {
    if (!draft) return;
    setBusy(true);
    try {
      const updated = await api.post<Required>(`/projects/${projectId}/drawings/required/request/sent`, { system: draft.system, keys: draft.keys });
      setData(updated);
      setDraft(null);
      setNotice(`Request noted as sent: ${draft.items.join(", ")}.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const copy = async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setNotice(`${what} copied.`);
    } catch {
      setNotice("Could not copy: select the text and copy it.");
    }
    setMenu(null);
  };

  const exportList = () =>
    api.download(`/projects/${projectId}/drawings/required/export.xlsx?system=${encodeURIComponent(system)}`, `Actions Required ${system}.xlsx`).catch((e) => setError(e.message));

  if (!data)
    return error ? (
      <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm text-rose-800">{error}</div>
    ) : (
      <div className="p-6 text-sm text-gray-500">Checking the project folder…</div>
    );

  const items = data.groups.flatMap((g) => g.items);
  const missing = items.filter((i) => !i.received && i.kind !== "approval");
  const q = query.trim().toLowerCase();
  const matches = (i: RequiredItem) => !q || [i.name, i.purpose, i.format, i.remarks].some((t) => t.toLowerCase().includes(q));
  let n = 0;

  const toggleGroup = (key: string) =>
    setClosed((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <div className="space-y-5">
      {error && <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm text-rose-800">{error}</div>}
      {notice && <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-900">{notice}</div>}
      {!data.reachable && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-900">
          The project folder is not reachable on this PC, so nothing can be seen as received. Check the project's folder on Project Info and that OneDrive is running.
        </div>
      )}
      {draft && (
        <div className="rounded-xl border border-brand-200 bg-brand-50 p-4 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="font-semibold text-navy-900">Request generated: {draft.items.join(", ")}</div>
            <div className="flex gap-2">
              <button type="button" onClick={() => copy(`${draft.subject}\n\n${draft.body}`, "The request")} className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium hover:bg-gray-50">Copy text</button>
              {canEdit && (
                <button type="button" disabled={busy} onClick={markSent} className="rounded-md bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50">
                  Mark as sent
                </button>
              )}
              <button type="button" onClick={() => setDraft(null)} className="rounded-md px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-100">Discard</button>
            </div>
          </div>
          <div className="mt-2 text-xs text-gray-600">
            The email opened in your mail program; if it did not, copy the text. Add the contractor's address{data.contractor ? ` (${data.contractor})` : ""}.
            Nothing is recorded until you press <span className="font-semibold">Mark as sent</span>.
          </div>
          <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-white p-3 font-sans text-gray-700">{`Subject: ${draft.subject}\n\n${draft.body}`}</pre>
        </div>
      )}
      <div className="rounded-xl border border-gray-200 bg-white">
        <div className="flex flex-wrap items-start justify-between gap-3 p-5">
          <div>
            <h2 className="text-xl font-bold text-navy-900">Actions Required – {data.system_name} ({data.system})</h2>
            <p className="mt-1 text-sm text-gray-500">
              What the contractor must hand over before the shop drawings start. A document is received when its file is in its folder on OneDrive; the approval is the material submittal register's.
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
              <span className={`rounded-md px-2.5 py-1 font-semibold ring-1 ring-inset ${data.ready === data.total ? "bg-emerald-50 text-emerald-800 ring-emerald-200" : "bg-amber-50 text-amber-800 ring-amber-200"}`}>{data.readiness}</span>
              {items.map((i) => (
                <span key={i.key} className={`text-xs ${i.received ? "text-emerald-700" : "text-rose-700"}`} title={i.status_label}>{i.received ? "✓" : "✕"} {i.name}</span>
              ))}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search document…" aria-label="Search document" className="w-60 rounded-lg border border-gray-300 px-3 py-2 text-sm" />
            <button type="button" onClick={exportList} className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50">Export</button>
            {canEdit && missing.length > 0 && (
              <button type="button" onClick={() => generate(missing.map((i) => i.key))} className="flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700">
                <SendIcon /> Request all not received ({missing.length})
              </button>
            )}
          </div>
        </div>
        <div className="overflow-x-auto px-5 pb-5">
          <table className="w-full min-w-[1100px] table-fixed text-sm">
            <colgroup>
              <col className="w-10" /><col className="w-[15%]" /><col className="w-[20%]" /><col className="w-[7%]" /><col className="w-[10%]" /><col className="w-[8%]" /><col className="w-[8%]" /><col /><col className="w-[160px]" />
            </colgroup>
            <thead className="border border-gray-200 bg-gray-50 text-left text-xs font-semibold text-navy-900">
              <tr>
                <th className="px-3 py-3">#</th><th className="px-3 py-3">Document Category</th><th className="px-3 py-3">Description / Scope</th><th className="px-3 py-3">Format</th>
                <th className="px-3 py-3">Status</th><th className="px-3 py-3">Requested Date</th><th className="px-3 py-3">Received / Approved</th><th className="px-3 py-3">Remarks</th><th className="px-3 py-3">Action</th>
              </tr>
            </thead>
            <tbody>
              {data.groups.map((g) => {
                const open = !closed.has(g.key);
                const shown = g.items.filter(matches);
                if (q && shown.length === 0) return null;
                return (
                  <Fragment key={g.key}>
                    <tr className="border-x border-t border-gray-200">
                      <td colSpan={9} className="px-3 py-3">
                        <button type="button" onClick={() => toggleGroup(g.key)} aria-expanded={open} className="flex items-center gap-3 font-semibold text-navy-900">
                          <span className="w-3 text-gray-500">{open ? "▾" : "▸"}</span>
                          <GroupIcon group={g.key} />
                          {g.name}
                        </button>
                      </td>
                    </tr>
                    {open && shown.map((i) => {
                      n += 1;
                      return (
                        <tr key={i.key} className="border-x border-t border-gray-200 align-top last:border-b">
                          <td className="px-3 py-3 text-gray-500">{n}</td>
                          <td className="px-3 py-3">
                            <div className="font-medium text-navy-900">{i.name}</div>
                            {i.kind === "approval" ? (
                              <div className="text-xs text-gray-500">From the material submittal register</div>
                            ) : (
                              <div className="truncate text-xs text-gray-500" title={i.folder}>{i.folder}</div>
                            )}
                            {openFiles === i.key && (
                              <ul className="mt-2 space-y-1 text-xs">
                                {i.files.map((f) => (
                                  <li key={f.path}>
                                    {VIEWABLE.test(f.name) ? (
                                      <a href={apiUrl(`/projects/${projectId}/logs/file?path=${encodeURIComponent(f.path)}`)} target="_blank" rel="noreferrer" className="text-brand-600 hover:underline">{f.name}</a>
                                    ) : f.name}{" "}
                                    <span className="text-gray-400">{day(f.modified)}</span>
                                  </li>
                                ))}
                                {i.file_count > i.files.length && <li className="text-gray-400">and {i.file_count - i.files.length} more</li>}
                              </ul>
                            )}
                          </td>
                          <td className="px-3 py-3 text-gray-700">{i.purpose}</td>
                          <td className="px-3 py-3 text-gray-700">{i.format}</td>
                          <td className="px-3 py-3"><StatusChip item={i} /></td>
                          <td className="whitespace-nowrap px-3 py-3 text-gray-700">
                            {day(i.requested_at)}
                            {i.request_count > 1 && <div className="text-[11px] text-gray-400">{i.request_count} times</div>}
                          </td>
                          <td className="whitespace-nowrap px-3 py-3 text-gray-700">{day(i.received_date)}</td>
                          <td className="px-3 py-3 text-gray-700">{i.remarks}</td>
                          <td className="relative px-3 py-3">
                            {i.kind === "approval" ? (
                              <Link to={`/projects/${projectId}/submittal`} className="inline-flex items-center justify-center whitespace-nowrap rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-brand-700 hover:bg-gray-50">Open register</Link>
                            ) : (
                              <>
                                <div className="flex items-center gap-1">
                                  {i.received ? (
                                    <button type="button" onClick={() => setOpenFiles(openFiles === i.key ? null : i.key)} className="flex w-28 items-center justify-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-brand-700 hover:bg-gray-50">
                                      <FolderIcon /> {openFiles === i.key ? "Hide" : "View"}
                                    </button>
                                  ) : (
                                    <button type="button" onClick={() => generate([i.key])} disabled={!canEdit} className="flex w-28 items-center justify-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-brand-700 hover:bg-gray-50 disabled:opacity-50">
                                      <SendIcon /> Request
                                    </button>
                                  )}
                                  <button type="button" onClick={() => setMenu(menu === i.key ? null : i.key)} className="rounded px-2 py-1 text-lg leading-none text-gray-500 hover:bg-gray-100" aria-label={`More for ${i.name}`}>⋮</button>
                                </div>
                                {menu === i.key && (
                                  <div className="absolute right-4 z-10 mt-1 w-48 rounded-lg border border-gray-200 bg-white py-1 text-sm shadow-lg">
                                    <button type="button" onClick={() => openFolder(i.files[0]?.path ?? i.folder)} className="block w-full px-3 py-1.5 text-left hover:bg-gray-50">Open folder</button>
                                    <button type="button" onClick={() => copy(i.folder, "The folder path")} className="block w-full px-3 py-1.5 text-left hover:bg-gray-50">Copy folder path</button>
                                    {canEdit && <button type="button" onClick={() => generate([i.key])} className="block w-full px-3 py-1.5 text-left hover:bg-gray-50">{i.received ? "Request again" : "Request"}</button>}
                                  </div>
                                )}
                              </>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
