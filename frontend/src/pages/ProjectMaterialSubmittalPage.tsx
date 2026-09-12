import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ApiError, api, apiUrl } from "../lib/api";
import {
  PROJECT_EDITOR_ROLES,
  type MaterialItem,
  type MaterialSubmittal,
  type StorageFolder,
  type Submittal,
  type SubmittalRegister,
  type SubmittalScan,
  type SubmittalStatus,
  type SubmittalSuggestion,
} from "../lib/types";
import { useProject } from "./ProjectWorkspace";

const ALL = "__all__";

const STATUS: Record<SubmittalStatus, { label: string; chip: string; dot: string }> = {
  approved: { label: "Approved", chip: "bg-green-50 text-green-700", dot: "bg-green-500" },
  under_review: { label: "Under Review", chip: "bg-blue-50 text-blue-700", dot: "bg-blue-500" },
  rejected: { label: "Rejected", chip: "bg-red-50 text-red-700", dot: "bg-red-500" },
  not_submitted: { label: "Not Submitted", chip: "bg-gray-100 text-gray-600", dot: "bg-gray-400" },
};

const STATUS_ORDER: SubmittalStatus[] = ["not_submitted", "under_review", "approved", "rejected"];

/** The API sends naive UTC; without a zone the browser reads it as local. */
function when(value: string, withTime = false): string {
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
  return date.toLocaleString(undefined, withTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" });
}

export function ProjectMaterialSubmittalPage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);

  const [data, setData] = useState<SubmittalRegister | null>(null);
  const [materials, setMaterials] = useState<MaterialSubmittal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState(ALL);
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState<SubmittalSuggestion | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState<SubmittalScan | null>(null);

  const load = useCallback(
    () =>
      api
        .get<SubmittalRegister>(`/projects/${project.id}/submittals`)
        .then(setData)
        .catch((err) => setError(err instanceof ApiError ? err.message : "Failed to load the submittals")),
    [project.id]
  );

  useEffect(() => {
    setData(null);
    setError(null);
    load();
    api
      .get<MaterialSubmittal>(`/projects/${project.id}/submittal/materials`)
      .then(setMaterials)
      .catch(() => setMaterials(null));
  }, [load, project.id]);

  async function create(values: { title: string; system_code: string | null; manufacturer: string | null }) {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/projects/${project.id}/submittals`, values);
      setCreating(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the submittal");
    } finally {
      setBusy(false);
    }
  }

  async function patch(id: number, changes: Partial<Submittal>) {
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/projects/${project.id}/submittals/${id}`, changes);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the change");
    } finally {
      setBusy(false);
    }
  }

  async function remove(item: Submittal) {
    if (!window.confirm(`Remove "${item.title}" from the register? Its history goes with it.`)) return;
    setBusy(true);
    try {
      await api.delete(`/projects/${project.id}/submittals/${item.id}`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not remove the submittal");
    } finally {
      setBusy(false);
    }
  }

  /** Read the project folder: the consultant's reply where a form carries
   * one, under review where it does not. */
  async function scan() {
    setScanning(true);
    setError(null);
    setScanResult(null);
    try {
      const result = await api.post<SubmittalScan>(`/projects/${project.id}/submittals/scan`);
      setData(result.updated_register);
      setScanResult(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not scan the project folder");
    } finally {
      setScanning(false);
    }
  }

  async function exportXlsx() {
    setBusy(true);
    try {
      await api.download(`/projects/${project.id}/submittals/export.xlsx`, `EP-${project.ep_number} Material Submittals.xlsx`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Export failed");
    } finally {
      setBusy(false);
    }
  }

  const needle = filter.trim().toLowerCase();
  const items = (data?.items ?? []).filter(
    (item) =>
      (tab === ALL || (item.system_code ?? "") === tab) &&
      (!needle ||
        item.title.toLowerCase().includes(needle) ||
        (item.manufacturer ?? "").toLowerCase().includes(needle) ||
        (item.system_code ?? "").toLowerCase().includes(needle))
  );
  const counts = data?.counts ?? {};

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold text-navy-900">Material Submittals</h1>
          <p className="mt-1 text-sm text-gray-500">Prepare, manage and track all material submittal documents for this project.</p>
        </div>
        <span
          className={`rounded-full px-3 py-1 text-xs font-semibold ${
            project.status === "active" ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-600"
          }`}
        >
          {project.status}
        </span>
      </div>

      <div className="mt-4 flex flex-wrap gap-x-8 gap-y-3 rounded-xl border border-gray-200 bg-white px-5 py-3">
        <Fact icon={<IconPin />} label="Location" value={project.location} />
        <Fact icon={<IconBuilding />} label="Project Name" value={project.project_name} />
        <Fact icon={<IconUsers />} label="Client" value={project.client} />
        <Fact icon={<IconBriefcase />} label="MEP Contractor" value={project.contractor} />
        <Fact icon={<IconGear />} label="Systems" value={project.systems.map((s) => s.name).join(", ") || null} />
      </div>

      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {scanning && (
        <div className="mt-4 rounded-lg bg-blue-50 px-3 py-2 text-sm text-blue-800">
          Reading the project folder: every PDF&rsquo;s first page, then the consultant&rsquo;s stamp on the forms found.
        </div>
      )}
      {scanResult && !scanning && (
        <div className="mt-4 rounded-lg bg-blue-50 px-3 py-2 text-sm text-blue-800">
          <div className="font-medium">
            {scanResult.found} submittal{scanResult.found === 1 ? "" : "s"} found in the project folder · {scanResult.created} added,{" "}
            {scanResult.updated} updated, {scanResult.unchanged} unchanged.
          </div>
          <ul className="mt-1 space-y-0.5 text-xs">
            {scanResult.forms.map((form) => (
              <li key={`${form.reference}:${form.revision}`}>
                <span className="font-medium">
                  {form.reference} {form.revision}
                </span>{" "}
                — {form.reply_code ? `reply ${form.reply_code}` : "no consultant reply yet"}
                {form.read_by_ocr && form.reply_code ? " (read from the stamp)" : ""} ·{" "}
                <span className="text-blue-700">{STATUS[form.status].label}</span> · {form.path}
              </li>
            ))}
            {scanResult.warnings.map((warning) => (
              <li key={warning} className="text-amber-700">
                {warning}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-3 border-b border-gray-200">
        <div className="flex flex-wrap gap-1">
          <TabButton active={tab === ALL} onClick={() => setTab(ALL)} label="All Submittals" />
          {(data?.systems ?? []).map((system) => (
            <TabButton
              key={system}
              active={tab === system}
              onClick={() => setTab(system)}
              label={system || "Unassigned"}
            />
          ))}
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2 pb-2">
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Search submittal, manufacturer, system..."
            className="input w-64 py-1.5"
          />
          <button
            onClick={exportXlsx}
            disabled={busy || !data}
            className="flex items-center gap-2 rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            <IconDownload /> Export
          </button>
        </div>
      </div>

      {!data ? (
        !error && <div className="mt-6 rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500">Loading...</div>
      ) : (
        <div className="mt-5 grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_20rem]">
          <div>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat icon={<IconDoc />} tint="bg-blue-50 text-blue-600" value={counts.total ?? 0} label="Total Submittals" />
              <Stat icon={<IconCheck />} tint="bg-green-50 text-green-600" value={counts.approved ?? 0} label="Approved" />
              <Stat icon={<IconClock />} tint="bg-orange-50 text-orange-500" value={counts.under_review ?? 0} label="Under Review" />
              <Stat icon={<IconCross />} tint="bg-red-50 text-red-500" value={counts.rejected ?? 0} label="Rejected" />
            </div>

            <div className="mt-4 overflow-x-auto rounded-xl border border-gray-200 bg-white">
              <table className="w-full min-w-[860px] text-sm">
                <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                  <tr>
                    <th className="px-3 py-2.5 font-medium">#</th>
                    <th className="px-3 py-2.5 font-medium">Submittal Title</th>
                    <th className="px-3 py-2.5 font-medium">System</th>
                    <th className="px-3 py-2.5 font-medium">Manufacturer</th>
                    <th className="px-3 py-2.5 font-medium">Revision</th>
                    <th className="px-3 py-2.5 font-medium">Status</th>
                    <th className="px-3 py-2.5 font-medium">Last Updated</th>
                    <th className="px-3 py-2.5 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {items.map((item, i) =>
                    editing === item.id && canEdit ? (
                      <EditRow key={item.id} item={item} busy={busy} onCancel={() => setEditing(null)} onSave={async (changes) => {
                        await patch(item.id, changes);
                        setEditing(null);
                      }} />
                    ) : (
                      <tr key={item.id}>
                        <td className="px-3 py-2.5 text-gray-400">{i + 1}</td>
                        <td className="px-3 py-2.5">
                          <div className="font-medium text-navy-900">{item.title}</div>
                          <div className="text-xs text-gray-500">
                            {item.reference && <span className="font-mono">{item.reference} · </span>}
                            {item.materials} material{item.materials === 1 ? "" : "s"} · {item.materials_with_datasheet} with a datasheet
                            {item.note && <span title={item.note}> · note</span>}
                          </div>
                        </td>
                        <td className="px-3 py-2.5">
                          {item.system_code && (
                            <span className="rounded-md bg-blue-50 px-2 py-0.5 text-xs font-semibold text-brand-700">{item.system_code}</span>
                          )}
                        </td>
                        <td className="px-3 py-2.5 text-gray-700">{item.manufacturer ?? "—"}</td>
                        <td className="px-3 py-2.5 tabular-nums text-gray-700">{item.revision}</td>
                        <td className="px-3 py-2.5">
                          {canEdit ? (
                            <select
                              value={item.status}
                              disabled={busy}
                              onChange={(e) => patch(item.id, { status: e.target.value as SubmittalStatus })}
                              className={`rounded-full border-0 px-2 py-1 text-xs font-semibold ${STATUS[item.status].chip}`}
                            >
                              {STATUS_ORDER.map((status) => (
                                <option key={status} value={status}>
                                  {STATUS[status].label}
                                </option>
                              ))}
                            </select>
                          ) : (
                            <span className={`rounded-full px-2 py-1 text-xs font-semibold ${STATUS[item.status].chip}`}>
                              {STATUS[item.status].label}
                            </span>
                          )}
                          {item.reply_code && (
                            <div className="mt-0.5 text-[11px] text-gray-500" title={item.note ?? undefined}>
                              Consultant reply {item.reply_code}
                              {item.reply_code === "B" ? " (as noted)" : item.reply_code === "C" ? " (resubmit)" : ""}
                            </div>
                          )}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2.5 text-gray-600">{when(item.updated_at)}</td>
                        <td className="whitespace-nowrap px-3 py-2.5 text-gray-400">
                          <Link to="../boq" title="The BOQ this submittal covers" className="mr-2 hover:text-brand-600">
                            <IconOpen />
                          </Link>
                          {canEdit && (
                            <button onClick={() => setEditing(item.id)} title="Edit" className="mr-2 hover:text-brand-600">
                              <IconPencil />
                            </button>
                          )}
                          {canEdit && (
                            <button onClick={() => remove(item)} title="Remove" className="hover:text-red-600">
                              <IconTrash />
                            </button>
                          )}
                        </td>
                      </tr>
                    )
                  )}
                  {items.length === 0 && (
                    <tr>
                      <td colSpan={8} className="px-3 py-8 text-center text-sm text-gray-400">
                        {data.items.length === 0
                          ? "No submittal yet. Create one from the BOQ under Quick Actions."
                          : "No submittal matches."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            {data.items.length > 0 && (
              <div className="mt-2 text-xs text-gray-500">
                Showing {items.length} of {data.items.length} submittals
              </div>
            )}

            <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-2">
              <section className="rounded-xl border border-gray-200 bg-white p-4">
                <h2 className="flex items-center gap-2 font-semibold text-navy-900">
                  <IconClock /> Recent Activity
                </h2>
                <ul className="mt-3 space-y-3 text-sm">
                  {data.activity.map((event, i) => (
                    <li key={i} className="flex items-start justify-between gap-3">
                      <div>
                        <div className="text-navy-900">
                          <span className="font-medium">{event.detail}</span> — {event.submittal_title}
                        </div>
                        <div className="text-xs text-gray-500">{event.by ?? "Someone"}</div>
                      </div>
                      <div className="whitespace-nowrap text-xs text-gray-500">{when(event.at, true)}</div>
                    </li>
                  ))}
                  {data.activity.length === 0 && <li className="text-gray-400">Nothing yet.</li>}
                </ul>
              </section>

              <section className="rounded-xl border border-gray-200 bg-white p-4">
                <h2 className="flex items-center gap-2 font-semibold text-navy-900">
                  <IconFolder /> Document Storage
                </h2>
                <ul className="mt-3 space-y-2 text-sm">
                  {data.storage.map((folder) => (
                    <StorageRow key={folder.path} folder={folder} />
                  ))}
                  {data.storage.length === 0 && (
                    <li className="text-gray-400">
                      {project.source_folder_path ? "The project folder is not reachable." : "The project has no archive folder."}
                    </li>
                  )}
                </ul>
              </section>
            </div>

            {materials && (
              <details className="mt-5 rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm">
                <summary className="cursor-pointer font-medium text-gray-600">
                  Materials from the BOQ ({materials.with_datasheet} of {materials.items.length} with a datasheet)
                </summary>
                <MaterialsTable materials={materials} system={tab === ALL ? null : tab} />
              </details>
            )}
          </div>

          <aside>
            <h2 className="flex items-center gap-2 font-semibold text-navy-900">
              <IconBolt /> Quick Actions
            </h2>
            {creating ? (
              <CreateForm suggestion={creating} busy={busy} onCancel={() => setCreating(null)} onCreate={create} />
            ) : (
              <div className="mt-3 space-y-3">
                {canEdit && (
                  <ActionCard
                    tint="bg-green-50/70"
                    icon={<IconScan />}
                    title={scanning ? "Reading the project folder..." : "Scan project folder"}
                    body="Find the submittals filed for this project and read the consultant's reply (A / B / C) off each."
                    disabled={scanning || !project.source_folder_path}
                    onClick={scan}
                  />
                )}
                {canEdit && (
                  <ActionCard
                    tint="bg-blue-50/70"
                    icon={<IconDoc />}
                    title="Create Material Submittal"
                    body="Start a submittal from the project's BOQ."
                    onClick={() => setCreating({ title: "", system_code: null, manufacturer: null, materials: 0, materials_with_datasheet: 0 })}
                  />
                )}
                {data.suggestions.map((suggestion) => (
                  <ActionCard
                    key={suggestion.system_code ?? suggestion.title}
                    tint="bg-white"
                    icon={<IconPlus />}
                    title={suggestion.title}
                    body={`${suggestion.materials} materials in the BOQ, ${suggestion.materials_with_datasheet} with a datasheet${
                      suggestion.manufacturer ? ` · ${suggestion.manufacturer}` : ""
                    }`}
                    disabled={!canEdit}
                    onClick={() => setCreating(suggestion)}
                  />
                ))}
                {data.suggestions.length === 0 && data.items.length > 0 && (
                  <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 text-xs text-gray-500">
                    Every system in the BOQ has a submittal.
                  </div>
                )}
              </div>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}

function StorageRow({ folder }: { folder: StorageFolder }) {
  const [copied, setCopied] = useState(false);
  return (
    <li className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <div className="truncate font-medium text-navy-900" title={folder.path}>
          {folder.name}
        </div>
        <div className="text-xs text-gray-500">
          {folder.items} item{folder.items === 1 ? "" : "s"}
          {folder.modified && ` · ${when(folder.modified)}`}
        </div>
      </div>
      <button
        onClick={() => {
          navigator.clipboard?.writeText(folder.path);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        }}
        className="whitespace-nowrap rounded-lg border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
      >
        {copied ? "Path copied" : "Copy path"}
      </button>
    </li>
  );
}

function MaterialsTable({ materials, system }: { materials: MaterialSubmittal; system: string | null }) {
  const rows = materials.items.filter((item) => system === null || (item.system_code ?? "") === system);
  return (
    <div className="mt-3 max-h-96 overflow-auto rounded-lg border border-gray-200">
      <table className="w-full min-w-[720px] text-sm">
        <thead className="sticky top-0 bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
          <tr>
            <th className="px-3 py-2 font-medium">System</th>
            <th className="px-3 py-2 font-medium">Part No.</th>
            <th className="px-3 py-2 font-medium">Description</th>
            <th className="px-3 py-2 text-right font-medium">Qty</th>
            <th className="px-3 py-2 font-medium">Datasheet</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.map((item) => (
            <tr key={`${item.system_code}:${item.part_no}`} className={item.datasheet_path ? undefined : "bg-orange-50/50"}>
              <td className="px-3 py-1.5 text-gray-600">{item.system_code ?? "—"}</td>
              <td className="px-3 py-1.5 font-medium text-navy-900">{item.part_no}</td>
              <td className="max-w-md truncate px-3 py-1.5 text-gray-700" title={item.description}>
                {item.description}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums text-gray-600">
                {item.quantity === null ? "—" : item.quantity.toLocaleString()}
              </td>
              <td className="px-3 py-1.5">
                {item.datasheet_path ? (
                  <a href={datasheetHref(item)} target="_blank" rel="noreferrer" className="text-brand-600 hover:underline">
                    {item.datasheet_filename}
                    {!item.datasheet_named_for_part && <span className="ml-1 text-xs text-amber-700">(mentions it)</span>}
                  </a>
                ) : (
                  <span className="text-xs font-medium text-orange-600">Not in the library</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function datasheetHref(item: MaterialItem): string {
  const query = new URLSearchParams({ library: item.datasheet_library ?? "", path: item.datasheet_path ?? "" });
  return apiUrl(`/design-rules/datasheets/file?${query}`);
}

function CreateForm({
  suggestion,
  busy,
  onCancel,
  onCreate,
}: {
  suggestion: SubmittalSuggestion;
  busy: boolean;
  onCancel: () => void;
  onCreate: (values: { title: string; system_code: string | null; manufacturer: string | null }) => void;
}) {
  const [title, setTitle] = useState(suggestion.title);
  const [system, setSystem] = useState(suggestion.system_code ?? "");
  const [manufacturer, setManufacturer] = useState(suggestion.manufacturer ?? "");
  return (
    <div className="mt-3 rounded-xl border border-gray-200 bg-white p-4 text-sm">
      <div className="font-semibold text-navy-900">New material submittal</div>
      <label className="mt-3 block">
        <span className="text-xs font-medium text-gray-500">Title</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Fire Alarm System" className="input mt-1 py-1.5" />
      </label>
      <div className="mt-2 flex gap-2">
        <label className="w-24">
          <span className="text-xs font-medium text-gray-500">System</span>
          <input value={system} onChange={(e) => setSystem(e.target.value)} placeholder="FAS" className="input mt-1 py-1.5" />
        </label>
        <label className="min-w-0 flex-1">
          <span className="text-xs font-medium text-gray-500">Manufacturer</span>
          <input value={manufacturer} onChange={(e) => setManufacturer(e.target.value)} className="input mt-1 py-1.5" />
        </label>
      </div>
      {suggestion.materials > 0 && (
        <div className="mt-2 text-xs text-gray-500">
          {suggestion.materials} materials in the BOQ, {suggestion.materials_with_datasheet} with a datasheet.
        </div>
      )}
      <div className="mt-3 flex gap-2">
        <button
          onClick={() => onCreate({ title: title.trim(), system_code: system.trim() || null, manufacturer: manufacturer.trim() || null })}
          disabled={busy || !title.trim()}
          className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
        >
          Create
        </button>
        <button onClick={onCancel} className="rounded-lg px-3 py-1.5 text-sm text-gray-500 hover:bg-gray-100">
          Cancel
        </button>
      </div>
    </div>
  );
}

function EditRow({
  item,
  busy,
  onCancel,
  onSave,
}: {
  item: Submittal;
  busy: boolean;
  onCancel: () => void;
  onSave: (changes: Partial<Submittal>) => void;
}) {
  const [values, setValues] = useState({
    title: item.title,
    system_code: item.system_code ?? "",
    manufacturer: item.manufacturer ?? "",
    revision: item.revision,
    note: item.note ?? "",
  });
  return (
    <tr className="bg-blue-50/40">
      <td className="px-3 py-2 text-gray-400">—</td>
      <td className="px-3 py-2">
        <input value={values.title} onChange={(e) => setValues({ ...values, title: e.target.value })} className="input py-1" />
        <input
          value={values.note}
          onChange={(e) => setValues({ ...values, note: e.target.value })}
          placeholder="Note (consultant comment, what changed)"
          className="input mt-1 py-1 text-xs"
        />
      </td>
      <td className="px-3 py-2">
        <input value={values.system_code} onChange={(e) => setValues({ ...values, system_code: e.target.value })} className="input w-16 py-1" />
      </td>
      <td className="px-3 py-2">
        <input value={values.manufacturer} onChange={(e) => setValues({ ...values, manufacturer: e.target.value })} className="input w-36 py-1" />
      </td>
      <td className="px-3 py-2">
        <input value={values.revision} onChange={(e) => setValues({ ...values, revision: e.target.value })} className="input w-16 py-1" />
      </td>
      <td className="px-3 py-2 text-xs text-gray-500" colSpan={2}>
        Status is set from the register.
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-xs">
        <button
          onClick={() =>
            onSave({
              title: values.title.trim(),
              system_code: values.system_code.trim() || null,
              manufacturer: values.manufacturer.trim() || null,
              revision: values.revision.trim(),
              note: values.note.trim() || null,
            })
          }
          disabled={busy || !values.title.trim() || !values.revision.trim()}
          className="rounded bg-brand-600 px-2 py-1 font-semibold text-white disabled:opacity-50"
        >
          Save
        </button>
        <button onClick={onCancel} className="ml-2 text-gray-500 hover:text-navy-900">
          Cancel
        </button>
      </td>
    </tr>
  );
}

function TabButton({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className={`-mb-px rounded-t-lg border-b-2 px-4 py-2 text-sm font-medium ${
        active ? "border-brand-600 text-brand-700" : "border-transparent text-gray-500 hover:text-navy-900"
      }`}
    >
      {label}
    </button>
  );
}

function Fact({ icon, label, value }: { icon: ReactNode; label: string; value: string | null }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-gray-400">{icon}</span>
      <div className="min-w-0">
        <div className="truncate text-sm font-medium text-navy-900" title={value ?? undefined}>
          {value || "—"}
        </div>
        <div className="text-xs text-gray-500">{label}</div>
      </div>
    </div>
  );
}

function Stat({ icon, tint, value, label }: { icon: ReactNode; tint: string; value: number; label: string }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3">
      <span className={`flex h-10 w-10 items-center justify-center rounded-xl ${tint}`}>{icon}</span>
      <div>
        <div className="text-2xl font-bold leading-none text-navy-900">{value}</div>
        <div className="mt-1 text-xs text-gray-500">{label}</div>
      </div>
    </div>
  );
}

function ActionCard({
  icon,
  tint,
  title,
  body,
  onClick,
  disabled,
}: {
  icon: ReactNode;
  tint: string;
  title: string;
  body: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`flex w-full items-start gap-3 rounded-xl border border-gray-200 px-4 py-3 text-left hover:border-brand-300 disabled:opacity-60 ${tint}`}
    >
      <span className="mt-0.5 text-brand-600">{icon}</span>
      <span className="min-w-0">
        <span className="block font-semibold text-navy-900">{title}</span>
        <span className="block text-xs text-gray-500">{body}</span>
      </span>
    </button>
  );
}

// --- icons --------------------------------------------------------------------

function Svg({ children }: { children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 shrink-0">
      {children}
    </svg>
  );
}
const IconDoc = () => <Svg><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v4h4M9 12h6M9 16h6" /></Svg>;
const IconCheck = () => <Svg><circle cx="12" cy="12" r="9" /><path d="m8 12 3 3 5-6" /></Svg>;
const IconClock = () => <Svg><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></Svg>;
const IconCross = () => <Svg><circle cx="12" cy="12" r="9" /><path d="m9 9 6 6M15 9l-6 6" /></Svg>;
const IconDownload = () => <Svg><path d="M12 4v11M7 10l5 5 5-5M5 20h14" /></Svg>;
const IconOpen = () => <Svg><path d="M14 4h6v6M20 4l-8 8M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></Svg>;
const IconPencil = () => <Svg><path d="m4 20 4-1L19 8l-3-3L5 16z" /></Svg>;
const IconTrash = () => <Svg><path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" /></Svg>;
const IconFolder = () => <Svg><path d="M4 7a1 1 0 0 1 1-1h4l2 2h8a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z" /></Svg>;
const IconBolt = () => <Svg><path d="M13 2 4 14h7l-1 8 9-12h-7z" /></Svg>;
const IconPlus = () => <Svg><path d="M12 5v14M5 12h14" /></Svg>;
const IconScan = () => <Svg><path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M4 12h16" /></Svg>;
const IconPin = () => <Svg><path d="M12 21s-6-5.5-6-11a6 6 0 1 1 12 0c0 5.5-6 11-6 11z" /><circle cx="12" cy="10" r="2" /></Svg>;
const IconBuilding = () => <Svg><path d="M4 21V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v16M15 10h4a1 1 0 0 1 1 1v10M8 8h3M8 12h3M8 16h3" /></Svg>;
const IconUsers = () => <Svg><circle cx="9" cy="8" r="3" /><path d="M3 20a6 6 0 0 1 12 0M16 6a3 3 0 0 1 0 6M18 20a5 5 0 0 0-3-4.6" /></Svg>;
const IconBriefcase = () => <Svg><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M9 7V5h6v2M3 12h18" /></Svg>;
const IconGear = () => <Svg><circle cx="12" cy="12" r="3" /><path d="M12 3v2M12 19v2M5 5l1.5 1.5M17.5 17.5 19 19M3 12h2M19 12h2M5 19l1.5-1.5M17.5 6.5 19 5" /></Svg>;
