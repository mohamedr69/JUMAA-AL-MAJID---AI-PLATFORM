import { useMemo, useState } from "react";
import { ApiError, api } from "../lib/api";
import type { ProjectBoqItemInput } from "../lib/types";

/** A line as the server read it, with what is worth a look before it is saved. */
type PastedLine = ProjectBoqItemInput & { problems: string[] };

type Pasted = {
  lines: PastedLine[];
  columns: Record<string, number>;
  headings: string[];
  header_row: boolean;
  heading_rows: number;
  skipped_rows: number;
};

/** The fields a column can hold, in the order a BOQ puts them. */
const FIELDS: { key: string; label: string }[] = [
  { key: "description", label: "Description" },
  { key: "catalog_no", label: "Model / Part No." },
  { key: "manufacturer", label: "Manufacturer" },
  { key: "quantity", label: "Qty" },
  { key: "unit", label: "Unit" },
  { key: "unit_price", label: "Unit rate" },
  { key: "total_price", label: "Total" },
  { key: "remarks", label: "Remarks" },
];

const EXAMPLE = [
  "Description\tPart No\tQty\tUnit",
  "MAIN FIRE ALARM CONTROL PANEL",
  "Central Processor Unit\t4-CPU\t1\tNos",
  "Loop Card\t3-SDDC2\t4\tNos",
].join("\n");

/** Paste a BOQ straight out of a spreadsheet.
 *
 * For the project whose BOQ never came as a Design Sheet: the engineer
 * copies the range in Excel and pastes it here rather than typing a
 * hundred lines one at a time. The server reads it -- headings carried
 * down the lines under them, quantities through the same parser the
 * extractor uses -- and hands back what it made of it.
 *
 * **Nothing is saved here.** The lines go onto the BOQ page as unsaved
 * changes, to be looked over and saved with everything else, so a paste
 * read wrongly costs a glance and not a revision. */
