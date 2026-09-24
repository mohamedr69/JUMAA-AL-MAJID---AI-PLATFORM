import { useMemo, useState } from "react";

/** A shop drawing at one revision, as the log shows it. */
export type RevisionCell = {
  revision: string;
  status: string;
  label: string;
  path: string;
  page: number;
  name: string;
  remarks: string | null;
};

export type UnplacedDrawing = {
  reference: string;
  revision: string;
  floor_named: string | null;
  status: string;
  label: string;
  path: string;
  page: number;
  name: string;
  remarks?: string | null;
  /** Every revision this drawing went through, keyed R0, R1, R2... */
  revisions?: Record<string, RevisionCell>;
  /** How many floors this one drawing stands for. */
  floors?: number;
};

const CHIP: Record<string, string> = {
  approved: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  approved_as_noted: "bg-cyan-50 text-cyan-800 ring-cyan-200",
  under_review: "bg-amber-50 text-amber-800 ring-amber-200",
  not_approved: "bg-rose-50 text-rose-700 ring-rose-200",
  superseded: "bg-slate-100 text-slate-600 ring-slate-200",
  not_submitted: "bg-gray-100 text-gray-600 ring-gray-200",
};

/** What kind of level this is, from the floor the drawing names.
 *
 *  A building's drawings sort and filter by the part of the building they
 *  belong to long before anyone cares about the floor number: every
 *  basement together, then the podium, then the typical floors. */
function floorType(floor: string | null): string {
  const name = (floor ?? "").toUpperCase();
  if (!name) return "Unnamed";
  if (/BASEMENT|^B\s*\d|^BSM/.test(name)) return "Basement";
  if (/GROUND|^GF|^BGF/.test(name)) return "Ground";
  if (/PODIUM|^P\s*\d/.test(name)) return "Podium";
  if (/MEZZ/.test(name)) return "Mezzanine";
  if (/ROOF/.test(name)) return "Roof";
  // A typical floor is a *numbered* one: "L01", "LEVEL 3", "22ND FLOOR".
  // Matching the bare word FLOOR put "LIFT MACHINE ROOM FLOOR PLAN" among
  // the levels -- and with no number in it, above every one of them.
  if (/^L\s*\d|^LEVEL\s*\d|\d+\s*(?:ST|ND|RD|TH)?\s*FLOOR/.test(name)) return "Typical";
  return "M&E";
}

const TYPE_CHIP: Record<string, string> = {
  Basement: "bg-purple-50 text-purple-700 ring-purple-200",
  Ground: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  Podium: "bg-blue-50 text-blue-700 ring-blue-200",
  Typical: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  Mezzanine: "bg-teal-50 text-teal-700 ring-teal-200",
  Roof: "bg-orange-50 text-orange-700 ring-orange-200",
  "M&E": "bg-amber-50 text-amber-700 ring-amber-200",
  Unnamed: "bg-gray-100 text-gray-600 ring-gray-200",
};

/** The block of the building, off the drawing number: the segment before
 *  the floor in "...-FP-FA-BSM-B01-010001". */
function block(reference: string): string {
  const parts = reference.split("-");
  return parts.length >= 3 ? parts[parts.length - 3] : "—";
}

/** Floors in building order -- basements down, then up through the tower --
 *  rather than alphabetically, where B01 sorts above GROUND FLOOR. */
const TYPE_ORDER = ["Basement", "Ground", "Mezzanine", "Podium", "Typical", "M&E", "Roof", "Unnamed"];

function floorOrder(row: UnplacedDrawing): [number, number, string] {
  const name = (row.floor_named ?? "").toUpperCase();
  const type = floorType(row.floor_named);
  const digits = name.match(/\d+/);
  const n = digits ? Number(digits[0]) : 0;
  // Basements count downwards: B04 is below B01.
  return [TYPE_ORDER.indexOf(type), type === "Basement" ? -n : n, name];
}

function revisionNumber(revision: string): number {
  const digits = (revision || "").match(/\d+/);
  return digits ? Number(digits[0]) : 0;
}

