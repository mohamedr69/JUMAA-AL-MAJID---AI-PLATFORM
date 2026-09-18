import { Fragment, type ReactNode } from "react";
import type { BoqField, BoqFieldSpec, BoqGroup, IndexedRow } from "../lib/boqGroups";
import type { ProjectBoqItemInput } from "../lib/types";

function numeric(text: string | null | undefined): number | null {
  const cleaned = (text ?? "").replace(/[,\s]/g, "");
  return /^\d+(\.\d+)?$/.test(cleaned) ? Number(cleaned) : null;
}

// --- icons ---------------------------------------------------------------------------------

function Glyph({ children, className = "h-5 w-5" }: { children: ReactNode; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

const ICONS = {
  panel: (
    <>
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M8 7h8M8 11h8M8 15h4" />
    </>
  ),
  power: <path d="M13 3 5 13h6l-1 8 8-10h-6l1-8Z" />,
  speaker: (
    <>
      <path d="M11 5 6 9H3v6h3l5 4V5Z" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" />
    </>
  ),
  sensor: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M5.6 5.6a9 9 0 0 0 0 12.8M18.4 5.6a9 9 0 0 1 0 12.8M8.5 8.5a5 5 0 0 0 0 7M15.5 8.5a5 5 0 0 1 0 7" />
    </>
  ),
  list: <path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01" />,
  box: (
    <>
      <path d="m21 8-9-5-9 5 9 5 9-5Z" />
      <path d="M3 8v8l9 5 9-5V8M12 13v8" />
    </>
  ),
  chevron: <path d="m9 6 6 6-6 6" />,
  edit: <path d="M4 20h4L19 9l-4-4L4 16v4ZM13.5 6.5l4 4" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
};

/** Only the picture changes with the heading's wording; the group is the
 * same whatever it is drawn with. */
function groupIcon(group: BoqGroup): { icon: ReactNode; tint: string } {
  const heading = group.heading.toLowerCase();
  if (!group.heading) return { icon: ICONS.box, tint: "bg-amber-50 text-amber-600" };
  if (/amplif|speaker|audio/.test(heading)) return { icon: ICONS.speaker, tint: "bg-emerald-50 text-emerald-600" };
  if (/power|battery|charger/.test(heading)) return { icon: ICONS.power, tint: "bg-sky-50 text-sky-600" };
  if (/field|device|detector|duct/.test(heading)) return { icon: ICONS.sensor, tint: "bg-rose-50 text-rose-600" };
  if (group.assembly) return { icon: ICONS.panel, tint: "bg-violet-50 text-violet-600" };
  return { icon: ICONS.list, tint: "bg-gray-100 text-gray-600" };
}

function TypeBadge({ label, tone }: { label: string; tone: "assembly" | "component" | "item" | "group" }) {
  const tones = {
    assembly: "bg-violet-50 text-violet-700 ring-violet-200",
    group: "bg-indigo-50 text-indigo-700 ring-indigo-200",
    component: "bg-sky-50 text-sky-700 ring-sky-200",
    item: "bg-gray-100 text-gray-600 ring-gray-200",
  };
  return (
    <span className={`inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${tones[tone]}`}>
      {label}
    </span>
  );
}

// --- the view ------------------------------------------------------------------------------

interface Props {
  groups: BoqGroup[];
  /** Indices of the lines that pass the filters. */
  visible: Set<number>;
  filtering: boolean;
  isOpen: (key: string) => boolean;
  onToggle: (key: string) => void;
  canEdit: boolean;
  duplicates: Set<number>;
  quantityProblems: Map<number, string | null>;
  editingKey: string | null;
  onEdit: (key: string | null) => void;
  onUpdate: (index: number, patch: Partial<ProjectBoqItemInput>) => void;
  onRemove: (index: number) => void;
  fields: BoqFieldSpec[];
  showPrices: boolean;
  /** A column nobody has filled in is only in the way: shown once a line has a unit. */
  showUnits: boolean;
  /** The line's source status and AI check, as the page draws them. */
  renderStatus: (item: IndexedRow) => ReactNode;
  /** What the sheet said for a field an engineer has since changed. */
  sheetValue: (item: IndexedRow, field: BoqField) => string | null | undefined;
  needsAttention: (item: IndexedRow) => boolean;
}

export function BoqGroupedView(props: Props) {
  const { groups, visible, filtering, isOpen, onToggle, showPrices, showUnits, canEdit } = props;
  // Item, type, manufacturer, part no., qty, [unit], [unit price, total], status, actions.
  const columnCount = 6 + (showUnits ? 1 : 0) + (showPrices ? 2 : 0) + (canEdit ? 1 : 0);
  const shown = groups.filter((group) => group.lines.some(({ index }) => visible.has(index)));

  if (shown.length === 0) {
    return (
      <div className="mt-4 rounded-xl border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-400">
        No lines match the filter.
      </div>
    );
  }

  return (
    <div className="mt-3 hidden overflow-x-auto rounded-2xl border border-gray-200 bg-white md:block">
      <table className="w-full min-w-[1080px] table-fixed text-sm">
        <caption className="sr-only">BOQ lines grouped by the Design Sheet's headings</caption>
        <thead className="border-b border-gray-200 bg-gray-50/80 text-left text-xs font-semibold text-gray-500">
          <tr>
            <th scope="col" className="px-4 py-3">Item</th>
            <th scope="col" className="w-28 px-3 py-3">Type</th>
            <th scope="col" className="w-28 px-3 py-3">Manufacturer</th>
            <th scope="col" className="w-40 px-3 py-3">Model / Part No.</th>
            <th scope="col" className="w-32 px-3 py-3 text-right">Qty</th>
            {showUnits && (
              <th scope="col" className="w-16 px-3 py-3">
                Unit
              </th>
            )}
            {showPrices && (
              <>
                <th scope="col" className="w-24 px-3 py-3 text-right">Unit Price</th>
                <th scope="col" className="w-28 px-3 py-3 text-right">Total Price</th>
              </>
            )}
            <th scope="col" className="w-48 px-3 py-3">Status</th>
            {canEdit && (
              <th scope="col" className="w-20 px-3 py-3">
                <span className="sr-only">Actions</span>
              </th>
            )}
          </tr>
        </thead>
        {shown.map((group) => (
          <GroupRows
            {...props}
            key={group.key}
            group={group}
            open={filtering || isOpen(group.key)}
            onToggleGroup={() => onToggle(group.key)}
            columnCount={columnCount}
          />
        ))}
      </table>
    </div>
  );
}

function GroupRows({
  group,
  open,
  onToggleGroup: onToggle,
  columnCount,
  ...props
}: Props & { group: BoqGroup; open: boolean; onToggleGroup: () => void; columnCount: number }) {
  const { visible, duplicates, quantityProblems, showPrices, showUnits, canEdit, needsAttention } = props;
  const lines = group.lines.filter(({ index }) => visible.has(index));
  const { icon, tint } = groupIcon(group);
  const components = group.lines.length - (group.assembly ? 1 : 0);
  const makers = [...new Set(group.lines.map(({ row }) => (row.manufacturer ?? "").trim()).filter(Boolean))];
  const toCheck = group.lines.filter((item) => needsAttention(item)).length;
  const units = group.lines.reduce((sum, { row }) => sum + (numeric(row.quantity) ?? 0), 0);
  const title = group.heading || "Lines without a group";
  const subtitle = group.assembly
    ? group.assembly.row.description
    : `${group.lines.length} line${group.lines.length === 1 ? "" : "s"}${group.heading ? "" : " with no heading on the sheet"}`;

  return (
    <tbody className="border-b border-gray-100 last:border-b-0">
      <tr className="cursor-pointer bg-white hover:bg-gray-50/70" onClick={onToggle}>
        <td className="px-4 py-3">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onToggle();
              }}
              aria-expanded={open}
              aria-label={`${open ? "Collapse" : "Expand"} ${title}`}
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-gray-500 hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            >
              <Glyph className={`h-4 w-4 transition-transform ${open ? "rotate-90" : ""}`}>{ICONS.chevron}</Glyph>
            </button>
            <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${tint}`}>
              <Glyph>{icon}</Glyph>
            </span>
            <span className="min-w-0">
              <span className="block truncate font-bold text-navy-900" title={title}>
                {title}
              </span>
              {/* Closed, the assembly's own words say what is inside; open,
                  its line is right below and saying it twice is noise. */}
              {!(open && group.assembly) && (
                <span className="block truncate text-xs text-gray-500" title={subtitle}>
                  {subtitle}
                </span>
              )}
            </span>
          </div>
        </td>
        <td className="px-3 py-3">
          <TypeBadge label={group.assembly ? "Assembly" : "Group"} tone={group.assembly ? "assembly" : "group"} />
        </td>
        <td className="truncate px-3 py-3 text-gray-700" title={makers.join(", ") || undefined}>
          {makers.length === 1 ? makers[0] : makers.length > 1 ? "Mixed" : "—"}
        </td>
        <td className="truncate px-3 py-3 font-medium text-navy-900">{group.assembly?.row.catalog_no?.trim() || "—"}</td>
        <td className="px-3 py-3 text-right">
          {group.assembly ? (
            <>
              <span className="block font-bold tabular-nums text-navy-900">{group.assembly.row.quantity ?? "—"}</span>
              <span className="block whitespace-nowrap text-xs text-gray-400">
                ({components} component{components === 1 ? "" : "s"})
              </span>
            </>
          ) : (
            <>
              <span className="block font-bold tabular-nums text-navy-900">{units.toLocaleString()}</span>
              <span className="block whitespace-nowrap text-xs text-gray-400">
                ({group.lines.length} line{group.lines.length === 1 ? "" : "s"})
              </span>
            </>
          )}
        </td>
        {showUnits && <td className="px-3 py-3 text-gray-500">{group.assembly?.row.unit ?? ""}</td>}
        {showPrices && <td colSpan={2} />}
        <td className="px-3 py-3">
          {toCheck > 0 ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-800 ring-1 ring-inset ring-amber-200">
              {toCheck} to check
            </span>
          ) : null}
        </td>
        {canEdit && <td />}
      </tr>

      {open &&
        lines.map((item, position) => {
          const { row, index } = item;
          const editing = props.editingKey === row.key;
          const duplicate = duplicates.has(index);
          const problem = quantityProblems.get(index);
          const last = position === lines.length - 1;
          const isAssembly = group.assembly?.index === index;
          const corrected = (field: BoqField) => props.sheetValue(item, field);
          return (
            <Fragment key={row.key}>
              <tr
                className={`group/line ${duplicate ? "bg-amber-50/70" : editing ? "bg-brand-50/40" : "bg-white hover:bg-gray-50/60"}`}
              >
                <td className="py-2 pl-4 pr-3">
                  <div className="flex items-start gap-2 pl-9">
                    <span className="select-none font-mono text-gray-300" aria-hidden="true">
                      {last ? "└─" : "├─"}
                    </span>
                    <span className="min-w-0">
                      <span
                        className={`line-clamp-2 ${isAssembly ? "font-semibold text-navy-900" : "text-navy-900"}`}
                        title={row.description || undefined}
                      >
                        {row.description || <span className="italic text-gray-400">No description</span>}
                        <Changed value={corrected("description")} />
                      </span>
                      {row.remarks && <span className="block text-xs text-gray-500">{row.remarks}</span>}
                    </span>
                  </div>
                </td>
                <td className="px-3 py-2">
                  <TypeBadge
                    label={isAssembly ? "Assembly" : group.assembly ? "Component" : "Item"}
                    tone={isAssembly ? "assembly" : group.assembly ? "component" : "item"}
                  />
                </td>
                <td className="truncate px-3 py-2 text-gray-700" title={row.manufacturer ?? undefined}>
                  {row.manufacturer || <span className="text-gray-300">—</span>}
                </td>
                <td className="truncate px-3 py-2 font-medium text-navy-900" title={row.catalog_no ?? undefined}>
                  {row.catalog_no || <span className="font-normal text-gray-300">—</span>}
                  <Changed value={corrected("catalog_no")} />
                </td>
                <td className={`px-3 py-2 text-right tabular-nums ${problem ? "font-semibold text-red-700" : "text-navy-900"}`}>
                  {row.quantity || <span className="text-gray-300">—</span>}
                  <Changed value={corrected("quantity")} />
                </td>
                {showUnits && <td className="px-3 py-2 text-gray-500">{row.unit}</td>}
                {showPrices && (
                  <>
                    <td className="px-3 py-2 text-right tabular-nums text-gray-700">{row.unit_price}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-gray-700">{row.total_price}</td>
                  </>
                )}
                <td className="px-3 py-2">
                  <div className="flex flex-wrap items-center gap-1">
                    {props.renderStatus(item)}
                    {duplicate && (
                      <span
                        className="inline-flex items-center gap-1 whitespace-nowrap rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-800"
                        title="The same item is listed again under this heading"
                      >
                        ⚠ Possible duplicate
                      </span>
                    )}
                    {problem && (
                      <span className="whitespace-nowrap rounded-full bg-red-50 px-2 py-0.5 text-[11px] font-semibold text-red-700" title={problem}>
                        Check quantity
                      </span>
                    )}
                  </div>
                </td>
                {canEdit && (
                  <td className="px-3 py-2">
                    <div className="flex items-center justify-end gap-0.5 opacity-60 transition-opacity group-hover/line:opacity-100 focus-within:opacity-100">
                      <button
                        type="button"
                        onClick={() => props.onEdit(editing ? null : row.key)}
                        aria-label={`${editing ? "Close" : "Edit"} ${row.catalog_no || row.description || "line"}`}
                        aria-expanded={editing}
                        className={`rounded-md p-1.5 hover:bg-brand-50 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${
                          editing ? "bg-brand-50 text-brand-700" : "text-gray-500"
                        }`}
                      >
                        <Glyph className="h-4 w-4">{ICONS.edit}</Glyph>
                      </button>
                      <button
                        type="button"
                        onClick={() => props.onRemove(index)}
                        aria-label={`Remove ${row.catalog_no || row.description || "line"}`}
                        className="rounded-md p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
                      >
                        <Glyph className="h-4 w-4">{ICONS.close}</Glyph>
                      </button>
                    </div>
                  </td>
                )}
              </tr>
              {editing && (
                <tr className="bg-brand-50/40">
                  <td colSpan={columnCount} className="px-4 pb-4 pt-1">
                    <LineEditor {...props} item={item} problem={problem ?? null} duplicate={duplicate} />
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
    </tbody>
  );
}

/** A small mark on a value an engineer changed, carrying what the sheet said. */
function Changed({ value }: { value: string | null | undefined }) {
  if (value === undefined) return null;
  return (
    <span
      className="ml-1.5 inline-block h-1.5 w-1.5 rounded-full bg-violet-500 align-middle"
      title={`Changed by an engineer. Read from the sheet as: ${value ?? "(blank)"}`}
      aria-label={`changed; the sheet said ${value ?? "nothing"}`}
    />
  );
}

/** Every field of one line, laid out to be read while it is edited. Edits
 * apply as they are typed, exactly as in the table view; Save keeps them. */
function LineEditor({
  item,
  fields,
  problem,
  duplicate,
  canEdit,
  onUpdate,
  onEdit,
  sheetValue,
}: Props & { item: IndexedRow; problem: string | null; duplicate: boolean }) {
  const { row, index } = item;
  const wide = new Set<BoqField>(["description", "remarks"]);
  return (
    <div
      className="rounded-xl border border-brand-200 bg-white p-4 shadow-sm"
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === "Escape") {
          event.preventDefault();
          onEdit(null);
        }
      }}
    >
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {fields.map((field) => {
          const sheet = sheetValue(item, field.key);
          return (
            <label
              key={field.key}
              className={`block text-xs font-medium text-gray-500 ${wide.has(field.key) ? "col-span-2" : ""}`}
            >
              {field.label}
              <input
                value={row[field.key] ?? ""}
                disabled={!canEdit}
                placeholder={field.placeholder}
                autoFocus={field.key === "description"}
                inputMode={field.align === "right" ? "decimal" : undefined}
                aria-invalid={field.key === "quantity" && Boolean(problem)}
                onChange={(event) =>
                  onUpdate(index, {
                    [field.key]: field.key === "description" ? event.target.value : event.target.value || null,
                  })
                }
                className={`input mt-1 py-1.5 text-sm text-navy-900 ${field.align === "right" ? "text-right" : ""} ${
                  field.key === "quantity" && problem ? "border-red-400 bg-red-50" : ""
                } ${sheet !== undefined ? "border-violet-300" : ""}`}
              />
              {field.key === "quantity" && problem && <span className="mt-0.5 block text-[11px] text-red-700">{problem}</span>}
              {sheet !== undefined && (
                <span className="mt-0.5 block text-[11px] text-violet-700">Sheet: {sheet ?? "(blank)"}</span>
              )}
              {field.key === "description" && duplicate && (
                <span className="mt-0.5 block text-[11px] text-amber-700">Possible duplicate: same item listed again under this group</span>
              )}
            </label>
          );
        })}
      </div>
      <div className="mt-3 flex items-center justify-between gap-3">
        <span className="text-xs text-gray-400">Changes apply as you type and are kept when you save. Enter or Esc closes.</span>
        <button
          type="button"
          onClick={() => onEdit(null)}
          className="rounded-lg bg-brand-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-brand-700"
        >
          Done
        </button>
      </div>
    </div>
  );
}
