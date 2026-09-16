import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useAuth } from "../context/AuthContext";
import { ApiError, api, apiUrl } from "../lib/api";
import {
  PROJECT_EDITOR_ROLES,
  type BatteryCalculation,
  type BatteryDesign,
  type BatteryLine,
  type BatteryPanel,
  type BatterySet,
  type ExtraComponent,
  type PanelSettings,
} from "../lib/types";
import { SyncDocumentsCard } from "../components/SyncDocumentsCard";
import { useProject } from "./ProjectWorkspace";

type SizingField = "standby_hours" | "alarm_minutes" | "spare_factor" | "panel_voltage";

const SETTINGS: { field: SizingField; label: string; unit?: string; help: string; step: string }[] = [
  { field: "standby_hours", label: "Standby duration", unit: "h", help: "Duration the system must operate in standby mode.", step: "1" },
  { field: "alarm_minutes", label: "Alarm duration", unit: "min", help: "Duration the system must operate in alarm mode.", step: "1" },
  { field: "spare_factor", label: "Design factor", help: "Additional capacity factor (e.g. ageing, temperature).", step: "0.05" },
  { field: "panel_voltage", label: "System voltage", unit: "V", help: "Nominal system voltage (DC).", step: "1" },
];

function fmt(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function int(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

/** The lines the page lists: parts that draw current, and parts whose
 * current is not known yet (they need input). Mechanical parts, batteries
 * and zero-load parts are left out. */
function consuming(panel: BatteryPanel): BatteryLine[] {
  return panel.lines.filter(
    (l) => l.kind === "load" && (l.missing_current || (l.standby_ma ?? 0) > 0 || (l.alarm_ma ?? 0) > 0)
  );
}

function needsInput(panel: BatteryPanel): boolean {
  return panel.lower_bound || panel.status === "incomplete";
}

function tone(panel: BatteryPanel): { dot: string; chip: string; label: string } {
  if (needsInput(panel)) return { dot: "bg-orange-500", chip: "bg-orange-50 text-orange-700", label: "Needs input" };
  if (panel.status === "no_selection") return { dot: "bg-gray-400", chip: "bg-gray-100 text-gray-600", label: "No battery selected" };
  // Calculated, but the battery the load needs is larger than the one quoted
  // -- the panel heading has to carry that, or it is only visible to someone
  // who opens the panel.
  if (panel.quoted_short) return { dot: "bg-red-500", chip: "bg-red-50 text-red-700", label: "BOQ battery too small" };
  return { dot: "bg-green-500", chip: "bg-green-50 text-green-700", label: "Calculated" };
}

function bankAh(sets: BatterySet[]): number {
  return sets.reduce((sum, s) => sum + s.strings * s.capacity_ah, 0);
}

function describeUnits(sets: BatterySet[]): string {
  return sets.map((s) => `${int(s.units)} × ${int(s.voltage)} V, ${int(s.capacity_ah)} Ah`).join(" + ");
}

/** The selection names its batteries: "4 × ROCKET ES65-12 (12 V, 65 Ah)". */
function describeSelection(sets: BatterySet[]): string {
  return sets
    .map((s) => `${int(s.units)} × ${s.brand ? `${s.brand} ` : ""}${s.part_no} (${int(s.voltage)} V, ${int(s.capacity_ah)} Ah)`)
    .join(" + ");
}

function strings(sets: BatterySet[]): number {
  return sets.reduce((n, s) => n + s.strings, 0);
}

function batteryHref(battery: BatterySet): string {
  const query = new URLSearchParams({ library: battery.datasheet_library ?? "", path: battery.datasheet_path ?? "" });
  return apiUrl(`/design-rules/datasheets/file?${query}`);
}

export function ProjectBatteryPage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);

  const [data, setData] = useState<BatteryCalculation | null>(null);
  const [draft, setDraft] = useState<BatteryDesign>({ panels: {} });
  const [dirty, setDirty] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "filling" | "saving" | "exporting">(null);
  const filled = useRef(false);

  const fetchCalculation = useCallback(
    () => api.get<BatteryCalculation>(`/projects/${project.id}/design/battery`),
    [project.id]
  );

  /** Take a fresh calculation; keep the engineer's unsaved panel edits. */
  const accept = useCallback((result: BatteryCalculation, keepDraft: boolean) => {
    setData(result);
    if (!keepDraft) {
      setDraft(result.design);
      setDirty(false);
    }
  }, []);

  const fillThenLoad = useCallback(
    async (keepDraft: boolean) => {
      setBusy("filling");
      try {
        await api.post(`/projects/${project.id}/design/battery/fill-currents`);
        accept(await fetchCalculation(), keepDraft);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Reading the datasheets failed");
      } finally {
        setBusy(null);
      }
    },
    [project.id, fetchCalculation, accept]
  );

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    filled.current = false;
    fetchCalculation()
      .then((result) => {
        if (cancelled) return;
        accept(result, false);
        // Fill what the datasheets can give, once per visit.
        if (canEdit && !filled.current && result.panels.some((p) => p.missing_parts.length > 0)) {
          filled.current = true;
          fillThenLoad(false);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load the battery calculations");
      });
    return () => {
      cancelled = true;
    };
  }, [fetchCalculation, accept, fillThenLoad, canEdit]);

  async function save(): Promise<boolean> {
    setBusy("saving");
    setError(null);
    try {
      // Saved against the version the page loaded: a save someone else made
      // in between is refused rather than overwritten.
      accept(await api.put<BatteryCalculation>(`/projects/${project.id}/design/battery`, draft, data?.design_version), false);
      return true;
    } catch (err) {
      setError(
        err instanceof ApiError && err.isStaleWrite
          ? `${err.message} Your unsaved panel edits are still on this page.`
          : err instanceof ApiError
            ? err.message
            : "Save failed"
      );
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function recalculate() {
    if (dirty && !(await save())) return;
    await fillThenLoad(false);
  }

  // PDF is the sheet that goes to a consultant, laid out to the company's
  // template; the workbook stays for working in Excel.
  async function exportCalculation(format: "pdf" | "xlsx", panelKey?: string) {
    setBusy("exporting");
    try {
      const query = panelKey ? `?${new URLSearchParams({ panel: panelKey })}` : "";
      await api.download(
        `/projects/${project.id}/design/battery/export.${format}${query}`,
        `EP-${project.ep_number} Battery Calculation.${format}`,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Export failed");
    } finally {
      setBusy(null);
    }
  }

  function editPanel(key: string, patch: Partial<PanelSettings>) {
    setDraft((d) => {
      const current: PanelSettings = d.panels[key] ?? { extra_components: [] };
      return { panels: { ...d.panels, [key]: { ...current, ...patch } } };
    });
    setDirty(true);
  }

  const panels = data?.panels ?? [];
  const panel = panels.find((p) => p.key === selected) ?? panels[0];
  const calculated = panels.filter((p) => !needsInput(p)).length;

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="text-3xl font-bold text-navy-900">Battery Calculations</h1>
          <p className="mt-1 text-sm text-gray-500">Select a panel to view its complete battery calculation.</p>
        </div>
      </div>

      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

      {/* Opening this page computes nothing the platform already holds: each
          panel comes from its saved calculation while its inputs stand. The
          folder is read only by "Sync documents". */}
      <div className="mt-4">
        <SyncDocumentsCard
          projectId={project.id}
          canEdit={canEdit}
          compact
          onSynced={() => {
            void fetchCalculation().then((calc) => accept(calc, true)).catch(() => undefined);
          }}
        />
      </div>
      {panels.some((p) => p.stale) && (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {panels.filter((p) => p.stale).map((p) => (
            <div key={p.key}>
              <span className="font-semibold">{p.name || p.heading}</span>: the previous calculation is shown because recalculating it failed ({p.error}). It is recalculated on the next open once the cause is fixed.
            </div>
          ))}
        </div>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-6 rounded-xl border border-gray-200 bg-white px-5 py-4">
        <Stat icon={<IconCalc />} tint="bg-blue-50 text-blue-600" value={panels.length} label="Panels" />
        <div className="hidden h-10 w-px bg-gray-200 sm:block" />
        <Stat icon={<IconCheck />} tint="bg-green-50 text-green-600" value={calculated} label="Calculated" />
        <div className="hidden h-10 w-px bg-gray-200 sm:block" />
        <Stat icon={<IconAlert />} tint="bg-orange-50 text-orange-500" value={panels.length - calculated} label="Need input" />
        <div className="ml-auto flex items-center gap-3">
          <button
            onClick={() => exportCalculation("pdf")}
            disabled={!panels.length || busy !== null || dirty}
            title={dirty ? "Save first -- the export is of the saved calculation" : undefined}
            className="flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-50"
          >
            <IconDownload /> Export all (PDF)
          </button>
          <button
            onClick={() => exportCalculation("xlsx")}
            disabled={!panels.length || busy !== null || dirty}
            title={dirty ? "Save first -- the export is of the saved calculation" : undefined}
            className="flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-50"
          >
            <IconDownload /> Workbook
          </button>
          {canEdit && (
            <button
              onClick={save}
              disabled={!dirty || busy !== null}
              className="flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
            >
              <IconSave /> {busy === "saving" ? "Saving..." : "Save changes"}
            </button>
          )}
        </div>
      </div>

      {busy === "filling" && (
        <div className="mt-4 rounded-lg bg-blue-50 px-3 py-2 text-sm text-blue-800">Reading module currents from the datasheets...</div>
      )}
      {dirty && (
        <div className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
          Unsaved changes. The figures update when you save or recalculate.
        </div>
      )}
      {data && !data.complete && (data.incomplete_reasons?.length ?? 0) > 0 && (
        <div role="status" className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div className="font-semibold">This calculation is not complete</div>
          <ul className="mt-1 list-disc pl-5">
            {data.incomplete_reasons!.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}
      {data && (data.needs_confirmation?.length ?? 0) > 0 && (
        <NoLoadConfirmations
          items={data.needs_confirmation!}
          canEdit={canEdit}
          onDecide={async (partNo, confirm) => {
            setError(null);
            try {
              accept(
                await api.post<BatteryCalculation>(`/projects/${project.id}/design/battery/confirm-no-load`, {
                  part_no: partNo,
                  confirm,
                }),
                true
              );
            } catch (err) {
              setError(err instanceof ApiError ? err.message : "Could not record the decision");
            }
          }}
        />
      )}

      {!data ? (
        !error && <div className="mt-6 rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500">Loading...</div>
      ) : panels.length === 0 ? (
        <div className="mt-6 rounded-xl border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-400">
          No BOQ group is headed as a fire alarm panel.
        </div>
      ) : (
        <>
          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {panels.map((p) => {
              const active = p.key === panel?.key;
              const t = tone(p);
              const settings = draft.panels[p.key];
              return (
                <button
                  key={p.key}
                  onClick={() => setSelected(p.key)}
                  className={`relative rounded-xl border px-4 py-3 text-left transition ${
                    active ? "border-brand-600 bg-brand-600 text-white shadow-md" : "border-gray-200 bg-white hover:border-brand-300"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-bold">{settings?.name || p.name}</span>
                    <span className={`h-2.5 w-2.5 rounded-full ${t.dot} ${active ? "ring-2 ring-white" : ""}`} title={t.label} />
                  </div>
                  <div className={`mt-1 line-clamp-1 text-xs ${active ? "text-blue-100" : "text-gray-500"}`}>{p.heading}</div>
                  <div className={`line-clamp-1 text-xs ${active ? "text-blue-100" : "text-gray-500"}`}>
                    {settings?.location || p.location || "Location not set"}
                  </div>
                  {active && <span className="absolute -bottom-2 left-1/2 h-4 w-4 -translate-x-1/2 rotate-45 bg-brand-600" />}
                </button>
              );
            })}
          </div>

          {panel && (
            <PanelDetail
              key={panel.key}
              panel={panel}
              data={data}
              settings={draft.panels[panel.key]}
              canEdit={canEdit}
              busy={busy !== null}
              dirty={dirty}
              onEdit={(patch) => editPanel(panel.key, patch)}
              onCatalogueSaved={async () => accept(await fetchCalculation(), true)}
              onExport={(format) => exportCalculation(format, panel.key)}
              onRecalculate={recalculate}
            />
          )}

          <details className="mt-6 rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm">
            <summary className="cursor-pointer font-medium text-gray-600">Battery catalogue and BOQ groups</summary>
            <BatteryCatalogue data={data} canEdit={canEdit} onSaved={async () => accept(await fetchCalculation(), true)} />
            <div className="mt-4 text-xs text-gray-500">
              {data.groups.map((g, i) => (
                <div key={i}>
                  {g.system_code ?? "—"} · {g.heading ?? "no heading"} ({g.lines} lines):{" "}
                  {g.treatment === "panel"
                    ? "calculated"
                    : g.treatment === "aps"
                      ? "calculated once (APS cabinet)"
                      : g.treatment === "bps"
                        ? "calculated once (BPS cabinet)"
                        : g.treatment === "repeater"
                          ? "not calculated (repeater panel: powered by the panel it repeats)"
                          : g.treatment === "ungrouped"
                            ? "not calculated (field devices)"
                            : "not calculated (not a panel heading)"}
                </div>
              ))}
            </div>
          </details>
        </>
      )}
    </div>
  );
}

function PanelDetail({
  panel,
  data,
  settings,
  canEdit,
  busy,
  dirty,
  onEdit,
  onCatalogueSaved,
  onExport,
  onRecalculate,
}: {
  panel: BatteryPanel;
  data: BatteryCalculation;
  settings: PanelSettings | undefined;
  canEdit: boolean;
  busy: boolean;
  dirty: boolean;
  onEdit: (patch: Partial<PanelSettings>) => void;
  onCatalogueSaved: () => void;
  onExport: (format: "pdf" | "xlsx") => void;
  onRecalculate: () => void;
}) {
  const t = tone(panel);
  const atLeast = panel.lower_bound ? "≥ " : "";
  const s = panel.settings as Record<SizingField, number>;
  const rule = (data.rule?.data ?? {}) as Record<SizingField, number>;
  const standbyA = panel.standby_ma / 1000;
  const alarmA = panel.alarm_ma / 1000;
  const alarmHours = s.alarm_minutes / 60;
  const brandName = ((data.selection_rule?.data.brand as string | undefined) ?? "").trim() || "catalogued";
  const lines = consuming(panel);
  const extras = settings?.extra_components ?? [];
  const firstDatasheet = lines.find((l) => l.datasheet_path);
  const [editing, setEditing] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState(false);
  const [editingName, setEditingName] = useState(false);

  const rowId = (line: BatteryLine, i: number) => (line.extra_index !== null ? `x${line.extra_index}` : `${line.part_no}:${i}`);

  return (
    <section className="mt-5 rounded-xl border border-gray-200 bg-white p-5">
      <div className="flex flex-wrap items-center gap-3">
        {editingName && canEdit ? (
          <input
            autoFocus
            defaultValue={settings?.name || panel.name}
            onBlur={(e) => {
              onEdit({ name: e.target.value.trim() || null });
              setEditingName(false);
            }}
            className="input w-40 py-1 text-lg font-bold"
          />
        ) : (
          <h2 className="text-2xl font-bold text-navy-900">
            {settings?.name || panel.name} · {panel.heading}
            {canEdit && (
              <button onClick={() => setEditingName(true)} className="ml-2 align-middle text-gray-400 hover:text-brand-600" title="Rename">
                <IconPencil />
              </button>
            )}
          </h2>
        )}
        <span className={`flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold ${t.chip}`}>
          {t.label === "Calculated" && <IconCheckSmall />} {t.label}
        </span>
        {panel.count > 1 && (
          <span className="text-xs text-gray-400">
            {panel.kind === "panel" ? `one of ${panel.count} identical panels` : `applies to each of the ${panel.count} cabinets quoted`}
          </span>
        )}
      </div>
      <div className="mt-1 flex items-center gap-1.5 text-sm text-gray-600">
        <IconPin />
        {canEdit ? (
          <input
            value={settings?.location ?? panel.location ?? ""}
            onChange={(e) => onEdit({ location: e.target.value || null })}
            placeholder="Set location (room · floor)"
            className="w-80 max-w-full rounded border border-transparent bg-transparent px-1 py-0.5 hover:border-gray-200 focus:border-brand-400 focus:outline-none"
          />
        ) : (
          <span>{panel.location || "Location not set"}</span>
        )}
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Headline icon={<IconBolt />} tint="bg-blue-50/70" iconTint="text-blue-600" label="Standby current" value={`${atLeast}${fmt(standbyA)} A`} />
        <Headline icon={<IconBell />} tint="bg-red-50/70" iconTint="text-red-500" label="Alarm current" value={`${atLeast}${fmt(alarmA)} A`} />
        <Headline icon={<IconCalc />} tint="bg-indigo-50/70" iconTint="text-indigo-600" label="Required capacity" value={`${atLeast}${fmt(panel.required_ah)} Ah`} />
        {/* The battery is selected from the load requirement, so when it
            comes out above what the BOQ quotes the tile says so rather than
            reading as a plain green pass -- that difference is a change the
            BOQ needs. */}
        <Headline
          icon={<IconBattery />}
          tint={panel.quoted_short ? "bg-red-50/70" : panel.selected ? "bg-green-50/70" : panel.lower_bound ? "bg-orange-50/70" : "bg-gray-50"}
          iconTint={panel.quoted_short ? "text-red-600" : panel.selected ? "text-green-600" : "text-gray-400"}
          label="Selected battery"
          value={panel.selected ? `${int(panel.selected_ah)} Ah` : "—"}
          hint={
            panel.selected
              ? `${describeSelection(panel.selected)}${panel.quoted_short ? ` · above the ${int(bankAh(panel.quoted))} Ah in the BOQ` : ""}`
              : undefined
          }
        />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="rounded-xl border border-gray-200 p-4 xl:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="flex items-center gap-2 font-semibold text-navy-900">
              <IconList /> Connected loads
            </h3>
            <div className="flex flex-wrap items-center gap-3 text-sm">
              {canEdit && (
                <button onClick={() => setAdding(true)} className="flex items-center gap-1 rounded-lg bg-blue-50 px-3 py-1.5 font-medium text-brand-700 hover:bg-blue-100">
                  + Add component
                </button>
              )}
              {firstDatasheet && (
                <a href={datasheetHref(firstDatasheet)} target="_blank" rel="noreferrer" className="flex items-center gap-1 font-medium text-brand-600 hover:underline">
                  <IconFile /> View datasheet
                </a>
              )}
              {canEdit && (
                <button
                  onClick={() => setEditing(editing.size ? new Set() : new Set(lines.map(rowId)))}
                  className="flex items-center gap-1 font-medium text-brand-600 hover:underline"
                >
                  <IconPencil /> {editing.size ? "Done" : "Edit load"}
                </button>
              )}
            </div>
          </div>

          <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="bg-gray-50 text-left text-xs text-gray-600">
                <tr>
                  <th className="px-3 py-2.5 font-medium">Component</th>
                  <th className="px-3 py-2.5 text-center font-medium">Qty</th>
                  <th className="px-3 py-2.5 text-center font-medium">Standby (mA/unit)</th>
                  <th className="px-3 py-2.5 text-center font-medium">Alarm (mA/unit)</th>
                  <th className="px-3 py-2.5 font-medium">Source</th>
                  <th className="px-3 py-2.5 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {lines.map((line, i) => {
                  const id = rowId(line, i);
                  return editing.has(id) && canEdit ? (
                    <EditRow
                      key={id}
                      line={line}
                      extra={line.extra_index !== null ? extras[line.extra_index] : undefined}
                      onDone={() => setEditing((e) => new Set([...e].filter((x) => x !== id)))}
                      onSaveExtra={(component) => {
                        onEdit({ extra_components: extras.map((c, j) => (j === line.extra_index ? component : c)) });
                      }}
                      onCatalogueSaved={onCatalogueSaved}
                    />
                  ) : (
                    <tr key={id} className={line.missing_current ? "bg-orange-50/50" : undefined}>
                      <td className="px-3 py-2.5">
                        <div className="font-medium text-navy-900">{line.part_no || line.description}</div>
                        {line.part_no && <div className="max-w-xs truncate text-xs text-gray-500" title={line.description}>{line.description}</div>}
                      </td>
                      <td className="px-3 py-2.5 text-center tabular-nums">{int(line.quantity)}</td>
                      <td className="px-3 py-2.5 text-center tabular-nums">{line.missing_current ? <span className="text-orange-600">—</span> : int(line.standby_ma)}</td>
                      <td className="px-3 py-2.5 text-center tabular-nums">{line.missing_current ? <span className="text-orange-600">—</span> : int(line.alarm_ma)}</td>
                      <td className="max-w-[220px] px-3 py-2.5">
                        <SourceCell line={line} />
                      </td>
                      <td className="whitespace-nowrap px-3 py-2.5 text-xs">
                        {line.datasheet_path && (
                          <a href={datasheetHref(line)} target="_blank" rel="noreferrer" className="mr-3 inline-flex items-center gap-1 text-brand-600 hover:underline">
                            <IconFile /> View datasheet
                          </a>
                        )}
                        {canEdit && (
                          <button onClick={() => setEditing((e) => new Set(e).add(id))} className="inline-flex items-center gap-1 text-gray-600 hover:text-brand-600">
                            <IconPencil /> {line.missing_current ? "Enter" : "Edit"}
                          </button>
                        )}
                        {canEdit && line.extra_index !== null && (
                          <button
                            onClick={() => onEdit({ extra_components: extras.filter((_, j) => j !== line.extra_index) })}
                            className="ml-3 text-gray-400 hover:text-red-600"
                          >
                            Remove
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {adding && (
                  <AddRow
                    onAdd={(component) => {
                      onEdit({ extra_components: [...extras, component] });
                      setAdding(false);
                    }}
                    onCancel={() => setAdding(false)}
                  />
                )}
                {lines.length === 0 && !adding && (
                  <tr>
                    <td colSpan={6} className="px-3 py-6 text-center text-gray-400">No component draws current.</td>
                  </tr>
                )}
              </tbody>
              <tfoot className="border-t border-gray-200 bg-gray-50 font-semibold text-navy-900">
                <tr>
                  <td className="px-3 py-2.5">Total{panel.lower_bound && <span className="ml-1 font-normal text-orange-600">(at least)</span>}</td>
                  <td className="px-3 py-2.5 text-center text-gray-400">—</td>
                  <td className="px-3 py-2.5 text-center tabular-nums" title="Sum of quantity × standby per unit">{int(panel.standby_ma)} mA</td>
                  <td className="px-3 py-2.5 text-center tabular-nums" title="Sum of quantity × alarm per unit">{int(panel.alarm_ma)} mA</td>
                  <td className="px-3 py-2.5 text-gray-400">—</td>
                  <td className="px-3 py-2.5 text-gray-400">—</td>
                </tr>
              </tfoot>
            </table>
          </div>
          {panel.notes.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-xs text-gray-500">
              {panel.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded-xl border border-gray-200 p-4">
          <h3 className="flex items-center gap-2 font-semibold text-navy-900">
            <IconGear /> Calculation settings
          </h3>
          <div className="mt-3 space-y-3">
            {SETTINGS.map(({ field, label, unit, help, step }) => {
              const override = settings?.[field];
              const value = override ?? s[field];
              const differs = override !== null && override !== undefined && override !== rule[field];
              return (
                <div key={field} className="grid grid-cols-[1fr_minmax(0,1.3fr)] items-start gap-3">
                  <label className="pt-2 text-sm text-gray-700">{label}</label>
                  <div>
                    <div className="flex">
                      <input
                        type="number"
                        step={step}
                        min={0}
                        disabled={!canEdit}
                        value={value ?? ""}
                        onChange={(e) => onEdit({ [field]: e.target.value === "" ? null : Number(e.target.value) } as Partial<PanelSettings>)}
                        className={`input py-1.5 disabled:bg-gray-50 ${unit ? "rounded-r-none" : ""}`}
                      />
                      {unit && <span className="flex items-center rounded-r-lg border border-l-0 border-gray-300 bg-gray-50 px-3 text-sm text-gray-500">{unit}</span>}
                    </div>
                    <div className="mt-0.5 text-[11px] text-gray-400">
                      {help}
                      {differs && canEdit && (
                        <>
                          {" "}
                          <span className="text-amber-700">Rule: {int(rule[field])}{unit ? ` ${unit}` : ""}.</span>{" "}
                          <button onClick={() => onEdit({ [field]: null } as Partial<PanelSettings>)} className="text-brand-600 hover:underline">
                            Reset
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-5">
        <div className="rounded-xl border border-gray-200 p-4 xl:col-span-3">
          <h3 className="flex items-center gap-2 font-semibold text-navy-900">
            <IconCalc /> Battery calculation
          </h3>
          <div className="mt-3 rounded-lg bg-gray-50 px-4 py-3 text-sm">
            <CalcRow label="Standby:" formula={`${fmt(standbyA)} A × ${int(s.standby_hours)} h =`} result={`${fmt(standbyA * s.standby_hours)} Ah`} />
            <CalcRow label="Alarm:" formula={`${fmt(alarmA)} A × ${fmt(alarmHours)} h =`} result={`${fmt(alarmA * alarmHours)} Ah`} />
            <div className="my-2 border-t border-gray-200" />
            <CalcRow
              bold
              label="Required:"
              formula={`(${fmt(standbyA * s.standby_hours)} + ${fmt(alarmA * alarmHours)}) × ${fmt(s.spare_factor)} =`}
              result={`${atLeast}${fmt(panel.required_ah)} Ah`}
            />
          </div>
        </div>
        <div className="rounded-xl border border-gray-200 p-4 xl:col-span-2">
          <h3 className="flex items-center gap-2 font-semibold text-navy-900">
            <IconBattery /> Battery selection
          </h3>
          <div className="mt-3 rounded-lg bg-gray-50 px-4 py-3 text-sm">
            {panel.selected ? (
              <>
                <div className="font-semibold text-navy-900">
                  {describeSelection(panel.selected)} ·{" "}
                  {strings(panel.selected) > 1 ? `${strings(panel.selected)} strings in parallel` : "Series connection"}
                </div>
                <div className="mt-0.5 text-gray-600">
                  Battery bank: {int(s.panel_voltage)} V, {int(panel.selected_ah)} Ah
                  {panel.selected.some((b) => b.datasheet_path) && (
                    <>
                      {" · "}
                      {panel.selected
                        .filter((b) => b.datasheet_path)
                        .map((b) => (
                          <a key={b.part_no} href={batteryHref(b)} target="_blank" rel="noreferrer" className="text-brand-600 hover:underline">
                            {b.part_no} datasheet
                          </a>
                        ))}
                    </>
                  )}
                </div>
              </>
            ) : (
              <div className="text-gray-600">
                {panel.lower_bound
                  ? "A battery is selected once every current is known."
                  : `No ${brandName} battery is on file to select from. Add its datasheet to the library, or a battery unit below.`}
              </div>
            )}
            {/* A panel whose selection is above the quoted battery is not a
                pass: the capacity is right but the BOQ is not, and saying
                "sufficient" there would hide the one thing to act on. */}
            <div
              className={`mt-3 flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${
                panel.lower_bound
                  ? "bg-orange-50 text-orange-700"
                  : panel.quoted_short
                    ? "bg-red-50 font-semibold text-red-700 ring-1 ring-red-200"
                    : panel.selected
                      ? "bg-green-50 text-green-700"
                      : "bg-gray-100 text-gray-600"
              }`}
            >
              {panel.selected && !panel.lower_bound && !panel.quoted_short ? <IconCheck /> : <IconAlert />}
              {panel.lower_bound
                ? `Needs input · ${panel.missing_parts.length} part${panel.missing_parts.length === 1 ? "" : "s"} without a current`
                : panel.quoted_short
                  ? `BOQ battery too small · the load needs ${fmt(panel.required_ah)} Ah, the BOQ quotes ${int(bankAh(panel.quoted))} Ah`
                  : panel.selected
                    ? "Capacity sufficient · charger compatibility pending"
                    : `Needs a battery of at least ${fmt(panel.required_ah)} Ah`}
            </div>
            <div className={`mt-2 text-xs ${panel.quoted_short ? "text-red-700" : "text-gray-600"}`}>
              BOQ quotes {panel.quoted.length ? `${describeUnits(panel.quoted)} (${int(bankAh(panel.quoted))} Ah)` : "no battery"} for this panel
              {panel.quoted_short && <span className="font-semibold"> · update the BOQ to {panel.selected ? describeSelection(panel.selected) : `at least ${fmt(panel.required_ah)} Ah`}</span>}
            </div>
          </div>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap justify-end gap-3">
        <button
          onClick={() => onExport("pdf")}
          disabled={busy || dirty}
          title={dirty ? "Save first -- the export is of the saved calculation" : undefined}
          className="flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-5 py-2.5 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-50"
        >
          <IconDownload /> Export this panel (PDF)
        </button>
        <button
          onClick={() => onExport("xlsx")}
          disabled={busy || dirty}
          title={dirty ? "Save first -- the export is of the saved calculation" : undefined}
          className="flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-5 py-2.5 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-50"
        >
          <IconDownload /> Workbook
        </button>
        {canEdit && (
          <button
            onClick={onRecalculate}
            disabled={busy}
            className="flex items-center gap-2 rounded-lg bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
          >
            <IconCalc /> Recalculate
          </button>
        )}
      </div>
    </section>
  );
}

function datasheetHref(line: BatteryLine): string {
  const query = new URLSearchParams({ library: line.datasheet_library ?? "", path: line.datasheet_path ?? "" });
  return apiUrl(`/design-rules/datasheets/file?${query}#page=${line.datasheet_page ?? 1}`);
}

function SourceCell({ line }: { line: BatteryLine }) {
  if (line.missing_current) return <span className="text-xs font-medium text-orange-600">Needs input · not in the datasheets</span>;
  const file = line.datasheet_path?.split("/").pop();
  return (
    <span className="flex items-center gap-1.5 text-gray-700" title={line.current_source ?? undefined}>
      <IconFile />
      <span className="truncate">{file ?? (line.extra_index !== null ? "Added manually" : "Entered manually")}</span>
    </span>
  );
}

function EditRow({
  line,
  extra,
  onDone,
  onSaveExtra,
  onCatalogueSaved,
}: {
  line: BatteryLine;
  extra: ExtraComponent | undefined;
  onDone: () => void;
  onSaveExtra: (component: ExtraComponent) => void;
  onCatalogueSaved: () => void;
}) {
  const [qty, setQty] = useState(String(line.quantity ?? 1));
  const [standby, setStandby] = useState(line.standby_ma === null ? "" : String(line.standby_ma));
  const [alarm, setAlarm] = useState(line.alarm_ma === null ? "" : String(line.alarm_ma));
  const [source, setSource] = useState(extra?.source ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = standby !== "" && alarm !== "" && source.trim().length >= 3;

  async function save() {
    if (extra) {
      onSaveExtra({ ...extra, quantity: Number(qty) || extra.quantity, standby_ma: Number(standby), alarm_ma: Number(alarm), source: source.trim() });
      onDone();
      return;
    }
    // A BOQ part's current is the catalogue's: correcting it is a new
    // version, used by every project with the part.
    setSaving(true);
    setError(null);
    try {
      await api.post("/design-rules/part-currents", {
        part_no: line.part_no,
        description: line.description,
        standby_ma: Number(standby),
        alarm_ma: Number(alarm),
        source: source.trim(),
      });
      onDone();
      onCatalogueSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Save failed");
      setSaving(false);
    }
  }

  return (
    <tr className="bg-blue-50/40">
      <td className="px-3 py-2">
        <div className="font-medium text-navy-900">{line.part_no || line.description}</div>
        {error && <div className="text-xs text-red-700">{error}</div>}
      </td>
      <td className="px-3 py-2 text-center">
        {extra ? <input type="number" min={0} value={qty} onChange={(e) => setQty(e.target.value)} className="input w-16 py-1 text-center" /> : int(line.quantity)}
      </td>
      <td className="px-3 py-2 text-center">
        <input type="number" min={0} step="any" value={standby} onChange={(e) => setStandby(e.target.value)} className="input w-20 py-1 text-center" />
      </td>
      <td className="px-3 py-2 text-center">
        <input type="number" min={0} step="any" value={alarm} onChange={(e) => setAlarm(e.target.value)} className="input w-20 py-1 text-center" />
      </td>
      <td className="px-3 py-2">
        <input value={source} onChange={(e) => setSource(e.target.value)} placeholder="Source (datasheet, page)" className="input py-1 text-xs" />
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-xs">
        <button onClick={save} disabled={!ready || saving} className="rounded bg-brand-600 px-2 py-1 font-semibold text-white disabled:opacity-50">
          Save
        </button>
        <button onClick={onDone} className="ml-2 text-gray-500 hover:text-navy-900">
          Cancel
        </button>
      </td>
    </tr>
  );
}

function AddRow({ onAdd, onCancel }: { onAdd: (component: ExtraComponent) => void; onCancel: () => void }) {
  const [c, setC] = useState({ description: "", part_no: "", quantity: "1", standby_ma: "", alarm_ma: "", source: "" });
  const ready = c.description.trim() && Number(c.quantity) > 0 && c.standby_ma !== "" && c.alarm_ma !== "" && c.source.trim().length >= 3;
  return (
    <tr className="bg-blue-50/40">
      <td className="px-3 py-2">
        <input value={c.description} onChange={(e) => setC({ ...c, description: e.target.value })} placeholder="Component" className="input py-1" />
        <input value={c.part_no} onChange={(e) => setC({ ...c, part_no: e.target.value })} placeholder="Part no. (optional)" className="input mt-1 py-1 text-xs" />
      </td>
      <td className="px-3 py-2 text-center">
        <input type="number" min={0} value={c.quantity} onChange={(e) => setC({ ...c, quantity: e.target.value })} className="input w-16 py-1 text-center" />
      </td>
      <td className="px-3 py-2 text-center">
        <input type="number" min={0} step="any" value={c.standby_ma} onChange={(e) => setC({ ...c, standby_ma: e.target.value })} className="input w-20 py-1 text-center" />
      </td>
      <td className="px-3 py-2 text-center">
        <input type="number" min={0} step="any" value={c.alarm_ma} onChange={(e) => setC({ ...c, alarm_ma: e.target.value })} className="input w-20 py-1 text-center" />
      </td>
      <td className="px-3 py-2">
        <input value={c.source} onChange={(e) => setC({ ...c, source: e.target.value })} placeholder="Source (datasheet, page)" className="input py-1 text-xs" />
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-xs">
        <button
          disabled={!ready}
          onClick={() =>
            onAdd({
              description: c.description.trim(),
              part_no: c.part_no.trim() || null,
              quantity: Number(c.quantity),
              standby_ma: Number(c.standby_ma),
              alarm_ma: Number(c.alarm_ma),
              source: c.source.trim(),
            })
          }
          className="rounded bg-brand-600 px-2 py-1 font-semibold text-white disabled:opacity-50"
        >
          Add
        </button>
        <button onClick={onCancel} className="ml-2 text-gray-500 hover:text-navy-900">
          Cancel
        </button>
      </td>
    </tr>
  );
}

function BatteryCatalogue({ data, canEdit, onSaved }: { data: BatteryCalculation; canEdit: boolean; onSaved: () => void }) {
  const [adding, setAdding] = useState<{ part_no: string; capacity_ah: string; voltage: string } | null>(null);
  const [source, setSource] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (!adding) return;
    setError(null);
    try {
      await api.post("/design-rules/battery-units", {
        part_no: adding.part_no,
        capacity_ah: Number(adding.capacity_ah),
        voltage: Number(adding.voltage),
        // Batteries are selected by brand, so an entry without one is never chosen.
        brand: (data.selection_rule?.data.brand as string | undefined) ?? null,
        source: source.trim(),
      });
      setAdding(null);
      setSource("");
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Save failed");
    }
  }

  return (
    <div className="mt-3">
      <div className="text-xs text-gray-500">
        Batteries a panel's battery is selected from
        {data.selection_rule?.data.brand ? ` (${String(data.selection_rule.data.brand)})` : ""}, read from their datasheets in the
        library. Add a datasheet to the library and it appears here.
      </div>
      <ul className="mt-2 space-y-0.5 text-sm">
        {data.selectable.map((b) => (
          <li key={b.part_no}>
            <span className="font-medium">
              {b.brand ? `${b.brand} ` : ""}
              {b.part_no}
            </span>{" "}
            · {int(b.capacity_ah)} Ah, {int(b.voltage)} V
            {b.datasheet_path && (
              <>
                {" · "}
                <a href={batteryHref(b)} target="_blank" rel="noreferrer" className="text-xs text-brand-600 hover:underline">
                  {b.datasheet_path.split("/").pop()}
                </a>
              </>
            )}
          </li>
        ))}
        {data.selectable.length === 0 && <li className="text-gray-400">None on file yet.</li>}
      </ul>
      {canEdit &&
        (adding ? (
          <div className="mt-2 flex flex-wrap items-end gap-2 text-xs">
            <input value={adding.part_no} onChange={(e) => setAdding({ ...adding, part_no: e.target.value })} placeholder="Part" className="input w-28 py-1" />
            <input type="number" value={adding.capacity_ah} onChange={(e) => setAdding({ ...adding, capacity_ah: e.target.value })} placeholder="Ah" className="input w-20 py-1" />
            <input type="number" value={adding.voltage} onChange={(e) => setAdding({ ...adding, voltage: e.target.value })} placeholder="V" className="input w-16 py-1" />
            <input value={source} onChange={(e) => setSource(e.target.value)} placeholder="Source (datasheet)" className="input min-w-48 flex-1 py-1" />
            <button onClick={save} disabled={!adding.part_no || !Number(adding.capacity_ah) || !Number(adding.voltage) || source.trim().length < 3} className="rounded bg-brand-600 px-3 py-1.5 font-semibold text-white disabled:opacity-50">
              Save
            </button>
            <button onClick={() => setAdding(null)} className="text-gray-500">
              Cancel
            </button>
            {error && <span className="text-red-700">{error}</span>}
          </div>
        ) : (
          <div className="mt-2 flex flex-wrap gap-2 text-xs">
            {data.unlisted_batteries.map((b) => (
              <button key={b.part_no} onClick={() => setAdding({ part_no: b.part_no, capacity_ah: String(b.capacity_ah), voltage: String(b.voltage) })} className="rounded-full border border-gray-300 px-2 py-0.5 hover:bg-gray-100">
                + {b.part_no} ({int(b.capacity_ah)} Ah, quoted in this BOQ)
              </button>
            ))}
            <button onClick={() => setAdding({ part_no: "", capacity_ah: "", voltage: "12" })} className="rounded-full border border-gray-300 px-2 py-0.5 hover:bg-gray-100">
              + Add battery unit
            </button>
          </div>
        ))}
    </div>
  );
}

/** Parts the platform set to draw no current by itself (a mechanical
 * description, a part built into a module). An engineer confirms each, or
 * rejects it so the part needs a real figure. */
function NoLoadConfirmations({
  items,
  canEdit,
  onDecide,
}: {
  items: { part_no: string; description: string | null; reason: string | null }[];
  canEdit: boolean;
  onDecide: (partNo: string, confirm: boolean) => Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  return (
    <section aria-labelledby="no-load-title" className="mt-4 rounded-xl border border-amber-200 bg-white p-4">
      <h2 id="no-load-title" className="text-sm font-semibold text-navy-900">
        Confirm parts set to draw no current ({items.length})
      </h2>
      <p className="text-xs text-gray-500">
        These were set automatically. A wrong one hides a real load, so each needs an engineer's confirmation.
      </p>
      <ul className="mt-2 divide-y divide-gray-100">
        {items.map((item) => (
          <li key={item.part_no} className="flex flex-wrap items-center gap-3 py-2 text-sm">
            <div className="min-w-0 flex-1">
              <div className="font-medium text-navy-900">
                {item.part_no}
                {item.description ? ` — ${item.description}` : ""}
              </div>
              <div className="text-xs text-gray-500">{item.reason}</div>
            </div>
            {canEdit && (
              <div className="flex gap-2">
                <button
                  disabled={busy !== null}
                  onClick={async () => {
                    setBusy(item.part_no);
                    await onDecide(item.part_no, true);
                    setBusy(null);
                  }}
                  className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
                >
                  Confirm: no current
                </button>
                <button
                  disabled={busy !== null}
                  onClick={async () => {
                    setBusy(item.part_no);
                    await onDecide(item.part_no, false);
                    setBusy(null);
                  }}
                  className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-60"
                >
                  It draws current
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Stat({ icon, tint, value, label }: { icon: ReactNode; tint: string; value: number; label: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className={`flex h-11 w-11 items-center justify-center rounded-xl ${tint}`}>{icon}</span>
      <div>
        <div className="text-2xl font-bold leading-none text-navy-900">{value}</div>
        <div className="mt-1 text-sm text-gray-500">{label}</div>
      </div>
    </div>
  );
}

function Headline({
  icon,
  tint,
  iconTint,
  label,
  value,
  hint,
}: {
  icon: ReactNode;
  tint: string;
  iconTint: string;
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className={`flex items-center gap-4 rounded-xl px-5 py-4 ${tint}`} title={hint}>
      <span className={`${iconTint} [&>svg]:h-8 [&>svg]:w-8`}>{icon}</span>
      <div>
        <div className="text-sm text-gray-600">{label}</div>
        <div className="text-2xl font-bold tabular-nums text-navy-900">{value}</div>
      </div>
    </div>
  );
}

function CalcRow({ label, formula, result, bold }: { label: string; formula: string; result: string; bold?: boolean }) {
  return (
    <div className={`grid grid-cols-[6rem_1fr_auto] items-baseline gap-2 py-0.5 ${bold ? "font-bold text-navy-900" : "text-gray-800"}`}>
      <span className={bold ? "" : "font-medium"}>{label}</span>
      <span className="tabular-nums">{formula}</span>
      <span className="text-right text-base font-bold tabular-nums text-navy-900">{result}</span>
    </div>
  );
}

// --- icons (inline, stroke) ---------------------------------------------------

function Svg({ children }: { children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 shrink-0">
      {children}
    </svg>
  );
}
const IconCalc = () => <Svg><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M8 7h8M8 11h2M12 11h0M16 11h0M8 15h2M12 15h0M16 15v3M8 18h2" /></Svg>;
const IconCheck = () => <Svg><circle cx="12" cy="12" r="9" /><path d="m8 12 3 3 5-6" /></Svg>;
const IconCheckSmall = () => <Svg><path d="m6 12 4 4 8-9" /></Svg>;
const IconAlert = () => <Svg><circle cx="12" cy="12" r="9" /><path d="M12 7v6M12 16.5v.5" /></Svg>;
const IconDownload = () => <Svg><path d="M12 4v11M7 10l5 5 5-5M5 20h14" /></Svg>;
const IconSave = () => <Svg><path d="M5 4h11l3 3v13H5z" /><path d="M8 4v5h7V4M8 20v-6h8v6" /></Svg>;
const IconBolt = () => <Svg><path d="M13 2 4 14h7l-1 8 9-12h-7z" /></Svg>;
const IconBell = () => <Svg><path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4z" /><path d="M10 20a2 2 0 0 0 4 0" /></Svg>;
const IconBattery = () => <Svg><rect x="3" y="7" width="16" height="11" rx="2" /><path d="M7 4v3M15 4v3M21 11v3M7 12.5h3M8.5 11v3M13 12.5h3" /></Svg>;
const IconList = () => <Svg><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h5" /></Svg>;
const IconGear = () => <Svg><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" /></Svg>;
const IconFile = () => <Svg><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v4h4M9 12h6M9 16h6" /></Svg>;
const IconPencil = () => <Svg><path d="m4 20 4-1L19 8l-3-3L5 16z" /></Svg>;
const IconPin = () => <Svg><path d="M12 21s-6-5.5-6-11a6 6 0 1 1 12 0c0 5.5-6 11-6 11z" /><circle cx="12" cy="10" r="2" /></Svg>;
