import type { ProjectBoqItemInput } from "./types";

/** Letters and digits only, lower-cased. OCR and hand entry disagree on
 * spacing and punctuation ("/ SIGA-CT2" against "SIGA-CT2"), which is not a
 * difference in the item. */
function normalize(text: string | null | undefined): string {
  return (text ?? "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

/**
 * Indices of lines that look like one item entered twice: same system, same
 * group heading, same catalog number and same description. Quantity is left
 * out -- a duplicate is as likely to carry a different count as the same one.
 *
 * Narrow on purpose. The same part legitimately recurs across a BOQ, since
 * every panel has its own CPU and batteries: on the three real projects a
 * catalog-number match flags 93 of 279 lines, nearly all correctly listed,
 * and a flag that is usually wrong gets ignored. This key flags 3 pairs
 * there, each an item listed twice under one heading.
 *
 * Same key as line_key in backend/app/services/boq_revisions.py, which
 * matches lines across BOQ revisions. Change one and look at the other.
 */
export function possibleDuplicates(rows: ProjectBoqItemInput[]): Set<number> {
  const byKey = new Map<string, number[]>();
  rows.forEach((row, index) => {
    const description = normalize(row.description);
    if (!description) return;
    const key = [
      row.system_code ?? "",
      normalize(row.group_heading),
      normalize(row.catalog_no),
      description,
    ].join("|");
    byKey.set(key, [...(byKey.get(key) ?? []), index]);
  });
  return new Set([...byKey.values()].filter((indices) => indices.length > 1).flat());
}

export interface QuantityTotals {
  lines: number;
  /** Sum of the numeric quantities. */
  units: number;
  /** Lines quoted as a word rather than a number, by word: { lot: 2 }. */
  byWord: Record<string, number>;
  /** Sum of the total prices entered, or null if none are. */
  totalPrice: number | null;
}

function toNumber(text: string | null | undefined): number | null {
  const cleaned = (text ?? "").replace(/[,\s]/g, "");
  return /^\d+(\.\d+)?$/.test(cleaned) ? Number(cleaned) : null;
}

export function quantityTotals(rows: ProjectBoqItemInput[]): QuantityTotals {
  const totals: QuantityTotals = { lines: rows.length, units: 0, byWord: {}, totalPrice: null };
  for (const row of rows) {
    const quantity = toNumber(row.quantity);
    if (quantity !== null) {
      totals.units += quantity;
    } else if (row.quantity?.trim()) {
      const word = row.quantity.trim().toLowerCase();
      totals.byWord[word] = (totals.byWord[word] ?? 0) + 1;
    }
    const price = toNumber(row.total_price);
    if (price !== null) totals.totalPrice = (totals.totalPrice ?? 0) + price;
  }
  return totals;
}

export function matchesFilter(row: ProjectBoqItemInput, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [row.group_heading, row.manufacturer, row.catalog_no, row.description, row.unit, row.remarks].some(
    (value) => (value ?? "").toLowerCase().includes(needle)
  );
}
