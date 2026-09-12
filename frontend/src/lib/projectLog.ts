import type { ProjectLogDrawing, Submittal } from "./types";

export interface LogRevision {
  title: string; reference: string; revision: string; status: string;
  path: string | null; system: string | null; updated: string; floor: string;
}
export interface LogDocument { key: string; title: string; reference: string; revisions: LogRevision[] }
export function directoryRevision(file: ProjectLogDrawing): LogRevision {
  const stem = file.name.replace(/\.[^.]+$/, "");
  const match = /(?:^|[-_ ]+)(?:REV(?:ISION)?[ ._-]*|R)(\d+)(?=$|[-_ ])/i.exec(stem);
  const folderRevision = /(?:^|[/\\])(?:REV(?:ISION)?[ ._-]*|R)(\d+)(?=$|[/\\])/i.exec(file.path);
  const revision = match?.[1] ?? folderRevision?.[1];
  const reference = (match ? stem.replace(match[0], " ") : stem).replace(/[-_ ]+$/, "").trim();
  return { title: reference, reference, revision: revision ? `R${Number(revision)}` : "Not recorded",
    status: "Not recorded", path: file.path, system: file.system_code, updated: file.modified,
    floor: /(?:^|[-_ ])(B\d+|L\d+|GF|RF)(?=$|[-_ ])/i.exec(stem)?.[1]?.toUpperCase() ?? "Not recorded" };
}
export function registerRevision(item: Submittal): LogRevision {
  return { title: item.title, reference: item.reference ?? item.title, revision: item.revision.replace(/^R0+(\d)/i, "R$1"),
    status: item.reply_code === "B" ? "ANN" : item.status.replaceAll("_", " "),
    path: item.document_path, system: item.system_code, updated: item.updated_at, floor: "Not recorded" };
}
export function groupRevisions(rows: LogRevision[], system: (value: string | null) => string): LogDocument[] {
  const groups = new Map<string, LogDocument>();
  for (const row of rows) {
    const base = row.reference.replace(/[-_ ]+(?:REV[ ._-]*|R)\d+$/i, "");
    const key = `${system(row.system)}:${base.toLowerCase()}`;
    const doc = groups.get(key) ?? { key, title: row.title, reference: base, revisions: [] };
    const existing = doc.revisions.findIndex((r) => r.revision === row.revision && Boolean(r.path && row.path && (r.path.replaceAll("\\", "/").toLowerCase().endsWith(row.path.replaceAll("\\", "/").toLowerCase()) || row.path.replaceAll("\\", "/").toLowerCase().endsWith(r.path.replaceAll("\\", "/").toLowerCase()))));
    if (existing >= 0) doc.revisions[existing] = row; else doc.revisions.push(row);
    groups.set(key, doc);
  }
  return [...groups.values()].map((doc) => ({ ...doc, revisions: doc.revisions.sort((a, b) =>
    b.revision.localeCompare(a.revision, undefined, { numeric: true }) || b.updated.localeCompare(a.updated)) }));
}
