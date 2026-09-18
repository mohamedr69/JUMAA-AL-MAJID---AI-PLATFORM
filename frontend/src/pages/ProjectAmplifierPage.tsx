/** Voice evacuation amplifier loading, worked out from the floor-wise BOQ.
 *
 * The speakers and their quantities per floor come from the BOQ Floor Wise
 * tab; what is chosen here is the tapping each speaker is set to. Floors
 * are then filled into amplifiers in the schedule's own order, and the
 * amplifiers paired into cabinets.
 *
 * The table is the calculation, so it is drawn the way an engineer draws
 * it: a row per floor, the amplifier and its cabinet ruled across the
 * floors they feed, and a line between one cabinet and the next.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { API_BASE_URL, ApiError, api } from "../lib/api";
import { useAuth } from "../context/AuthContext";
import { PROJECT_EDITOR_ROLES } from "../lib/types";
import type { AmplifierResult, AmplifierSchedule, SpeakerDatabaseRow, StaircaseResult } from "../lib/types";
import { useProject } from "./ProjectWorkspace";

/** A wattage as it should read: 40 rather than 40.0000. */
function watts(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

// --- the small drawings the page carries ---------------------------------------------------

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
  speaker: (
    <>
      <path d="M11 5 6 9H3v6h3l5 4z" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7" />
      <path d="M18.5 5.5a9 9 0 0 1 0 13" />
    </>
  ),
  bolt: <path d="M13 2 4 14h7l-1 8 9-12h-7z" />,
  layers: (
    <>
      <path d="m12 2 9 5-9 5-9-5z" />
      <path d="m3 12 9 5 9-5" />
      <path d="m3 17 9 5 9-5" />
    </>
  ),
  cabinet: (
    <>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M3 12h18M9 7.5h.01M9 16.5h.01" />
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
  gear: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M20.3 14.5a1.6 1.6 0 0 0 .3 1.8l.1.1a1.9 1.9 0 1 1-2.7 2.7l-.1-.1a1.6 1.6 0 0 0-2.7 1.1v.3a1.9 1.9 0 1 1-3.8 0v-.2a1.6 1.6 0 0 0-2.8-1.1l-.1.1a1.9 1.9 0 1 1-2.7-2.7l.1-.1a1.6 1.6 0 0 0-1.1-2.7h-.2a1.9 1.9 0 1 1 0-3.8h.2a1.6 1.6 0 0 0 1.1-2.8l-.1-.1a1.9 1.9 0 1 1 2.7-2.7l.1.1a1.6 1.6 0 0 0 2.8-1.1v-.2a1.9 1.9 0 1 1 3.8 0v.2a1.6 1.6 0 0 0 2.7 1.1l.1-.1a1.9 1.9 0 1 1 2.7 2.7l-.1.1a1.6 1.6 0 0 0 1.1 2.8h.2a1.9 1.9 0 1 1 0 3.8h-.2a1.6 1.6 0 0 0-1.4.9z" />
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

export function ProjectAmplifierPage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);
  const [data, setData] = useState<AmplifierSchedule | null>(null);
  const [database, setDatabase] = useState<SpeakerDatabaseRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [panel, setPanel] = useState<"none" | "database" | "settings">("none");
  // Speakers on the floors, or the staircases' on circuits of their own.
  const [view, setView] = useState<"speakers" | "staircase">("speakers");

  const load = useCallback(async () => {
    try {
      setData(await api.get<AmplifierSchedule>(`/projects/${project.id}/design/amplifier`));
      setDatabase(await api.get<SpeakerDatabaseRow[]>("/design/speakers"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The amplifier schedule could not be loaded");
    }
  }, [project.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const result = data?.result ?? null;

  async function setTap(part: string, tap: number) {
    if (!result) return;
    setSaving(`tap:${part}`);
    setError(null);
    try {
      const taps: Record<string, number> = {};
      for (const column of [...result.columns, ...(result.staircase?.columns ?? [])]) {
        if (column.tap !== null) taps[column.key] = column.tap;
      }
      taps[part] = tap;
      setData(await api.put<AmplifierSchedule>(`/projects/${project.id}/design/amplifier`, { taps }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The tapping could not be set");
    } finally {
      setSaving(null);
    }
  }

  /** How many speakers a floor has belongs to the floor-wise BOQ, so this
   * changes it there: the two tabs are two views of one number. */
  async function setCount(floor: string, part: string, count: number) {
    setSaving(`${floor}:${part}`);
    setError(null);
    try {
      setData(
        await api.patch<AmplifierSchedule>(`/projects/${project.id}/design/amplifier/counts`, {
          floor,
          part_no: part,
          count: Math.max(0, count),
        }),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The quantity could not be changed");
    } finally {
      setSaving(null);
    }
  }

  if (!result) {
    return <p className="mt-6 text-sm text-gray-400">{error ?? "Loading the amplifier schedule…"}</p>;
  }

  return (
    <div className="mt-4">
      <Toolbar
        projectId={project.id}
        panel={panel}
        onPanel={(next) => setPanel((was) => (was === next ? "none" : next))}
      />
      {result.staircase && (
        <div className="mt-4 flex flex-wrap gap-1" role="tablist">
          {(
            [
              ["speakers", "Speakers", result.total_speakers],
              [
                "staircase",
                `Staircase speakers${result.staircase.columns.length ? ` (${result.staircase.columns.map((c) => c.key).join(", ")})` : ""}`,
                result.staircase.total_speakers,
              ],
            ] as const
          ).map(([key, label, count]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={view === key}
              onClick={() => setView(key)}
              className={`rounded-t-lg border border-gray-200 px-6 py-2.5 text-sm font-semibold ${
                view === key ? "bg-brand-600 text-white" : "bg-gray-50 text-gray-500 hover:text-navy-900"
              }`}
            >
              {label}
              <span className={`ml-2 text-xs ${view === key ? "text-white/80" : "text-gray-400"}`}>{count}</span>
            </button>
          ))}
        </div>
      )}
      <Tiles result={result} view={result.staircase ? view : "speakers"} />
      {error && <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p>}
      {panel === "database" && <SpeakerDatabase rows={database} />}
      {panel === "settings" && <Settings result={result} scheduleFile={data?.schedule_file ?? null} />}

      {view === "staircase" && result.staircase ? (
        result.staircase.columns.length === 0 ? (
          <p className="mt-4 rounded-2xl border border-dashed border-gray-200 p-8 text-center text-sm text-gray-400">
            No staircase speaker is on the floor-wise BOQ.
          </p>
        ) : (
          <StaircaseLoading
            result={result}
            stair={result.staircase}
            canEdit={canEdit}
            saving={saving}
            onTap={setTap}
            onCount={setCount}
          />
        )
      ) : result.columns.length === 0 ? (
        <p className="mt-4 rounded-2xl border border-dashed border-gray-200 p-8 text-center text-sm text-gray-400">
          {result.staircase?.columns.length
            ? "Every speaker on the floor-wise BOQ is a staircase's: see the Staircase speakers tab."
            : "No speaker is proposed on the floor-wise BOQ yet."}
        </p>
      ) : (
        <Loading result={result} canEdit={canEdit} saving={saving} onTap={setTap} onCount={setCount} />
      )}

      <Notes result={result} />
    </div>
  );
}

function Toolbar({
  projectId,
  panel,
  onPanel,
}: {
  projectId: number;
  panel: string;
  onPanel: (next: "database" | "settings") => void;
}) {
  const quiet =
    "inline-flex items-center gap-2 rounded-xl border bg-white px-4 py-2 text-sm font-semibold text-navy-900 hover:border-brand-300";
  return (
    <div className="flex flex-wrap justify-end gap-2">
      <button
        onClick={() => onPanel("settings")}
        className={`${quiet} ${panel === "settings" ? "border-brand-400" : "border-gray-200"}`}
      >
        <Icon path={GLYPHS.gear} className="h-4 w-4 text-gray-500" />
        Settings
      </button>
      <button
        onClick={() => onPanel("database")}
        className={`${quiet} ${panel === "database" ? "border-brand-400" : "border-gray-200"}`}
      >
        <Icon path={GLYPHS.database} className="h-4 w-4 text-gray-500" />
        Speaker database
      </button>
      <a
        href={`${API_BASE_URL}/projects/${projectId}/design/amplifier/export.pdf`}
        className="inline-flex items-center gap-2 rounded-xl bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700"
      >
        <Icon path={GLYPHS.page} className="h-4 w-4" />
        Export PDF
      </a>
    </div>
  );
}

function Tiles({ result, view }: { result: AmplifierResult; view: "speakers" | "staircase" }) {
  const stair = result.staircase;
  // The cabinets are one set for the job: say so where the staircases share it.
  const cabinetNote = stair?.amplifiers.length
    ? `${result.amplifiers_per_cabinet} × ${result.amplifier_part} per APS, staircases included`
    : `${result.amplifiers_per_cabinet} × ${result.amplifier_part} per APS`;
  const tiles = view === "staircase" && stair ? [
    {
      glyph: GLYPHS.building,
      tint: "bg-sky-50 text-sky-600",
      label: "Stairs",
      value: String(stair.stairs),
      note: "one speaker a floor in each",
    },
    {
      glyph: GLYPHS.speaker,
      tint: "bg-indigo-50 text-indigo-600",
      label: "Staircase speakers",
      value: String(stair.total_speakers),
      note: "Speakers",
    },
    {
      glyph: GLYPHS.bolt,
      tint: "bg-emerald-50 text-emerald-600",
      label: `Total load (+${Math.round(result.spare_fraction * 100)}%)`,
      value: `${watts(stair.total_watts_with_spare)} W`,
      note: `${watts(stair.total_watts)} W as counted`,
    },
    {
      glyph: GLYPHS.layers,
      tint: "bg-violet-50 text-violet-600",
      label: `${result.amplifier_part} required`,
      value: String(stair.amplifiers.length),
      note: `${watts(result.limit_watts)} W each`,
    },
    {
      glyph: GLYPHS.cabinet,
      tint: "bg-amber-50 text-amber-600",
      label: "APS cabinets",
      value: String(result.cabinets.length),
      note: cabinetNote,
    },
    {
      glyph: GLYPHS.module,
      tint: "bg-rose-50 text-rose-600",
      label: `${result.module_part} required`,
      value: String(stair.total_circuits),
      note: `one a circuit, up to ${watts(stair.circuit_limit_watts)} W`,
    },
  ] : [
    {
      glyph: GLYPHS.building,
      tint: "bg-sky-50 text-sky-600",
      label: "Total floors",
      value: String(result.total_floors),
      note: "Floors",
    },
    {
      glyph: GLYPHS.speaker,
      tint: "bg-indigo-50 text-indigo-600",
      label: "Total speakers",
      value: String(result.total_speakers),
      note: "Speakers",
    },
    {
      glyph: GLYPHS.bolt,
      tint: "bg-emerald-50 text-emerald-600",
      label: `Total load (+${Math.round(result.spare_fraction * 100)}%)`,
      value: `${watts(result.total_watts_with_spare)} W`,
      note: `${watts(result.total_watts)} W as counted`,
    },
    {
      glyph: GLYPHS.layers,
      tint: "bg-violet-50 text-violet-600",
      label: `${result.amplifier_part} required`,
      value: String(result.amplifiers.length),
      note: `${watts(result.limit_watts)} W each`,
    },
    {
      glyph: GLYPHS.cabinet,
      tint: "bg-amber-50 text-amber-600",
      label: "APS cabinets",
      value: String(result.cabinets.length),
      note: cabinetNote,
    },
    {
      glyph: GLYPHS.module,
      tint: "bg-rose-50 text-rose-600",
      label: `${result.module_part} required`,
      value: String(result.total_modules),
      note: `one a floor, up to ${watts(result.module_max_watts)} W`,
    },
  ];
  return (
    <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      {tiles.map((tile) => (
        <div key={tile.label} className="rounded-2xl border border-gray-200 bg-white p-4">
          <div className="flex items-start gap-3">
            <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${tile.tint}`}>
              <Icon path={tile.glyph} />
            </span>
            <div className="min-w-0">
              <p className="truncate text-xs text-gray-500" title={tile.label}>
                {tile.label}
              </p>
              <p className="mt-0.5 text-2xl font-bold leading-tight text-navy-900">{tile.value}</p>
              <p className="truncate text-xs text-gray-400" title={tile.note}>
                {tile.note}
              </p>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function Loading({
  result,
  canEdit,
  saving,
  onTap,
  onCount,
}: {
  result: AmplifierResult;
  canEdit: boolean;
  saving: string | null;
  onTap: (part: string, tap: number) => void;
  onCount: (floor: string, part: string, count: number) => void;
}) {
  const [query, setQuery] = useState("");

  const cabinetOf = useMemo(
    () => new Map(result.amplifiers.map((a) => [a.name, a.cabinet])),
    [result.amplifiers],
  );
  const matching = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle
      ? result.floors.filter((floor) => floor.floor.toLowerCase().includes(needle))
      : result.floors;
  }, [result.floors, query]);

  // Every floor, on one page: a calculation is read down the building, and
  // paging it hid the run of floors an amplifier feeds -- the one thing the
  // table is drawn to show.
  const shown = matching;

  /** Where each amplifier's run of floors begins among the rows on show,
   * and how many of them it covers.
   *
   * It has to be a run over the rows as drawn rather than a tally per
   * amplifier: a floor with no speakers takes no amplifier, and one in the
   * middle of a run used to be counted out of the span while still drawing
   * a cell, which pushed every label a row down the table. */
  const runs = useMemo(() => {
    const starts = new Map<number, { amplifier: string | null; cabinet: string | null; rows: number }>();
    let open = -1;
    shown.forEach((floor, index) => {
      const amplifier = floor.amplifier ?? null;
      const previous = open >= 0 ? starts.get(open) : undefined;
      if (previous && previous.amplifier === amplifier) {
        previous.rows += 1;
        return;
      }
      starts.set(index, {
        amplifier,
        cabinet: amplifier ? cabinetOf.get(amplifier) ?? null : null,
        rows: 1,
      });
      open = index;
    });
    return starts;
  }, [shown, cabinetOf]);

  /** The rows on this page where one cabinet gives way to the next, so the
   * two are ruled apart. */
  const cabinetStarts = useMemo(() => {
    const first = new Set<number>();
    let previous: string | null | undefined;
    shown.forEach((floor, index) => {
      const cabinet = floor.amplifier ? cabinetOf.get(floor.amplifier) ?? null : null;
      if (index > 0 && cabinet && cabinet !== previous) first.add(index);
      previous = cabinet;
    });
    return first;
  }, [shown, cabinetOf]);

  const columns = result.columns;
  return (
    <section className="mt-4 overflow-hidden rounded-2xl border border-gray-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
        <h2 className="inline-flex items-center gap-2 text-base font-bold text-navy-900">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
            <Icon path={GLYPHS.chart} />
          </span>
          Speaker loading by floor
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
                <th
                  key={column.key}
                  className="px-3 py-3 text-center font-semibold"
                  title={column.lines.join(", ")}
                >
                  {column.key}
                  <span className="block text-[11px] font-normal text-gray-400">
                    {column.tap === null ? "no tapping" : `(${watts(column.tap)} W)`}
                    {column.unsettled ? " · not settled" : ""}
                  </span>
                </th>
              ))}
              <th className="px-3 py-3 text-right font-semibold">Total (W)</th>
              <th className="px-3 py-3 text-center font-semibold">{result.amplifier_part}</th>
              <th className="px-3 py-3 text-center font-semibold">APS</th>
            </tr>
            <tr className="border-t border-gray-100 bg-white">
              <th className="sticky left-0 z-10 bg-white px-5 py-2 text-left text-xs font-medium text-gray-400">
                Tapping (W)
              </th>
              <th className="px-3 py-2" />
              {columns.map((column) => (
                <th key={column.key} className="px-3 py-2 text-center">
                  <select
                    value={column.tap ?? ""}
                    disabled={!canEdit || column.taps.length === 0 || saving === `tap:${column.key}`}
                    onChange={(e) => e.target.value && onTap(column.key, Number(e.target.value))}
                    className="w-28 rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-sm text-navy-900 focus:border-brand-400 focus:outline-none disabled:bg-gray-50 disabled:text-gray-400"
                  >
                    <option value="">—</option>
                    {column.taps.map((tap) => (
                      <option key={tap} value={tap}>
                        {watts(tap)} W
                      </option>
                    ))}
                  </select>
                </th>
              ))}
              <th colSpan={3} />
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 && (
              <tr>
                <td colSpan={columns.length + 5} className="px-5 py-10 text-center text-sm text-gray-400">
                  No floor matches “{query}”.
                </td>
              </tr>
            )}
            {shown.map((floor, index) => {
              const run = runs.get(index);
              const amplifier = result.amplifiers.find((a) => a.name === floor.amplifier);
              return (
                <tr
                  key={floor.floor}
                  className={
                    cabinetStarts.has(index) ? "border-t-2 border-t-emerald-200" : "border-t border-gray-100"
                  }
                >
                  <td className="sticky left-0 z-10 bg-white px-5 py-2.5 font-semibold text-navy-900">
                    {floor.floor}
                  </td>
                  <td className="px-3 py-2.5 text-center text-xs">
                    {floor.modules === 0 ? (
                      <span className="text-gray-300">—</span>
                    ) : (
                      <>
                        <span className="font-medium text-navy-900">{result.module_part}</span>
                        {floor.modules > 1 && (
                          <span
                            className="block font-semibold text-amber-700"
                            title={`${watts(floor.watts)} W is more than one ${result.module_part} takes at ${watts(result.module_max_watts)} W`}
                          >
                            × {floor.modules}
                          </span>
                        )}
                      </>
                    )}
                  </td>
                  {columns.map((column) => (
                    <td key={column.key} className="px-2 py-1.5 text-center">
                      <Stepper
                        value={floor.counts[column.key] ?? 0}
                        canEdit={canEdit}
                        busy={saving === `${floor.floor}:${column.key}`}
                        onStep={(by) => onCount(floor.floor, column.key, (floor.counts[column.key] ?? 0) + by)}
                      />
                    </td>
                  ))}
                  <td className="px-3 py-2.5 text-right font-bold tabular-nums text-navy-900">
                    {watts(floor.watts)}
                  </td>
                  {run && (
                    <>
                      <td
                        rowSpan={run.rows}
                        className={`px-3 py-2.5 text-center align-middle text-sm font-bold ${
                          !run.amplifier
                            ? "text-gray-300"
                            : amplifier?.over_limit
                              ? "bg-red-50/70 text-red-800"
                              : "bg-sky-50/70 text-sky-900"
                        }`}
                      >
                        {run.amplifier ? (
                          <>
                            {run.amplifier}
                            <span className="block text-xs font-medium text-sky-700">
                              ({watts(amplifier?.watts ?? 0)} W)
                            </span>
                            {amplifier?.over_limit && (
                              <span className="block text-xs font-medium">over limit</span>
                            )}
                          </>
                        ) : (
                          <span className="text-xs font-normal">no speakers</span>
                        )}
                      </td>
                      <td
                        rowSpan={run.rows}
                        className={`px-3 py-2.5 text-center align-middle text-sm font-bold ${
                          run.cabinet ? "bg-emerald-50/70 text-emerald-800" : "text-gray-300"
                        }`}
                      >
                        {run.cabinet ?? "—"}
                      </td>
                    </>
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
                {watts(result.total_watts)}
              </td>
              <td className="px-3 py-3 text-center font-bold text-navy-900">{result.amplifiers.length}</td>
              <td className="px-3 py-3 text-center font-bold text-navy-900">{result.cabinets.length}</td>
            </tr>
          </tfoot>
        </table>
      </div>

      {query && (
        <div className="border-t border-gray-100 px-5 py-3 text-sm text-gray-500">
          Showing {matching.length} of {result.floors.length} floor
          {result.floors.length === 1 ? "" : "s"} matching “{query}”.{" "}
          <button onClick={() => setQuery("")} className="font-semibold text-brand-600 hover:underline">
            Show all
          </button>
        </div>
      )}
    </section>
  );
}

/** One floor's speaker count: stepped, never typed. The number belongs to
 * the floor-wise BOQ, so stepping it here changes it there. */
/** The circuits a run of staircase floors is on, told apart by colour. */
const CIRCUIT_TINTS = [
  "bg-sky-50 text-sky-800",
  "bg-violet-50 text-violet-800",
  "bg-emerald-50 text-emerald-800",
  "bg-amber-50 text-amber-800",
  "bg-rose-50 text-rose-800",
  "bg-indigo-50 text-indigo-800",
];

function StaircaseLoading({
  result,
  stair,
  canEdit,
  saving,
  onTap,
  onCount,
}: {
  result: AmplifierResult;
  stair: StaircaseResult;
  canEdit: boolean;
  saving: string | null;
  onTap: (part: string, tap: number) => void;
  onCount: (floor: string, part: string, count: number) => void;
}) {
  const tintOf = useMemo(
    () => new Map(stair.circuits.map((circuit, index) => [circuit.name, CIRCUIT_TINTS[index % CIRCUIT_TINTS.length]])),
    [stair.circuits],
  );
  const cabinetOf = useMemo(
    () => new Map(result.cabinets.flatMap((cabinet) => cabinet.amplifiers.map((name) => [name, cabinet.name] as const))),
    [result.cabinets],
  );
  const numbers = Array.from({ length: stair.stairs }, (_, index) => index + 1);
  // A floor no stair reaches has nothing to show.
  const floors = stair.floors.filter((floor) => floor.speakers > 0);
  const columns = stair.columns;
  const span = (list: string[]) => (list.length <= 1 ? list[0] ?? "" : `${list[0]} – ${list[list.length - 1]}`);

  // The amplifiers a floor's stairs are fed from, stair 1's first. A floor
  // can be on more than one: each stair is a circuit of its own, and two
  // stairs' circuits need not share an amplifier.
  const amplifierOf = new Map(stair.amplifiers.map((amplifier) => [amplifier.name, amplifier]));
  const circuitOf = new Map(stair.circuits.map((circuit) => [circuit.name, circuit]));
  const amplifiersOn = (circuits: string[]): string[] => [
    ...new Set(circuits.map((name) => circuitOf.get(name)?.amplifier).filter((name): name is string => Boolean(name))),
  ];
  const cabinetsOf = (amplifiers: string[]): string[] => [
    ...new Set(amplifiers.map((name) => cabinetOf.get(name)).filter((name): name is string => Boolean(name))),
  ];
  // Runs of floors fed from the same amplifiers, drawn as one cell each,
  // as the Speakers tab draws a run of floors on one amplifier.
  const runs = new Map<number, { amplifiers: string[]; rows: number }>();
  let open = -1;
  floors.forEach((floor, index) => {
    const amplifiers = amplifiersOn(floor.circuits);
    const previous = open >= 0 ? runs.get(open) : undefined;
    if (previous && previous.amplifiers.join("|") === amplifiers.join("|")) {
      previous.rows += 1;
      return;
    }
    runs.set(index, { amplifiers, rows: 1 });
    open = index;
  });
  const stairCabinets = cabinetsOf(stair.amplifiers.map((amplifier) => amplifier.name));

  return (
    <>
      <section className="mt-4 overflow-hidden rounded-2xl border border-gray-200 bg-white">
        <div className="px-5 py-4">
          <h2 className="inline-flex items-center gap-2 text-base font-bold text-navy-900">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
              <Icon path={GLYPHS.chart} />
            </span>
            Staircase speakers by floor
          </h2>
          <p className="mt-1 text-xs text-gray-500">
            One speaker a floor in each stair, so a floor&apos;s count is its number of stairs. Each stair is put
            on a circuit of its own until the next floor would take it over {watts(stair.circuit_limit_watts)} W,
            then a new circuit starts on a new {result.module_part}.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-y border-gray-100 bg-gray-50/60 text-gray-600">
              <tr>
                <th className="sticky left-0 z-10 bg-gray-50/60 px-5 py-3 text-left font-semibold">Floor</th>
                {columns.map((column) => (
                  <th key={column.key} className="px-3 py-3 text-center font-semibold" title={column.lines.join(", ")}>
                    {column.key}
                    <span className="block text-[11px] font-normal text-gray-400">
                      {column.tap === null ? "no tapping" : `(${watts(column.tap)} W)`}
                    </span>
                  </th>
                ))}
                {numbers.map((number) => (
                  <th key={number} className="px-3 py-3 text-center font-semibold">
                    Stair {number}
                  </th>
                ))}
                <th className="px-3 py-3 text-right font-semibold">Total (W)</th>
                <th className="px-3 py-3 text-center font-semibold">{result.amplifier_part}</th>
                <th className="px-3 py-3 text-center font-semibold">APS</th>
              </tr>
              <tr className="border-t border-gray-100 bg-white">
                <th className="sticky left-0 z-10 bg-white px-5 py-2 text-left text-xs font-medium text-gray-400">
                  Tapping (W)
                </th>
                {columns.map((column) => (
                  <th key={column.key} className="px-3 py-2 text-center">
                    <select
                      value={column.tap ?? ""}
                      disabled={!canEdit || column.taps.length === 0 || saving === `tap:${column.key}`}
                      onChange={(e) => e.target.value && onTap(column.key, Number(e.target.value))}
                      className="w-28 rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-sm text-navy-900 focus:border-brand-400 focus:outline-none disabled:bg-gray-50 disabled:text-gray-400"
                    >
                      <option value="">—</option>
                      {column.taps.map((tap) => (
                        <option key={tap} value={tap}>
                          {watts(tap)} W
                        </option>
                      ))}
                    </select>
                  </th>
                ))}
                <th colSpan={numbers.length + 3} />
              </tr>
            </thead>
            <tbody>
              {floors.map((floor, index) => {
                const run = runs.get(index);
                return (
                <tr key={floor.floor} className="border-t border-gray-100">
                  <td className="sticky left-0 z-10 bg-white px-5 py-2.5 font-semibold text-navy-900">{floor.floor}</td>
                  {columns.map((column) => (
                    <td key={column.key} className="px-2 py-1.5 text-center">
                      <Stepper
                        value={floor.counts[column.key] ?? 0}
                        canEdit={canEdit}
                        busy={saving === `${floor.floor}:${column.key}`}
                        onStep={(by) => onCount(floor.floor, column.key, (floor.counts[column.key] ?? 0) + by)}
                      />
                    </td>
                  ))}
                  {numbers.map((number) => {
                    const name = floor.circuits[number - 1];
                    return (
                      <td key={number} className="px-3 py-2 text-center text-xs">
                        {name ? (
                          <span className={`inline-block rounded-md px-2 py-1 font-semibold ${tintOf.get(name) ?? ""}`}>
                            {name}
                          </span>
                        ) : (
                          <span className="text-gray-300">—</span>
                        )}
                      </td>
                    );
                  })}
                  <td className="px-3 py-2.5 text-right font-bold tabular-nums text-navy-900">{watts(floor.watts)}</td>
                  {run && (
                    <>
                      <td
                        rowSpan={run.rows}
                        className={`px-3 py-2.5 text-center align-middle text-sm font-bold ${
                          run.amplifiers.length === 0
                            ? "text-gray-300"
                            : run.amplifiers.some((name) => amplifierOf.get(name)?.over_limit)
                              ? "bg-red-50/70 text-red-800"
                              : "bg-sky-50/70 text-sky-900"
                        }`}
                      >
                        {run.amplifiers.length === 0 ? (
                          <span className="text-xs font-normal">no load yet</span>
                        ) : (
                          run.amplifiers.map((name) => (
                            <span key={name} className="block">
                              {name}
                              <span className="block text-xs font-medium text-sky-700">
                                ({watts(amplifierOf.get(name)?.watts ?? 0)} W)
                              </span>
                              {amplifierOf.get(name)?.over_limit && (
                                <span className="block text-xs font-medium">over limit</span>
                              )}
                            </span>
                          ))
                        )}
                      </td>
                      <td
                        rowSpan={run.rows}
                        className={`px-3 py-2.5 text-center align-middle text-sm font-bold ${
                          cabinetsOf(run.amplifiers).length ? "bg-emerald-50/70 text-emerald-800" : "text-gray-300"
                        }`}
                      >
                        {cabinetsOf(run.amplifiers).length
                          ? cabinetsOf(run.amplifiers).map((name) => (
                              <span key={name} className="block">
                                {name}
                              </span>
                            ))
                          : "—"}
                      </td>
                    </>
                  )}
                </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-gray-200 bg-gray-50/60">
                <td className="sticky left-0 z-10 bg-gray-50/60 px-5 py-3 font-bold text-navy-900">
                  All {floors.length} floors
                </td>
                {columns.map((column) => (
                  <td key={column.key} className="px-3 py-3 text-center font-bold tabular-nums text-navy-900">
                    {stair.totals_by_column[column.key] ?? 0}
                  </td>
                ))}
                {numbers.map((number) => {
                  const made = stair.circuits.filter((circuit) => circuit.stair === number).length;
                  return (
                    <td key={number} className="px-3 py-3 text-center text-xs font-semibold text-navy-900">
                      {made} {made === 1 ? "circuit" : "circuits"}
                    </td>
                  );
                })}
                <td className="px-3 py-3 text-right font-bold tabular-nums text-navy-900">{watts(stair.total_watts)}</td>
                <td className="px-3 py-3 text-center font-bold text-navy-900">{stair.amplifiers.length}</td>
                <td className="px-3 py-3 text-center font-bold text-navy-900">{stairCabinets.length}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      </section>

      <section className="mt-4 overflow-hidden rounded-2xl border border-gray-200 bg-white">
        <h2 className="px-5 py-4 text-base font-bold text-navy-900">Staircase circuits</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-y border-gray-100 bg-gray-50/60 text-left text-gray-600">
              <tr>
                {["Circuit", "Stair", "Floors", "Speakers", "Load", "Module", result.amplifier_part, "APS"].map((label) => (
                  <th key={label} className="px-4 py-3 font-semibold">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {stair.circuits.map((circuit) => (
                <tr key={circuit.name} className="border-t border-gray-100">
                  <td className="px-4 py-2.5">
                    <span className={`inline-block rounded-md px-2 py-1 text-xs font-semibold ${tintOf.get(circuit.name) ?? ""}`}>
                      {circuit.name}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-gray-700">Stair {circuit.stair}</td>
                  <td className="px-4 py-2.5 text-gray-700">
                    {span(circuit.floors)}
                    <span className="ml-1 text-xs text-gray-400">({circuit.floors.length})</span>
                  </td>
                  <td className="px-4 py-2.5 tabular-nums text-gray-700">{circuit.speakers}</td>
                  <td className={`px-4 py-2.5 tabular-nums ${circuit.over_limit ? "font-bold text-red-700" : "text-navy-900"}`}>
                    {watts(circuit.watts)} / {watts(stair.circuit_limit_watts)} W
                    {circuit.over_limit && <span className="ml-1 text-xs">over limit</span>}
                  </td>
                  <td className="px-4 py-2.5 text-xs font-medium text-navy-900">{result.module_part}</td>
                  <td className="px-4 py-2.5 text-xs font-semibold text-violet-900">
                    {circuit.amplifier ?? <span className="font-normal text-gray-400">no load yet</span>}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-gray-700">
                    {circuit.amplifier ? cabinetOf.get(circuit.amplifier) ?? "—" : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {stair.warnings.length > 0 && (
          <ul className="border-t border-gray-100 px-5 py-3 text-xs text-amber-800">
            {stair.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

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
      <span className="tabular-nums text-gray-700">{value}</span>
    ) : (
      <span className="text-gray-300">&mdash;</span>
    );
  }
  const button =
    "flex h-7 w-7 items-center justify-center rounded-lg border border-gray-200 text-gray-500 hover:border-brand-300 hover:text-brand-600 disabled:opacity-30 disabled:hover:border-gray-200";
  return (
    <span className="inline-flex items-center gap-1.5">
      <button
        type="button"
        aria-label="one fewer"
        onClick={() => onStep(-1)}
        disabled={busy || value <= 0}
        className={button}
      >
        &minus;
      </button>
      <span className={`w-8 tabular-nums ${value ? "font-medium text-navy-900" : "text-gray-300"}`}>
        {value || "—"}
      </span>
      <button type="button" aria-label="one more" onClick={() => onStep(1)} disabled={busy} className={button}>
        +
      </button>
    </span>
  );
}

function SpeakerDatabase({ rows }: { rows: SpeakerDatabaseRow[] }) {
  return (
    <section className="mt-3 rounded-2xl border border-gray-200 bg-white p-5">
      <h2 className="text-sm font-bold text-navy-900">Speaker database</h2>
      <p className="mt-1 max-w-3xl text-xs text-gray-500">
        What each speaker can be tapped at, read off its datasheet in the library, for the 70 V line. Held as
        a design rule, so a tapping is corrected without a release and a calculation already issued does not
        shift when one is. Nothing is chosen for you: a datasheet says what a speaker <em>can</em> be set to,
        not what this company sets it to.
      </p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-gray-100 text-left text-xs uppercase tracking-wide text-gray-500">
            <tr>
              <th className="py-2 font-semibold">Part</th>
              <th className="py-2 font-semibold">Description</th>
              <th className="py-2 font-semibold">Tappings (W)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.part_no} className="border-b border-gray-50">
                <td className="py-2 font-semibold text-navy-900">{row.part_no}</td>
                <td className="py-2 text-gray-600">{row.description}</td>
                <td className="py-2 tabular-nums text-gray-700">{row.taps.map(watts).join("  ·  ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Settings({ result, scheduleFile }: { result: AmplifierResult; scheduleFile: string | null }) {
  const rows = [
    {
      label: "Speaker quantities",
      value: scheduleFile ?? "no floor-wise BOQ read",
      note: "From the BOQ Floor Wise tab; changing a count here changes it there.",
    },
    {
      label: "Amplifier",
      value: `${result.amplifier_part} — ${watts(result.amplifier_watts)} W rated`,
      note: `Loaded to ${watts(result.limit_watts)} W, the design rule's fraction of its rating.`,
    },
    {
      label: "Cabinet",
      value: `${result.amplifiers_per_cabinet} × ${result.amplifier_part} = 1 APS`,
      note: "An odd amplifier still needs a cabinet of its own.",
    },
    {
      label: "Audio riser module",
      value: `${result.module_part} — up to ${watts(result.module_max_watts)} W`,
      note: "One a floor; a floor carrying more needs another.",
    },
    {
      label: "Spare capacity",
      value: `${Math.round(result.spare_fraction * 100)}%`,
      note: "Shown beside the load. Amplifiers are assigned on the plain sum, against a limit that already carries its own headroom.",
    },
  ];
  return (
    <section className="mt-3 rounded-2xl border border-gray-200 bg-white p-5">
      <h2 className="text-sm font-bold text-navy-900">What this calculation is built on</h2>
      <p className="mt-1 text-xs text-gray-500">
        These are design rules, held in the database rather than in code so they can be corrected without a
        release, and versioned so a calculation already issued does not shift when one is.
      </p>
      <dl className="mt-3 divide-y divide-gray-100">
        {rows.map((row) => (
          <div key={row.label} className="grid gap-1 py-2.5 sm:grid-cols-[190px_1fr]">
            <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">{row.label}</dt>
            <dd>
              <p className="text-sm font-medium text-navy-900">{row.value}</p>
              <p className="text-xs text-gray-500">{row.note}</p>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function Notes({ result }: { result: AmplifierResult }) {
  return (
    <section className="mt-4 grid gap-4 lg:grid-cols-2">
      <div className="rounded-2xl border border-gray-200 bg-white p-5">
        <h2 className="text-sm font-bold text-navy-900">How this is worked out</h2>
        <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm text-gray-700">
          <li>Speaker quantities come from the floor-wise BOQ; the tapping is chosen here.</li>
          <li>A floor's load is the sum over speakers of count × tapping.</li>
          <li>
            Floors are added to an amplifier in the schedule's order until the next would take it over{" "}
            {watts(result.limit_watts)} W, then a new {result.amplifier_part} starts. A floor is never split
            between two.
          </li>
          <li>
            {result.amplifiers_per_cabinet} × {result.amplifier_part} = 1 APS cabinet; an odd amplifier still
            needs one.
          </li>
          <li>
            Every floor with speakers is fed through one {result.module_part}; a floor carrying more than{" "}
            {watts(result.module_max_watts)} W needs another, and says so.
          </li>
          <li>A sounder, horn or flasher is on a notification circuit and is not counted here.</li>
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
