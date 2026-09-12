import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ApiError, api } from "../lib/api";
import { matchesFilter, possibleDuplicates, quantityTotals } from "../lib/boq";
import {
  PROJECT_EDITOR_ROLES,
  type BoqEnsureResponse,
  type ProjectBoqItem,
  type ProjectBoqItemInput,
} from "../lib/types";
import { UnderMaintenance } from "../components/UnderMaintenance";
import { useProject } from "./ProjectWorkspace";

// Stands in for "no system" so a tab always has a key. Lines only land here
// if they predate the per-system tabs or were extracted from a sheet whose
// filename carried no system code.
const UNASSIGNED = " unassigned";

/** Where a BOQ's quantities come from. Reading them off the issued-for-
 * construction drawings is in the platform's design but not built yet. */
type SourceKey = "design" | "ifc";
const SOURCES: { key: SourceKey; label: string; soon?: boolean }[] = [
  { key: "design", label: "As per Design Sheet" },
  { key: "ifc", label: "As per IFC Drawings", soon: true },
];

const PAGE_SIZE = 25;

function inTab(row: ProjectBoqItemInput, tab: string): boolean {
  return tab === UNASSIGNED ? !row.system_code : row.system_code === tab;
}

function toInput(item: ProjectBoqItemInput): ProjectBoqItemInput {
  return {
    system_code: item.system_code ?? null,
    group_heading: item.group_heading ?? null,
    manufacturer: item.manufacturer ?? null,
    catalog_no: item.catalog_no ?? null,
    description: item.description,
    quantity: item.quantity ?? null,
    unit: item.unit ?? null,
    unit_price: item.unit_price ?? null,
    total_price: item.total_price ?? null,
    remarks: item.remarks ?? null,
  };
}

type TextColumn = Exclude<keyof ProjectBoqItemInput, "system_code">;

const COLUMNS: { key: TextColumn; label: string; width: string; align?: "right"; placeholder?: string }[] = [
  { key: "group_heading", label: "Group", width: "w-36" },
  { key: "manufacturer", label: "Manufacturer", width: "w-28" },
  { key: "catalog_no", label: "Model / Part No.", width: "w-52" },
  { key: "description", label: "Description", width: "min-w-96" },
  { key: "quantity", label: "Qty", width: "w-20", placeholder: "1 or Lot" },
  { key: "unit", label: "Unit", width: "w-20" },
  { key: "unit_price", label: "Unit Price", width: "w-24", align: "right" },
  { key: "total_price", label: "Total Price", width: "w-24", align: "right" },
  { key: "remarks", label: "Remarks", width: "w-40" },
];

