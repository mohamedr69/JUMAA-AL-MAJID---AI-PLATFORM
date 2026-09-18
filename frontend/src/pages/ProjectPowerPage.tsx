/** The 24 V power calculation, worked out from the floor-wise BOQ.
 *
 * The other half of the amplifier page, and drawn the same way. Where the
 * amplifier feeds speakers off a 70 V line, this feeds everything that
 * runs on direct current: sounders, flashers, the flasher half of a
 * speaker-flasher, and sounder bases.
 *
 * Two things are particular to it. A floor's notification circuit is
 * driven from a SIGA-CC1 rather than a CC2A; and a sounder base is
 * powered but sits on the Signature loop, so a floor carrying only bases
 * draws current and takes no module.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { API_BASE_URL, ApiError, api } from "../lib/api";
import { useAuth } from "../context/AuthContext";
import { PROJECT_EDITOR_ROLES } from "../lib/types";
import type { DeviceCurrentRow, PowerResult, PowerSchedule } from "../lib/types";
import { useProject } from "./ProjectWorkspace";

function ma(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

function amps(milliamps: number): string {
  return (Math.round((milliamps / 1000) * 1000) / 1000).toString();
}

function Icon({ path, className = "" }: { path: ReactNode; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`h-5 w-5 ${className}`}
      aria-hidden="true"
    >
      {path}
    </svg>
  );
}

const GLYPHS = {
  building: (
    <>
      <path d="M4 21V5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v16" />
      <path d="M15 9h3a2 2 0 0 1 2 2v10" />
      <path d="M8 7h3M8 11h3M8 15h3M2 21h20" />
    </>
  ),
  bell: (
    <>
      <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.7 21a2 2 0 0 1-3.4 0" />
    </>
  ),
  bolt: <path d="M13 2 4 14h7l-1 8 9-12h-7z" />,
  battery: (
    <>
      <rect x="2" y="7" width="17" height="10" rx="2" />
      <path d="M22 11v2M6 11v2M10 11v2" />
    </>
  ),
  module: (
    <>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M9 13h6M9 17h6" />
    </>
  ),
  chart: (
    <>
      <path d="M3 3v18h18" />
      <rect x="7" y="10" width="3" height="7" />
      <rect x="13" y="6" width="3" height="11" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m21 21-4.3-4.3" />
    </>
  ),
  database: (
    <>
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
      <path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" />
    </>
  ),
  page: (
    <>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="m9 15 2 2 4-4" />
    </>
  ),
} as const;

export function ProjectPowerPage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);
  const [data, setData] = useState<PowerSchedule | null>(null);
  const [database, setDatabase] = useState<DeviceCurrentRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [showDatabase, setShowDatabase] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api.get<PowerSchedule>(`/projects/${project.id}/design/power`));
      setDatabase(await api.get<DeviceCurrentRow[]>("/design/device-currents"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The power schedule could not be loaded");
    }
  }, [project.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const result = data?.result ?? null;

  async function setCurrent(part: string, milliamps: number) {
    if (!result) return;
    setSaving(part);
    setError(null);
    try {
      const currents: Record<string, number> = {};
      for (const column of result.columns) {
        if (column.current_ma !== null) currents[column.key] = column.current_ma;
      }
      currents[part] = milliamps;
      setData(await api.put<PowerSchedule>(`/projects/${project.id}/design/power`, { currents }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The current could not be set");
    } finally {
      setSaving(null);
    }
  }

  if (!result) {
    return <p className="mt-6 text-sm text-gray-400">{error ?? "Loading the power schedule…"}</p>;
  }

  return (
    <div className="mt-4">
      <div className="flex flex-wrap justify-end gap-2">
        <button
          onClick={() => setShowDatabase((was) => !was)}
          className={`inline-flex items-center gap-2 rounded-xl border bg-white px-4 py-2 text-sm font-semibold text-navy-900 hover:border-brand-300 ${
            showDatabase ? "border-brand-400" : "border-gray-200"
          }`}
        >
          <Icon path={GLYPHS.database} className="h-4 w-4 text-gray-500" />
          Device currents
        </button>
        <a
          href={`${API_BASE_URL}/projects/${project.id}/design/power/export.pdf`}
          className="inline-flex items-center gap-2 rounded-xl bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700"
        >
          <Icon path={GLYPHS.page} className="h-4 w-4" />
          Export PDF
        </a>
      </div>

      <Tiles result={result} />
      {error && <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p>}
      {showDatabase && <CurrentDatabase rows={database} />}

      {result.columns.length === 0 ? (
        <p className="mt-4 rounded-2xl border border-dashed border-gray-200 p-8 text-center text-sm text-gray-400">
          Nothing on the floor-wise BOQ runs on 24 V yet.
        </p>
      ) : (
        <Load result={result} canEdit={canEdit} saving={saving} onCurrent={setCurrent} />
      )}

      <Notes result={result} />
    </div>
  );
}

function Tiles({ result }: { result: PowerResult }) {
  const tiles = [
    { glyph: GLYPHS.building, tint: "bg-sky-50 text-sky-600", label: "Total floors",
      value: String(result.total_floors), note: "Floors" },
    { glyph: GLYPHS.bell, tint: "bg-indigo-50 text-indigo-600", label: "24 V appliances",
      value: String(result.total_devices), note: "Sounders, flashers, bases" },
    { glyph: GLYPHS.bolt, tint: "bg-emerald-50 text-emerald-600", label: "Connected load",
      value: `${amps(result.total_ma)} A`, note: `${ma(result.total_ma)} mA` },
    { glyph: GLYPHS.battery, tint: "bg-violet-50 text-violet-600",
      label: `${result.supply_part} required`, value: String(result.supplies.length),
      note: `${amps(result.limit_ma)} A each` },
    { glyph: GLYPHS.module, tint: "bg-rose-50 text-rose-600",
      label: `${result.module_part} required`, value: String(result.total_modules),
      note: "one a floor with a circuit" },
  ];
  return (
    <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
      {tiles.map((tile) => (
        <div key={tile.label} className="rounded-2xl border border-gray-200 bg-white p-4">
          <div className="flex items-start gap-3">
            <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${tile.tint}`}>
              <Icon path={tile.glyph} />
            </span>
            <div className="min-w-0">
              <p className="truncate text-xs text-gray-500" title={tile.label}>{tile.label}</p>
              <p className="mt-0.5 text-2xl font-bold leading-tight text-navy-900">{tile.value}</p>
              <p className="truncate text-xs text-gray-400" title={tile.note}>{tile.note}</p>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function Load({
  result,
  canEdit,
  saving,
  onCurrent,
}: {
  result: PowerResult;
  canEdit: boolean;
  saving: string | null;
  onCurrent: (part: string, milliamps: number) => void;
}) {
  const [query, setQuery] = useState("");
  const matching = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle
      ? result.floors.filter((floor) => floor.floor.toLowerCase().includes(needle))
      : result.floors;
  }, [result.floors, query]);

  /** Each supply's run of floors, over the rows as drawn. */
  const runs = useMemo(() => {
    const starts = new Map<number, { supply: string | null; rows: number }>();
    let open = -1;
    matching.forEach((floor, index) => {
      const supply = floor.supply ?? null;
      const previous = open >= 0 ? starts.get(open) : undefined;
      if (previous && previous.supply === supply) {
        previous.rows += 1;
        return;
      }
      starts.set(index, { supply, rows: 1 });
      open = index;
    });
    return starts;
  }, [matching]);

  /** Each circuit's run of floors, over the rows as drawn. */
  const circuitRuns = useMemo(() => {
    const starts = new Map<number, { key: string | null; rows: number }>();
    let open = -1;
    matching.forEach((floor, index) => {
      const key = floor.supply && floor.circuit ? `${floor.supply}|${floor.circuit}` : null;
      const previous = open >= 0 ? starts.get(open) : undefined;
      if (previous && previous.key === key) {
        previous.rows += 1;
        return;
      }
      starts.set(index, { key, rows: 1 });
      open = index;
    });
    return starts;
  }, [matching]);

  const supplyStarts = useMemo(() => {
    const first = new Set<number>();
    let previous: string | null | undefined;
    matching.forEach((floor, index) => {
      if (index > 0 && floor.supply && floor.supply !== previous) first.add(index);
      previous = floor.supply;
    });
    return first;
  }, [matching]);

  const columns = result.columns;
  return (
    <section className="mt-4 overflow-hidden rounded-2xl border border-gray-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
        <h2 className="inline-flex items-center gap-2 text-base font-bold text-navy-900">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
            <Icon path={GLYPHS.chart} />
          </span>
          24 V load by floor
        </h2>
        <label className="relative">
          <span className="sr-only">Search floor</span>
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">
            <Icon path={GLYPHS.search} className="h-4 w-4" />
          </span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search floor..."
            className="w-64 rounded-xl border border-gray-200 py-2 pl-9 pr-3 text-sm placeholder:text-gray-400 focus:border-brand-400 focus:outline-none"
          />
        </label>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-y border-gray-100 bg-gray-50/60 text-gray-600">
            <tr>
              <th className="sticky left-0 z-10 bg-gray-50/60 px-5 py-3 text-left font-semibold">Floor</th>
              <th className="px-3 py-3 text-center font-semibold">
                Module
                <span className="block text-[11px] font-normal text-gray-400">({result.module_part})</span>
              </th>
              {columns.map((column) => (
                <th key={column.key} className="px-3 py-3 text-center font-semibold" title={column.lines.join(", ")}>
                  {column.key}
                  <span className="block text-[11px] font-normal text-gray-400">
                    {column.current_ma === null ? "no current" : `(${ma(column.current_ma)} mA)`}
                    {column.needs_module ? "" : " · no module"}
                  </span>
                </th>
              ))}
              <th className="px-3 py-3 text-right font-semibold">Load (mA)</th>
              <th className="px-3 py-3 text-center font-semibold">
                Circuit load
                <span className="block text-xs font-normal text-gray-400">
                  max {ma(result.circuit_limit_ma)} mA
                </span>
              </th>
              <th className="px-3 py-3 text-center font-semibold">{result.supply_part}</th>
            </tr>
            <tr className="border-t border-gray-100 bg-white">
              <th className="sticky left-0 z-10 bg-white px-5 py-2 text-left text-xs font-medium text-gray-400">
                Current (mA)
              </th>
              <th className="px-3 py-2" />
              {columns.map((column) => (
                <th key={column.key} className="px-3 py-2 text-center">
                  <select
                    value={column.current_ma ?? ""}
                    disabled={!canEdit || column.currents.length === 0 || saving === column.key}
                    onChange={(e) => e.target.value && onCurrent(column.key, Number(e.target.value))}
                    className="w-44 rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs text-navy-900 focus:border-brand-400 focus:outline-none disabled:bg-gray-50 disabled:text-gray-400"
                  >
                    <option value="">—</option>
                    {column.currents.map((entry) => (
                      <option key={`${entry.ma}:${entry.label ?? ""}`} value={entry.ma}>
                        {ma(entry.ma)} mA{entry.label ? ` — ${entry.label}` : ""}
                      </option>
                    ))}
                  </select>
                </th>
              ))}
              <th colSpan={3} />
            </tr>
          </thead>
          <tbody>
            {matching.length === 0 && (
              <tr>
                <td colSpan={columns.length + 5} className="px-5 py-10 text-center text-sm text-gray-400">
                  No floor matches “{query}”.
                </td>
              </tr>
            )}
            {matching.map((floor, index) => {
              const run = runs.get(index);
              const supply = result.supplies.find((s) => s.name === floor.supply);
              const circuitRun = circuitRuns.get(index);
              const circuit = result.circuits.find((c) => c.supply === floor.supply && c.name === floor.circuit);
              return (
                <tr
                  key={floor.floor}
                  className={supplyStarts.has(index) ? "border-t-2 border-t-violet-200" : "border-t border-gray-100"}
                >
                  <td className="sticky left-0 z-10 bg-white px-5 py-2.5 font-semibold text-navy-900">
                    {floor.floor}
                  </td>
                  <td className="px-3 py-2.5 text-center text-xs">
                    {floor.modules === 0 ? (
                      <span className="text-gray-300" title="No notification circuit on this floor">—</span>
                    ) : (
                      <span className="font-medium text-navy-900">{result.module_part}</span>
                    )}
                  </td>
                  {columns.map((column) => (
                    <td key={column.key} className="px-3 py-2.5 text-center tabular-nums text-gray-700">
                      {floor.counts[column.key] || <span className="text-gray-300">—</span>}
                    </td>
                  ))}
                  <td className="px-3 py-2.5 text-right font-bold tabular-nums text-navy-900">
                    {ma(floor.current_ma)}
                  </td>
                  {circuitRun && (
                    <td
                      rowSpan={circuitRun.rows}
                      className={`px-3 py-2.5 text-center align-middle text-sm tabular-nums ${
                        !circuit
                          ? "text-gray-300"
                          : circuit.over_limit
                            ? "bg-red-50/70 font-bold text-red-800"
                            : "bg-sky-50/60 font-semibold text-sky-900"
                      }`}
                    >
                      {circuit ? (
                        <>
                          {circuit.name}
                          <span className="block text-xs font-medium">
                            {ma(circuit.current_ma)} / {ma(result.circuit_limit_ma)} mA
                          </span>
                          {circuit.over_limit && <span className="block text-xs font-medium">over limit</span>}
                        </>
                      ) : (
                        <span className="text-xs font-normal">—</span>
                      )}
                    </td>
                  )}
                  {run && (
                    <td
                      rowSpan={run.rows}
                      className={`px-3 py-2.5 text-center align-middle text-sm font-bold ${
                        !run.supply
                          ? "text-gray-300"
                          : supply?.over_limit
                            ? "bg-red-50/70 text-red-800"
                            : "bg-violet-50/70 text-violet-900"
                      }`}
                    >
                      {run.supply ? (
                        <>
                          {run.supply}
                          <span className="block text-xs font-medium text-violet-700">
                            ({amps(supply?.current_ma ?? 0)} A)
                          </span>
                          {supply?.over_limit && <span className="block text-xs font-medium">over limit</span>}
                        </>
                      ) : (
                        <span className="text-xs font-normal">no 24 V load</span>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="border-t-2 border-gray-200 bg-gray-50/60">
              <td className="sticky left-0 z-10 bg-gray-50/60 px-5 py-3 font-bold text-navy-900">
                All {result.total_floors} floors
              </td>
              <td className="px-3 py-3 text-center font-bold text-navy-900">{result.total_modules}</td>
              {columns.map((column) => (
                <td key={column.key} className="px-3 py-3 text-center font-bold tabular-nums text-navy-900">
                  {result.totals_by_column[column.key] ?? 0}
                </td>
              ))}
              <td className="px-3 py-3 text-right font-bold tabular-nums text-navy-900">
                {ma(result.total_ma)}
              </td>
              <td className="px-3 py-3 text-center font-bold text-navy-900">{result.supplies.length}</td>
            </tr>
          </tfoot>
        </table>
      </div>

      {query && (
        <div className="border-t border-gray-100 px-5 py-3 text-sm text-gray-500">
          Showing {matching.length} of {result.floors.length} floors matching “{query}”.{" "}
          <button onClick={() => setQuery("")} className="font-semibold text-brand-600 hover:underline">
            Show all
          </button>
        </div>
      )}
    </section>
  );
}

function CurrentDatabase({ rows }: { rows: DeviceCurrentRow[] }) {
  return (
    <section className="mt-3 rounded-2xl border border-gray-200 bg-white p-5">
      <h2 className="text-sm font-bold text-navy-900">Device currents</h2>
      <p className="mt-1 max-w-3xl text-xs text-gray-500">
        What each 24 V appliance draws, read off its datasheet in the library. A datasheet often gives more
        than one figure — a strobe by candela, a horn by volume, direct current against full-wave rectified —
        so every one is offered and the engineer chooses. Nothing is picked for you.
      </p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-gray-100 text-left text-xs uppercase tracking-wide text-gray-500">
            <tr>
              <th className="py-2 font-semibold">Part</th>
              <th className="py-2 font-semibold">Description</th>
              <th className="py-2 font-semibold">Currents</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.part_no} className="border-b border-gray-50 align-top">
                <td className="py-2 font-semibold text-navy-900">{row.part_no}</td>
                <td className="py-2 text-gray-600">{row.description}</td>
                <td className="py-2 text-gray-700">
                  {row.currents.map((entry) => (
                    <span key={`${entry.ma}:${entry.label ?? ""}`} className="block tabular-nums">
                      {ma(entry.ma)} mA
                      {entry.label && <span className="text-gray-500"> — {entry.label}</span>}
                    </span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Notes({ result }: { result: PowerResult }) {
  return (
    <section className="mt-4 grid gap-4 lg:grid-cols-2">
      <div className="rounded-2xl border border-gray-200 bg-white p-5">
        <h2 className="text-sm font-bold text-navy-900">How this is worked out</h2>
        <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm text-gray-700">
          <li>Quantities come from the floor-wise BOQ; the current is chosen here, off the datasheet.</li>
          <li>
            Everything on 24 V is counted: sounders, flashers, the flasher half of a speaker-flasher, and
            sounder bases. A plain speaker is on the amplifier's 70 V line and draws nothing here.
          </li>
          <li>
            Each {result.supply_part} has {result.circuits_per_supply} circuits. A circuit is worked to{" "}
            {ma(result.circuit_limit_ma)} mA &mdash; the supply's {result.supply_amps} A shared between its
            circuits, with the same spare kept &mdash; so {result.circuits_per_supply} circuits within their
            limit keep the supply within its {amps(result.limit_ma)} A.
          </li>
          <li>
            Floors are wired to a circuit in the schedule's order until the next would take it over its
            limit, then the next circuit starts; after {result.circuits_per_supply} circuits, the next{" "}
            {result.supply_part}. A floor is never split between two circuits.
          </li>
          <li>
            A floor with a notification circuit is driven from one {result.module_part}. A sounder base is on
            the Signature loop and needs none, so a floor carrying only bases draws power and takes no
            module.
          </li>
        </ul>
      </div>
      <div className="rounded-2xl border border-gray-200 bg-white p-5">
        <h2 className="text-sm font-bold text-navy-900">Notes</h2>
        {result.warnings.length === 0 ? (
          <p className="mt-2 text-sm text-gray-400">Nothing needs an engineer's attention.</p>
        ) : (
          <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm text-amber-900">
            {result.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
