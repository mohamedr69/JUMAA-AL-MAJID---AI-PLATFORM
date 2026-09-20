/** The BOQ page's "BOQ Floor Wise" tab: the floor-wise BOQ read off the
 * schedule an engineer keeps in Excel.
 *
 * The table is laid out as the workbook is. A schedule does not write out
 * thirteen identical columns; it writes "1 to 13" once, and so does this:
 * one column, headed as the sheet heads it, carrying the quantity on each
 * of its floors. The floors are still counted one by one underneath, so
 * every total includes all thirteen.
 *
 * A fire alarm job and an emergency lighting job are two BOQs, so the
 * table is shown a system at a time.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { API_BASE_URL, ApiError, api } from "../lib/api";
import type {
  FloorSchedule,
  FloorScheduleCheck,
  FloorScheduleItem,
  FloorScheduleMaterial,
  FloorScheduleResult,
} from "../lib/types";
import { formatApiDate } from "../lib/format";

/** What each system's tab is called. A line the platform could not name
 * belongs to neither, and is shown in its own tab rather than dropped --
 * the tabs must always account for every row on the sheet. */
const SYSTEM_LABELS: Record<string, string> = {
  FAS: "Fire alarm (FA)",
  ELS: "Emergency lighting (ELS)",
  "": "Not recognised",
};