function Chip({ status, label, title }: { status: string; label: string; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset ${
        CHIP[status] ?? CHIP.not_submitted
      }`}
    >
      {label}
    </span>
  );
}

function Stat({ value, label, tone }: { value: number; label: string; tone: string }) {
  return (
    <div className={`rounded-xl border px-4 py-3 ${tone}`}>
      <div className="text-2xl font-bold leading-tight">{value}</div>
      <div className="text-xs font-medium">{label}</div>
    </div>
  );
}

/** The shop drawings whose floor no IFC plan has.
 *
 *  One row per drawing, at the revision that stands, with every revision
 *  it went through beside it: R0 next to R1 next to R2, each saying what
 *  it came back as. Before this they were a flat list, one line per
 *  revision, so a floor that had been round once appeared twice. */
export function UnplacedDrawingsTable({
  rows,
  fileHref,
}: {
  rows: UnplacedDrawing[];
  fileHref: (path: string, page: number) => string;
}) {
  const [search, setSearch] = useState("");
  const [blockFilter, setBlockFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sort, setSort] = useState<"floor" | "reference" | "status">("floor");

  /** Every revision column the drawings between them reach, R0 first. */
  const columns = useMemo(() => {
    const seen = new Set<string>();
    for (const row of rows) for (const revision of Object.keys(row.revisions ?? {})) seen.add(revision);
    if (!seen.size) for (const row of rows) seen.add(row.revision);
    return Array.from(seen).sort((a, b) => revisionNumber(a) - revisionNumber(b));
  }, [rows]);

  const blocks = useMemo(
    () => Array.from(new Set(rows.map((r) => block(r.reference)))).sort(),
    [rows],
  );
  const types = useMemo(
    () => TYPE_ORDER.filter((t) => rows.some((r) => floorType(r.floor_named) === t)),
    [rows],
  );
  const statuses = useMemo(
    () => Array.from(new Set(rows.map((r) => r.status))),
    [rows],
  );

  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const found = rows.filter((row) => {
      if (blockFilter !== "all" && block(row.reference) !== blockFilter) return false;
      if (typeFilter !== "all" && floorType(row.floor_named) !== typeFilter) return false;
      if (statusFilter !== "all" && row.status !== statusFilter) return false;
      if (!needle) return true;
      return [row.floor_named, row.reference, row.name].some((v) =>
        (v ?? "").toLowerCase().includes(needle),
      );
    });
    return found.sort((a, b) => {
      if (sort === "reference") return a.reference.localeCompare(b.reference);
      if (sort === "status") return a.label.localeCompare(b.label) || a.reference.localeCompare(b.reference);
      const [at, an, aname] = floorOrder(a);
      const [bt, bn, bname] = floorOrder(b);
      return at - bt || an - bn || aname.localeCompare(bname);
    });
  }, [rows, search, blockFilter, typeFilter, statusFilter, sort]);

  const counts = useMemo(() => {
    const tally: Record<string, number> = {};
    for (const row of rows) tally[row.status] = (tally[row.status] ?? 0) + 1;
    return tally;
  }, [rows]);

  /** The rows as they are shown, for a spreadsheet. */
  function exportCsv() {
    const head = ["Floor", "Floor type", "Block", "Drawing reference", ...columns, "Latest", "Status", "Notes"];
    const body = shown.map((row) => [
      row.floor_named ?? "",
      floorType(row.floor_named),
      block(row.reference),
      row.reference,
      ...columns.map((rev) => row.revisions?.[rev]?.label ?? ""),
      row.revision,
      row.label,
      row.remarks ?? "",
    ]);
    const csv = [head, ...body]
      .map((line) => line.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(","))
      .join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "shop-drawings.csv";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="border-t border-gray-100 px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-bold text-navy-900">
            Shop drawings on no IFC floor ({rows.length})
          </h3>
          <p className="mt-0.5 max-w-xl text-xs text-gray-500">
            Their floor is not named on the drawing, or no IFC floor plan has it: listed here, not
            dropped. One row per drawing, at the revision that stands.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <div className="flex flex-wrap gap-2">
          <Stat value={rows.length} label="Drawings" tone="border-blue-100 bg-blue-50 text-blue-800" />
          <Stat
            value={counts.not_approved ?? 0}
            label="Not Approved"
            tone="border-rose-100 bg-rose-50 text-rose-700"
          />
          <Stat
            value={counts.under_review ?? 0}
            label="Under Review"
            tone="border-amber-100 bg-amber-50 text-amber-800"
          />
          <Stat
            value={(counts.approved ?? 0) + (counts.approved_as_noted ?? 0)}
            label="Approved"
            tone="border-emerald-100 bg-emerald-50 text-emerald-800"
          />
          </div>
          <p className="text-[11px] text-gray-500">
            Counted at the revision that stands, so a drawing answered at R0 and resubmitted is under
            review here.
          </p>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-end gap-2">
        <label className="flex-1 min-w-[15rem] text-xs font-medium text-gray-600">
          Search
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Floor (L01, B01, PODIUM) or drawing number..."
            className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-navy-900 focus:border-brand-500 focus:outline-none"
          />
        </label>
        <Select label="Block" value={blockFilter} onChange={setBlockFilter} options={blocks} />
        <Select label="Floor type" value={typeFilter} onChange={setTypeFilter} options={types} />
        <Select
          label="Status"
          value={statusFilter}
          onChange={setStatusFilter}
          options={statuses}
          labelFor={(status) => rows.find((r) => r.status === status)?.label ?? status}
        />
        <label className="text-xs font-medium text-gray-600">
          Sort by
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as typeof sort)}
            className="mt-1 block rounded-lg border border-gray-300 px-2 py-2 text-sm"
          >
            <option value="floor">Floor (building order)</option>
            <option value="reference">Drawing number</option>
            <option value="status">Status</option>
          </select>
        </label>
        <button
          type="button"
          onClick={exportCsv}
          className="rounded-lg border border-gray-300 px-3 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50"
        >
          Export
        </button>
      </div>

      <div className="mt-3 overflow-x-auto rounded-xl border border-gray-200">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
            <tr>
              <th className="px-3 py-2">Floor</th>
              <th className="px-3 py-2">Type</th>
              <th className="px-3 py-2">Drawing reference</th>
              {columns.map((revision) => (
                <th key={revision} className="px-3 py-2 text-center">{revision}</th>
              ))}
              <th className="px-3 py-2">Latest</th>
              <th className="px-3 py-2">Notes</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {shown.map((row) => {
              const type = floorType(row.floor_named);
              return (
                <tr key={`${row.reference}|${row.revision}`} className="hover:bg-gray-50">
                  <td className="px-3 py-2 font-semibold text-navy-900">
                    {row.floor_named ?? <span className="font-normal text-gray-400">no floor named</span>}
                    {(row.floors ?? 1) > 1 && (
                      <span className="ml-2 rounded bg-gray-100 px-1.5 py-0.5 text-[11px] font-medium text-gray-600">
                        {row.floors} floors
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
                        TYPE_CHIP[type] ?? TYPE_CHIP.Unnamed
                      }`}
                    >
                      {type}
                    </span>
                  </td>
                  <td
                    className="max-w-[16rem] truncate px-3 py-2 font-mono text-xs text-gray-700"
                    title={row.reference}
                  >
                    {row.reference}
                  </td>
                  {columns.map((revision) => {
                    const cell = row.revisions?.[revision];
                    return (
                      <td key={revision} className="px-3 py-2 text-center">
                        {cell ? (
                          <a
                            href={fileHref(cell.path, cell.page)}
                            target="_blank"
                            rel="noreferrer"
                            title={cell.remarks ?? cell.name}
                          >
                            <Chip status={cell.status} label={cell.label} />
                          </a>
                        ) : (
                          <span className="text-gray-300">—</span>
                        )}
                      </td>
                    );
                  })}
                  <td className="whitespace-nowrap px-3 py-2 font-semibold text-navy-900">{row.revision}</td>
                  <td className="px-3 py-2 text-xs text-gray-500">{row.remarks ?? "—"}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-right">
                    <a
                      href={fileHref(row.path, row.page)}
                      target="_blank"
                      rel="noreferrer"
                      className="font-semibold text-brand-600 hover:underline"
                    >
                      View
                    </a>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {shown.length === 0 && (
          <p className="px-3 py-6 text-center text-sm text-gray-500">
            No drawing matches these filters.
          </p>
        )}
      </div>
      {shown.length !== rows.length && (
        <p className="mt-2 text-xs text-gray-500">
          Showing {shown.length} of {rows.length} drawings
        </p>
      )}
    </div>
  );
}

function Select({
  label,
  value,
  onChange,
  options,
  labelFor,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: string[];
  labelFor?: (option: string) => string;
}) {
  return (
    <label className="text-xs font-medium text-gray-600">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 block rounded-lg border border-gray-300 px-2 py-2 text-sm"
      >
        <option value="all">All</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {labelFor ? labelFor(option) : option}
          </option>
        ))}
      </select>
    </label>
  );
}
