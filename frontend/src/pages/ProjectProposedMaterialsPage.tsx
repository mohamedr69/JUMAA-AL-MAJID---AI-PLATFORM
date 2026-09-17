import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { ApiError, api, apiUrl } from "../lib/api";
import { PROJECT_EDITOR_ROLES, type DatasheetFile, type FrcCables, type PartSuggestion, type ProposedMaterial, type ProposedMaterials, type Supplier } from "../lib/types";
import { useProject } from "./ProjectWorkspace";

/** The materials proposed for each system: the BOQ's parts as they are,
 * and the ones an engineer adds here -- a part number completed from
 * everything on file for the system's brand, no quantity needed. Loaded
 * from the database; nothing is scanned when the page opens. */
export function ProjectProposedMaterialsPage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);
  const [data, setData] = useState<ProposedMaterials | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [system, setSystem] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [linking, setLinking] = useState<ProposedMaterial | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.get<ProposedMaterials>(`/projects/${project.id}/materials`));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the proposed materials");
    }
  }, [project.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const systems = data?.systems ?? [];
  const current = system ?? systems[0]?.code ?? null;
  const currentSystem = systems.find((s) => s.code === current) ?? null;
  const items = (data?.items ?? []).filter((item) => (item.system_code ?? "") === (current ?? ""));

  async function withdraw(item: ProposedMaterial) {
    if (!item.id) return;
    try {
      await api.delete(`/projects/${project.id}/materials/${item.id}`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not withdraw the material");
    }
  }

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs text-gray-400">
            EP-{project.ep_number}
            {project.project_name ? ` — ${project.project_name}` : ""} / Proposed Materials
          </div>
          <h1 className="text-3xl font-bold text-navy-900">Proposed Materials</h1>
          <p className="mt-1 text-sm text-gray-500">
            What is proposed for each system: the BOQ's parts, and any material added here. A quantity is not needed for an added material.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          {currentSystem && currentSystem.code !== "FRC" && (
            <a
              href={apiUrl(`/projects/${project.id}/materials/schedule.pdf?system_code=${encodeURIComponent(currentSystem.code)}`)}
              target="_blank"
              rel="noreferrer"
              className="rounded-lg border border-brand-600 px-5 py-2 font-semibold text-brand-600"
              title="The Schedule of Material for this system, as the submittal package encloses it"
            >
              Export PDF
            </a>
          )}
          {canEdit && currentSystem && currentSystem.code !== "FRC" && (
            <button onClick={() => setAdding(true)} className="rounded-lg bg-brand-600 px-5 py-2 font-semibold text-white">
              Add material
            </button>
          )}
        </div>
      </div>

      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

      {systems.length > 0 && (
        <div className="mt-5 flex flex-wrap gap-3">
          {systems.map((s) => (
            <button
              key={s.code}
              onClick={() => setSystem(s.code)}
              className={`flex items-center gap-3 rounded-xl border-2 bg-white px-4 py-2.5 text-left ${
                s.code === current ? "border-brand-600 shadow-sm" : "border-gray-200 hover:border-brand-300"
              }`}
              title={s.title}
            >
              <SystemIcon code={s.code} />
              <span>
                <span className={`block text-sm font-semibold ${s.code === current ? "text-brand-700" : "text-navy-900"}`}>{s.title}</span>
                {s.brand && <span className="block text-xs text-gray-500">{s.brand}</span>}
              </span>
            </button>
          ))}
        </div>
      )}

      {linking && (
        <LinkDatasheetDialog
          item={linking}
          onDone={() => {
            setLinking(null);
            void load();
          }}
          onCancel={() => setLinking(null)}
        />
      )}

      {currentSystem?.code === "FRC" && (
        <FrcCablesPanel
          projectId={project.id}
          canEdit={canEdit}
          onSaved={() => void load()}
          items={items}
          systems={systems}
          onWithdraw={(item) => void withdraw(item)}
        />
      )}

      {adding && currentSystem && (
        <AddMaterialForm
          projectId={project.id}
          systemCode={currentSystem.code}
          systemTitle={currentSystem.title}
          brand={currentSystem.brand}
          onDone={() => {
            setAdding(false);
            void load();
          }}
          onCancel={() => setAdding(false)}
        />
      )}

      {currentSystem?.code !== "FRC" && (
      <div className="mt-5 overflow-x-auto rounded-xl border border-gray-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
            <tr>
              {["Part number", "Description", "Manufacturer", "Quantity", "Source", "Datasheet", ""].map((label) => (
                <th key={label} className="px-3 py-2 font-semibold">{label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data === null && (
              <tr><td colSpan={7} className="px-3 py-6 text-center text-gray-400">Loading...</td></tr>
            )}
            {data !== null && items.length === 0 && (
              <tr><td colSpan={7} className="px-3 py-6 text-center text-gray-400">No material is proposed for this system yet.</td></tr>
            )}
            {items.map((item) => (
              <tr key={`${item.source}:${item.id ?? ""}:${item.part_no}`} className="border-t border-gray-100">
                <td className="px-3 py-2 font-medium text-navy-900">{item.part_no}</td>
                <td className="px-3 py-2 text-gray-700">{item.description}{item.note ? <span className="block text-xs text-gray-500">{item.note}</span> : null}</td>
                <td className="px-3 py-2 text-gray-600">{item.manufacturer ?? "—"}</td>
                <td className="px-3 py-2 text-gray-600">{item.quantity ?? "—"}</td>
                <td className="px-3 py-2">
                  <span
                    className={`rounded-md px-2 py-0.5 text-xs font-semibold ${item.source === "boq" ? "bg-blue-50 text-brand-700" : item.source === "battery" ? "bg-amber-50 text-amber-800" : item.source === "cable" ? "bg-purple-50 text-purple-800" : "bg-green-50 text-green-700"}`}
                    title={item.source === "battery" || item.source === "cable" ? item.groups.join("; ") : undefined}
                  >
                    {item.source === "boq" ? "BOQ" : item.source === "battery" ? "Battery calculation" : item.source === "cable" ? "FRC cable" : "Added"}
                  </span>
                </td>
                <td className="px-3 py-2">
                  {item.datasheet_path ? (
                    <>
                      <a className="text-brand-600 hover:underline" href={datasheetHref(item)} target="_blank" rel="noreferrer">{item.datasheet_filename}</a>
                      {item.datasheet_linked && <span className="ml-2 rounded-md bg-gray-100 px-1.5 py-0.5 text-[10px] font-semibold text-gray-600" title="Linked to this datasheet by hand, for every project">linked</span>}
                    </>
                  ) : (
                    <span className="text-orange-700">Not in the library</span>
                  )}
                  {canEdit && item.manufacturer && (
                    <button onClick={() => setLinking(item)} className="ml-2 text-xs font-semibold text-brand-600 hover:underline">
                      {item.datasheet_path ? "Change link" : "Link datasheet"}
                    </button>
                  )}
                </td>
                <td className="px-3 py-2 text-right">
                  {canEdit && item.source === "added" && (
                    <button onClick={() => void withdraw(item)} className="text-xs font-semibold text-red-600 hover:underline">Withdraw</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      )}
    </div>
  );
}

/** Who supplies a brand, from the database, beside the brand wherever it
 * is chosen; an editor can correct it, for every project at once. */
function SupplierCard({ brand, supplier, canEdit, onSaved, title }: { brand: string; supplier: Supplier | null; canEdit: boolean; onSaved: () => void; title?: string }) {
  const [editing, setEditing] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function startEdit() {
    setValues({
      supplier: supplier?.supplier ?? "", contact: supplier?.contact ?? "", phone: supplier?.phone ?? "", emails: supplier?.emails ?? "",
      address: supplier?.address ?? "", map_url: supplier?.map_url ?? "", website: supplier?.website ?? "", notes: supplier?.notes ?? "",
    });
    setEditing(true);
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.put(`/suppliers/${encodeURIComponent(brand)}`, values);
      setEditing(false);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the supplier");
    } finally {
      setBusy(false);
    }
  }

  const website = supplier?.website ? (supplier.website.startsWith("http") ? supplier.website : `https://${supplier.website}`) : null;
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="text-sm font-semibold text-navy-900">{title ?? `${brand}: supplier`}</div>
        {canEdit && !editing && <button onClick={startEdit} className="text-xs font-semibold text-brand-600 hover:underline">{supplier ? "Edit" : "Add supplier"}</button>}
      </div>
      {!editing && supplier && (
        <div className="mt-2 space-y-1 text-sm text-gray-700">
          <div className="font-semibold">{supplier.supplier}</div>
          {supplier.contact && <div>{supplier.contact}</div>}
          {supplier.phone && <div>m: <a className="text-brand-600 hover:underline" href={`tel:${supplier.phone.replace(/\s+/g, "")}`}>{supplier.phone}</a></div>}
          {supplier.emails && (
            <div>
              e:{" "}
              {supplier.emails.split(/[,;]\s*/).map((e, i) => (
                <span key={e}>{i > 0 && ", "}<a className="text-brand-600 hover:underline" href={`mailto:${e}`}>{e}</a></span>
              ))}
            </div>
          )}
          {supplier.address && <div>{supplier.address}{supplier.map_url && <> · <a className="text-brand-600 hover:underline" href={supplier.map_url} target="_blank" rel="noreferrer">Map</a></>}</div>}
          {website && <div>w: <a className="text-brand-600 hover:underline" href={website} target="_blank" rel="noreferrer">{supplier.website}</a></div>}
          {supplier.notes && <div className="text-xs text-gray-500">{supplier.notes}</div>}
        </div>
      )}
      {!editing && !supplier && <div className="mt-2 text-xs text-gray-500">No supplier on file for {brand} yet.</div>}
      {editing && (
        <div className="mt-2 grid gap-2">
          {[["supplier", "Supplier"], ["contact", "Contact"], ["phone", "Phone"], ["emails", "Emails (comma separated)"], ["address", "Address"], ["map_url", "Map link"], ["website", "Website"], ["notes", "Notes"]].map(([key, label]) => (
            <label key={key} className="block text-xs font-semibold text-gray-600">
              {label}
              <input className="input mt-1 w-full" value={values[key] ?? ""} onChange={(e) => setValues({ ...values, [key]: e.target.value })} />
            </label>
          ))}
          {error && <div className="text-sm text-red-700">{error}</div>}
          <div className="flex gap-2">
            <button onClick={() => void save()} disabled={busy || !values.supplier?.trim()} className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-60">Save</button>
            <button onClick={() => setEditing(false)} disabled={busy} className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-navy-900">Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

/** A small icon per system, as the tabs and the cable panels show it. */
function SystemIcon({ code, size = 22 }: { code: string; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  if (code === "FAS" || code === "VES") {
    return (
      <span className="rounded-lg bg-red-50 p-1.5 text-red-600" aria-hidden="true">
        <svg {...common}><path d="M12 3c1 3 4 4.5 4 8.5a4 4 0 0 1-8 0c0-1.5.5-2.5 1.5-3.5.5 1.5 1.5 2 2.5 2 0-2-1-3.5 0-7Z" /><path d="M8 18a6 6 0 0 0 8 0" /></svg>
      </span>
    );
  }
  if (code === "ELS") {
    return (
      <span className="rounded-lg bg-green-50 p-1.5 text-green-700" aria-hidden="true">
        <svg {...common}><circle cx="14" cy="4" r="1.5" /><path d="M9 21l2-6 3 2v4" /><path d="M6 13l3-5 4 1 3 3" /><path d="M11 8l-2 5" /></svg>
      </span>
    );
  }
  if (code === "FRC") {
    return (
      <span className="rounded-lg bg-blue-50 p-1.5 text-brand-700" aria-hidden="true">
        <svg {...common}><path d="M4 7h12a3 3 0 0 1 0 6H8a3 3 0 0 0 0 6h12" /><circle cx="4" cy="7" r="1.5" /><circle cx="20" cy="19" r="1.5" /></svg>
      </span>
    );
  }
  return (
    <span className="rounded-lg bg-gray-100 p-1.5 text-gray-600" aria-hidden="true">
      <svg {...common}><rect x="4" y="4" width="16" height="16" rx="3" /></svg>
    </span>
  );
}

const SIZE_LABEL = (s: string) => s.replace("Cx", "C × ").replace("mm", " mm²");

/** The two sizes a cable comes in, the chosen one filled. */
function SizeButtons({ sizes, fields, value, disabled, onChoose }: { sizes: string[]; fields: string[]; value: string | null; disabled?: boolean; onChoose: (fields: string[], size: string) => void }) {
  return (
    <div className="mt-2 grid grid-cols-2 gap-2">
      {sizes.map((size) => (
        <button
          key={size}
          type="button"
          disabled={disabled}
          onClick={() => onChoose(fields, size)}
          className={`rounded-lg border px-3 py-2 text-sm font-semibold ${
            value === size ? "border-brand-600 bg-brand-600 text-white" : "border-gray-300 bg-white text-navy-900 hover:border-brand-300 disabled:opacity-50"
          }`}
        >
          {SIZE_LABEL(size)}
        </button>
      ))}
    </div>
  );
}

/** The warning a size against the standard raises. */
function Warning({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <div className="mt-2 flex items-start gap-2 rounded-md bg-amber-50 px-2 py-1.5 text-xs text-amber-900">
      <span aria-hidden="true" className="mt-0.5 inline-block h-3.5 w-3.5 rounded-sm bg-amber-500 text-center text-[10px] font-bold leading-[14px] text-white">!</span>
      <span>{text}</span>
    </div>
  );
}

/** The fire-rated cables, as the engineers lay them out: the fire alarm
 * system's cables (the loop, voice evacuation & 24 VDC power, the fire
 * telephone) with the cable brand's supplier under them; the monitored
 * self-contained system's monitoring cable with its supplier beside; and
 * the cable list -- the material schedule -- below. A size against the
 * standard is warned about, not refused. */
function FrcCablesPanel({
  projectId,
  canEdit,
  onSaved,
  items,
  systems,
  onWithdraw,
}: {
  projectId: number;
  canEdit: boolean;
  onSaved: () => void;
  items: ProposedMaterial[];
  systems: { code: string; title: string; brand: string | null }[];
  onWithdraw: (item: ProposedMaterial) => void;
}) {
  const [data, setData] = useState<FrcCables | null>(null);
  const [brandOpen, setBrandOpen] = useState(false);
  const [draft, setDraft] = useState<Record<string, string | null>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  const load = useCallback(async () => {
    try {
      const got = await api.get<FrcCables>(`/projects/${projectId}/frc-cables`);
      setData(got);
      setDraft(Object.fromEntries(got.cables.map((c) => [c.field, c.size])));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the cables");
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(next: { brand?: string | null; sizes?: Record<string, string | null> }) {
    if (!data) return;
    setBusy(true);
    setError(null);
    try {
      const sizes = next.sizes ?? draft;
      const saved = await api.put<FrcCables>(`/projects/${projectId}/frc-cables`, { brand: next.brand === undefined ? data.brand : next.brand, ...sizes });
      setData(saved);
      setDraft(Object.fromEntries(saved.cables.map((c) => [c.field, c.size])));
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the cables");
    } finally {
      setBusy(false);
    }
  }

  if (!data) return error ? <div className="mt-4 text-sm text-red-700">{error}</div> : null;
  const fire = systems.find((s) => s.code === "FAS");
  const lighting = systems.find((s) => s.code === "ELS");
  const byField = Object.fromEntries(data.cables.map((c) => [c.field, c]));
  const loop = byField.fire_alarm_loop;
  const voice = byField.voice_evacuation;
  const power = byField.power_24vdc;
  const telephone = byField.fire_telephone;
  const choose = (fields: string[], size: string) => {
    const sizes = { ...draft };
    for (const f of fields) sizes[f] = size;
    setDraft(sizes);
    void save({ sizes });
  };
  const shown = items.filter((i) => !filter || `${i.part_no} ${i.description} ${i.manufacturer ?? ""}`.toLowerCase().includes(filter.toLowerCase()));
  const systemName = (item: ProposedMaterial) =>
    item.groups[0]?.startsWith("Emergency light") ? "Emergency Light (Monitored)" : "Fire Alarm";

  return (
    <div className="mt-5">
      <div className="rounded-xl border border-gray-200 bg-white p-4">
        <div className="flex items-center gap-3">
          <SystemIcon code="FRC" size={26} />
          <div>
            <div className="text-lg font-bold text-navy-900">Fire-rated cables</div>
            <div className="text-xs text-gray-500">Identify the cables: select the brand first, then the size of each system's cable. A size against the standard is warned about, not refused.</div>
          </div>
        </div>

        <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          {/* The fire alarm system's cables. */}
          <div className="rounded-xl border border-red-100 bg-red-50/40 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="flex items-center gap-3">
                <SystemIcon code="FAS" size={26} />
                <div>
                  <div className="text-base font-bold text-red-700">
                    Fire Alarm System{fire?.brand ? <span className="font-semibold text-navy-900"> ({fire.brand})</span> : null}
                  </div>
                  <div className="text-xs text-gray-600">Includes: fire alarm loop, voice evacuation, 24 VDC power and fire telephone.</div>
                </div>
              </div>
              <div className="relative">
                <button
                  type="button"
                  disabled={!canEdit}
                  onClick={() => setBrandOpen((v) => !v)}
                  onBlur={() => window.setTimeout(() => setBrandOpen(false), 150)}
                  className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-semibold text-navy-900"
                  title="The cable brand"
                >
                  {data.brand ?? "Choose the cable brand"} <span aria-hidden="true">▾</span>
                </button>
                {brandOpen && canEdit && (
                  <ul className="absolute right-0 z-20 mt-1 w-56 rounded-lg border border-gray-200 bg-white shadow-lg">
                    {data.brands.map((b) => (
                      <li key={b}>
                        <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => { setBrandOpen(false); void save({ brand: b }); }} className={`block w-full px-3 py-2 text-left text-sm hover:bg-brand-50 ${b === data.brand ? "font-semibold text-brand-700" : ""}`}>
                          {b}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>

            {!data.brand && <div className="mt-3 text-sm text-gray-600">Choose the cable brand to set the sizes.</div>}
            {data.brand && (
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <div className="rounded-lg border border-gray-200 bg-white p-3">
                  <div className="text-sm font-semibold text-navy-900">Fire alarm loop cable</div>
                  <div className="text-xs text-gray-500">Standard: {SIZE_LABEL(loop.standard)}</div>
                  <SizeButtons sizes={data.sizes} fields={["fire_alarm_loop"]} value={draft.fire_alarm_loop ?? null} disabled={!canEdit || busy} onChoose={choose} />
                  <Warning text={loop.warning} />
                </div>
                <div className="rounded-lg border border-gray-200 bg-white p-3">
                  <div className="text-sm font-semibold text-navy-900">Voice evacuation & 24 VDC power cable</div>
                  <div className="text-xs text-gray-500">Standard: {SIZE_LABEL(voice.standard)}</div>
                  <SizeButtons sizes={data.sizes} fields={["voice_evacuation", "power_24vdc"]} value={draft.voice_evacuation === draft.power_24vdc ? draft.voice_evacuation ?? null : null} disabled={!canEdit || busy} onChoose={choose} />
                  <Warning text={voice.warning ?? power.warning ? "Voice evacuation / 24 VDC power on 2C × 1.5 mm² is subject to the voltage drop calculation." : null} />
                </div>
                <div className="rounded-lg border border-gray-200 bg-white p-3 md:col-span-2">
                  <div className="text-sm font-semibold text-navy-900">Fire telephone power cable</div>
                  <div className="text-xs text-gray-500">Standard: {SIZE_LABEL(telephone.standard)}</div>
                  <div className="md:w-1/2"><SizeButtons sizes={data.sizes} fields={["fire_telephone"]} value={draft.fire_telephone ?? null} disabled={!canEdit || busy} onChoose={choose} /></div>
                  <Warning text={telephone.warning} />
                </div>
              </div>
            )}
            {data.brand && (
              <div className="mt-3">
                <SupplierCard brand={data.brand} supplier={data.supplier ?? null} canEdit={canEdit} onSaved={() => void load()} title={`${data.brand}: supplier`} />
              </div>
            )}
          </div>

          {/* The monitored self-contained system's monitoring cable. */}
          {data.monitoring.applies ? (
            <div className="rounded-xl border border-green-100 bg-green-50/40 p-4">
              <div className="flex items-center gap-3">
                <SystemIcon code="ELS" size={26} />
                <div>
                  <div className="text-base font-bold text-navy-900">{lighting?.title ?? "Monitored Self-Contained Emergency Light System"}</div>
                  <div className="text-xs text-gray-600">Emergency light monitoring cable only.{lighting?.brand ? ` ${lighting.brand}.` : ""}</div>
                </div>
              </div>
              <div className="mt-3 rounded-lg border border-gray-200 bg-white p-3">
                <div className="text-sm font-semibold text-navy-900">{data.monitoring.name}</div>
                <div className="text-xs text-gray-500">Standard: {SIZE_LABEL(data.monitoring.size ?? "2Cx1.5mm")} · brand {data.monitoring.brand} · selected automatically (one brand, one size for now)</div>
                <SizeButtons sizes={data.sizes} fields={[]} value={data.monitoring.size} disabled onChoose={choose} />
              </div>
              {data.monitoring.brand && (
                <div className="mt-3">
                  <SupplierCard brand={data.monitoring.brand} supplier={data.monitoring.supplier ?? null} canEdit={canEdit} onSaved={() => void load()} title={`${data.monitoring.name}: supplier`} />
                </div>
              )}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-gray-200 p-4 text-sm text-gray-500">
              No monitored self-contained emergency light system on this project: no monitoring cable.
            </div>
          )}
        </div>
        {error && <div className="mt-3 text-sm text-red-700">{error}</div>}
      </div>

      {/* The cable list: the material schedule of the FRC system. */}
      <div className="mt-5 rounded-xl border border-gray-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 px-4 py-3">
          <div className="text-base font-bold text-navy-900">Cable List / Material Schedule</div>
          <div className="flex flex-wrap items-center gap-2">
            <input className="input w-56 py-1.5" placeholder="Search cable..." value={filter} onChange={(e) => setFilter(e.target.value)} />
            <a
              href={apiUrl(`/projects/${projectId}/materials/schedule.pdf?system_code=FRC`)}
              target="_blank"
              rel="noreferrer"
              className="rounded-lg bg-brand-600 px-4 py-1.5 text-sm font-semibold text-white"
            >
              Export PDF
            </a>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
              <tr>
                {["Part number", "Description", "System", "Manufacturer", "Supplier", "Quantity", "Source", "Datasheet", ""].map((label) => (
                  <th key={label} className="px-3 py-2 font-semibold">{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.length === 0 && (
                <tr><td colSpan={9} className="px-3 py-6 text-center text-gray-400">{items.length === 0 ? "Choose the cable brand and sizes above; the cables are listed here." : "No cable matches."}</td></tr>
              )}
              {shown.map((item) => {
                const supplier = item.source === "cable" && item.manufacturer === data.monitoring.brand ? data.monitoring.supplier : item.manufacturer === data.brand ? data.supplier : null;
                return (
                  <tr key={`${item.source}:${item.id ?? ""}:${item.part_no}:${item.groups[0] ?? ""}`} className="border-t border-gray-100">
                    <td className="px-3 py-2 font-medium text-navy-900">{item.part_no}</td>
                    <td className="px-3 py-2 text-gray-700">{item.description}{item.note ? <span className="block text-xs text-amber-800">{item.note}</span> : null}</td>
                    <td className="px-3 py-2 text-gray-600">{item.source === "cable" ? systemName(item) : "Fire Rated Cables"}</td>
                    <td className="px-3 py-2 text-gray-600">{item.manufacturer ?? "—"}</td>
                    <td className="px-3 py-2 text-gray-600">{supplier?.supplier ?? "—"}</td>
                    <td className="px-3 py-2 text-gray-600">{item.quantity ?? "—"}</td>
                    <td className="px-3 py-2">
                      <span className={`rounded-md px-2 py-0.5 text-xs font-semibold ${item.source === "cable" ? "bg-purple-50 text-purple-800" : item.source === "boq" ? "bg-blue-50 text-brand-700" : "bg-green-50 text-green-700"}`}>
                        {item.source === "cable" ? "FRC cable" : item.source === "boq" ? "BOQ" : "Added"}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      {item.datasheet_path ? (
                        <a className="text-brand-600 hover:underline" href={datasheetHref(item)} target="_blank" rel="noreferrer">{item.datasheet_filename}</a>
                      ) : (
                        <span className="text-orange-700">Not in the library</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right">
                      {canEdit && item.source === "added" && (
                        <button onClick={() => onWithdraw(item)} className="text-xs font-semibold text-red-600 hover:underline">Withdraw</button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/** Link a part to the datasheet that documents it -- for every project,
 * since the library's file names do not carry that number. The choice is
 * from the manufacturer's library listing. */
function LinkDatasheetDialog({ item, onDone, onCancel }: { item: ProposedMaterial; onDone: () => void; onCancel: () => void }) {
  const [files, setFiles] = useState<DatasheetFile[] | null>(null);
  const [filter, setFilter] = useState("");
  const [chosen, setChosen] = useState<DatasheetFile | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<DatasheetFile[]>(`/design-rules/datasheets/all?manufacturer=${encodeURIComponent(item.manufacturer ?? "")}`)
      .then(setFiles)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not list the library"));
  }, [item.manufacturer]);

  const shown = (files ?? []).filter((f) => !filter || `${f.folder}/${f.filename}`.toLowerCase().includes(filter.toLowerCase())).slice(0, 60);

  async function save() {
    if (!chosen) return;
    setBusy(true);
    setError(null);
    try {
      await api.put("/materials/datasheet-link", {
        manufacturer: item.manufacturer,
        part_no: item.part_no,
        library: chosen.library,
        path: chosen.path,
      });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the link");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-navy-950/40 p-6" role="dialog" aria-modal="true">
      <div className="w-full max-w-2xl rounded-2xl bg-white p-6 shadow-xl">
        <h2 className="text-lg font-bold text-navy-900">Link a datasheet to {item.part_no}</h2>
        <p className="mt-1 text-sm text-gray-600">
          The {item.manufacturer} library's file names do not carry this number. Choose the sheet that documents it; the link holds for every project.
        </p>
        <input className="input mt-3 w-full" placeholder="Filter the library" value={filter} onChange={(e) => setFilter(e.target.value)} autoFocus />
        <div className="mt-2 max-h-72 overflow-y-auto rounded-lg border border-gray-200">
          {files === null && !error && <div className="p-3 text-sm text-gray-400">Loading the library...</div>}
          {files !== null && shown.length === 0 && <div className="p-3 text-sm text-gray-400">No datasheet matches.</div>}
          {shown.map((f) => (
            <button
              key={`${f.library}:${f.path}`}
              type="button"
              onClick={() => setChosen(f)}
              className={`block w-full border-b border-gray-100 px-3 py-2 text-left text-sm ${chosen?.path === f.path ? "bg-brand-50 font-semibold text-brand-800" : "hover:bg-gray-50"}`}
            >
              {f.filename}
              {f.folder && <span className="ml-2 text-xs text-gray-500">{f.folder}</span>}
            </button>
          ))}
        </div>
        {error && <div className="mt-2 text-sm text-red-700">{error}</div>}
        <div className="mt-4 flex justify-end gap-3">
          <button onClick={onCancel} disabled={busy} className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-navy-900">Cancel</button>
          <button onClick={() => void save()} disabled={busy || !chosen} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60">
            {busy ? "Saving..." : "Link"}
          </button>
        </div>
      </div>
    </div>
  );
}

function datasheetHref(item: ProposedMaterial): string {
  const query = new URLSearchParams({ library: item.datasheet_library ?? "", path: item.datasheet_path ?? "" });
  return apiUrl(`/design-rules/datasheets/file?${query}`);
}

/** The add form: the manufacturer is the system's already; the part number
 * is completed from what is on file for that brand as it is typed. */
function AddMaterialForm({
  projectId,
  systemCode,
  systemTitle,
  brand,
  onDone,
  onCancel,
}: {
  projectId: number;
  systemCode: string;
  systemTitle: string;
  brand: string | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [partNo, setPartNo] = useState("");
  const [description, setDescription] = useState("");
  const [quantity, setQuantity] = useState("");
  const [note, setNote] = useState("");
  const [suggestions, setSuggestions] = useState<PartSuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current);
    if (!open) return;
    timer.current = window.setTimeout(() => {
      const query = new URLSearchParams({ brand: brand ?? "", q: partNo, limit: "12" });
      api.get<PartSuggestion[]>(`/parts/search?${query}`).then(setSuggestions).catch(() => setSuggestions([]));
    }, 200);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [partNo, brand, open]);

  function choose(suggestion: PartSuggestion) {
    setPartNo(suggestion.part_no);
    if (!description) setDescription(suggestion.description);
    setOpen(false);
  }

  async function submit() {
    if (!partNo.trim()) {
      setError("Enter a part number");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post(`/projects/${projectId}/materials`, {
        system_code: systemCode,
        catalog_no: partNo.trim(),
        description: description.trim() || null,
        manufacturer: brand,
        quantity: quantity.trim() || null,
        note: note.trim() || null,
      });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not add the material");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-5 rounded-xl border border-brand-200 bg-brand-50/40 p-4">
      <div className="text-sm font-semibold text-navy-900">
        Add a material to {systemTitle}
        {brand ? <span className="ml-2 rounded-md bg-white px-2 py-0.5 text-xs font-semibold text-brand-700">{brand}</span> : <span className="ml-2 text-xs text-amber-700">no brand on the DRF for this system</span>}
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-4">
        <label className="relative block text-xs font-semibold text-gray-600 md:col-span-1">
          Part number
          <input
            className="input mt-1 w-full"
            value={partNo}
            autoFocus
            onChange={(e) => { setPartNo(e.target.value); setOpen(true); }}
            onFocus={() => setOpen(true)}
            onBlur={() => window.setTimeout(() => setOpen(false), 150)}
            placeholder={brand ? `Type to search ${brand} parts` : "Part number"}
          />
          {open && suggestions.length > 0 && (
            <ul className="absolute z-20 mt-1 max-h-64 w-full min-w-72 overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-lg">
              {suggestions.map((s) => (
                <li key={s.part_no}>
                  <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => choose(s)} className="block w-full px-3 py-2 text-left hover:bg-brand-50">
                    <span className="font-semibold text-navy-900">{s.part_no}</span>
                    {s.description && <span className="ml-2 text-xs text-gray-600">{s.description}</span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </label>
        <label className="block text-xs font-semibold text-gray-600 md:col-span-2">
          Description
          <input className="input mt-1 w-full" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Filled from the catalogue when the part is on file" />
        </label>
        <label className="block text-xs font-semibold text-gray-600">
          Quantity <span className="font-normal text-gray-400">(optional)</span>
          <input className="input mt-1 w-full" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
        </label>
        <label className="block text-xs font-semibold text-gray-600 md:col-span-4">
          Note <span className="font-normal text-gray-400">(optional)</span>
          <input className="input mt-1 w-full" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why it is proposed, where it goes" />
        </label>
      </div>
      {error && <div className="mt-2 text-sm text-red-700">{error}</div>}
      <div className="mt-3 flex gap-3">
        <button onClick={() => void submit()} disabled={busy} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60">
          {busy ? "Adding..." : "Add"}
        </button>
        <button onClick={onCancel} disabled={busy} className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-navy-900">Cancel</button>
      </div>
    </div>
  );
}
