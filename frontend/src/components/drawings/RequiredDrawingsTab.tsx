import { Fragment, useCallback, useEffect, useState } from "react";
import { api, apiUrl } from "../../lib/api";

interface RequiredFile {
  path: string;
  name: string;
  modified: string;
}

interface RequiredItem {
  key: string;
  group: string;
  name: string;
  purpose: string;
  folder: string;
  format: string;
  received: boolean;
  received_date: string | null;
  files: RequiredFile[];
  file_count: number;
  folder_exists: boolean;
  requested_at: string | null;
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
  reachable: boolean;
  contractor: string | null;
}

interface RequestText {
  subject: string;
  body: string;
  items: string[];
}

const VIEWABLE = /\.(pdf|dwg|dxf|doc|docx|xls|xlsx|zip)$/i;
const SYSTEM_KEY = "drawings.required.system";

function day(value: string | null): string {
  return value
    ? new Date(value).toLocaleDateString(undefined, {
        day: "2-digit",
        month: "short",
        year: "numeric",
      })
    : "–";
}

const GroupIcon = ({ group }: { group: string }) =>
  group === "electrical" ? (
    <svg
      viewBox="0 0 24 24"
      className="h-5 w-5 fill-brand-600"
      aria-hidden="true"
    >
      <path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z" />
    </svg>
  ) : group === "mechanical" ? (
    <svg
      viewBox="0 0 24 24"
      className="h-5 w-5 fill-none stroke-brand-600"
      strokeWidth="2.2"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="3" />
      <path
        d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"
        strokeLinecap="round"
      />
    </svg>
  ) : (
    <svg
      viewBox="0 0 24 24"
      className="h-5 w-5 fill-none stroke-brand-600"
      strokeWidth="2"
      aria-hidden="true"
    >
      <path d="M6 2h8l5 5v13a2 2 0 01-2 2H6a2 2 0 01-2-2V4a2 2 0 012-2z" />
      <path d="M9 12h6M9 16h6" strokeLinecap="round" />
    </svg>
  );

const SendIcon = () => (
  <svg
    viewBox="0 0 24 24"
    className="h-4 w-4"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    aria-hidden="true"
  >
    <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" strokeLinejoin="round" />
  </svg>
);

const FolderIcon = () => (
  <svg
    viewBox="0 0 24 24"
    className="h-4 w-4"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    aria-hidden="true"
  >
    <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
  </svg>
);

function StatusChip({ received }: { received: boolean }) {
  return received ? (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-800 ring-1 ring-inset ring-emerald-200">
      <span className="h-2.5 w-2.5 rounded-full bg-emerald-600" /> Received
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700 ring-1 ring-inset ring-rose-200">
      <span className="h-2.5 w-2.5 rounded-full bg-rose-600" /> Not Received
    </span>
  );
}

/** Drawings > Actions Required: what the contractor must hand over before a
 *  system's shop drawings start, one list per system of the project (fire
 *  alarm, emergency lighting), each shown the same way. Each item is a folder of the project
 *  structure on OneDrive; it is received when a file is in its folder, and
 *  not received while the folder is empty or missing. Requests are emails
 *  to the contractor, noted against each item. */
