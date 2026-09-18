/** The BOQ page's "BOQ Floor Wise" tab: the floor-wise BOQ read off the
 * schedule an engineer keeps in Excel.
 *
 * The tab's whole point is the typical column. A schedule does not write
 * out thirteen identical columns; it writes "1 to 13" once, and this
 * shows the thirteen -- Level 1, Level 2, Level 3 -- because that is what
 * anyone ordering for a floor needs.
 *
 * A fire alarm job and an emergency lighting job are two BOQs, so the
 * table is shown a system at a time.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

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
                column standing for a range, <span className="font-medium text-navy-900">1 to 13</span>, is
                written out here as Level 1, Level 2, Level 3, one floor at a time.
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
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const list = await api.get<FloorScheduleMaterial[]>(
          `/projects/${projectId}/floor-schedule/materials${system ? `?system=${encodeURIComponent(system)}` : ""}`,
        );
        if (live) setMaterials(list);
      } catch {
        if (live) setMaterials([]);
      }
    })();
    return () => {
      live = false;
    };
  }, [projectId, system]);

  const rows = (result.items ?? []).filter((item) => (item.system ?? "") === system);
  const floors = result.floors ?? [];
  const totals = Object.fromEntries(
    floors.map((floor) => [floor, rows.reduce((sum, item) => sum + (item.per_floor?.[floor] ?? 0), 0)]),
  );
  const total = rows.reduce((sum, item) => sum + (item.total ?? 0), 0);

  /** A quantity is only ever stepped, never typed: the schedule is a count
   * of devices, and a keyboard invites a decimal or a paste. */
  async function step(item: FloorScheduleItem, floor: string, by: number) {
    const now = item.per_floor?.[floor] ?? 0;
    const next = Math.max(0, now + by);
    if (next === now) return;
    setSaving(`${item.row}:${floor}`);
    try {
      const body = await api.patch<FloorSchedule>(
        `/projects/${projectId}/floor-schedule/items/${item.row}`,
        { floor, quantity: next },
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

  return (
    <>
      <section className="mt-4 rounded-xl border border-gray-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-5 py-3">
          <h2 className="text-sm font-bold text-navy-900">
            Quantities per floor
            <span className="ml-2 text-xs font-normal text-gray-500">
              {rows.length} item{rows.length === 1 ? "" : "s"} · {floors.length} floor
              {floors.length === 1 ? "" : "s"} · {count(total)} in this system · {count(result.grand_total ?? 0)} in all
            </span>
          </h2>
        </div>
        {systems.length > 1 && (
          <div className="flex flex-wrap gap-1 border-b border-gray-100 px-5 py-2">
            {systems.map((code) => (
              <button
                key={code}
                onClick={() => setSystem(code)}
                className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${
                  code === system ? "bg-brand-600 text-white" : "text-gray-600 hover:bg-gray-50"
                }`}
              >
                {SYSTEM_LABELS[code] ?? code}
                <span className={`ml-2 text-xs font-normal ${code === system ? "text-white/80" : "text-gray-400"}`}>
                  {count(totalsBySystem[code] ?? 0)}
                </span>
              </button>
            ))}
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="sticky left-0 z-10 bg-gray-50 px-4 py-2 font-semibold">Item</th>
                <th className="px-3 py-2 font-semibold">Device</th>
                <th className="px-3 py-2 font-semibold">Proposed material</th>
                {floors.map((floor) => (
                  <th key={floor} className="px-3 py-2 text-center font-semibold">{floor}</th>
                ))}
                <th className="px-4 py-2 text-right font-semibold">Total</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <tr key={item.row} className="border-t border-gray-100">
                  <td className="sticky left-0 z-10 bg-white px-4 py-2">
                    <span className="font-medium text-navy-900">{item.catalog_no ?? item.description}</span>
                    {item.catalog_no && (
                      <span className="block truncate text-xs font-normal text-gray-500" title={item.description}>
                        {item.description}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-gray-700">
                    {item.device ? (
                      <>
                        {item.device}
                        {item.family && <span className="block text-xs text-gray-400">{item.family}</span>}
                      </>
                    ) : (
                      <span className="text-gray-300" title="No device the platform knows is named in this line">
                        &mdash;
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {canEdit ? (
                      <select
                        value={item.material?.part_no ?? ""}
                        disabled={saving === `${item.row}:part`}
                        onChange={(e) => void choose(item, e.target.value)}
                        className="w-44 rounded-lg border border-gray-300 px-2 py-1 text-xs"
                      >
                        <option value="">&mdash; not settled &mdash;</option>
                        {materials.map((material) => (
                          <option key={material.part_no} value={material.part_no}>
                            {material.part_no}
                            {material.description ? ` — ${material.description.slice(0, 40)}` : ""}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className="text-xs text-gray-700">{item.material?.part_no ?? "—"}</span>
                    )}
                  </td>
                  {floors.map((floor) => (
                    <td key={floor} className="px-1 py-1 text-center">
                      <Stepper
                        value={item.per_floor?.[floor] ?? 0}
                        canEdit={canEdit}
                        busy={saving === `${item.row}:${floor}`}
                        onStep={(by) => void step(item, floor, by)}
                      />
                    </td>
                  ))}
                  <td className="px-4 py-2 text-right font-semibold tabular-nums text-navy-900">{count(item.total)}</td>
                </tr>
              ))}
            </tbody>
            {rows.length > 0 && (
              <tfoot>
                <tr className="border-t-2 border-gray-200 bg-gray-50">
                  <td className="sticky left-0 z-10 bg-gray-50 px-4 py-2 font-bold text-navy-900">
                    {SYSTEM_LABELS[system] ?? system}
                  </td>
                  <td className="px-3 py-2" />
                  <td className="px-3 py-2" />
                  {floors.map((floor) => (
                    <td key={floor} className="px-3 py-2 text-center font-semibold tabular-nums text-navy-900">
                      {totals[floor] ? count(totals[floor]) : <span className="font-normal text-gray-300">&mdash;</span>}
                    </td>
                  ))}
                  <td className="px-4 py-2 text-right font-bold tabular-nums text-navy-900">{count(total)}</td>
                </tr>
              </tfoot>
            )}
          </table>
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

/** A quantity as it should read: whole where it is whole, and to two
 * places where a typical column's total did not divide evenly over its
 * floors. The stored figure keeps its full precision -- only the page
 * rounds. */
function count(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

/** One floor's quantity: stepped up and down, never typed.
 *
 * A blank is not a zero -- it means the item is not on that floor -- so a
 * cell at nothing shows a dash, and stepping down from one returns it to
 * a dash rather than writing a BOQ line of none. */
function Stepper({
  value,
  canEdit,
  busy,
  onStep,
}: {
  value: number;
  canEdit: boolean;
  busy: boolean;
  onStep: (by: number) => void;
}) {
  if (!canEdit) {
    return value ? (
      <span className="tabular-nums text-gray-700">{count(value)}</span>
    ) : (
      <span className="text-gray-300">&mdash;</span>
    );
  }
  return (
    <span className="inline-flex items-center gap-0.5">
      <button
        type="button"
        aria-label="one fewer"
        onClick={() => onStep(-1)}
        disabled={busy || value <= 0}
        className="h-5 w-5 rounded border border-gray-200 text-xs leading-none text-gray-500 disabled:opacity-30"
      >
        &minus;
      </button>
      <span className={`w-9 tabular-nums ${value ? "text-gray-800" : "text-gray-300"}`}>
        {value ? count(value) : "—"}
      </span>
      <button
        type="button"
        aria-label="one more"
        onClick={() => onStep(1)}
        disabled={busy}
        className="h-5 w-5 rounded border border-gray-200 text-xs leading-none text-gray-500 disabled:opacity-30"
      >
        +
      </button>
    </span>
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
