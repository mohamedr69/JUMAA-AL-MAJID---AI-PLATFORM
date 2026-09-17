import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { ApiError, api, API_BASE_URL } from "../lib/api";
import { formatApiDate } from "../lib/format";
import {
  PROJECT_EDITOR_ROLES,
  type FloorBoq,
  type FloorBoqInstance,
  type FloorBoqResult,
  type FloorBoqSymbol,
} from "../lib/types";
import { useProject } from "./ProjectWorkspace";

/** The BOQ floor by floor, read off the layouts.
 *
 * The engineer hands in the drawings (DWG or DXF); each one is converted,
 * its floors are found and the devices on them counted -- the legend and
 * anything outside the plan left out -- and the result is a row per floor
 * from the lowest to the highest. It stands on its own: nothing here is
 * synced from the project folder and nothing is written back to the BOQ.
 */
export function ProjectFloorBoqPage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);

  const [data, setData] = useState<FloorBoq | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<File[]>([]);
  const input = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.get<FloorBoq>(`/projects/${project.id}/floor-boq`));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the floor-wise BOQ");
    }
  }, [project.id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function read() {
    if (chosen.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const body = new FormData();
      for (const file of chosen) body.append("files", file);
      const response = await fetch(`${API_BASE_URL}/projects/${project.id}/floor-boq`, {
        method: "POST",
        credentials: "include",
        body,
      });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          detail = (await response.json()).detail ?? detail;
        } catch {
          /* no JSON body */
        }
        throw new ApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      setData((await response.json()) as FloorBoq);
      setChosen([]);
      if (input.current) input.current.value = "";
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The drawings could not be read");
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    if (!window.confirm("Clear the floor-wise BOQ? The drawings themselves are not touched.")) return;
    setBusy(true);
    try {
      await api.delete(`/projects/${project.id}/floor-boq`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not clear it");
    } finally {
      setBusy(false);
    }
  }

  const result = data?.result ?? null;
  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs text-gray-400">
            EP-{project.ep_number}
            {project.project_name ? ` — ${project.project_name}` : ""} / BOQ floor wise
          </div>
          <h1 className="text-3xl font-bold text-navy-900">BOQ floor wise</h1>
          <p className="mt-1 max-w-3xl text-sm text-gray-500">
            Hand in the fire alarm layouts (DWG or DXF). Each drawing is read floor by floor and its devices counted — the
            legend and anything drawn outside the plan are left out. A layout issued for a range of floors ("typical 2nd to
            14th") is counted for each of them.
          </p>
        </div>
        {result && canEdit && (
          <button onClick={() => void clear()} disabled={busy} className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-navy-900">
            Clear
          </button>
        )}
      </div>

      {canEdit && (
        <section className="mt-5 rounded-xl border border-gray-200 bg-white p-5">
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs font-semibold text-gray-600">
              Drawings
              <input
                ref={input}
                type="file"
                multiple
                accept=".dwg,.dxf"
                onChange={(e) => setChosen([...(e.target.files ?? [])])}
                className="mt-1 block w-full max-w-md text-sm text-gray-700 file:mr-3 file:rounded-lg file:border-0 file:bg-brand-50 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-brand-700"
              />
            </label>
            <button
              onClick={() => void read()}
              disabled={busy || chosen.length === 0}
              className="rounded-lg bg-brand-600 px-5 py-2 text-sm font-semibold text-white disabled:opacity-60"
            >
              {busy ? "Reading..." : `Read ${chosen.length || ""} drawing${chosen.length === 1 ? "" : "s"}`.trim()}
            </button>
          </div>
          {chosen.length > 0 && (
            <p className="mt-2 text-xs text-gray-500">{chosen.map((f) => f.name).join(", ")}</p>
          )}
          {data?.converter && (
            <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">{data.converter}</p>
          )}
        </section>
      )}

      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

      {result && <Schedule result={result} updatedAt={data?.updated_at ?? null} />}
      {result && (
        <Recognition
          result={result}
          canEdit={canEdit}
          onConfirm={async (block, device) => {
            setError(null);
            try {
              setData(
                await api.post<FloorBoq>(`/projects/${project.id}/floor-boq/symbols/confirm`, { block, device }),
              );
            } catch (err) {
              setError(err instanceof ApiError ? err.message : "The symbol could not be confirmed");
            }
          }}
        />
      )}
      {!result && !busy && (
        <p className="mt-5 rounded-xl border border-dashed border-gray-200 p-6 text-center text-sm text-gray-400">
          No drawing has been read yet.
        </p>
      )}
    </div>
  );
}

function Schedule({ result, updatedAt }: { result: FloorBoqResult; updatedAt: string | null }) {
  const devices = result.devices;
  const totals = devices.map((device) => result.floors.reduce((sum, floor) => sum + (floor.devices[device] ?? 0), 0));
  const grand = totals.reduce((sum, value) => sum + value, 0);
  return (
    <>
      <section className="mt-5 rounded-xl border border-gray-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-5 py-3">
          <h2 className="text-sm font-bold text-navy-900">
            Devices per floor
            <span className="ml-2 text-xs font-normal text-gray-500">
              {result.floors.length} floor{result.floors.length === 1 ? "" : "s"} · {grand} device{grand === 1 ? "" : "s"}
            </span>
          </h2>
          {updatedAt && <span className="text-xs text-gray-500">Read {formatApiDate(updatedAt, "short")}</span>}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="sticky left-0 z-10 bg-gray-50 px-4 py-2 font-semibold">Floor</th>
                {devices.map((device) => (
                  <th key={device} className="px-3 py-2 text-right font-semibold">{device}</th>
                ))}
                <th className="px-4 py-2 text-right font-semibold">Total</th>
              </tr>
            </thead>
            <tbody>
              {result.floors.length === 0 && (
                <tr>
                  <td colSpan={devices.length + 2} className="px-4 py-6 text-center text-gray-400">
                    No floor could be read from the drawings.
                  </td>
                </tr>
              )}
              {result.floors.map((floor) => (
                <tr key={`${floor.floor}:${floor.source}`} className="border-t border-gray-100">
                  <td className="sticky left-0 z-10 bg-white px-4 py-2 font-medium text-navy-900">
                    {floor.floor}
                    {floor.typical && (
                      <span className="ml-2 rounded-md bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800" title={`Typical layout for ${floor.covers.join(", ")}`}>
                        typical
                      </span>
                    )}
                    <span className="block truncate text-xs font-normal text-gray-400" title={floor.source}>{floor.source}</span>
                  </td>
                  {devices.map((device) => (
                    <td key={device} className="px-3 py-2 text-right tabular-nums text-gray-700">
                      {floor.devices[device] || <span className="text-gray-300">—</span>}
                    </td>
                  ))}
                  <td className="px-4 py-2 text-right font-semibold tabular-nums text-navy-900">{floor.total}</td>
                </tr>
              ))}
            </tbody>
            {result.floors.length > 0 && (
              <tfoot>
                <tr className="border-t-2 border-gray-200 bg-gray-50">
                  <td className="sticky left-0 z-10 bg-gray-50 px-4 py-2 font-bold text-navy-900">All floors</td>
                  {totals.map((total, index) => (
                    <td key={devices[index]} className="px-3 py-2 text-right font-semibold tabular-nums text-navy-900">{total}</td>
                  ))}
                  <td className="px-4 py-2 text-right font-bold tabular-nums text-navy-900">{grand}</td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      </section>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <section className="rounded-xl border border-gray-200 bg-white p-5">
          <h2 className="text-sm font-bold text-navy-900">What was read</h2>
          <ul className="mt-2 space-y-1.5 text-sm">
            {result.files.map((file) => (
              <li key={file.file} className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-medium text-navy-900">{file.file}</span>
                <span className="text-xs text-gray-500">
                  {file.floors.length > 0 ? file.floors.join(", ") : "no floor found"} · {file.devices} device{file.devices === 1 ? "" : "s"}
                </span>
              </li>
            ))}
          </ul>
          <dl className="mt-3 grid grid-cols-2 gap-3 border-t border-gray-100 pt-3 text-center sm:grid-cols-4">
            <Excluded label="Legend symbols" value={result.legend_excluded} />
            <Excluded label="Outside the plan" value={result.outside_plan_excluded} />
            <Excluded label="Drawing furniture" value={result.furniture_excluded} />
            <Excluded label="Risers & details" value={result.diagram_excluded ?? 0} />
          </dl>
        </section>

        <section className="rounded-xl border border-gray-200 bg-white p-5">
          <h2 className="text-sm font-bold text-navy-900">Notes</h2>
          {result.warnings.length === 0 ? (
            <p className="mt-2 text-sm text-gray-400">Every drawing was read.</p>
          ) : (
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-amber-900">
              {result.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </>
  );
}

/** How sure the platform is, in the words the engineer uses. */
const BANDS: Record<FloorBoqSymbol["state"], { label: string; chip: string; bar: string }> = {
  accepted: { label: "Accepted", chip: "bg-emerald-50 text-emerald-800", bar: "bg-emerald-500" },
  accepted_flagged: { label: "Accepted, flagged", chip: "bg-sky-50 text-sky-800", bar: "bg-sky-500" },
  review: { label: "Needs review", chip: "bg-amber-50 text-amber-900", bar: "bg-amber-500" },
  unresolved: { label: "Unresolved", chip: "bg-red-50 text-red-700", bar: "bg-red-500" },
};

const ORDER: FloorBoqSymbol["state"][] = ["unresolved", "review", "accepted_flagged", "accepted"];

/** What each symbol was taken to be.
 *
 * The drawings are counted by their geometry, not by the block names --
 * a symbol is recognised once however many times it is drawn -- so this
 * is the short list an engineer actually checks: a dozen or two symbols
 * behind thousands of devices. Settling one here counts the schedule
 * again and teaches the platform the symbol for every project after it.
 */
function Recognition({
  result,
  canEdit,
  onConfirm,
}: {
  result: FloorBoqResult;
  canEdit: boolean;
  onConfirm: (block: string, device: string) => Promise<void>;
}) {
  const symbols = result.symbols ?? [];
  const [open, setOpen] = useState<string | null>(null);
  if (symbols.length === 0) return null;

  const sorted = [...symbols].sort(
    (a, b) => ORDER.indexOf(a.state) - ORDER.indexOf(b.state) || b.instances - a.instances,
  );
  const unsure = symbols.filter((symbol) => symbol.state === "review" || symbol.state === "unresolved").length;

  return (
    <section className="mt-4 rounded-xl border border-gray-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-5 py-3">
        <div>
          <h2 className="text-sm font-bold text-navy-900">
            What each symbol is
            <span className="ml-2 text-xs font-normal text-gray-500">
              {symbols.length} symbol{symbols.length === 1 ? "" : "s"} ·{" "}
              {symbols.reduce((sum, symbol) => sum + symbol.instances, 0)} devices
            </span>
          </h2>
          <p className="mt-0.5 text-xs text-gray-500">
            Recognised from the geometry inside each block, this drawing's own legend and the symbols the platform has been
            taught. The block name counts for little.
          </p>
        </div>
        {unsure > 0 && (
          <span className="rounded-md bg-amber-50 px-2 py-1 text-xs font-semibold text-amber-900">
            {unsure} to check
          </span>
        )}
      </div>
      <ul className="divide-y divide-gray-100">
        {sorted.map((symbol) => {
          const band = BANDS[symbol.state];
          const key = `${symbol.file}:${symbol.block}`;
          const showing = open === key;
          return (
            <li key={key} className="px-5 py-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-navy-900">{symbol.device}</span>
                    <span className={`rounded-md px-1.5 py-0.5 text-[10px] font-semibold ${band.chip}`}>{band.label}</span>
                    {symbol.conflict && (
                      <span className="rounded-md bg-orange-50 px-1.5 py-0.5 text-[10px] font-semibold text-orange-800">
                        name disagrees
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 truncate text-xs text-gray-500">
                    block {symbol.block} · layer {symbol.layer || "—"} · {symbol.instances} on{" "}
                    {symbol.floors.slice(0, 3).join(", ")}
                    {symbol.floors.length > 3 ? ` +${symbol.floors.length - 3}` : ""}
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <div className="w-28">
                    <div className="h-1.5 rounded-full bg-gray-100">
                      <div className={`h-1.5 rounded-full ${band.bar}`} style={{ width: `${Math.max(symbol.confidence, 3)}%` }} />
                    </div>
                    <div className="mt-0.5 text-right text-xs tabular-nums text-gray-500">
                      {symbol.confidence.toFixed(0)}%
                    </div>
                  </div>
                  <button
                    onClick={() => setOpen(showing ? null : key)}
                    className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-semibold text-navy-900"
                  >
                    {showing ? "Hide" : "Evidence"}
                  </button>
                </div>
              </div>
              {showing && (
                <Evidence
                  symbol={symbol}
                  instances={(result.instances ?? []).filter(
                    (instance) => instance.block === symbol.block && instance.file === symbol.file,
                  )}
                  canEdit={canEdit}
                  onConfirm={onConfirm}
                />
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Evidence({
  symbol,
  instances,
  canEdit,
  onConfirm,
}: {
  symbol: FloorBoqSymbol;
  instances: FloorBoqInstance[];
  canEdit: boolean;
  onConfirm: (block: string, device: string) => Promise<void>;
}) {
  const [device, setDevice] = useState(symbol.device);
  const [saving, setSaving] = useState(false);
  return (
    <div className="mt-3 grid gap-4 rounded-lg bg-gray-50 p-4 lg:grid-cols-2">
      <div>
        <h3 className="text-xs font-bold uppercase tracking-wide text-gray-500">What says so</h3>
        {symbol.evidence.length === 0 ? (
          <p className="mt-1 text-sm text-gray-500">Nothing on the drawing speaks for this symbol.</p>
        ) : (
          <ul className="mt-1 space-y-1 text-sm">
            {symbol.evidence.map((evidence, index) => (
              <li key={`${evidence.source}:${index}`} className="flex items-baseline justify-between gap-3">
                <span className="text-gray-700">
                  <span className="font-medium text-navy-900">{evidence.source}</span> — {evidence.device}
                  {evidence.detail ? <span className="block text-xs text-gray-500">{evidence.detail}</span> : null}
                </span>
                <span className="text-xs tabular-nums text-gray-500">{evidence.weight}</span>
              </li>
            ))}
          </ul>
        )}
        {symbol.conflict && <p className="mt-2 text-xs text-orange-800">{symbol.conflict}</p>}
        {canEdit && (
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <label className="text-xs font-semibold text-gray-600">
              This symbol is
              <input
                value={device}
                onChange={(e) => setDevice(e.target.value)}
                className="mt-1 block w-56 rounded-lg border border-gray-300 px-3 py-1.5 text-sm"
              />
            </label>
            <button
              onClick={async () => {
                setSaving(true);
                try {
                  await onConfirm(symbol.block, device.trim());
                } finally {
                  setSaving(false);
                }
              }}
              disabled={saving || device.trim().length === 0 || device.trim() === symbol.device}
              className="rounded-lg bg-brand-600 px-4 py-1.5 text-sm font-semibold text-white disabled:opacity-60"
            >
              {saving ? "Saving..." : "Confirm"}
            </button>
          </div>
        )}
        <p className="mt-2 text-xs text-gray-500">
          Confirming counts the schedule again and keeps the symbol, so the next project recognises it on its own.
        </p>
      </div>
      <div>
        <h3 className="text-xs font-bold uppercase tracking-wide text-gray-500">
          Where it is <span className="font-normal normal-case text-gray-400">({instances.length} shown)</span>
        </h3>
        <div className="mt-1 max-h-48 overflow-y-auto rounded-lg border border-gray-200 bg-white">
          <table className="w-full text-xs">
            <thead className="bg-gray-50 text-left uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-3 py-1.5 font-semibold">Floor</th>
                <th className="px-3 py-1.5 font-semibold">Address</th>
                <th className="px-3 py-1.5 text-right font-semibold">X</th>
                <th className="px-3 py-1.5 text-right font-semibold">Y</th>
              </tr>
            </thead>
            <tbody>
              {instances.slice(0, 200).map((instance, index) => (
                <tr key={`${instance.floor}:${instance.x}:${instance.y}:${index}`} className="border-t border-gray-100">
                  <td className="px-3 py-1 text-gray-700">{instance.floor}</td>
                  <td className="px-3 py-1 text-gray-500">{instance.address || "—"}</td>
                  <td className="px-3 py-1 text-right tabular-nums text-gray-500">{instance.x}</td>
                  <td className="px-3 py-1 text-right tabular-nums text-gray-500">{instance.y}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Excluded({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-xs text-gray-500">{label}</dt>
      <dd className="text-lg font-bold text-navy-900">{value}</dd>
    </div>
  );
}
