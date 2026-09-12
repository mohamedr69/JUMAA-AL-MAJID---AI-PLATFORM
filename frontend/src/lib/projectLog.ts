import type { ProjectLogDrawing, Submittal } from "./types";

export interface LogRevision {
  title: string; reference: string; revision: string; status: string; groupReference?: string | null;
  path: string | null; system: string | null; updated: string; floor: string; page?: number; evidence?: string | null; source?: string;
}
export interface LogDocument { key: string; title: string; reference: string; revisions: LogRevision[] }
export function directoryRevision(file: ProjectLogDrawing): LogRevision {
  return { title: file.name, reference: file.reference ?? file.name, revision: file.revision ?? "R0",
    status: file.status ?? "UR", path: file.path, system: file.system_code, updated: file.modified,
    floor: file.floor ?? "Not recorded", page: file.page, evidence: file.reply_text, source: file.source, groupReference: file.group_reference };
}
export function registerRevision(item: Submittal): LogRevision {
  return { title: item.title, reference: item.reference ?? item.title, revision: item.revision.replace(/^R0+(\d)/i, "R$1"),
    status: item.reply_code === "B" ? "ANN" : item.reply_code ? item.status.replaceAll("_", " ") : "UR",
    path: item.document_path, system: item.system_code, updated: item.updated_at, floor: "Not recorded" };
}
export function groupRevisions(rows: LogRevision[], system: (value: string | null) => string): LogDocument[] {
  const groups = new Map<string, LogDocument>();
  for (const row of rows) {
    const base = (row.groupReference ?? row.reference).replace(/[-_ ]+(?:REV[ ._-]*|R)\d+$/i, "");
    const key = `${system(row.system)}:${base.toLowerCase()}`;
    const doc = groups.get(key) ?? { key, title: row.title, reference: base, revisions: [] };
    const existing = doc.revisions.findIndex((r) => r.revision === row.revision);
    if (existing < 0) doc.revisions.push(row);
    else {
      const old = doc.revisions[existing];
      if ((!old.source && row.source) || (Boolean(old.source) === Boolean(row.source) && ((old.status === "UR" && row.status !== "UR") || (old.status === row.status && row.updated > old.updated)))) doc.revisions[existing] = row;
    }
    groups.set(key, doc);
  }
  return [...groups.values()].map((doc) => ({ ...doc, revisions: doc.revisions.sort((a, b) =>
    b.revision.localeCompare(a.revision, undefined, { numeric: true }) || b.updated.localeCompare(a.updated)) }));
}
