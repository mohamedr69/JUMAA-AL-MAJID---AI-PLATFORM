import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api, apiUrl } from "../lib/api";
import { formatApiDate } from "../lib/format";
import type { DatasheetProposal, EquipmentAudit, EquipmentCurrent } from "../lib/types";

const KIND: Record<EquipmentCurrent["kind"], { label: string; className: string }> = {
  mechanical: { label: "No load", className: "bg-gray-100 text-gray-700 ring-gray-300" },
  built_in: { label: "Built in", className: "bg-sky-50 text-sky-800 ring-sky-200" },
  device: { label: "Draws current", className: "bg-emerald-50 text-emerald-800 ring-emerald-200" },
  unknown: { label: "Figure needed", className: "bg-amber-50 text-amber-900 ring-amber-200" },
};

type Draft = {
  part_no: string;
  description: string;
  no_load: boolean;
  standby_ma: string;
  alarm_ma: string;
  included_in: string;
  source: string;
  aliases: string;
};

const EMPTY: Draft = { part_no: "", description: "", no_load: false, standby_ma: "", alarm_ma: "", included_in: "", source: "", aliases: "" };

function toDraft(row: EquipmentCurrent): Draft {
  return {
    part_no: row.part_no,
    description: row.description ?? "",
    no_load: row.no_load,
    standby_ma: row.standby_ma === null ? "" : String(row.standby_ma),
    alarm_ma: row.alarm_ma === null ? "" : String(row.alarm_ma),
    included_in: row.included_in ?? "",
    source: row.source,
    aliases: row.aliases.join(", "),
  };
}

/** The equipment current table: what each part of a fire alarm system
 * draws, settled once for every project. A part here is never offered for
 * confirmation on a battery page; a part missing here is set automatically
 * and asked about, and joins the table when an engineer answers. */
