import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ApiError, api } from "../lib/api";
import { matchesFilter, possibleDuplicates, quantityTotals } from "../lib/boq";
import { formatApiDate } from "../lib/format";
import { quantityProblem } from "../lib/quantity";
import {
  PROJECT_EDITOR_ROLES,
  type BoqCompare,
  type BoqEnsureResponse,
  type BoqLineStatus,
  type BoqRevisionSummary,
  type ExtractionState,
  type ProjectBoqItem,
  type ProjectBoqItemInput,
} from "../lib/types";
import { useUnsavedChanges } from "../lib/useUnsavedChanges";
import { useJob } from "../lib/useJob";
import { JobProgress } from "../components/JobProgress";
import { SyncDocumentsCard } from "../components/SyncDocumentsCard";
import { FloorScheduleTab } from "../components/FloorScheduleTab";
import IfcBoqTab from "../components/ifc/IfcBoqTab";
import ComparisonTab from "../components/ifc/ComparisonTab";
import { ExtractionReview } from "../components/ExtractionReview";
import { AiCheckBadge, AiVerificationPanel } from "../components/AiVerificationPanel";
import { StaleWriteNotice } from "../components/StaleWriteNotice";
import { BoqGroupedView } from "../components/BoqGroupedView";
import { boqGroupKey, groupBoqRows, type IndexedRow } from "../lib/boqGroups";
import { useProject } from "./ProjectWorkspace";

// Stands in for "no system" so a tab always has a key. Lines only land here
// if they predate the per-system tabs or were extracted from a sheet whose
// filename carried no system code.
const UNASSIGNED = " unassigned";

/** Where a BOQ's quantities come from: the Design Sheet, the engineer's
 * floor-wise schedule, or the issued-for-construction drawings (fire alarm
 * for now; emergency lighting is next). */
type SourceKey = "design" | "floor" | "ifc" | "compare";
const SOURCES: { key: SourceKey; label: string; soon?: boolean }[] = [
  { key: "design", label: "As per Design Sheet" },
  { key: "floor", label: "BOQ Floor Wise" },
  { key: "ifc", label: "As per IFC Drawings" },
  { key: "compare", label: "Comparison" },
];

const PAGE_SIZE = 25;

/** How each line's origin is shown: text first, colour second, so the
 * status reads without colour. */
const STATUS: Record<BoqLineStatus | "new", { label: string; className: string; help: string }> = {
  extracted: { label: "Read", className: "bg-sky-50 text-sky-800 ring-sky-200", help: "Read off the Design Sheet, unchanged." },
  corrected: {
    label: "Corrected",
    className: "bg-violet-50 text-violet-800 ring-violet-200",
    help: "Read off the Design Sheet, then changed by an engineer. The machine's value is kept.",
  },
  ai_accepted: {
    label: "AI read, accepted",
    className: "bg-amber-50 text-amber-900 ring-amber-200",
    help: "A row the scan could not settle; an engineer accepted the AI's reading of the cell.",
  },
  review_accepted: {
    label: "Reviewed",
    className: "bg-emerald-50 text-emerald-800 ring-emerald-200",
    help: "A row the scan could not settle; an engineer entered its quantity from the cell image.",
  },
  manual: { label: "Typed in", className: "bg-gray-100 text-gray-700 ring-gray-200", help: "Entered by hand." },
  legacy: {
    label: "No source record",
    className: "bg-white text-gray-500 ring-gray-300",
    help: "Stored before the platform kept where each line came from. Re-read the sheets to attach a source.",
  },
  new: { label: "Unsaved", className: "bg-white text-gray-600 ring-gray-300 ring-dashed", help: "Added on this page, not saved yet." },
};

type StatusFilter = "all" | BoqLineStatus | "attention" | "duplicates";

/** How the lines are laid out: under the Design Sheet's headings to read,
 * or as the flat grid to edit many at once. Either shows the same lines. */
type View = "grouped" | "table";
const VIEW_KEY = "boq.view";

interface Row extends ProjectBoqItemInput {
  /** Stable key for React while the line has no id yet. */
  key: string;
}

let keySeed = 0;

function inTab(row: ProjectBoqItemInput, tab: string): boolean {
  return tab === UNASSIGNED ? !row.system_code : row.system_code === tab;
}

