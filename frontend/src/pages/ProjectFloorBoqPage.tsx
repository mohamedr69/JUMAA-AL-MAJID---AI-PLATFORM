import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { ApiError, api, API_BASE_URL } from "../lib/api";
import { formatApiDate } from "../lib/format";
import { PROJECT_EDITOR_ROLES, type FloorBoq, type FloorBoqResult } from "../lib/types";
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
          <dl className="mt-3 grid grid-cols-3 gap-3 border-t border-gray-100 pt-3 text-center">
            <Excluded label="Legend symbols" value={result.legend_excluded} />
            <Excluded label="Outside the plan" value={result.outside_plan_excluded} />
            <Excluded label="Drawing furniture" value={result.furniture_excluded} />
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

function Excluded({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-xs text-gray-500">{label}</dt>
      <dd className="text-lg font-bold text-navy-900">{value}</dd>
    </div>
  );
}