export function AdminEquipmentCurrentsPage() {
  const [rows, setRows] = useState<EquipmentCurrent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<{ id: number | null; draft: Draft } | null>(null);
  const [busy, setBusy] = useState(false);
  const [audit, setAudit] = useState<EquipmentAudit | null>(null);
  const [auditing, setAuditing] = useState(false);
  const [proposals, setProposals] = useState<DatasheetProposal[] | null>(null);
  const [loadingProposals, setLoadingProposals] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api.get<EquipmentCurrent[]>("/design-rules/equipment-currents"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not read the equipment current table");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!rows) return [];
    if (!needle) return rows;
    return rows.filter(
      (r) =>
        r.part_no.toLowerCase().includes(needle) ||
        (r.description ?? "").toLowerCase().includes(needle) ||
        r.aliases.some((a) => a.toLowerCase().includes(needle))
    );
  }, [rows, filter]);

  const counts = useMemo(() => {
    const all = rows ?? [];
    return {
      total: all.length,
      noLoad: all.filter((r) => r.no_load).length,
      devices: all.filter((r) => r.kind === "device").length,
      unknown: all.filter((r) => !r.settled).length,
    };
  }, [rows]);

  async function save() {
    if (!editing) return;
    const d = editing.draft;
    setBusy(true);
    setError(null);
    try {
      await api.post<EquipmentCurrent>("/design-rules/equipment-currents", {
        part_no: d.part_no.trim(),
        description: d.description.trim() || null,
        no_load: d.no_load,
        standby_ma: d.no_load || d.standby_ma === "" ? null : Number(d.standby_ma),
        alarm_ma: d.no_load || d.alarm_ma === "" ? null : Number(d.alarm_ma),
        included_in: d.no_load && d.included_in.trim() ? d.included_in.trim() : null,
        source: d.source.trim(),
        aliases: d.aliases
          .split(",")
          .map((a) => a.trim())
          .filter(Boolean),
      });
      setEditing(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The row could not be saved");
    } finally {
      setBusy(false);
    }
  }

  async function remove(row: EquipmentCurrent) {
    if (!window.confirm(`Remove ${row.part_no} from the equipment current table? Projects keep what they computed; the part is no longer settled here.`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.delete(`/design-rules/equipment-currents/${row.id}`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The row could not be removed");
    } finally {
      setBusy(false);
    }
  }

  async function runAudit() {
    setAuditing(true);
    setError(null);
    try {
      setAudit(await api.post<EquipmentAudit>("/design-rules/equipment-currents/audit"));
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The audit could not run");
    } finally {
      setAuditing(false);
    }
  }

  async function loadProposals() {
    setLoadingProposals(true);
    setError(null);
    try {
      setProposals(await api.get<DatasheetProposal[]>("/design-rules/datasheets/proposals"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The datasheet proposals could not be read");
    } finally {
      setLoadingProposals(false);
    }
  }

  async function confirmProposal(proposal: DatasheetProposal) {
    const key = `${proposal.library}:${proposal.path}:${proposal.part_no}`;
    setConfirming(key);
    setError(null);
    try {
      await api.post("/design-rules/datasheets/proposals/confirm", {
        manufacturer: proposal.library,
        part_no: proposal.part_no,
        library: proposal.library,
        path: proposal.path,
        pages: proposal.pages,
      });
      setProposals((current) => (current ?? []).filter((item) => `${item.library}:${item.path}:${item.part_no}` !== key));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The datasheet link could not be saved");
    } finally {
      setConfirming(null);
    }
  }

  const draft = editing?.draft;
  const ready =
    !!draft &&
    draft.part_no.trim().length > 0 &&
    draft.source.trim().length >= 3 &&
    (draft.no_load || (draft.standby_ma === "" && draft.alarm_ma === "") || (draft.standby_ma !== "" && draft.alarm_ma !== ""));

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-navy-900">Equipment current table</h1>
          <p className="mt-1 max-w-3xl text-sm text-gray-500">
            What each part of a fire alarm system draws, settled once for every project: the mechanical parts that draw nothing
            (backboxes, chassis, doors, brackets, filler plates, battery cabinets), the parts built into another module, and the
            devices with their standby and alarm figures. A part in this table is never offered for confirmation on a battery page.
            A part missing here is set automatically and asked about once; the engineer&apos;s answer is written here.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={runAudit}
            disabled={auditing}
            className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-60"
            title="Link every part to its datasheet in the library and re-read each figure off it"
          >
            {auditing ? "Reading the datasheets..." : "Audit datasheets"}
          </button>
          <button
            onClick={() => void loadProposals()}
            disabled={loadingProposals}
            className="rounded-lg border border-brand-300 bg-brand-50 px-3 py-1.5 text-sm font-semibold text-brand-800 hover:bg-brand-100 disabled:opacity-60"
          >
            {loadingProposals ? "Finding part numbers..." : "Review PDF part numbers"}
          </button>
          <button
            onClick={() => setEditing({ id: null, draft: EMPTY })}
            className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-700"
          >
            Add a part
          </button>
        </div>
      </div>

      {audit && (
        <section aria-label="Datasheet audit" className={`mt-4 rounded-xl border p-4 ${audit.findings.length ? "border-amber-200 bg-amber-50/60" : "border-emerald-200 bg-emerald-50/60"}`}>
          <div className="text-sm font-semibold text-navy-900">
            Datasheet audit: {audit.rows} parts checked against the library
            {audit.linked > 0 ? ` · ${audit.linked} newly linked to their sheet` : ""}
            {audit.findings.length === 0 ? " · every part has its datasheet and the figures still read the same" : ` · ${audit.findings.length} to look at`}
          </div>
          {audit.findings.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs">
              {audit.findings.map((f) => (
                <li key={f.id} className="flex flex-wrap gap-x-2">
                  <span className="font-mono font-semibold text-navy-900">{f.part_no}</span>
                  <span className="rounded-full bg-white px-2 py-0.5 text-[11px] font-medium text-amber-800 ring-1 ring-amber-200">{f.status.replace(/_/g, " ")}</span>
                  <span className="text-gray-700">{f.detail}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <div className="mt-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <div className="rounded-xl border border-gray-200 bg-white p-3">
          <div className="text-xs text-gray-500">Parts</div>
          <div className="text-xl font-semibold text-navy-900">{counts.total}</div>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-3">
          <div className="text-xs text-gray-500">Draw no current</div>
          <div className="text-xl font-semibold text-navy-900">{counts.noLoad}</div>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-3">
          <div className="text-xs text-gray-500">With a figure</div>
          <div className="text-xl font-semibold text-navy-900">{counts.devices}</div>
        </div>
        <div className={`rounded-xl border p-3 ${counts.unknown > 0 ? "border-amber-200 bg-amber-50" : "border-gray-200 bg-white"}`}>
          <div className="text-xs text-gray-500">Figure still needed</div>
          <div className="text-xl font-semibold text-navy-900">{counts.unknown}</div>
        </div>
      </div>

      {error && <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

      {proposals !== null && (
        <section aria-label="Datasheet part proposals" className="mt-4 rounded-xl border border-amber-200 bg-amber-50/60 p-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold text-navy-900">Part numbers found inside datasheets</h2>
              <p className="mt-1 text-xs text-gray-700">Confirm only when the PDF actually documents the material. Nothing here is saved until you confirm it.</p>
            </div>
            <span className="text-xs font-semibold text-amber-800">{proposals.length} awaiting review</span>
          </div>
          {proposals.length > 0 && (
            <div className="mt-3 overflow-x-auto rounded-lg border border-amber-200 bg-white">
              <table className="w-full text-left text-xs">
                <thead className="border-b border-gray-200 bg-gray-50 text-gray-500">
                  <tr><th className="px-3 py-2">Manufacturer</th><th className="px-3 py-2">Part number</th><th className="px-3 py-2">Datasheet</th><th className="px-3 py-2">Pages</th><th className="px-3 py-2" /></tr>
                </thead>
                <tbody>
                  {proposals.map((proposal) => {
                    const key = `${proposal.library}:${proposal.path}:${proposal.part_no}`;
                    return (
                      <tr key={key} className="border-b border-gray-100 last:border-0">
                        <td className="px-3 py-2 font-semibold text-navy-900">{proposal.library}</td>
                        <td className="px-3 py-2 font-mono font-semibold text-navy-900">{proposal.part_no}</td>
                        <td className="px-3 py-2"><a className="text-brand-700 hover:underline" href={`${apiUrl(`/design-rules/datasheets/file?${new URLSearchParams({ library: proposal.library, path: proposal.path })}`)}#page=${proposal.pages[0] ?? 1}`} target="_blank" rel="noreferrer">{proposal.filename}</a></td>
                        <td className="px-3 py-2 text-gray-600">{proposal.pages.join(", ") || "-"}</td>
                        <td className="px-3 py-2 text-right"><button onClick={() => void confirmProposal(proposal)} disabled={confirming !== null} className="rounded-md bg-emerald-600 px-2.5 py-1.5 font-semibold text-white hover:bg-emerald-700 disabled:opacity-60">{confirming === key ? "Saving..." : "Confirm material"}</button></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {editing && draft && (
        <section aria-label={editing.id === null ? "Add a part" : `Edit ${draft.part_no}`} className="mt-4 rounded-xl border border-brand-200 bg-brand-50/40 p-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="text-xs text-gray-600">
              Part number
              <input
                value={draft.part_no}
                disabled={editing.id !== null}
                onChange={(e) => setEditing({ ...editing, draft: { ...draft, part_no: e.target.value } })}
                className="input mt-1 py-1"
                placeholder="3-CAB14B"
              />
            </label>
            <label className="text-xs text-gray-600 lg:col-span-3">
              Description
              <input
                value={draft.description}
                onChange={(e) => setEditing({ ...editing, draft: { ...draft, description: e.target.value } })}
                className="input mt-1 py-1"
              />
            </label>
            <label className="flex items-center gap-2 text-sm text-navy-900">
              <input
                type="checkbox"
                checked={draft.no_load}
                onChange={(e) => setEditing({ ...editing, draft: { ...draft, no_load: e.target.checked } })}
              />
              Draws no current
            </label>
            {draft.no_load ? (
              <label className="text-xs text-gray-600">
                Built into (optional)
                <input
                  value={draft.included_in}
                  onChange={(e) => setEditing({ ...editing, draft: { ...draft, included_in: e.target.value } })}
                  className="input mt-1 py-1"
                  placeholder="4-CPU"
                />
              </label>
            ) : (
              <>
                <label className="text-xs text-gray-600">
                  Standby (mA)
                  <input
                    type="number"
                    min={0}
                    step="any"
                    value={draft.standby_ma}
                    onChange={(e) => setEditing({ ...editing, draft: { ...draft, standby_ma: e.target.value } })}
                    className="input mt-1 py-1"
                  />
                </label>
                <label className="text-xs text-gray-600">
                  Alarm (mA)
                  <input
                    type="number"
                    min={0}
                    step="any"
                    value={draft.alarm_ma}
                    onChange={(e) => setEditing({ ...editing, draft: { ...draft, alarm_ma: e.target.value } })}
                    className="input mt-1 py-1"
                  />
                </label>
              </>
            )}
            <label className="text-xs text-gray-600 sm:col-span-2">
              Source (datasheet and page, or who decided)
              <input
                value={draft.source}
                onChange={(e) => setEditing({ ...editing, draft: { ...draft, source: e.target.value } })}
                className="input mt-1 py-1"
              />
            </label>
            <label className="text-xs text-gray-600 sm:col-span-2">
              Other spellings the scans produce (comma separated)
              <input
                value={draft.aliases}
                onChange={(e) => setEditing({ ...editing, draft: { ...draft, aliases: e.target.value } })}
                className="input mt-1 py-1"
                placeholder="3-CABSB"
              />
            </label>
          </div>
          <div className="mt-3 flex gap-2">
            <button
              onClick={save}
              disabled={!ready || busy}
              className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
            >
              {busy ? "Saving..." : "Save"}
            </button>
            <button onClick={() => setEditing(null)} className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-navy-900 hover:bg-gray-50">
              Cancel
            </button>
            {!draft.no_load && draft.standby_ma === "" && draft.alarm_ma === "" && (
              <span className="self-center text-xs text-amber-800">No figure: the part is recorded as drawing current, figure still needed.</span>
            )}
          </div>
        </section>
      )}

      <div className="mt-4 flex items-center gap-3">
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter by part number or description" className="input max-w-md py-1.5" />
        <span className="text-xs text-gray-500">
          {rows ? `${shown.length} of ${rows.length}` : "Loading..."}
        </span>
      </div>

      <div className="mt-3 overflow-x-auto rounded-xl border border-gray-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
            <tr>
              <th className="px-3 py-2">Part</th>
              <th className="px-3 py-2">Description</th>
              <th className="px-3 py-2">Current</th>
              <th className="px-3 py-2 text-right">Standby mA</th>
              <th className="px-3 py-2 text-right">Alarm mA</th>
              <th className="px-3 py-2">Source</th>
              <th className="px-3 py-2">Datasheet</th>
              <th className="px-3 py-2">Settled by</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {shown.map((row) => (
              <tr key={row.id} className={row.settled ? "" : "bg-amber-50/50"}>
                <td className="px-3 py-2 align-top font-medium text-navy-900">
                  {row.part_no}
                  {row.aliases.length > 0 && <div className="text-xs font-normal text-gray-500">also {row.aliases.join(", ")}</div>}
                </td>
                <td className="px-3 py-2 align-top text-gray-700">{row.description ?? "—"}</td>
                <td className="px-3 py-2 align-top">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${KIND[row.kind].className}`}>{KIND[row.kind].label}</span>
                  {row.included_in && <div className="text-xs text-gray-500">in {row.included_in}</div>}
                </td>
                <td className="px-3 py-2 text-right align-top tabular-nums">{row.no_load ? "0" : row.standby_ma ?? "—"}</td>
                <td className="px-3 py-2 text-right align-top tabular-nums">{row.no_load ? "0" : row.alarm_ma ?? "—"}</td>
                <td className="max-w-md px-3 py-2 align-top text-xs text-gray-600">{row.source}</td>
                <td className="px-3 py-2 align-top text-xs">
                  {row.datasheet_path ? (
                    <a
                      href={apiUrl(
                        `/design-rules/datasheets/file?${new URLSearchParams({ library: row.datasheet_library ?? "", path: row.datasheet_path })}#page=${row.datasheet_pages[0] ?? 1}`
                      )}
                      target="_blank"
                      rel="noreferrer"
                      className="text-brand-600 hover:underline"
                      title={`${row.datasheet_path}${row.datasheet_pages.length ? ` p.${row.datasheet_pages.join(", ")}` : ""} · matched by ${row.datasheet_match ?? "?"}`}
                    >
                      {row.datasheet_path.split("/").pop()}
                      {row.datasheet_pages.length > 0 && <span className="text-gray-500"> p.{row.datasheet_pages[0]}</span>}
                    </a>
                  ) : (
                    <span className="text-gray-400">none</span>
                  )}
                  {row.datasheet_match === "text" && !row.no_load && (
                    <div className="text-amber-700" title="The sheet only mentions the part in its text: confirm the figure against the part's own sheet">
                      mention only
                    </div>
                  )}
                </td>
                <td className="px-3 py-2 align-top text-xs text-gray-600">
                  {row.confirmed_by ?? "not confirmed"}
                  <div className="text-gray-400">{formatApiDate(row.updated_at ?? row.created_at, "short")}</div>
                </td>
                <td className="px-3 py-2 align-top text-right">
                  <div className="flex justify-end gap-2">
                    <button onClick={() => setEditing({ id: row.id, draft: toDraft(row) })} className="text-xs font-semibold text-brand-700 hover:underline">
                      Edit
                    </button>
                    <button onClick={() => remove(row)} disabled={busy} className="text-xs font-semibold text-red-700 hover:underline disabled:opacity-60">
                      Remove
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {rows && shown.length === 0 && (
              <tr>
                <td colSpan={9} className="px-3 py-6 text-center text-sm text-gray-500">
                  No part matches.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
