import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { ApiError, api, apiUrl } from "../lib/api";
import { PROJECT_EDITOR_ROLES, type DatasheetFile, type PartSuggestion, type ProposedMaterial, type ProposedMaterials } from "../lib/types";
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
          {currentSystem && (
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
          {canEdit && currentSystem && (
            <button onClick={() => setAdding(true)} className="rounded-lg bg-brand-600 px-5 py-2 font-semibold text-white">
              Add material
            </button>
          )}
        </div>
      </div>

      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

      {systems.length > 0 && (
        <div className="mt-5 flex flex-wrap gap-2">
          {systems.map((s) => (
            <button
              key={s.code}
              onClick={() => setSystem(s.code)}
              className={`rounded-xl border px-5 py-2.5 text-sm font-semibold ${
                s.code === current ? "border-brand-600 bg-brand-600 text-white" : "border-gray-200 bg-white text-navy-900 hover:border-brand-300"
              }`}
              title={s.title}
            >
              {s.title}
              {s.brand && <span className={`ml-2 text-xs ${s.code === current ? "text-white/80" : "text-gray-500"}`}>{s.brand}</span>}
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
                  <span className={`rounded-md px-2 py-0.5 text-xs font-semibold ${item.source === "boq" ? "bg-blue-50 text-brand-700" : "bg-green-50 text-green-700"}`}>
                    {item.source === "boq" ? "BOQ" : "Added"}
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