function toRow(item: ProjectBoqItemInput): Row {
  return {
    key: item.id ? `id-${item.id}` : `new-${++keySeed}`,
    id: item.id ?? null,
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

type TextColumn = Exclude<keyof ProjectBoqItemInput, "system_code" | "id">;

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

function rowStatus(row: Row, meta: Map<number, ProjectBoqItem>): BoqLineStatus | "new" {
  return row.id && meta.has(row.id) ? meta.get(row.id)!.status : "new";
}

export function ProjectBoqPage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);

  const [rows, setRows] = useState<Row[]>([]);
  // The stored lines by id: provenance to show beside each row.
  const [meta, setMeta] = useState<Map<number, ProjectBoqItem>>(new Map());
  const [version, setVersion] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [staleError, setStaleError] = useState<ApiError | null>(null);
  const [warnings, setWarnings] = useState<string[]>(project.boq_extraction_warnings ?? []);
  // Bumped to reload the table: after a reviewed row is added, or to take
  // someone else's newer save.
  const [reloadEpoch, setReloadEpoch] = useState(0);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [extractNote, setExtractNote] = useState<string | null>(null);
  const [selectedTab, setSelectedTab] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [buildingFilter, setBuildingFilter] = useState<string>("");
  const [groupFilter, setGroupFilter] = useState("");
  const [manufacturerFilter, setManufacturerFilter] = useState("");
  // Remembered on this browser; storage can be refused (a private window),
  // and then the page simply opens grouped.
  const [view, setView] = useState<View>(() => {
    try {
      return localStorage.getItem(VIEW_KEY) === "table" ? "table" : "grouped";
    } catch {
      return "grouped";
    }
  });
  // Which groups are open, by tab and heading. Unset, a tab opens on its
  // first group, with the rest closed until asked for.
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [source, setSource] = useState<SourceKey>("design");
  const [page, setPage] = useState(1);
  const [extraction, setExtraction] = useState<ExtractionState | null>(null);
  const [latestRevision, setLatestRevision] = useState<BoqRevisionSummary | null>(null);
  const [changesSinceRevision, setChangesSinceRevision] = useState<number | null>(null);
  const [openDetail, setOpenDetail] = useState<string | null>(null);

  useUnsavedChanges(dirty);

  // The first open of a project's BOQ has the AI read its Design Sheets,
  // which takes minutes and runs as a job: the page follows it and loads
  // the BOQ out of the stored reading when it ends.
  const reading = useJob(project.id, "boq_read", `/projects/${project.id}/boq/ensure`, () => setReloadEpoch((n) => n + 1));
  const followReading = reading.follow;

  function applyItems(items: ProjectBoqItem[], newVersion: number | null) {
    setRows(items.map(toRow));
    setMeta(new Map(items.map((item) => [item.id, item])));
    setVersion(newVersion);
  }

  const loadStatus = useCallback(() => {
    api.get<ExtractionState>(`/projects/${project.id}/extraction`).then(setExtraction).catch(() => setExtraction(null));
    api
      .get<BoqRevisionSummary[]>(`/projects/${project.id}/boq/revisions`)
      .then((revisions) => {
        const latest = revisions[0] ?? null;
        setLatestRevision(latest);
        if (!latest) {
          setChangesSinceRevision(null);
          return;
        }
        return api
          .get<BoqCompare>(`/projects/${project.id}/boq/compare?from_rev=${latest.number}`)
          .then((compare) => setChangesSinceRevision(compare.changes.length));
      })
      .catch(() => setLatestRevision(null));
  }, [project.id]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    // Opening the BOQ is what triggers the one-off read of the Design Sheets;
    // the server decides whether this is that first open. Viewers cannot
    // trigger it -- it writes -- so they just read what is stored.
    const load: Promise<BoqEnsureResponse> = canEdit
      ? api.post<BoqEnsureResponse>(`/projects/${project.id}/boq/ensure`)
      : api
          .getVersioned<ProjectBoqItem[]>(`/projects/${project.id}/boq`)
          .then(({ data, version: v }) => ({ items: data, extracted: false, warnings: [], version: v ?? 0 }));

    load
      .then((result) => {
        if (cancelled) return;
        if (result.reading) {
          // The AI is reading the sheets: follow the job; the finish reloads.
          followReading(result.reading);
          applyItems([], result.version);
          setDirty(false);
          loadStatus();
          return;
        }
        applyItems(result.items, result.version);
        setDirty(false);
        setStaleError(null);
        if (result.warnings.length > 0) setWarnings(result.warnings);
        if (result.extracted && result.items.length > 0) {
          setExtractNote(
            `${result.items.length} line${result.items.length > 1 ? "s were" : " was"} read from the Design Sheets. Check them over and edit anything the scan got wrong.`
          );
        }
        loadStatus();
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
  }, [project.id, canEdit, reloadEpoch, loadStatus, followReading]);

  // One tab per system the project has a Design Sheet for -- FAS and ELS on a
  // project with those two sheets. Systems only present on existing BOQ lines
  // are included too: a line whose system has no tab would be invisible but
  // still saved, which is worse than an extra tab.
  const tabs = useMemo(() => {
    const fromSheets = project.design_sheets
      .map((sheet) => sheet.system_code)
      .filter((code): code is string => Boolean(code));
    const fromRows = rows.map((row) => row.system_code).filter((code): code is string => Boolean(code));
    const named = Array.from(new Set([...fromSheets, ...fromRows]));
    return rows.some((row) => !row.system_code) ? [...named, UNASSIGNED] : named;
  }, [project.design_sheets, rows]);

  const activeTab = selectedTab ?? tabs[0] ?? UNASSIGNED;

  // Recomputed on every edit, so a fix clears its flag before the save.
  const duplicates = useMemo(() => possibleDuplicates(rows), [rows]);
  const quantityProblems = useMemo(() => new Map(rows.map((row, i) => [i, quantityProblem(row.quantity)])), [rows]);

  const counts = useMemo(() => {
    const byStatus: Record<string, number> = {};
    rows.forEach((row) => {
      const status = rowStatus(row, meta);
      byStatus[status] = (byStatus[status] ?? 0) + 1;
    });
    return byStatus;
  }, [rows, meta]);

  function needsAttention(row: Row, index: number): boolean {
    return !row.system_code || Boolean(quantityProblems.get(index)) || duplicates.has(index) || !row.quantity;
  }

  // Kept alongside its index in `rows`, because edits and removals address the
  // full list while the table only renders one tab of it.
  const tabRows = useMemo(
    () => rows.map((row, index) => ({ row, index })).filter(({ row }) => inTab(row, activeTab)),
    [rows, activeTab]
  );
  const buildings = useMemo(
    () => Array.from(new Set([...meta.values()].map((item) => item.building).filter((b): b is string => Boolean(b)))).sort(),
    [meta]
  );
  // The tab's lines under the Design Sheet's own headings, for the grouped
  // view and for the group filter. Only how they are shown; not the lines.
  const groups = useMemo(() => groupBoqRows(tabRows), [tabRows]);
  const manufacturers = useMemo(
    () => Array.from(new Set(tabRows.map(({ row }) => (row.manufacturer ?? "").trim()).filter(Boolean))).sort(),
    [tabRows]
  );
  const visibleRows = tabRows.filter(({ row, index }) => {
    if (!matchesFilter(row, filter)) return false;
    if (buildingFilter && (row.id ? meta.get(row.id)?.building : null) !== buildingFilter) return false;
    if (groupFilter && boqGroupKey(row) !== groupFilter) return false;
    if (manufacturerFilter && (row.manufacturer ?? "").trim() !== manufacturerFilter) return false;
    if (statusFilter === "duplicates") return duplicates.has(index);
    if (statusFilter === "attention") return needsAttention(row, index);
    if (statusFilter !== "all") return rowStatus(row, meta) === statusFilter;
    return true;
  });
  const visibleIndices = new Set(visibleRows.map(({ index }) => index));
  const filtering =
    filter.trim() !== "" || statusFilter !== "all" || buildingFilter !== "" || groupFilter !== "" || manufacturerFilter !== "";
  // Price columns only earn their width once somebody has entered a price.
  const showPrices = tabRows.some(({ row }) => Boolean(row.unit_price?.trim() || row.total_price?.trim()));
  const showUnits = tabRows.some(({ row }) => Boolean(row.unit?.trim()));
  const attentionCount = rows.filter((row, index) => needsAttention(row, index)).length;
  const duplicateCount = duplicates.size;

  function chooseView(next: View) {
    setView(next);
    setEditingKey(null);
    try {
      localStorage.setItem(VIEW_KEY, next);
    } catch {
      // Not remembered on this browser; the choice still holds for this visit.
    }
  }

  function isOpen(key: string): boolean {
    return expanded[`${activeTab}|${key}`] ?? groups[0]?.key === key;
  }

  function toggleGroup(key: string) {
    setExpanded((was) => ({ ...was, [`${activeTab}|${key}`]: !isOpen(key) }));
  }

  function setAllGroups(open: boolean) {
    setExpanded((was) => ({ ...was, ...Object.fromEntries(groups.map((group) => [`${activeTab}|${group.key}`, open])) }));
  }

  /** What the sheet said for a field an engineer has since changed, or
   * undefined where the line is as read. */
  function sheetValue({ row }: IndexedRow, field: TextColumn): string | null | undefined {
    const stored = row.id ? meta.get(row.id) : undefined;
    if (stored?.status !== "corrected" || !(field in (stored.extracted_values ?? {}))) return undefined;
    const original = (stored.extracted_values as Record<string, string | null>)[field];
    return (original ?? "") !== (row[field] ?? "") ? original : undefined;
  }

  /** A line's source status and AI check, and its source record on demand --
   * the same as the table view's first column. */
  function lineStatus({ row }: IndexedRow) {
    const stored = row.id ? meta.get(row.id) : undefined;
    return (
      <>
        <button
          type="button"
          onClick={() => setOpenDetail(openDetail === row.key ? null : row.key)}
          aria-expanded={openDetail === row.key}
          title="Where this line came from"
          className="rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
        >
          <StatusBadge status={rowStatus(row, meta)} />
        </button>
        <AiCheckBadge check={stored?.ai_check} />
        {openDetail === row.key && (
          <div className="basis-full">
            <Provenance row={row} meta={meta} />
          </div>
        )}
      </>
    );
  }
  // Long BOQs are paged, but an edit must never move a line out from under
  // the engineer, so the page only changes when they change it.
  const pageCount = Math.max(1, Math.ceil(visibleRows.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const pagedRows = visibleRows.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const totals = quantityTotals(visibleRows.map(({ row }) => row));
  const allTotals = quantityTotals(rows);
  const tabDuplicates = tabRows.filter(({ index }) => duplicates.has(index)).length;
  const invalidQuantities = [...quantityProblems.values()].filter(Boolean).length;

  function touched() {
    setDirty(true);
    setSavedAt(null);
  }

  function update(index: number, patch: Partial<ProjectBoqItemInput>) {
    setRows((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));
    touched();
  }

  function addRow() {
    const added = toRow({ system_code: activeTab === UNASSIGNED ? null : activeTab, description: "" });
    setRows((prev) => [...prev, added]);
    // A new, blank line would be hidden by an active filter, or by the
    // drawings tab being the one on show.
    setFilter("");
    setStatusFilter("all");
    setGroupFilter("");
    setManufacturerFilter("");
    setSource("design");
    setPage(pageCount);
    // Grouped, it opens ready to fill in, among the lines without a heading
    // -- where a line with none is listed until it is given one.
    setEditingKey(added.key);
    setExpanded((was) => ({ ...was, [`${activeTab}|${boqGroupKey(added)}`]: true }));
    touched();
  }

  function removeRow(index: number) {
    const row = rows[index];
    const label = row.catalog_no || row.description || "this line";
    if (row.id && !window.confirm(`Remove ${label} from the BOQ? It is removed when you save.`)) return;
    setRows((prev) => prev.filter((_, i) => i !== index));
    touched();
  }

  async function save() {
    setSaving(true);
    setError(null);
    setStaleError(null);
    try {
      // Every tab is sent, not just the visible one -- this replaces the
      // project's whole BOQ. Each line keeps its id so its source record
      // survives the save.
      const payload = rows
        .filter((row) => row.description.trim() !== "")
        .map(({ key: _key, ...row }) => ({ ...row, description: row.description.trim() }));
      const saved = await api.putVersioned<ProjectBoqItem[]>(`/projects/${project.id}/boq`, payload, version);
      applyItems(saved.data, saved.version);
      setDirty(false);
      setSavedAt(new Date().toISOString());
      loadStatus();
    } catch (err) {
      if (err instanceof ApiError && err.isStaleWrite) setStaleError(err);
      else setError(err instanceof ApiError ? err.message : "Failed to save the BOQ");
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
  const openIssues = extraction?.open_issues ?? 0;
  const designRuns = (extraction?.runs ?? []).filter((run) => run.kind === "design_sheet");
  const aiRead = designRuns.some((run) => run.reader === "ai");
  const unprocessedPages = designRuns.reduce((sum, run) => sum + run.unprocessed_pages.length, 0);
  const parserVersions = Array.from(new Set([...meta.values()].map((item) => item.parser_version).filter(Boolean)));
  const readTimes = designRuns.map((run) => run.started_at).sort();
  const unassigned = rows.filter((row) => !row.system_code).length;
  const changedByEngineer = (counts.corrected ?? 0) + (counts.manual ?? 0) + (counts.new ?? 0);

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs text-gray-400">
            EP-{project.ep_number}
            {project.project_name ? ` — ${project.project_name}` : ""} / BOQ
          </div>
          <h1 className="text-3xl font-bold text-navy-900">Bill of Quantities (BOQ)</h1>
          <p className="mt-1 text-sm text-gray-500">Line items per system. The Design Sheets under Documents are the source.</p>
          <div className="mt-3">
            <SyncDocumentsCard projectId={project.id} canEdit={canEdit} compact onSynced={() => setReloadEpoch((n) => n + 1)} />
          </div>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <Link
            to="revisions"
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50"
          >
            Revisions
          </Link>
          <Link
            to="reread"
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50"
            title="Read the Design Sheets again and review every difference before anything changes"
          >
            Re-read sheets
          </Link>
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
                {saving ? "Saving..." : dirty ? "Save changes" : "Saved"}
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
            aria-pressed={source === option.key}
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
                not available yet
              </span>
            )}
          </button>
        ))}
      </div>

      {source === "floor" ? (
        <FloorScheduleTab projectId={project.id} canEdit={canEdit} />
      ) : source === "ifc" ? (
        <div className="mt-4">
          <IfcBoqTab projectId={project.id} canEdit={canEdit} />
        </div>
      ) : source === "compare" ? (
        <div className="mt-4">
          <ComparisonTab projectId={project.id} />
        </div>
      ) : (
        <>
          <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Tile
              icon="lines"
              label="Total Lines"
              value={rows.length.toLocaleString()}
              note={(() => {
                const systems = tabs.filter((tab) => tab !== UNASSIGNED);
                return systems.length ? `items · ${systems.join(", ")}` : "items";
              })()}
              tint="bg-blue-50 text-blue-600"
            />
            <Tile
              icon="sum"
              label="Total Quantity"
              value={allTotals.units.toLocaleString()}
              note={[
                "units",
                ...Object.entries(allTotals.byWord).map(([word, count]) => `+ ${count} × ${word}`),
                allTotals.totalPrice !== null
                  ? `est. ${allTotals.totalPrice.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                  : null,
              ]
                .filter(Boolean)
                .join(" ")}
              tint="bg-emerald-50 text-emerald-600"
            />
            <Tile
              icon="groups"
              label="Groups"
              value={String(new Set(rows.filter((row) => row.group_heading?.trim()).map((row) => `${row.system_code}|${boqGroupKey(row)}`)).size)}
              note="headings on the Design Sheets"
              tint="bg-violet-50 text-violet-600"
            />
            <Tile
              icon="attention"
              label="Needs attention"
              value={String(attentionCount)}
              note={
                attentionCount === 0
                  ? "every line checks out"
                  : [
                      unassigned ? `${unassigned} unassigned` : null,
                      duplicateCount ? `${duplicateCount} possible duplicate${duplicateCount === 1 ? "" : "s"}` : null,
                      invalidQuantities ? `${invalidQuantities} invalid qty` : null,
                    ]
                      .filter(Boolean)
                      .join(" · ") || "lines to look over"
              }
              tint={attentionCount ? "bg-amber-50 text-amber-600" : "bg-gray-100 text-gray-500"}
              active={statusFilter === "attention"}
              onClick={
                attentionCount
                  ? () => {
                      setStatusFilter(statusFilter === "attention" ? "all" : "attention");
                      setPage(1);
                    }
                  : undefined
              }
            />
          </div>

          {/* What the table on screen is: its source, how it was read, what is
              unresolved, what engineers changed, and where it stands against
              the last issued revision. */}
          <section
            aria-label="BOQ status"
            className="mt-4 grid gap-px overflow-hidden rounded-xl border border-gray-200 bg-gray-200 text-xs sm:grid-cols-2 lg:grid-cols-4"
          >
            <StripCell label="Source">
              {project.design_sheets.length === 0
                ? "No Design Sheets attached"
                : `${project.design_sheets.length} Design Sheet${project.design_sheets.length > 1 ? "s" : ""}: ${project.design_sheets
                    .map((sheet) => sheet.system_code ?? "?")
                    .join(", ")}`}
              <div className="text-gray-500">
                {readTimes.length > 0
                  ? `${aiRead ? "Read by AI" : "Read"} ${formatApiDate(readTimes[readTimes.length - 1], "short")}`
                  : "No recorded read"}
                {aiRead
                  ? " · reading stored, reused on every open"
                  : parserVersions.length > 0
                    ? ` · parser ${parserVersions.join(", ")}`
                    : ""}
              </div>
            </StripCell>
            <StripCell label="Coverage" tone={unprocessedPages > 0 || openIssues > 0 ? "warn" : "ok"}>
              {designRuns.length === 0
                ? "No read on record for these lines"
                : unprocessedPages > 0
                  ? `${unprocessedPages} page${unprocessedPages > 1 ? "s" : ""} not read`
                  : "Every page read"}
              <div className={openIssues > 0 ? "font-semibold text-amber-800" : "text-gray-500"}>
                {openIssues > 0 ? `${openIssues} unresolved row${openIssues > 1 ? "s" : ""} to review` : "No unresolved rows"}
              </div>
            </StripCell>
            <StripCell label="Lines" tone={unassigned > 0 || invalidQuantities > 0 ? "warn" : undefined}>
              {rows.length} lines · {counts.extracted ?? 0} as read · {changedByEngineer} changed or typed by engineers
              <div className={unassigned > 0 || invalidQuantities > 0 ? "font-semibold text-amber-800" : "text-gray-500"}>
                {[
                  unassigned > 0 ? `${unassigned} without a system` : null,
                  invalidQuantities > 0 ? `${invalidQuantities} invalid quantit${invalidQuantities > 1 ? "ies" : "y"}` : null,
                  counts.legacy ? `${counts.legacy} with no source record` : null,
                ]
                  .filter(Boolean)
                  .join(" · ") || "Every line has a system and a valid quantity"}
              </div>
            </StripCell>
            <StripCell label="State" tone={dirty ? "warn" : undefined}>
              {dirty ? "Unsaved changes on this page" : `Saved · version ${version ?? "—"}`}
              <div className="text-gray-500">
                {savedAt
                  ? `Saved ${formatApiDate(savedAt, "short")}`
                  : latestRevision
                    ? changesSinceRevision === 0
                      ? `Matches ${latestRevision.label} (issued ${formatApiDate(latestRevision.issued_at, "short")})`
                      : `${changesSinceRevision ?? "?"} change${changesSinceRevision === 1 ? "" : "s"} since ${latestRevision.label} — not issued`
                    : "No revision issued yet"}
              </div>
            </StripCell>
          </section>

          {extractNote && <div className="mt-4 rounded-lg bg-blue-50 px-3 py-2 text-sm text-blue-800">{extractNote}</div>}
          {reading.job && (reading.active || reading.job.status === "failed") && (
            <JobProgress job={reading.job} onCancel={reading.cancel} what="the AI read of the Design Sheets" />
          )}
          {reading.error && <div className="mt-2 text-xs text-red-700">{reading.error}</div>}
          {warnings.length > 0 && (
            <div className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
              <div className="font-medium">
                {warnings.length === 1 ? "A Design Sheet" : `${warnings.length} Design Sheets`} could not be read
                {aiRead ? " by the AI" : ""}, so {warnings.length === 1 ? "its" : "their"} lines are not below. Enter them by hand.
              </div>
              <ul className="mt-1 list-disc pl-5 text-xs">
                {warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          )}
          {!loading && (
            <AiVerificationPanel
              projectId={project.id}
              scope="boq"
              canEdit={canEdit}
              onApplied={() => {
                if (dirty && !window.confirm("The AI check changed the saved BOQ. Reloading discards your unsaved edits. Reload now?")) {
                  return;
                }
                setReloadEpoch((n) => n + 1);
              }}
            />
          )}
          {!loading && (
            <ExtractionReview
              key={reloadEpoch}
              projectId={project.id}
              canEdit={canEdit}
              onAccepted={() => {
                if (dirty && !window.confirm("The accepted line is added to the saved BOQ. Reloading discards your unsaved edits. Reload now?")) {
                  return;
                }
                setReloadEpoch((n) => n + 1);
              }}
            />
          )}
          {staleError && (
            <StaleWriteNotice
              error={staleError}
              what="the BOQ"
              onReload={() => setReloadEpoch((n) => n + 1)}
              onDismiss={() => setStaleError(null)}
            />
          )}
          {error && (
            <div role="alert" className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}
          {blankRows > 0 && (
            <div className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
              {blankRows} line{blankRows > 1 ? "s have" : " has"} no description and will be dropped on save.
            </div>
          )}

          {loading ? (
            <div className="mt-6 rounded-xl border border-gray-200 bg-white p-8 text-center" aria-live="polite">
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
              {canEdit && " Use Add Item to start one."}
            </div>
          ) : (
            <>
              <div className="mt-6 flex gap-1 overflow-x-auto border-b border-gray-200" role="tablist" aria-label="Systems">
                {tabs.map((tab) => {
                  const count = rows.filter((row) => inTab(row, tab)).length;
                  const isActive = tab === activeTab;
                  return (
                    <button
                      key={tab}
                      role="tab"
                      aria-selected={isActive}
                      onClick={() => {
                        setSelectedTab(tab);
                        setPage(1);
                        // A group or maker picked on one system's tab means
                        // nothing on another's.
                        setGroupFilter("");
                        setManufacturerFilter("");
                        setEditingKey(null);
                      }}
                      className={`-mb-px shrink-0 rounded-t-lg border-b-2 px-4 py-2 text-sm font-medium ${
                        isActive ? "border-brand-600 text-brand-700" : "border-transparent text-gray-500 hover:text-navy-900"
                      } ${tab === UNASSIGNED ? "text-amber-800" : ""}`}
                    >
                      {tab === UNASSIGNED ? "Unassigned (needs a system)" : tab}
                      <span className="ml-2 rounded-full bg-gray-100 px-1.5 py-0.5 text-xs text-gray-500">{count}</span>
                    </button>
                  );
                })}
              </div>

              <div className="sticky top-0 z-10 mt-4 flex flex-wrap items-center gap-2 bg-gray-50/95 py-2 backdrop-blur">
                <label className="sr-only" htmlFor="boq-filter">
                  Search lines
                </label>
                <div className="relative min-w-60 max-w-md flex-1">
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
                    aria-hidden="true"
                  >
                    <circle cx="11" cy="11" r="7" />
                    <path d="m20 20-3.5-3.5" />
                  </svg>
                  <input
                    id="boq-filter"
                    type="search"
                    value={filter}
                    onChange={(e) => {
                      setFilter(e.target.value);
                      setPage(1);
                    }}
                    placeholder="Search groups, part numbers, descriptions..."
                    className="input py-2 pl-9"
                  />
                </div>
                <select
                  aria-label="Group"
                  value={groupFilter}
                  onChange={(e) => {
                    setGroupFilter(e.target.value);
                    setPage(1);
                  }}
                  className="input w-auto max-w-56 py-2"
                >
                  <option value="">All groups ({groups.length})</option>
                  {groups.map((group) => (
                    <option key={group.key} value={group.key}>
                      {group.heading || "Lines without a group"} ({group.lines.length})
                    </option>
                  ))}
                </select>
                <select
                  aria-label="Manufacturer"
                  value={manufacturerFilter}
                  onChange={(e) => {
                    setManufacturerFilter(e.target.value);
                    setPage(1);
                  }}
                  className="input w-auto max-w-48 py-2"
                >
                  <option value="">All manufacturers</option>
                  {manufacturers.map((maker) => (
                    <option key={maker} value={maker}>
                      {maker}
                    </option>
                  ))}
                </select>
                <label className="contents">
                  <span className="sr-only">Status</span>
                  <select
                    value={statusFilter}
                    onChange={(e) => {
                      setStatusFilter(e.target.value as StatusFilter);
                      setPage(1);
                    }}
                    className="input w-auto py-2"
                  >
                    <option value="all">All statuses</option>
                    <option value="attention">Needs attention</option>
                    {(tabDuplicates > 0 || statusFilter === "duplicates") && (
                      <option value="duplicates">Possible duplicates ({tabDuplicates})</option>
                    )}
                    <option value="extracted">Read, unchanged ({counts.extracted ?? 0})</option>
                    <option value="corrected">Corrected by an engineer ({counts.corrected ?? 0})</option>
                    <option value="ai_accepted">AI reading accepted ({counts.ai_accepted ?? 0})</option>
                    <option value="review_accepted">Reviewed rows ({counts.review_accepted ?? 0})</option>
                    <option value="manual">Typed in ({counts.manual ?? 0})</option>
                    <option value="legacy">No source record ({counts.legacy ?? 0})</option>
                  </select>
                </label>
                {buildings.length > 0 && (
                  <select
                    aria-label="Building"
                    value={buildingFilter}
                    onChange={(e) => {
                      setBuildingFilter(e.target.value);
                      setPage(1);
                    }}
                    className="input w-auto py-2"
                  >
                    <option value="">All buildings ({buildings.length})</option>
                    {buildings.map((building) => (
                      <option key={building} value={building}>
                        {building}
                      </option>
                    ))}
                  </select>
                )}
                <div className="ml-auto flex flex-wrap items-center gap-2">
                  {filtering && (
                    <span className="text-xs text-gray-500" aria-live="polite">
                      Showing {visibleRows.length} of {tabRows.length} lines
                      <button
                        type="button"
                        onClick={() => {
                          setFilter("");
                          setStatusFilter("all");
                          setBuildingFilter("");
                          setGroupFilter("");
                          setManufacturerFilter("");
                          setPage(1);
                        }}
                        className="ml-2 font-semibold text-brand-600 hover:underline"
                      >
                        Clear
                      </button>
                    </span>
                  )}
                  {view === "grouped" && !filtering && groups.length > 1 && (
                    <span className="hidden items-center gap-1 text-xs md:flex">
                      <button type="button" onClick={() => setAllGroups(true)} className="rounded-md px-2 py-1 font-medium text-gray-600 hover:bg-gray-100">
                        Expand all
                      </button>
                      <button type="button" onClick={() => setAllGroups(false)} className="rounded-md px-2 py-1 font-medium text-gray-600 hover:bg-gray-100">
                        Collapse all
                      </button>
                    </span>
                  )}
                  <div role="group" aria-label="Layout" className="hidden overflow-hidden rounded-lg border border-gray-300 bg-white md:flex">
                    {(
                      [
                        ["grouped", "Grouped", "Lines under the Design Sheet's headings, to read"],
                        ["table", "Table", "Every line as an editable grid, to change many at once"],
                      ] as const
                    ).map(([key, label, help]) => (
                      <button
                        key={key}
                        type="button"
                        onClick={() => chooseView(key)}
                        aria-pressed={view === key}
                        title={help}
                        className={`px-3 py-1.5 text-sm font-semibold ${
                          view === key ? "bg-brand-600 text-white" : "text-gray-600 hover:bg-gray-50"
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {visibleRows.length === 0 ? (
                <div className="mt-4 rounded-xl border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-400">
                  {filtering ? "No lines match the filter." : `No lines for ${activeTab === UNASSIGNED ? "unassigned items" : activeTab} yet.`}
                </div>
              ) : (
                <>
                  {/* Narrow screens: one card per line, every field labelled.
                      Grouped, the whole tab is listed; the grid pages it. */}
                  <ul className="mt-3 space-y-3 md:hidden">
                    {(view === "grouped" ? visibleRows : pagedRows).map(({ row, index }) => (
                      <li key={row.key} className="rounded-xl border border-gray-200 bg-white p-3">
                        <div className="flex items-start justify-between gap-2">
                          <span>
                            <StatusBadge status={rowStatus(row, meta)} />
                            <AiCheckBadge check={row.id ? meta.get(row.id)?.ai_check : null} />
                          </span>
                          {canEdit && (
                            <button
                              onClick={() => removeRow(index)}
                              className="rounded-md px-2 py-1 text-xs font-medium text-gray-500 hover:bg-red-50 hover:text-red-700"
                            >
                              Remove
                            </button>
                          )}
                        </div>
                        <div className="mt-2 grid grid-cols-1 gap-2">
                          {COLUMNS.map((column) => (
                            <label key={column.key} className="block text-xs text-gray-500">
                              {column.label}
                              <input
                                value={row[column.key] ?? ""}
                                disabled={!canEdit}
                                placeholder={column.placeholder}
                                aria-invalid={column.key === "quantity" && Boolean(quantityProblems.get(index))}
                                onChange={(e) =>
                                  update(index, {
                                    [column.key]: column.key === "description" ? e.target.value : e.target.value || null,
                                  })
                                }
                                className="input mt-0.5 py-1.5 text-sm disabled:bg-gray-50"
                              />
                              {column.key === "quantity" && quantityProblems.get(index) && (
                                <span className="text-[11px] text-red-700">{quantityProblems.get(index)}</span>
                              )}
                            </label>
                          ))}
                        </div>
                        <Provenance row={row} meta={meta} />
                      </li>
                    ))}
                  </ul>

                  {view === "grouped" ? (
                    <BoqGroupedView
                      groups={groups}
                      visible={visibleIndices}
                      filtering={filtering}
                      isOpen={isOpen}
                      onToggle={toggleGroup}
                      canEdit={canEdit}
                      duplicates={duplicates}
                      quantityProblems={quantityProblems}
                      editingKey={editingKey}
                      onEdit={setEditingKey}
                      onUpdate={update}
                      onRemove={removeRow}
                      fields={COLUMNS}
                      showPrices={showPrices}
                      showUnits={showUnits}
                      renderStatus={lineStatus}
                      sheetValue={sheetValue}
                      needsAttention={({ row, index }) => needsAttention(row, index)}
                    />
                  ) : (
                  <div className="mt-3 hidden max-h-[70vh] overflow-auto rounded-xl border border-gray-200 bg-white md:block">
                    <table className="w-full min-w-[1500px] text-sm">
                      <caption className="sr-only">
                        BOQ lines for {activeTab === UNASSIGNED ? "unassigned items" : activeTab}
                      </caption>
                      <thead className="sticky top-0 z-10 bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                        <tr>
                          <th scope="col" className="sticky left-0 z-20 w-32 bg-gray-50 px-2 py-2 font-medium">
                            Source
                          </th>
                          {COLUMNS.map((column) => (
                            <th
                              key={column.key}
                              scope="col"
                              className={`${column.width} px-2 py-2 font-medium ${column.align === "right" ? "text-right" : ""}`}
                            >
                              {column.label}
                            </th>
                          ))}
                          {canEdit && (
                            <th scope="col" className="w-10 px-2 py-2">
                              <span className="sr-only">Remove</span>
                            </th>
                          )}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-100">
                        {pagedRows.map(({ row, index }) => {
                          const duplicate = duplicates.has(index);
                          const problem = quantityProblems.get(index);
                          const status = rowStatus(row, meta);
                          const stored = row.id ? meta.get(row.id) : undefined;
                          return (
                            <tr key={row.key} className={duplicate ? "bg-amber-50/60" : undefined}>
                              <td className="sticky left-0 z-[5] bg-white px-2 py-1.5 align-top">
                                <button
                                  type="button"
                                  onClick={() => setOpenDetail(openDetail === row.key ? null : row.key)}
                                  aria-expanded={openDetail === row.key}
                                  className="rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                                >
                                  <StatusBadge status={status} />
                                </button>
                                <AiCheckBadge check={stored?.ai_check} />
                                {openDetail === row.key && <Provenance row={row} meta={meta} />}
                              </td>
                              {COLUMNS.map((column) => {
                                const original =
                                  stored?.status === "corrected" && column.key in (stored.extracted_values ?? {})
                                    ? (stored.extracted_values as Record<string, string | null>)[column.key]
                                    : undefined;
                                const differs = original !== undefined && (original ?? "") !== (row[column.key] ?? "");
                                return (
                                  <td key={column.key} className="px-2 py-1.5 align-top">
                                    <input
                                      value={row[column.key] ?? ""}
                                      title={differs ? `Read from the sheet as: ${original ?? "(blank)"}` : (row[column.key] ?? undefined)}
                                      disabled={!canEdit}
                                      placeholder={column.placeholder}
                                      aria-label={`${column.label}, line ${index + 1}`}
                                      aria-invalid={column.key === "quantity" && Boolean(problem)}
                                      data-boq-cell={`${pagedRows.findIndex((r) => r.index === index)}:${column.key}`}
                                      onKeyDown={moveBetweenRows}
                                      inputMode={column.align === "right" ? "decimal" : undefined}
                                      onChange={(e) =>
                                        update(index, {
                                          [column.key]: column.key === "description" ? e.target.value : e.target.value || null,
                                        })
                                      }
                                      className={`input py-1 disabled:bg-gray-50 ${column.align === "right" ? "text-right" : ""} ${
                                        column.key === "quantity" && problem ? "border-red-400 bg-red-50" : ""
                                      } ${differs ? "border-violet-300" : ""}`}
                                    />
                                    {column.key === "quantity" && problem && (
                                      <div className="mt-0.5 text-[11px] text-red-700">{problem}</div>
                                    )}
                                    {differs && (
                                      <div className="mt-0.5 text-[11px] text-violet-700">Sheet: {original ?? "(blank)"}</div>
                                    )}
                                    {column.key === "description" && duplicate && (
                                      <div className="mt-0.5 text-[11px] text-amber-700">
                                        Possible duplicate: same item listed again under this group
                                      </div>
                                    )}
                                  </td>
                                );
                              })}
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
                </>
              )}

              {view === "table" && visibleRows.length > PAGE_SIZE && (
                <nav aria-label="BOQ pages" className="mt-3 flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="text-gray-500">
                    Showing {(currentPage - 1) * PAGE_SIZE + 1} to {Math.min(currentPage * PAGE_SIZE, visibleRows.length)} of{" "}
                    {visibleRows.length} items
                  </span>
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => setPage(currentPage - 1)}
                      disabled={currentPage === 1}
                      aria-label="Previous page"
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
                            aria-current={n === currentPage ? "page" : undefined}
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
                      aria-label="Next page"
                      className="rounded-lg border border-gray-300 px-3 py-1.5 font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40"
                    >
                      &rsaquo;
                    </button>
                  </div>
                </nav>
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

/** Up/Down (and Enter) move to the same column on the row above or below,
 * as in a spreadsheet; Left/Right stay within the text being edited. */
function moveBetweenRows(event: React.KeyboardEvent<HTMLInputElement>) {
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  const step = event.key === "ArrowDown" || event.key === "Enter" ? 1 : event.key === "ArrowUp" ? -1 : 0;
  if (!step) return;
  const [row, column] = (event.currentTarget.dataset.boqCell ?? "").split(":");
  const next = document.querySelector<HTMLInputElement>(`[data-boq-cell="${Number(row) + step}:${column}"]`);
  if (next) {
    event.preventDefault();
    next.focus();
    next.select();
  }
}

function StripCell({ label, tone, children }: { label: string; tone?: "ok" | "warn"; children: React.ReactNode }) {
  return (
    <div className={`bg-white px-3 py-2 ${tone === "warn" ? "bg-amber-50/70" : ""}`}>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">{label}</div>
      <div className="mt-0.5 text-navy-900">{children}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: BoqLineStatus | "new" }) {
  const shown = STATUS[status];
  return (
    <span
      title={shown.help}
      className={`inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${shown.className}`}
    >
      {shown.label}
    </span>
  );
}

/** Where a line came from, as recorded: the sheet page, what was read, what
 * the parser made of it, and the machine's value where an engineer changed it. */
function Provenance({ row, meta }: { row: Row; meta: Map<number, ProjectBoqItem> }) {
  const stored = row.id ? meta.get(row.id) : undefined;
  if (!stored) return <p className="mt-2 max-w-56 text-[11px] text-gray-500">{STATUS.new.help}</p>;
  const parse = stored.raw_values?.quantity_parse;
  return (
    <dl className="mt-2 max-w-64 space-y-0.5 text-[11px] text-gray-600">
      <div>{STATUS[stored.status].help}</div>
      {stored.building && (
        <div>
          <dt className="inline font-semibold">Building: </dt>
          <dd className="inline">{stored.building}</dd>
        </div>
      )}
      {stored.catalog_match && (stored.catalog_match.source || stored.catalog_canonical) && (
        <div>
          <dt className="inline font-semibold">Part no.: </dt>
          <dd className="inline">
            read "{stored.catalog_match.source ?? stored.catalog_no}"
            {stored.catalog_canonical && stored.catalog_canonical !== stored.catalog_no && ` · matches ${stored.catalog_canonical}`}
            {(stored.catalog_match.library_reason || stored.catalog_match.reason) &&
              ` (${stored.catalog_match.library_reason ?? stored.catalog_match.reason})`}
          </dd>
        </div>
      )}
      {stored.source_page !== null && (
        <div>
          <dt className="inline font-semibold">Sheet page: </dt>
          <dd className="inline">{stored.source_page}</dd>
        </div>
      )}
      {stored.raw_values?.quantity !== undefined && (
        <div>
          <dt className="inline font-semibold">Read quantity: </dt>
          <dd className="inline">"{stored.raw_values?.quantity ?? ""}"</dd>
        </div>
      )}
      {parse && (
        <div>
          <dt className="inline font-semibold">Parsed: </dt>
          <dd className="inline">
            {parse.value ?? "—"} ({parse.rule})
          </dd>
        </div>
      )}
      {stored.ocr_confidence !== null && (
        <div>
          <dt className="inline font-semibold">Read confidence: </dt>
          <dd className="inline">{Number(stored.ocr_confidence).toFixed(0)}%</dd>
        </div>
      )}
      {stored.parser_version && (
        <div>
          <dt className="inline font-semibold">Parser: </dt>
          <dd className="inline">{stored.parser_version}</dd>
        </div>
      )}
      {stored.edited_at && (
        <div>
          <dt className="inline font-semibold">Last changed: </dt>
          <dd className="inline">{formatApiDate(stored.edited_at, "short")}</dd>
        </div>
      )}
    </dl>
  );
}

const TILE_ICONS = {
  lines: (
    <>
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M8 8h8M8 12h8M8 16h5" />
    </>
  ),
  sum: <path d="M17 4H7l6 8-6 8h10" />,
  groups: (
    <>
      <rect x="3" y="4" width="18" height="6" rx="1.5" />
      <rect x="3" y="14" width="18" height="6" rx="1.5" />
      <path d="M7 7h.01M7 17h.01" />
    </>
  ),
  attention: (
    <>
      <path d="m21 8-9-5-9 5 9 5 9-5Z" />
      <path d="M3 8v8l9 5 9-5V8M12 13v8" />
    </>
  ),
};

/** One figure about the BOQ. Given `onClick`, the tile is also the way to
 * the lines it counts. */
function Tile({
  icon,
  label,
  value,
  note,
  tint,
  active,
  onClick,
}: {
  icon: keyof typeof TILE_ICONS;
  label: string;
  value: string;
  note?: string;
  tint: string;
  active?: boolean;
  onClick?: () => void;
}) {
  const body = (
    <>
      <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ${tint}`} aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
          {TILE_ICONS[icon]}
        </svg>
      </span>
      <span className="min-w-0 text-left">
        <span className="block text-xs font-medium text-gray-500">{label}</span>
        <span className="block text-2xl font-bold leading-tight tabular-nums text-navy-900">{value}</span>
        {note && (
          <span className="block truncate text-xs text-gray-400" title={note}>
            {note}
          </span>
        )}
      </span>
    </>
  );
  const frame = `flex w-full items-center gap-3 rounded-2xl border bg-white px-4 py-3 ${
    active ? "border-amber-400 ring-2 ring-amber-100" : "border-gray-200"
  }`;
  return onClick ? (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={active ? "Show every line again" : "Show only these lines"}
      className={`${frame} hover:border-amber-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500`}
    >
      {body}
    </button>
  ) : (
    <div className={frame}>{body}</div>
  );
}