export function BoqPasteDialog({
  projectId,
  systems,
  system,
  onAdd,
  onCancel,
}: {
  projectId: number;
  systems: string[];
  system: string | null;
  onAdd: (lines: ProjectBoqItemInput[]) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState("");
  const [chosenSystem, setChosenSystem] = useState<string | null>(system);
  const [pasted, setPasted] = useState<Pasted | null>(null);
  const [columns, setColumns] = useState<Record<string, number> | null>(null);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");

  /** The columns of the block, so a mis-read one can be pointed elsewhere. */
  const width = useMemo(() => {
    const rows = text.split("\n").filter((row) => row.trim());
    const delimiter = rows.some((row) => row.includes("\t")) ? "\t" : ",";
    return rows.reduce((widest, row) => Math.max(widest, row.split(delimiter).length), 0);
  }, [text]);

  async function read(mapping?: Record<string, number>) {
    if (!text.trim()) return;
    setReading(true);
    setError("");
    try {
      const body: Record<string, unknown> = { text, system_code: chosenSystem };
      if (mapping) body.columns = mapping;
      const out = await api.post<Pasted>(`/projects/${projectId}/boq/paste`, body);
      setPasted(out);
      setColumns(out.columns);
    } catch (e) {
      setPasted(null);
      setError(e instanceof ApiError ? e.message : "The paste could not be read");
    } finally {
      setReading(false);
    }
  }

  /** Point a field at another column, and read the block again by it. */
  function remap(field: string, column: number | null) {
    const next = { ...(columns ?? {}) };
    if (column === null) delete next[field];
    else next[field] = column;
    setColumns(next);
    void read(next);
  }

  const flagged = pasted?.lines.filter((line) => line.problems.length) ?? [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-navy-950/40 p-4 sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-labelledby="paste-boq-title"
    >
      <div className="flex max-h-full w-full max-w-5xl flex-col overflow-hidden rounded-2xl bg-white shadow-xl">
        <div className="border-b border-gray-200 px-6 py-4">
          <h2 id="paste-boq-title" className="text-lg font-bold text-navy-900">Paste a BOQ from Excel</h2>
          <p className="mt-1 text-sm text-gray-600">
            Copy the rows in your spreadsheet and paste them below. A row with words and no quantity is
            taken as a group heading and carried down the lines under it. Nothing is saved until you save
            the BOQ.
          </p>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          <textarea
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setPasted(null);
            }}
            onBlur={() => !pasted && void read()}
            rows={pasted ? 4 : 10}
            spellCheck={false}
            placeholder={EXAMPLE}
            className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs text-navy-900 focus:border-brand-500 focus:outline-none"
          />

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <label className="text-sm text-gray-700">
              System
              <select
                value={chosenSystem ?? ""}
                onChange={(e) => setChosenSystem(e.target.value || null)}
                className="ml-2 rounded-lg border border-gray-300 px-2 py-1 text-sm"
              >
                <option value="">Leave unassigned</option>
                {systems.map((code) => (
                  <option key={code} value={code}>{code}</option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => void read(columns ?? undefined)}
              disabled={!text.trim() || reading}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-50"
            >
              {reading ? "Reading..." : pasted ? "Read again" : "Read the paste"}
            </button>
          </div>

          {error && <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

          {pasted && (
            <>
              <div className="mt-4 rounded-lg bg-gray-50 px-3 py-2 text-sm text-gray-700">
                <span className="font-semibold text-navy-900">{pasted.lines.length}</span> line
                {pasted.lines.length === 1 ? "" : "s"}
                {pasted.heading_rows > 0 && <> under <span className="font-semibold text-navy-900">{pasted.heading_rows}</span> heading{pasted.heading_rows === 1 ? "" : "s"}</>}
                {pasted.header_row && <> · the first row was read as the column names</>}
                {pasted.skipped_rows > 0 && <> · {pasted.skipped_rows} row{pasted.skipped_rows === 1 ? "" : "s"} left out</>}
                {flagged.length > 0 && (
                  <> · <span className="font-semibold text-amber-700">{flagged.length} to check</span></>
                )}
              </div>

              {width > 0 && (
                <div className="mt-3">
                  <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Columns</div>
                  <div className="mt-1 flex flex-wrap gap-2">
                    {FIELDS.map((field) => (
                      <label key={field.key} className="flex items-center gap-1 rounded-lg border border-gray-200 px-2 py-1 text-xs text-gray-700">
                        {field.label}
                        <select
                          value={columns?.[field.key] ?? ""}
                          onChange={(e) => remap(field.key, e.target.value === "" ? null : Number(e.target.value))}
                          className="rounded border border-gray-300 px-1 py-0.5 text-xs"
                        >
                          <option value="">—</option>
                          {Array.from({ length: width }, (_, i) => (
                            <option key={i} value={i}>{i + 1}</option>
                          ))}
                        </select>
                      </label>
                    ))}
                  </div>
                </div>
              )}

              <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
                <table className="min-w-full text-sm">
                  <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                    <tr>
                      <th className="px-3 py-2">Group</th>
                      <th className="px-3 py-2">Description</th>
                      <th className="px-3 py-2">Part no.</th>
                      <th className="px-3 py-2 text-right">Qty</th>
                      <th className="px-3 py-2">Unit</th>
                      <th className="px-3 py-2">To check</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {pasted.lines.map((line, index) => (
                      <tr key={index} className={line.problems.length ? "bg-amber-50" : undefined}>
                        <td className="px-3 py-1.5 text-xs text-gray-500">{line.group_heading ?? "—"}</td>
                        <td className="px-3 py-1.5 text-navy-900">{line.description || "—"}</td>
                        <td className="px-3 py-1.5 font-mono text-xs">{line.catalog_no ?? "—"}</td>
                        <td className="px-3 py-1.5 text-right">{line.quantity ?? "—"}</td>
                        <td className="px-3 py-1.5 text-xs">{line.unit ?? "—"}</td>
                        <td className="px-3 py-1.5 text-xs text-amber-800">{line.problems.join("; ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {pasted.lines.length === 0 && (
                <p className="mt-2 text-sm text-gray-600">
                  No lines were read. Check the columns above, or paste the rows with their header.
                </p>
              )}
            </>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-gray-200 px-6 py-3">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => pasted && onAdd(pasted.lines.map(({ problems: _problems, ...line }) => line))}
            disabled={!pasted || pasted.lines.length === 0}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50"
          >
            Add {pasted?.lines.length ?? 0} line{pasted?.lines.length === 1 ? "" : "s"}
          </button>
        </div>
      </div>
    </div>
  );
}