export function RequiredDrawingsTab({
  projectId,
  canEdit,
  onSystem,
}: {
  projectId: number;
  canEdit: boolean;
  /** The system shown, for the page header. */
  onSystem?: (code: string) => void;
}) {
  const [system, setSystem] = useState<string>(() => {
    try {
      return localStorage.getItem(SYSTEM_KEY) ?? "FAS";
    } catch {
      return "FAS";
    }
  });
  const [data, setData] = useState<Required | null>(null);
  const [error, setError] = useState("");
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [openFiles, setOpenFiles] = useState<string | null>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const [draft, setDraft] = useState<RequestText | null>(null);
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");

  const load = useCallback(() => {
    api
      .get<Required>(
        `/projects/${projectId}/drawings/required?system=${system}`,
      )
      .then((d) => {
        // A system remembered from another project that this one does not have: its first system instead.
        if (!d.systems.some((s) => s.code === system))
          setSystem(d.systems[0]?.code ?? "FAS");
        else {
          setData(d);
          onSystem?.(d.system);
        }
      })
      .catch((e) =>
        setError(`The required documents could not be loaded: ${e.message}`),
      );
  }, [projectId, system, onSystem]);

  useEffect(() => {
    load();
  }, [load]);

  const choose = (code: string) => {
    setSystem(code);
    setOpenFiles(null);
    setMenu(null);
    setDraft(null);
    setQuery("");
    try {
      localStorage.setItem(SYSTEM_KEY, code);
    } catch {
      /* storage blocked: the system is not remembered */
    }
  };

  const openFolder = async (path: string) => {
    setNotice("");
    setMenu(null);
    try {
      await api.post(`/projects/${projectId}/drawings/open-folder`, { path });
    } catch (e) {
      setNotice((e as Error).message);
    }
  };

  const request = async (keys: string[]) => {
    setError("");
    setMenu(null);
    try {
      const text = await api.post<RequestText>(
        `/projects/${projectId}/drawings/required/request`,
        { keys },
      );
      setDraft(text);
      const mail = document.createElement("a");
      mail.href = `mailto:?subject=${encodeURIComponent(text.subject)}&body=${encodeURIComponent(text.body)}`;
      mail.click();
      load();
    } catch (e) {
      setError((e as Error).message);
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
    api
      .download(
        `/projects/${projectId}/drawings/required/export.xlsx?system=${system}`,
        `Actions Required ${system}.xlsx`,
      )
      .catch((e) => setError(e.message));

  if (!data)
    return error ? (
      <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm text-rose-800">
        {error}
      </div>
    ) : (
      <div className="p-6 text-sm text-gray-500">
        Checking the project folder…
      </div>
    );

  const items = data.groups.flatMap((g) => g.items);
  const missing = items.filter((i) => !i.received);

  // --- pieces both views use -------------------------------------------------------------------

  const systemTabs = (
    <div className="flex gap-1 border-b border-gray-200">
      {data.systems.map((s) => (
        <button
          key={s.code}
          type="button"
          onClick={() => choose(s.code)}
          aria-pressed={s.code === data.system}
          className={`-mb-px border-b-2 px-4 py-2.5 text-sm font-semibold ${
            s.code === data.system
              ? "border-brand-600 text-brand-700"
              : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
        >
          {s.name} ({s.code})
        </button>
      ))}
    </div>
  );

  const messages = (
    <>
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
      {!data.reachable && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-900">
          The project folder is not reachable on this PC, so nothing can be seen
          as received. Check the project's folder on Project Info and that
          OneDrive is running.
        </div>
      )}
      {draft && (
        <div className="rounded-xl border border-gray-200 bg-white p-4 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="font-semibold text-navy-900">
              Request opened in your email: {draft.items.join(", ")}
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() =>
                  copy(`${draft.subject}\n\n${draft.body}`, "The request")
                }
                className="rounded-md border border-gray-300 px-3 py-1.5 text-xs font-medium hover:bg-gray-50"
              >
                Copy text
              </button>
              <button
                type="button"
                onClick={() => setDraft(null)}
                className="rounded-md px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-100"
              >
                Close
              </button>
            </div>
          </div>
          <div className="mt-2 text-xs text-gray-500">
            If no email opened, copy the text. Add the contractor's address
            {data.contractor ? ` (${data.contractor})` : ""}; the request date
            is noted against each item.
          </div>
          <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-gray-50 p-3 font-sans text-gray-700">{`Subject: ${draft.subject}\n\n${draft.body}`}</pre>
        </div>
      )}
    </>
  );

  const fileList = (i: RequiredItem) =>
    openFiles === i.key && (
      <ul className="mt-2 space-y-1 text-xs">
        {i.files.map((f) => (
          <li key={f.path}>
            {VIEWABLE.test(f.name) ? (
              <a
                href={apiUrl(
                  `/projects/${projectId}/logs/file?path=${encodeURIComponent(f.path)}`,
                )}
                target="_blank"
                rel="noreferrer"
                className="text-brand-600 hover:underline"
              >
                {f.name}
              </a>
            ) : (
              f.name
            )}{" "}
            <span className="text-gray-400">{day(f.modified)}</span>
          </li>
        ))}
        {i.file_count > i.files.length && (
          <li className="text-gray-400">
            and {i.file_count - i.files.length} more
          </li>
        )}
      </ul>
    );

  const actions = (i: RequiredItem, viewIcon = false) => (
    <>
      <div className="flex items-center gap-1">
        {i.received ? (
          <button
            type="button"
            onClick={() => setOpenFiles(openFiles === i.key ? null : i.key)}
            className="flex w-28 items-center justify-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-brand-700 hover:bg-gray-50"
          >
            {viewIcon && <FolderIcon />} {openFiles === i.key ? "Hide" : "View"}
          </button>
        ) : (
          <button
            type="button"
            onClick={() => request([i.key])}
            disabled={!canEdit}
            className="flex w-28 items-center justify-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-brand-700 hover:bg-gray-50 disabled:opacity-50"
          >
            <SendIcon /> Request
          </button>
        )}
        <button
          type="button"
          onClick={() => setMenu(menu === i.key ? null : i.key)}
          className="rounded px-2 py-1 text-lg leading-none text-gray-500 hover:bg-gray-100"
          aria-label={`More for ${i.name}`}
        >
          ⋮
        </button>
      </div>
      {menu === i.key && (
        <div className="absolute right-4 z-10 mt-1 w-48 rounded-lg border border-gray-200 bg-white py-1 text-sm shadow-lg">
          <button
            type="button"
            onClick={() => openFolder(i.files[0]?.path ?? i.folder)}
            className="block w-full px-3 py-1.5 text-left hover:bg-gray-50"
          >
            Open folder
          </button>
          <button
            type="button"
            onClick={() => copy(i.folder, "The folder path")}
            className="block w-full px-3 py-1.5 text-left hover:bg-gray-50"
          >
            Copy folder path
          </button>
          {canEdit && (
            <button
              type="button"
              onClick={() => request([i.key])}
              className="block w-full px-3 py-1.5 text-left hover:bg-gray-50"
            >
              {i.received ? "Request again" : "Request"}
            </button>
          )}
        </div>
      )}
    </>
  );

  const toggleGroup = (key: string) =>
    setClosed((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  // --- one table for every system, a row per group --------------------------------------------------

  const q = query.trim().toLowerCase();
  const matches = (i: RequiredItem) =>
    !q ||
    [i.name, i.purpose, i.format, i.remarks].some((t) =>
      t.toLowerCase().includes(q),
    );
  let n = 0;
  return (
    <div className="space-y-5">
      {systemTabs}
      {messages}
      <div className="rounded-xl border border-gray-200 bg-white">
        <div className="flex flex-wrap items-start justify-between gap-3 p-5">
          <div>
            <h2 className="text-xl font-bold text-navy-900">
              Actions Required – {data.system}
            </h2>
            <p className="mt-1 text-sm text-gray-500">
              Request the required documents from the contractor. Received when
              its file is in its folder of the project folder on OneDrive.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search document…"
              aria-label="Search document"
              className="w-60 rounded-lg border border-gray-300 px-3 py-2 text-sm"
            />
            <button
              type="button"
              onClick={exportList}
              className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50"
            >
              Export
            </button>
            {canEdit && missing.length > 0 && (
              <button
                type="button"
                onClick={() => request(missing.map((i) => i.key))}
                className="flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700"
              >
                <SendIcon /> Request all not received ({missing.length})
              </button>
            )}
          </div>
        </div>
        <div className="overflow-x-auto px-5 pb-5">
          <table className="w-full min-w-[1100px] table-fixed text-sm">
            <colgroup>
              <col className="w-10" />
              <col className="w-[15%]" />
              <col className="w-[20%]" />
              <col className="w-[7%]" />
              <col className="w-[10%]" />
              <col className="w-[8%]" />
              <col className="w-[8%]" />
              <col />
              <col className="w-[160px]" />
            </colgroup>
            <thead className="border border-gray-200 bg-gray-50 text-left text-xs font-semibold text-navy-900">
              <tr>
                <th className="px-3 py-3">#</th>
                <th className="px-3 py-3">Document Category</th>
                <th className="px-3 py-3">Description / Scope</th>
                <th className="px-3 py-3">Format</th>
                <th className="px-3 py-3">Status</th>
                <th className="px-3 py-3">Requested Date</th>
                <th className="px-3 py-3">Received Date</th>
                <th className="px-3 py-3">Remarks</th>
                <th className="px-3 py-3">Action</th>
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
                        <button
                          type="button"
                          onClick={() => toggleGroup(g.key)}
                          aria-expanded={open}
                          className="flex items-center gap-3 font-semibold text-navy-900"
                        >
                          <span className="w-3 text-gray-500">
                            {open ? "▾" : "▸"}
                          </span>
                          <GroupIcon group={g.key} />
                          {g.name}
                        </button>
                      </td>
                    </tr>
                    {open &&
                      shown.map((i) => {
                        n += 1;
                        return (
                          <tr
                            key={i.key}
                            className="border-x border-t border-gray-200 align-top last:border-b"
                          >
                            <td className="px-3 py-3 text-gray-500">{n}</td>
                            <td className="px-3 py-3">
                              <div className="font-medium text-navy-900">
                                {i.name}
                              </div>
                              <div
                                className="truncate text-xs text-gray-500"
                                title={i.folder}
                              >
                                {i.folder}
                              </div>
                              {fileList(i)}
                            </td>
                            <td className="px-3 py-3 text-gray-700">
                              {i.purpose}
                            </td>
                            <td className="px-3 py-3 text-gray-700">
                              {i.format}
                            </td>
                            <td className="px-3 py-3">
                              <StatusChip received={i.received} />
                            </td>
                            <td className="whitespace-nowrap px-3 py-3 text-gray-700">
                              {day(i.requested_at)}
                            </td>
                            <td className="whitespace-nowrap px-3 py-3 text-gray-700">
                              {day(i.received_date)}
                            </td>
                            <td className="px-3 py-3 text-gray-700">
                              {i.remarks}
                            </td>
                            <td className="relative px-3 py-3">
                              {actions(i, true)}
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