export function FloorScheduleTab({ projectId, canEdit }: { projectId: number; canEdit: boolean }) {
  const [data, setData] = useState<FloorSchedule | null>(null);
  const [check, setCheck] = useState<FloorScheduleCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<File | null>(null);
  const [replacing, setReplacing] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const body = await api.get<FloorSchedule>(`/projects/${projectId}/floor-schedule`);
      setData(body);
      if (body.result) {
        try {
          setCheck(await api.get<FloorScheduleCheck>(`/projects/${projectId}/floor-schedule/check`));
        } catch {
          setCheck(null);
        }
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The schedule could not be loaded");
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function read() {
    if (!chosen) return;
    setBusy(true);
    setError(null);
    try {
      const body = new FormData();
      body.append("file", chosen);
      const response = await fetch(`${API_BASE_URL}/projects/${projectId}/floor-schedule`, {
        method: "POST",
        credentials: "include",
        body,
      });
      if (!response.ok) {
        let detail: unknown = response.statusText;
        try {
          detail = (await response.json()).detail ?? detail;
        } catch {
          /* no JSON body */
        }
        throw new ApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      setData((await response.json()) as FloorSchedule);
      setChosen(null);
      setReplacing(false);
      if (input.current) input.current.value = "";
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The schedule could not be read");
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    if (!window.confirm("Clear the floor-wise BOQ? The workbook itself is not touched.")) return;
    try {
      await api.delete(`/projects/${projectId}/floor-schedule`);
      setData(null);
      setCheck(null);
      setReplacing(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "It could not be cleared");
    }
  }

  const result = data?.result ?? null;
  // A schedule already linked to the project is shown as linked: the file
  // it came from, where it is filed, and a Replace button. Handing another
  // one in is a deliberate act, not the first thing on the page.
  const linked = Boolean(result);
  return (
    <div className="mt-4">
      <section className="rounded-xl border border-gray-200 bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-bold text-navy-900">
              {linked ? "The floor-wise BOQ is linked" : "The floor-wise schedule"}
            </h2>
            {linked ? (
              <p className="mt-1 max-w-2xl text-sm text-gray-600">
                Read from <span className="font-medium text-navy-900">{data?.file_name}</span>
                {data?.sheet_name ? ` · ${data.sheet_name}` : ""}
                {data?.source_path ? (
                  <>
                    {" "}in <span className="font-medium text-navy-900">{data.source_path}</span>. It is read
                    again by itself whenever that workbook changes, so it never needs handing in twice.
                  </>
                ) : (
                  "."
                )}
              </p>
            ) : (
              <p className="mt-1 max-w-2xl text-sm text-gray-600">
                Nothing is filed in this project&rsquo;s <span className="font-medium text-navy-900">03- Design</span>{" "}
                folder yet. Hand in the Excel schedule the project is run from &mdash; a row per item and a
                column per floor &mdash; and it is kept there and read again by itself whenever it changes. A
                typical column, <span className="font-medium text-navy-900">1 to 13</span>, stays one column as
                in the sheet, and its quantity is counted on each of its floors.
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            {linked && (
              <a
                href={`${API_BASE_URL}/projects/${projectId}/floor-schedule/export.pdf`}
                className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-gray-700"
              >
                Export PDF
              </a>
            )}
            {data?.updated_at && (
              <span className="text-xs text-gray-500">read {formatApiDate(data.updated_at, "short")}</span>
            )}
          </div>
        </div>

        {canEdit && (linked ? replacing : true) && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <input
              ref={input}
              type="file"
              accept=".xlsx,.xlsm"
              onChange={(e) => setChosen(e.target.files?.[0] ?? null)}
              className="text-sm"
            />
            <button
              onClick={() => void read()}
              disabled={!chosen || busy}
              className="rounded-lg bg-brand-600 px-4 py-1.5 text-sm font-semibold text-white disabled:opacity-60"
            >
              {busy ? "Reading..." : linked ? "Replace the schedule" : "Read the schedule"}
            </button>
            {linked && (
              <button
                onClick={() => {
                  setReplacing(false);
                  setChosen(null);
                  if (input.current) input.current.value = "";
                }}
                className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700"
              >
                Cancel
              </button>
            )}
          </div>
        )}
        {canEdit && linked && !replacing && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              onClick={() => setReplacing(true)}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-gray-700"
            >
              Replace
            </button>
            <button onClick={() => void clear()} className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700">
              Clear
            </button>
          </div>
        )}
        {error && <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p>}
        {data?.filed_note && (
          <p
            className={`mt-3 rounded-lg px-3 py-2 text-sm ${
              data.source_path ? "bg-sky-50 text-sky-900" : "bg-amber-50 text-amber-900"
            }`}
          >
            {data.filed_note}
          </p>
        )}
      </section>

      {result && (
        <ScheduleTable
          projectId={projectId}
          result={result}
          canEdit={canEdit}
          onChanged={(next) => setData((was) => (was ? { ...was, result: next } : was))}
        />
      )}
      {result && check && <AgainstBoq check={check} />}

      {!result && !busy && (
        <p className="mt-4 rounded-xl border border-dashed border-gray-200 p-6 text-center text-sm text-gray-400">
          No schedule has been read yet.
        </p>
      )}
    </div>
  );
}

function ScheduleTable({
  projectId,
  result,
  canEdit,
  onChanged,
}: {
  projectId: number;
  result: FloorScheduleResult;
  canEdit: boolean;
  onChanged: (next: FloorScheduleResult) => void;
}) {
  const typical = (result.columns ?? []).filter((column) => column.typical);
  // A schedule stored before the systems existed has no `systems` at all.
  // The tabs are then worked out from the rows themselves, so an older
  // schedule opens instead of throwing on Object.keys(undefined).
  const totalsBySystem = useMemo(() => {
    if (result.systems && Object.keys(result.systems).length > 0) return result.systems;
    const found: Record<string, number> = {};
    for (const item of result.items ?? []) {
      const code = item.system ?? "";
      found[code] = (found[code] ?? 0) + (item.total ?? 0);
    }
    return found;
  }, [result.systems, result.items]);
  const systems = useMemo(
    () =>
      Object.keys(totalsBySystem).sort(
        (a, b) => (a === "" ? 1 : b === "" ? -1 : a === "FAS" ? -1 : b === "FAS" ? 1 : a.localeCompare(b)),
      ),
    [totalsBySystem],
  );
  const [system, setSystem] = useState(systems[0] ?? "");
  const [materials, setMaterials] = useState<FloorScheduleMaterial[]>([]);
  // Each line's own order for the same parts: a smoke detector line opens
  // on the smoke detector. Worked out by the server, once for the system.
  const [orderByLine, setOrderByLine] = useState<Record<number, string[]>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [familyFilter, setFamilyFilter] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const options = await api.get<{ materials: FloorScheduleMaterial[]; by_line: Record<number, string[]> }>(
          `/projects/${projectId}/floor-schedule/materials/by-line${system ? `?system=${encodeURIComponent(system)}` : ""}`,
        );
        if (live) {
          setMaterials(options.materials);
          setOrderByLine(options.by_line ?? {});
        }
      } catch {
        if (live) {
          setMaterials([]);
          setOrderByLine({});
        }
      }
    })();
    return () => {
      live = false;
    };
  }, [projectId, system]);

  // The parts this line may be settled as, the ones its wording asks for
  // first. An order the server did not send leaves the list as it came.
  const byPart = new Map(materials.map((material) => [material.part_no, material]));
  const optionsFor = (row: number | null | undefined): FloorScheduleMaterial[] => {
    const order = row == null ? undefined : orderByLine[row];
    if (!order) return materials;
    const ranked = order.map((part) => byPart.get(part)).filter((m): m is FloorScheduleMaterial => Boolean(m));
    // Anything the ordering did not mention still belongs in the list.
    return [...ranked, ...materials.filter((material) => !order.includes(material.part_no))];
  };

  const rows = (result.items ?? []).filter((item) => (item.system ?? "") === system);
  const floors = result.floors ?? [];
  const columns = useMemo(() => sheetColumns(result), [result]);
  const floorTotals = Object.fromEntries(
    floors.map((floor) => [floor, rows.reduce((sum, item) => sum + (item.per_floor?.[floor] ?? 0), 0)]),
  );
  const total = rows.reduce((sum, item) => sum + (item.total ?? 0), 0);

  /** A quantity is only ever stepped, never typed: the schedule is a count
   * of devices, and a keyboard invites a decimal or a paste. A typical
   * column is stepped as one: every floor it stands for gets the new
   * quantity. */
  async function step(item: FloorScheduleItem, column: SheetColumn, by: number) {
    const values = column.floors.map((floor) => item.per_floor?.[floor] ?? 0);
    const next = Math.max(0, Math.max(...values) + by);
    if (values.every((value) => value === next)) return;
    setSaving(`${item.row}:${column.heading}`);
    try {
      const body = await api.patch<FloorSchedule>(
        `/projects/${projectId}/floor-schedule/items/${item.row}`,
        { floors: column.floors, quantity: next },
      );
      if (body.result) onChanged(body.result);
    } finally {
      setSaving(null);
    }
  }

  async function choose(item: FloorScheduleItem, part: string) {
    setSaving(`${item.row}:part`);
    try {
      const body = await api.patch<FloorSchedule>(
        `/projects/${projectId}/floor-schedule/items/${item.row}/material`,
        { part_no: part || null },
      );
      if (body.result) onChanged(body.result);
    } finally {
      setSaving(null);
    }
  }

  // What is shown: the system's lines, narrowed by the family chip and the
  // search box, grouped under their families in the order a BOQ is read.
  // Only the view changes -- every total below is the system's own.
  const familyOf = (item: FloorScheduleItem) => item.family || OTHER_FAMILY;
  const familyCounts = new Map<string, number>();
  for (const item of rows) familyCounts.set(familyOf(item), (familyCounts.get(familyOf(item)) ?? 0) + 1);
  const families = [...familyCounts.keys()].sort((a, b) => familyRank(a) - familyRank(b));
  const needle = search.trim().toLowerCase();
  const shown = rows.filter(
    (item) =>
      (!familyFilter || familyOf(item) === familyFilter) &&
      (!needle ||
        [item.description, item.catalog_no, item.device, item.material?.part_no, item.material?.manufacturer]
          .filter(Boolean)
          .join(" ")
          .toLowerCase()
          .includes(needle)),
  );
  const groups = families
    .map((family) => ({ family, items: shown.filter((item) => familyOf(item) === family) }))
    .filter((group) => group.items.length > 0);
  // Numbered down the page as shown, family by family.
  const numbers = new Map(groups.flatMap((group) => group.items).map((item, index) => [item.row, index + 1]));
  const fixedColumns = 4; // #, item, material, unit

  return (
    <>
      <section className="mt-4 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-5 py-4">
          <div className="flex flex-wrap items-center gap-3">
            {systems.length > 1 ? (
              <div className="inline-flex rounded-xl border border-gray-200 bg-gray-50 p-1" role="tablist" aria-label="System">
                {systems.map((code) => (
                  <button
                    key={code}
                    role="tab"
                    aria-selected={code === system}
                    onClick={() => {
                      setSystem(code);
                      setFamilyFilter(null);
                    }}
                    className={`rounded-lg px-4 py-2 text-sm font-semibold transition ${
                      code === system ? "bg-brand-600 text-white shadow-sm" : "text-gray-600 hover:text-navy-900"
                    }`}
                  >
                    {SYSTEM_LABELS[code] ?? code}
                    <span className={`ml-2 text-xs font-normal ${code === system ? "text-white/80" : "text-gray-400"}`}>
                      {count(totalsBySystem[code] ?? 0)}
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <h2 className="text-base font-bold text-navy-900">{SYSTEM_LABELS[system] ?? system}</h2>
            )}
            <span className="text-xs text-gray-500">
              {floors.length} floor{floors.length === 1 ? "" : "s"} in {columns.length} column{columns.length === 1 ? "" : "s"}
            </span>
          </div>
          <label className="relative block w-full sm:w-80">
            <span className="sr-only">Search items</span>
            <svg aria-hidden="true" viewBox="0 0 20 20" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" fill="none" stroke="currentColor" strokeWidth="1.8">
              <circle cx="9" cy="9" r="5.5" />
              <path d="m13.5 13.5 3.5 3.5" strokeLinecap="round" />
            </svg>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search item, part number, or keyword..."
              className="w-full rounded-xl border border-gray-200 py-2 pl-9 pr-3 text-sm focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/20"
            />
          </label>
        </div>

        <div className="flex flex-wrap gap-2 border-b border-gray-100 px-5 py-3">
          <FamilyChip label="All" count={rows.length} active={familyFilter === null} onClick={() => setFamilyFilter(null)} />
          {families.map((family) => (
            <FamilyChip
              key={family}
              family={family}
              label={family}
              count={familyCounts.get(family) ?? 0}
              active={familyFilter === family}
              onClick={() => setFamilyFilter(familyFilter === family ? null : family)}
            />
          ))}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full border-separate border-spacing-0 text-sm">
            <thead>
              <tr className="bg-gray-50 text-xs font-semibold text-gray-600">
                <th className="sticky left-0 z-20 w-12 min-w-12 max-w-12 border-b border-gray-200 bg-gray-50 px-3 py-3 text-center">#</th>
                <th className="sticky left-12 z-20 min-w-[16rem] border-b border-r border-gray-200 bg-gray-50 px-3 py-3 text-left">Item / Device</th>
                <th className="min-w-[11rem] border-b border-gray-200 px-3 py-3 text-left">Proposed material (part no.)</th>
                <th className="border-b border-gray-200 px-3 py-3 text-center">Unit</th>
                {columns.map((column) => (
                  <th key={column.heading} className="min-w-[5.5rem] border-b border-l border-gray-100 px-2 py-3 text-center leading-tight">
                    {column.heading}
                    {column.floors.length > 1 && (
                      <span
                        className="mt-0.5 block text-[10px] font-normal text-gray-400"
                        title={`${column.floors[0]} to ${column.floors[column.floors.length - 1]}: the quantity is on each floor`}
                      >
                        &times;{column.floors.length} floors
                      </span>
                    )}
                  </th>
                ))}
                <th className="border-b border-l border-gray-200 bg-gray-100 px-4 py-3 text-center">Total</th>
              </tr>
            </thead>
            {groups.map(({ family, items }) => {
              const style = familyStyle(family);
              const open = !collapsed.has(family);
              const groupTotal = items.reduce((sum, item) => sum + (item.total ?? 0), 0);
              return (
                <tbody key={family}>
                  <tr className={style.band}>
                    <td colSpan={2} className={`sticky left-0 z-10 border-b border-r border-gray-200 px-3 py-2.5 ${style.band}`}>
                      <button
                        type="button"
                        aria-expanded={open}
                        onClick={() =>
                          setCollapsed((was) => {
                            const next = new Set(was);
                            if (next.has(family)) next.delete(family);
                            else next.add(family);
                            return next;
                          })
                        }
                        className="flex items-center gap-3 whitespace-nowrap text-left"
                      >
                        <svg aria-hidden="true" viewBox="0 0 20 20" className={`h-4 w-4 text-gray-500 transition ${open ? "" : "-rotate-90"}`} fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="m5 8 5 5 5-5" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                        <FamilyIcon family={family} />
                        <span className={`text-base font-bold ${style.text}`}>{family}</span>
                        <span className="text-xs text-gray-500">
                          {items.length} item{items.length === 1 ? "" : "s"}
                        </span>
                      </button>
                    </td>
                    <td colSpan={fixedColumns - 2} className="border-b border-gray-200" />
                    {columns.map((column) => {
                      const values = column.floors.map((floor) =>
                        items.reduce((sum, item) => sum + (item.per_floor?.[floor] ?? 0), 0),
                      );
                      const most = Math.max(...values);
                      return (
                        <td key={column.heading} className="border-b border-gray-200 px-2 py-2.5 text-center font-bold tabular-nums text-navy-900">
                          {most ? (
                            <>
                              {spread(values) ?? count(most)}
                              <Times floors={column.floors.length} />
                            </>
                          ) : (
                            <span className="font-normal text-gray-300">&mdash;</span>
                          )}
                        </td>
                      );
                    })}
                    <td className="border-b border-l border-gray-200 bg-black/[0.03] px-4 py-2.5 text-center font-bold tabular-nums text-navy-900">
                      {count(groupTotal)}
                    </td>
                  </tr>
                  {open &&
                    items.map((item) => {
                      const sub = [item.catalog_no && item.catalog_no !== item.description ? item.catalog_no : null, item.device]
                        .filter(Boolean)
                        .join(" · ");
                      return (
                        <tr key={item.row} className="group/row hover:bg-gray-50/70">
                          <td className="sticky left-0 z-10 w-12 min-w-12 max-w-12 border-b border-gray-100 bg-white px-1 py-2 text-center text-xs text-gray-500 group-hover/row:bg-gray-50">
                            {numbers.get(item.row)}
                          </td>
                          <td className="sticky left-12 z-10 border-b border-r border-gray-100 bg-white px-3 py-2 group-hover/row:bg-gray-50">
                            <span className="block font-semibold text-navy-900">{item.description}</span>
                            <span className={`block text-xs ${item.device ? "text-gray-500" : "text-amber-700"}`}>
                              {sub || "Not recognised as a device"}
                            </span>
                          </td>
                          <td className="border-b border-gray-100 px-3 py-2">
                            {canEdit ? (
                              <select
                                aria-label={`Proposed material for ${item.description}`}
                                value={item.material?.part_no ?? ""}
                                disabled={saving === `${item.row}:part`}
                                onChange={(e) => void choose(item, e.target.value)}
                                className="w-44 rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs text-navy-900 focus:border-brand-600 focus:outline-none"
                              >
                                <option value="">&mdash; not settled &mdash;</option>
                                {optionsFor(item.row).map((material) => (
                                  <option key={material.part_no} value={material.part_no}>
                                    {material.part_no}
                                    {material.description ? ` — ${material.description.slice(0, 40)}` : ""}
                                  </option>
                                ))}
                              </select>
                            ) : (
                              <span className="block text-xs font-medium text-navy-900">{item.material?.part_no ?? "—"}</span>
                            )}
                            {item.material?.manufacturer && (
                              <span className="mt-0.5 block text-[11px] text-gray-400">{item.material.manufacturer}</span>
                            )}
                          </td>
                          <td className="border-b border-gray-100 px-3 py-2 text-center text-xs text-gray-600">
                            {item.unit ?? <span className="text-gray-300">&mdash;</span>}
                          </td>
                          {columns.map((column) => {
                            const values = column.floors.map((floor) => item.per_floor?.[floor] ?? 0);
                            return (
                              <td key={column.heading} className="border-b border-gray-100 px-1.5 py-1.5 text-center">
                                <Stepper
                                  value={Math.max(...values)}
                                  // Floors of a typical column changed one by one no longer agree: say so.
                                  label={spread(values)}
                                  times={column.floors.length}
                                  canEdit={canEdit}
                                  busy={saving === `${item.row}:${column.heading}`}
                                  onStep={(by) => void step(item, column, by)}
                                />
                              </td>
                            );
                          })}
                          <td className="border-b border-l border-gray-100 bg-gray-50 px-4 py-2 text-center font-semibold tabular-nums text-navy-900">
                            {count(item.total)}
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              );
            })}
            {groups.length > 0 && (
              <tfoot>
                <tr className="bg-gray-50">
                  <td colSpan={2} className="sticky left-0 z-10 border-r border-t-2 border-gray-200 bg-gray-50 px-3 py-3 font-bold text-navy-900">
                    {SYSTEM_LABELS[system] ?? system} total
                  </td>
                  <td colSpan={fixedColumns - 2} className="border-t-2 border-gray-200" />
                  {columns.map((column) => {
                    // Per floor, like the column's cells; the row total counts every floor.
                    const values = column.floors.map((floor) => floorTotals[floor] ?? 0);
                    const most = Math.max(...values);
                    return (
                      <td key={column.heading} className="border-t-2 border-gray-200 px-2 py-3 text-center font-bold tabular-nums text-navy-900">
                        {most ? (
                          <>
                            {spread(values) ?? count(most)}
                            <Times floors={column.floors.length} />
                          </>
                        ) : (
                          <span className="font-normal text-gray-300">&mdash;</span>
                        )}
                      </td>
                    );
                  })}
                  <td className="border-l border-t-2 border-gray-200 bg-gray-100 px-4 py-3 text-center font-bold tabular-nums text-navy-900">
                    {count(total)}
                  </td>
                </tr>
              </tfoot>
            )}
          </table>
          {groups.length === 0 && (
            <p className="p-10 text-center text-sm text-gray-500">{rows.length ? "No items match this view." : "No items in this system."}</p>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 bg-gray-50/60 px-5 py-3">
          <div className="flex items-center gap-4 rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-600">
            <span>
              Total items: <span className="font-semibold text-navy-900">{rows.length}</span>
            </span>
            <span aria-hidden="true" className="h-4 w-px bg-gray-200" />
            <span>
              Showing: <span className="font-semibold text-navy-900">{shown.length}</span> item{shown.length === 1 ? "" : "s"}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-gray-500">{count(result.grand_total ?? 0)} in all systems</span>
            <div className="flex items-center gap-4 rounded-xl bg-brand-600 px-5 py-2.5 text-white shadow-sm">
              <span className="text-sm font-semibold">
                <span aria-hidden="true" className="mr-2 text-lg leading-none">&Sigma;</span>
                Grand total
              </span>
              <span className="text-xl font-bold tabular-nums">{count(total)}</span>
            </div>
          </div>
        </div>
      </section>
      <section className="mt-4 grid gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-gray-200 bg-white p-5">
          <h2 className="text-sm font-bold text-navy-900">How the typical floors were read</h2>
          <p className="mt-2 text-sm text-gray-700">{result.typical_reason}</p>
          {result.stated_grand_total !== null && result.stated_grand_total !== result.grand_total && (
            <p className="mt-2 text-xs text-amber-900">
              The sheet totals {result.stated_grand_total}; {result.grand_total} could be counted floor by
              floor. The difference is the lines whose floor cells are not numbers.
            </p>
          )}
          {typical.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs text-gray-500">
              {typical.map((column) => (
                <li key={column.heading}>
                  <span className="font-medium text-navy-900">{column.heading}</span> &rarr;{" "}
                  {column.floors[0]} to {column.floors[column.floors.length - 1]} ({column.floors.length} floors)
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-5">
          <h2 className="text-sm font-bold text-navy-900">Notes</h2>
          {result.warnings.length === 0 ? (
            <p className="mt-2 text-sm text-gray-400">The schedule was read without a question.</p>
          ) : (
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-amber-900">
              {result.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </>
  );
}

/** "×14" beside a typical column's quantity: the quantity is on each of its
 * fourteen floors. Nothing for a single floor. */
function Times({ floors }: { floors: number }) {
  if (floors <= 1) return null;
  return <span className="ml-1 text-xs font-normal text-gray-400">&times;{floors}</span>;
}

/** A floor column as the workbook has it: a typical column once, standing
 * for its floors; every other floor on its own. The same rule as the PDF
 * export (`floor_schedule.sheet_columns` on the server). */
interface SheetColumn {
  heading: string;
  floors: string[];
}

function sheetColumns(result: FloorScheduleResult): SheetColumn[] {
  const floors = result.floors ?? [];
  const seen = new Set<string>();
  const columns: SheetColumn[] = [];
  for (const column of result.columns ?? []) {
    if (column.kind !== "floor") continue;
    const mine = (column.floors ?? []).filter((floor) => floors.includes(floor) && !seen.has(floor));
    if (mine.length === 0) continue;
    mine.forEach((floor) => seen.add(floor));
    columns.push({ heading: mine.length > 1 ? column.heading || mine[0] : mine[0], floors: mine });
  }
  for (const floor of floors) if (!seen.has(floor)) columns.push({ heading: floor, floors: [floor] });
  return columns.sort((a, b) => floors.indexOf(a.floors[0]) - floors.indexOf(b.floors[0]));
}

/** "2–3" where the floors of a typical column carry different quantities
 * (changed one by one before); nothing where they agree. */
function spread(values: number[]): string | undefined {
  return new Set(values).size > 1 ? `${count(Math.min(...values))}–${count(Math.max(...values))}` : undefined;
}

/** A quantity as it should read: whole where it is whole, and to two
 * places where a typical column's total did not divide evenly over its
 * floors. The stored figure keeps its full precision -- only the page
 * rounds. */
function count(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

/** One cell's quantity: stepped up and down, never typed.
 *
 * A blank is not a zero -- it means the item is not on that floor -- so a
 * cell at nothing shows a dash, and stepping down from one returns it to
 * a dash rather than writing a BOQ line of none. The value sits in a box;
 * the − and + come up when the pointer or the keyboard reaches it. */
function Stepper({
  value,
  label,
  times = 1,
  canEdit,
  busy,
  onStep,
}: {
  value: number;
  /** Shown instead of the value: a typical column whose floors differ. */
  label?: string;
  /** How many floors a typical column's quantity stands on: shown as "×14". */
  times?: number;
  canEdit: boolean;
  busy: boolean;
  onStep: (by: number) => void;
}) {
  const shown = value ? (
    <>
      {label ?? count(value)}
      <Times floors={times} />
    </>
  ) : (
    <span className="text-gray-300">&ndash;</span>
  );
  if (!canEdit) {
    return (
      <span className="mx-auto flex h-9 min-w-[4.5rem] items-center justify-center whitespace-nowrap rounded-lg border border-gray-100 bg-white px-2 tabular-nums text-navy-900">
        {shown}
      </span>
    );
  }
  const reveal = "opacity-0 group-hover/cell:opacity-100 focus-visible:opacity-100 group-focus-within/cell:opacity-100";
  return (
    <span
      className={`group/cell relative mx-auto flex h-9 min-w-[4.5rem] items-center justify-center whitespace-nowrap rounded-lg border bg-white px-6 tabular-nums text-navy-900 transition ${
        busy ? "border-brand-600/40 opacity-60" : "border-gray-200 hover:border-brand-600/50"
      }`}
    >
      <button
        type="button"
        aria-label="one fewer"
        onClick={() => onStep(-1)}
        disabled={busy || value <= 0}
        className={`absolute left-1 top-1/2 h-5 w-5 -translate-y-1/2 rounded text-xs leading-none text-gray-500 hover:bg-gray-100 disabled:hidden ${reveal}`}
      >
        &minus;
      </button>
      {shown}
      <button
        type="button"
        aria-label="one more"
        onClick={() => onStep(1)}
        disabled={busy}
        className={`absolute right-1 top-1/2 h-5 w-5 -translate-y-1/2 rounded text-xs leading-none text-gray-500 hover:bg-gray-100 ${reveal}`}
      >
        +
      </button>
    </span>
  );
}

// --- the families the lines are grouped under ------------------------------------------------

/** A line naming no device the platform knows. */
const OTHER_FAMILY = "Other";

/** The order a BOQ is read in -- the server's `symbol_taxonomy.FAMILY_ORDER`. */
const FAMILY_ORDER = [
  "Detectors",
  "Pull station",
  "Speaker",
  "Exit & emergency light",
  "Fire telephone",
  "Modules & isolators",
  "Panels & power",
  OTHER_FAMILY,
];

function familyRank(family: string): number {
  const at = FAMILY_ORDER.indexOf(family);
  return at < 0 ? FAMILY_ORDER.length : at;
}

/** Each family's colours: the band behind its heading row, its icon, its name. */
const FAMILY_STYLES: Record<string, { band: string; icon: string; text: string }> = {
  Detectors: { band: "bg-blue-50", icon: "bg-blue-600", text: "text-blue-900" },
  "Pull station": { band: "bg-rose-50", icon: "bg-rose-600", text: "text-rose-900" },
  Speaker: { band: "bg-violet-50", icon: "bg-violet-600", text: "text-violet-900" },
  "Exit & emergency light": { band: "bg-amber-50", icon: "bg-amber-500", text: "text-amber-900" },
  "Fire telephone": { band: "bg-teal-50", icon: "bg-teal-600", text: "text-teal-900" },
  "Modules & isolators": { band: "bg-emerald-50", icon: "bg-emerald-600", text: "text-emerald-900" },
  "Panels & power": { band: "bg-orange-50", icon: "bg-orange-500", text: "text-orange-900" },
  [OTHER_FAMILY]: { band: "bg-gray-100", icon: "bg-gray-500", text: "text-gray-800" },
};

function familyStyle(family: string) {
  return FAMILY_STYLES[family] ?? FAMILY_STYLES[OTHER_FAMILY];
}

/** A small drawing of what the family is, on its colour. */
function FamilyIcon({ family, small = false }: { family: string; small?: boolean }) {
  const paths: Record<string, ReactNode> = {
    Detectors: (
      <>
        <circle cx="12" cy="12" r="7" />
        <circle cx="12" cy="12" r="2.5" />
      </>
    ),
    "Pull station": (
      <>
        <rect x="5" y="5" width="14" height="14" rx="2" />
        <rect x="9" y="9" width="6" height="6" rx="1" />
      </>
    ),
    Speaker: (
      <>
        <path d="M5 10h3l4-4v12l-4-4H5z" strokeLinejoin="round" />
        <path d="M15.5 9.5a3.5 3.5 0 0 1 0 5M18 7a7 7 0 0 1 0 10" strokeLinecap="round" />
      </>
    ),
    "Exit & emergency light": (
      <>
        <rect x="4" y="7" width="16" height="10" rx="2" />
        <path d="M10 10h-2.5v4H10M7.5 12H9.5M13 10l3 4m0-4-3 4" strokeLinecap="round" />
      </>
    ),
    "Fire telephone": (
      <path d="M7 4h3l1.5 4-2 1.5a10 10 0 0 0 5 5l1.5-2 4 1.5v3a2 2 0 0 1-2 2A15 15 0 0 1 5 6a2 2 0 0 1 2-2z" strokeLinejoin="round" />
    ),
    "Modules & isolators": (
      <>
        <rect x="7" y="7" width="10" height="10" rx="1.5" />
        <path d="M10 4v3M14 4v3M10 17v3M14 17v3M4 10h3M4 14h3M17 10h3M17 14h3" strokeLinecap="round" />
      </>
    ),
    "Panels & power": (
      <>
        <rect x="5" y="4" width="14" height="16" rx="2" />
        <path d="M8 8h8M8 12h8M8 16h4" strokeLinecap="round" />
      </>
    ),
  };
  const size = small ? "h-5 w-5 rounded-md" : "h-8 w-8 rounded-lg";
  return (
    <span aria-hidden="true" className={`inline-flex shrink-0 items-center justify-center text-white ${size} ${familyStyle(family).icon}`}>
      <svg viewBox="0 0 24 24" className={small ? "h-3.5 w-3.5" : "h-5 w-5"} fill="none" stroke="currentColor" strokeWidth="1.8">
        {paths[family] ?? (
          <>
            <circle cx="7" cy="12" r="1.5" />
            <circle cx="12" cy="12" r="1.5" />
            <circle cx="17" cy="12" r="1.5" />
          </>
        )}
      </svg>
    </span>
  );
}

/** A family filter: "Detectors (6)". "All" carries no family. */
function FamilyChip({
  family,
  label,
  count: total,
  active,
  onClick,
}: {
  family?: string;
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`inline-flex items-center gap-2 rounded-xl border px-3 py-1.5 text-sm font-medium transition ${
        active ? "border-brand-600 bg-brand-600 text-white shadow-sm" : "border-gray-200 bg-white text-navy-900 hover:border-gray-300"
      }`}
    >
      {family && <FamilyIcon family={family} small />}
      {label}
      <span className={active ? "text-white/80" : "text-gray-500"}>({total})</span>
    </button>
  );
}

/** The schedule's own totals beside the design sheet BOQ. The two are read
 * from different documents and should agree; where they do not, the
 * difference is the question. */
function AgainstBoq({ check }: { check: FloorScheduleCheck }) {
  const off = check.rows.filter((row) => row.difference !== null && row.difference !== 0);
  return (
    <section className="mt-4 rounded-xl border border-gray-200 bg-white">
      <div className="border-b border-gray-100 px-5 py-3">
        <h2 className="text-sm font-bold text-navy-900">
          Against the design sheet BOQ
          <span className="ml-2 text-xs font-normal text-gray-500">
            {check.matched} item{check.matched === 1 ? "" : "s"} on both · {off.length} that differ
          </span>
        </h2>
      </div>
      {check.rows.length === 0 && check.only_in_schedule.length === 0 ? (
        <p className="px-5 py-4 text-sm text-gray-400">Nothing on the schedule matched a BOQ line.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-4 py-2 font-semibold">Item</th>
                <th className="px-3 py-2 text-right font-semibold">Schedule</th>
                <th className="px-3 py-2 text-right font-semibold">Design sheet</th>
                <th className="px-3 py-2 text-right font-semibold">Difference</th>
              </tr>
            </thead>
            <tbody>
              {check.rows.map((row) => (
                <tr key={`${row.catalog_no}:${row.description}`} className="border-t border-gray-100">
                  <td className="px-4 py-2">
                    <span className="font-medium text-navy-900">{row.catalog_no ?? row.description}</span>
                    {row.catalog_no && (
                      <span className="block truncate text-xs text-gray-500">{row.description}</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-gray-700">{row.schedule_total}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-gray-700">
                    {row.boq_quantity ?? <span className="text-gray-300">&mdash;</span>}
                  </td>
                  <td
                    className={`px-3 py-2 text-right font-semibold tabular-nums ${
                      row.difference ? "text-amber-800" : "text-gray-400"
                    }`}
                  >
                    {row.difference === null
                      ? "—"
                      : row.difference === 0
                        ? "0"
                        : row.difference > 0
                          ? `+${row.difference}`
                          : row.difference}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(check.only_in_schedule.length > 0 || check.only_in_boq.length > 0) && (
        <div className="grid gap-4 border-t border-gray-100 px-5 py-3 text-xs sm:grid-cols-2">
          <div>
            <h3 className="font-semibold text-navy-900">On the schedule only</h3>
            {check.only_in_schedule.length === 0 ? (
              <p className="mt-1 text-gray-400">None.</p>
            ) : (
              <ul className="mt-1 space-y-0.5 text-gray-600">
                {check.only_in_schedule.map((entry) => (
                  <li key={`${entry.catalog_no}:${entry.description}`}>
                    {entry.catalog_no ?? entry.description} &middot; {entry.total}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <h3 className="font-semibold text-navy-900">On the design sheet only</h3>
            {check.only_in_boq.length === 0 ? (
              <p className="mt-1 text-gray-400">None.</p>
            ) : (
              <ul className="mt-1 space-y-0.5 text-gray-600">
                {check.only_in_boq.map((entry) => (
                  <li key={`${entry.catalog_no}:${entry.description}`}>
                    {entry.catalog_no ?? entry.description} &middot; {entry.quantity}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
