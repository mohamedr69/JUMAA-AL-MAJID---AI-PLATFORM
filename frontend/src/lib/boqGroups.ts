import type { ProjectBoqItemInput } from "./types";

/** A BOQ line as the page holds it, with the key React tracks it by. */
export interface BoqViewRow extends ProjectBoqItemInput {
  key: string;
}

/** A line and its place in the page's full list, which edits address. */
export interface IndexedRow {
  row: BoqViewRow;
  index: number;
}

export type BoqField = Exclude<keyof ProjectBoqItemInput, "system_code" | "id">;

export interface BoqFieldSpec {
  key: BoqField;
  label: string;
  placeholder?: string;
  align?: "right";
}

/** The lines under one heading of the Design Sheet. */
export interface BoqGroup {
  key: string;
  /** The sheet's own heading; empty for lines it gave none. */
  heading: string;
  /** Every line of the group on this tab, in the sheet's order. */
  lines: IndexedRow[];
  /** The line naming the assembly the rest are part of, when there is one. */
  assembly: IndexedRow | null;
}

/** Which group a line is listed under: its heading, however it is cased,
 * or the one group of lines the sheet gave no heading. */
export function boqGroupKey(row: ProjectBoqItemInput): string {
  return (row.group_heading ?? "").trim().toLowerCase() || "∅";
}

/**
 * The tab's lines under their Design Sheet headings, in the order each
 * heading first appears, and each line where the sheet put it.
 *
 * A Design Sheet sets an assembly out as one line naming it -- "EST4 fire
 * alarm control panel", "Remote audio closet cabinet" -- with no part
 * number and the number of them as its quantity, followed by the parts one
 * of them is built from. That first line is shown as the group's assembly.
 * A heading whose first line has a part number ("Field Devices") is a list
 * of items and has none. This reads the lines as they are; nothing about
 * them is changed.
 */
export function groupBoqRows(rows: IndexedRow[]): BoqGroup[] {
  const groups = new Map<string, BoqGroup>();
  for (const item of rows) {
    const heading = (item.row.group_heading ?? "").trim();
    const key = boqGroupKey(item.row);
    const group = groups.get(key) ?? { key, heading, lines: [], assembly: null };
    group.lines.push(item);
    groups.set(key, group);
  }
  for (const group of groups.values()) {
    const [first] = group.lines;
    if (group.heading && group.lines.length > 1 && first && !(first.row.catalog_no ?? "").trim()) {
      group.assembly = first;
    }
  }
  // The lines the sheet gave no heading are listed last, where a reader
  // looks for what is left over, rather than ahead of the panel they
  // belong to; every headed group keeps the sheet's own order.
  return [...groups.values()].sort((a, b) => Number(!a.heading) - Number(!b.heading));
}