export function ProjectBoqPage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);

  const [rows, setRows] = useState<ProjectBoqItemInput[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>(project.boq_extraction_warnings ?? []);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [extractNote, setExtractNote] = useState<string | null>(null);
  const [selectedTab, setSelectedTab] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [duplicatesOnly, setDuplicatesOnly] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [source, setSource] = useState<SourceKey>("design");
  const [page, setPage] = useState(1);

  useEffect(() => {
    let cancelled = false;

    // Opening the BOQ is what triggers the one-off read of the Design Sheets;
    // the server decides whether this is that first open. Viewers cannot
    // trigger it -- it writes -- so they just read what is stored.
    const load = canEdit
      ? api.post<BoqEnsureResponse>(`/projects/${project.id}/boq/ensure`)
      : api
          .get<ProjectBoqItem[]>(`/projects/${project.id}/boq`)
          .then((items) => ({ items, extracted: false, warnings: [] }) as BoqEnsureResponse);

    load
      .then((result) => {
        if (cancelled) return;
        setRows(result.items.map(toInput));
        if (result.warnings.length > 0) setWarnings(result.warnings);
        if (result.extracted && result.items.length > 0) {
          setExtractNote(
            `${result.items.length} line${result.items.length > 1 ? "s were" : " was"} read from the Design Sheets. Check them over and edit anything the scan got wrong.`
          );
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load the BOQ");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [project.id, canEdit]);

  // One tab per system the project has a Design Sheet for -- FAS and EML on a
  // project with those two sheets. Systems only present on existing BOQ lines
  // are included too: a line whose system has no tab would be invisible but
  // still saved, which is worse than an extra tab.
  const tabs = useMemo(() => {
    const fromSheets = project.design_sheets
      .map((sheet) => sheet.system_code)
      .filter((code): code is string => Boolean(code));
    const fromRows = rows
      .map((row) => row.system_code)
      .filter((code): code is string => Boolean(code));
    const named = Array.from(new Set([...fromSheets, ...fromRows]));
    return rows.some((row) => !row.system_code) ? [...named, UNASSIGNED] : named;
  }, [project.design_sheets, rows]);

  const activeTab = selectedTab ?? tabs[0] ?? UNASSIGNED;

  // Recomputed on every edit, so a fix clears its flag before the save.
  const duplicates = useMemo(() => possibleDuplicates(rows), [rows]);

  // Kept alongside its index in `rows`, because edits and removals address the
  // full list while the table only renders one tab of it.
  const tabRows = useMemo(
    () => rows.map((row, index) => ({ row, index })).filter(({ row }) => inTab(row, activeTab)),
    [rows, activeTab]
  );
  const visibleRows = tabRows.filter(
    ({ row, index }) => matchesFilter(row, filter) && (!duplicatesOnly || duplicates.has(index))
  );
  const filtering = filter.trim() !== "" || duplicatesOnly;
  // Long BOQs are paged, but an edit must never move a line out from under
  // the engineer, so the page only changes when they change it.
  const pageCount = Math.max(1, Math.ceil(visibleRows.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const pagedRows = visibleRows.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const totals = quantityTotals(visibleRows.map(({ row }) => row));
  const allTotals = quantityTotals(rows);
  const tabDuplicates = tabRows.filter(({ index }) => duplicates.has(index)).length;

  function touched() {
    setDirty(true);
    setSavedAt(null);
  }

  function update(index: number, patch: Partial<ProjectBoqItemInput>) {
    setRows((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));
    touched();
  }

  function addRow() {
    setRows((prev) => [
      ...prev,
      toInput({ system_code: activeTab === UNASSIGNED ? null : activeTab, description: "" }),
    ]);
    // A new, blank line would be hidden by an active filter, or by the
    // drawings tab being the one on show.
    setFilter("");
    setDuplicatesOnly(false);
    setSource("design");
    setPage(1);
    touched();
  }

  function removeRow(index: number) {
    setRows((prev) => prev.filter((_, i) => i !== index));
    touched();
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      // Every tab is sent, not just the visible one -- this replaces the
      // project's whole BOQ.
      const payload = rows
        .filter((row) => row.description.trim() !== "")
        .map((row) => ({ ...row, description: row.description.trim() }));
      const saved = await api.put<ProjectBoqItem[]>(`/projects/${project.id}/boq`, payload);
      setRows(saved.map(toInput));
      setDirty(false);
      setSavedAt(new Date().toLocaleTimeString());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save the BOQ");
    } finally {
      setSaving(false);
    }
  }

  async function exportXlsx() {
    setExporting(true);
    setError(null);
    try {
      await api.download(`/projects/${project.id}/boq/export.xlsx`, `EP-${project.ep_number} BOQ.xlsx`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to export the BOQ");
    } finally {
      setExporting(false);
    }
  }

  const blankRows = rows.filter((row) => row.description.trim() === "").length;

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs text-gray-400">
            EP-{project.ep_number}
            {project.project_name ? ` — ${project.project_name}` : ""} / BOQ
          </div>
          <h1 className="text-3xl font-bold text-navy-900">Bill of Quantities (BOQ)</h1>
          <p className="mt-1 text-sm text-gray-500">
            Line items per system. The Design Sheets under Documents are the source.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs font-medium text-gray-500">
            Revision
            <Link to="revisions" className="ml-2 text-brand-600 hover:underline">
              history
            </Link>
            <select
              value="current"
              onChange={() => undefined}
              disabled
              title="Issued revisions are under Revisions; this page is always the current BOQ."
              className="input mt-1 w-44 py-2 disabled:bg-gray-50"
            >
              <option value="current">Current (unissued)</option>
            </select>
          </label>
          <button
            onClick={exportXlsx}
            // The export is of the saved BOQ; unsaved edits would be missing
            // from it without any sign that they were.
            disabled={exporting || dirty || loading}
            title={dirty ? "Save first -- the export is of the saved BOQ" : undefined}
            className="flex items-center gap-2 rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-50"
          >
            {exporting ? "Exporting..." : "Export BOQ"}
          </button>
          {canEdit && (
            <>
              <button
                onClick={addRow}
                className="flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700"
              >
                + Add Item
              </button>
              <button
                onClick={save}
                disabled={saving || !dirty}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-50"
              >
                {saving ? "Saving..." : dirty ? "Save changes" : savedAt ? `Saved ${savedAt}` : "Saved"}
              </button>
            </>
          )}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {SOURCES.map((option) => (
          <button
            key={option.key}
            onClick={() => setSource(option.key)}
            className={`flex items-center gap-2 rounded-xl border px-5 py-3 text-sm font-semibold ${
              source === option.key
                ? "border-brand-600 bg-brand-600 text-white"
                : "border-gray-200 bg-white text-navy-900 hover:border-brand-300"
            }`}
          >
            {option.label}
            {option.soon && (
              <span
                className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
                  source === option.key ? "bg-white/20 text-white" : "bg-amber-50 text-amber-700"
                }`}
              >
                soon
              </span>
            )}
          </button>
        ))}
      </div>

      {source === "ifc" ? (
        <div className="mt-4">
          <UnderMaintenance
            title="BOQ from IFC drawings"
            note="Taking quantities off the issued-for-construction drawings — uploading them, extracting the items and reviewing them against the design sheet BOQ — is being built. The BOQ as per Design Sheet is beside it."
          />
        </div>
      ) : (
        <>
      <div className="mt-4 text-xs text-gray-500">
        Source: Design Sheets
        {project.design_sheets.length > 0 &&
          ` · ${project.design_sheets.map((sheet) => sheet.system_code ?? "?").join(", ")}`}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <SummaryCard label="Total Items" value={rows.length.toLocaleString()} tint="bg-blue-50 text-blue-600" />
        <SummaryCard label="Total Quantity" value={allTotals.units.toLocaleString()} tint="bg-green-50 text-green-600" />
        <SummaryCard
          label="Systems"
          value={String(tabs.filter((tab) => tab !== UNASSIGNED).length)}
          note={tabs.filter((tab) => tab !== UNASSIGNED).join(", ")}
          tint="bg-orange-50 text-orange-500"
        />
        <SummaryCard
          label="Estimated Cost"
          value={
            allTotals.totalPrice === null
              ? "—"
              : allTotals.totalPrice.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          }
          note={allTotals.totalPrice === null ? "No prices entered yet" : "From the lines' total prices"}
          tint="bg-purple-50 text-purple-600"
        />
      </div>

      {extractNote && (
        <div className="mt-4 rounded-lg bg-blue-50 px-3 py-2 text-sm text-blue-800">{extractNote}</div>
      )}
      {warnings.length > 0 && (
        <div className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <div className="font-medium">
            {warnings.length === 1 ? "A Design Sheet" : `${warnings.length} Design Sheets`} could not be read,
            so {warnings.length === 1 ? "its" : "their"} lines are not below. Enter them by hand.
          </div>
          <ul className="mt-1 list-disc pl-5 text-xs">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}
      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {blankRows > 0 && (
        <div className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {blankRows} line{blankRows > 1 ? "s have" : " has"} no description and will be dropped on save.
        </div>
      )}

      {loading ? (
        <div className="mt-6 rounded-xl border border-gray-200 bg-white p-8 text-center">
          <div className="text-sm text-gray-500">Loading the Bill of Quantities...</div>
          {canEdit && project.design_sheets.length > 0 && (
            <div className="mt-1 text-xs text-gray-400">
              The first time a project is opened its Design Sheets are read, which can take a minute.
            </div>
          )}
        </div>
      ) : tabs.length === 0 ? (
        <div className="mt-6 rounded-xl border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-400">
          No BOQ lines yet.
          {canEdit && " Use Add line to start one."}
        </div>
      ) : (
        <>
          <div className="mt-6 flex flex-wrap gap-1 border-b border-gray-200">
            {tabs.map((tab) => {
              const count = rows.filter((row) => inTab(row, tab)).length;
              const isActive = tab === activeTab;
              return (
                <button
                  key={tab}
                  onClick={() => setSelectedTab(tab)}
                  className={`-mb-px rounded-t-lg border-b-2 px-4 py-2 text-sm font-medium ${
                    isActive
                      ? "border-brand-600 text-brand-700"
                      : "border-transparent text-gray-500 hover:text-navy-900"
                  }`}
                >
                  {tab === UNASSIGNED ? "Unassigned" : tab}
                  <span className="ml-2 rounded-full bg-gray-100 px-1.5 py-0.5 text-xs text-gray-500">
                    {count}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter by part no., description, manufacturer..."
              className="input max-w-sm py-1.5"
            />
            {tabDuplicates > 0 && (
              <label className="flex items-center gap-2 text-sm text-amber-800">
                <input
                  type="checkbox"
                  checked={duplicatesOnly}
                  onChange={(e) => setDuplicatesOnly(e.target.checked)}
                  className="h-4 w-4 rounded border-gray-300"
                />
                Only possible duplicates ({tabDuplicates})
              </label>
            )}
            {filtering && (
              <span className="text-xs text-gray-400">
                Showing {visibleRows.length} of {tabRows.length} lines
              </span>
            )}
          </div>

          {visibleRows.length === 0 ? (
            <div className="mt-4 rounded-xl border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-400">
              {filtering
                ? "No lines match the filter."
                : `No lines for ${activeTab === UNASSIGNED ? "unassigned items" : activeTab} yet.`}
            </div>
          ) : (
            <div className="mt-3 overflow-x-auto rounded-xl border border-gray-200 bg-white">
              <table className="w-full min-w-[1400px] text-sm">
                <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                  <tr>
                    {COLUMNS.map((column) => (
                      <th
                        key={column.key}
                        className={`${column.width} px-2 py-2 font-medium ${column.align === "right" ? "text-right" : ""}`}
                      >
                        {column.label}
                      </th>
                    ))}
                    {canEdit && <th className="w-10 px-2 py-2" />}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {pagedRows.map(({ row, index }) => {
                    const duplicate = duplicates.has(index);
                    return (
                      <tr key={index} className={duplicate ? "bg-amber-50/60" : undefined}>
                        {COLUMNS.map((column) => (
                          <td key={column.key} className="px-2 py-1.5 align-top">
                            <input
                              value={row[column.key] ?? ""}
                              title={row[column.key] ?? undefined}
                              disabled={!canEdit}
                              placeholder={column.placeholder}
                              inputMode={column.align === "right" ? "decimal" : undefined}
                              onChange={(e) =>
                                update(index, {
                                  [column.key]:
                                    column.key === "description" ? e.target.value : e.target.value || null,
                                })
                              }
                              className={`input py-1 disabled:bg-gray-50 ${column.align === "right" ? "text-right" : ""}`}
                            />
                            {column.key === "description" && duplicate && (
                              <div className="mt-0.5 text-[11px] text-amber-700">
                                Possible duplicate: same item listed again under this group
                              </div>
                            )}
                          </td>
                        ))}
                        {canEdit && (
                          <td className="px-2 py-1.5 text-center align-top">
                            <button
                              onClick={() => removeRow(index)}
                              aria-label={`Remove line ${index + 1}`}
                              className="rounded-md px-2 py-1 text-xs font-medium text-gray-400 hover:bg-red-50 hover:text-red-700"
                            >
                              &times;
                            </button>
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {visibleRows.length > PAGE_SIZE && (
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className="text-gray-500">
                Showing {(currentPage - 1) * PAGE_SIZE + 1} to {Math.min(currentPage * PAGE_SIZE, visibleRows.length)} of{" "}
                {visibleRows.length} items
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setPage(currentPage - 1)}
                  disabled={currentPage === 1}
                  className="rounded-lg border border-gray-300 px-3 py-1.5 font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40"
                >
                  &lsaquo;
                </button>
                {Array.from({ length: pageCount }, (_, i) => i + 1)
                  .filter((n) => n === 1 || n === pageCount || Math.abs(n - currentPage) <= 2)
                  .map((n, i, shown) => (
                    <span key={n} className="flex items-center gap-1">
                      {i > 0 && shown[i - 1] !== n - 1 && <span className="px-1 text-gray-400">...</span>}
                      <button
                        onClick={() => setPage(n)}
                        className={`rounded-lg px-3 py-1.5 font-medium ${
                          n === currentPage ? "bg-brand-600 text-white" : "border border-gray-300 text-gray-700 hover:bg-gray-50"
                        }`}
                      >
                        {n}
                      </button>
                    </span>
                  ))}
                <button
                  onClick={() => setPage(currentPage + 1)}
                  disabled={currentPage === pageCount}
                  className="rounded-lg border border-gray-300 px-3 py-1.5 font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40"
                >
                  &rsaquo;
                </button>
              </div>
            </div>
          )}

          <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 px-1 text-sm text-gray-600">
            <span>
              {filtering ? "Shown" : "Total"}: <strong className="tabular-nums">{totals.lines}</strong> lines
            </span>
            <span>
              Quantity: <strong className="tabular-nums">{totals.units.toLocaleString()}</strong>
              {Object.entries(totals.byWord).map(([word, count]) => (
                <span key={word}>
                  {" "}
                  + {count} &times; {word}
                </span>
              ))}
            </span>
            {totals.totalPrice !== null && (
              <span>
                Total price:{" "}
                <strong className="tabular-nums">
                  {totals.totalPrice.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </strong>
              </span>
            )}
          </div>
        </>
      )}
        </>
      )}
    </div>
  );
}

function SummaryCard({ label, value, note, tint }: { label: string; value: string; note?: string; tint: string }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3">
      <span className={`flex h-10 w-10 items-center justify-center rounded-xl ${tint}`}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
          <rect x="4" y="3" width="16" height="18" rx="2" />
          <path d="M8 8h8M8 12h8M8 16h5" />
        </svg>
      </span>
      <div className="min-w-0">
        <div className="text-xs text-gray-500">{label}</div>
        <div className="text-xl font-bold tabular-nums text-navy-900">{value}</div>
        {note && (
          <div className="truncate text-xs text-gray-400" title={note}>
            {note}
          </div>
        )}
      </div>
    </div>
  );
}
