import type { ProjectLogDrawing } from "./types";

export interface LogRevision {
  title: string; reference: string; revision: string; status: string; groupReference?: string | null;
  path: string | null; system: string | null; updated: string; floor: string; page?: number; evidence?: string | null; source?: string;
}
export interface LogDocument { key: string; title: string; reference: string; revisions: LogRevision[] }

/** The system a code stands for: the spellings a form, a folder and a
 * register each use for the one system, reduced to the one name. Shared
 * so the Logs tab, the home page and anything else counting documents
 * agree on what "the same system" means. An Edwards fire alarm carries
 * voice evacuation and fire telephone, which is a property of the job. */
export function systemGroup(value: string | null, voiceEvacuationIntegrated = false): string {
  const code = (value ?? "").trim().toUpperCase().replace(/[_-]+/g, " ");
  if (["FAS", "FA", "FIRE ALARM"].includes(code)) return "FAS";
  if (["VE", "VES", "VOICE EVACUATION", "FT", "FIRE TELEPHONE"].includes(code)) {
    if (["FT", "FIRE TELEPHONE"].includes(code)) return "FAS";
    return voiceEvacuationIntegrated ? "FAS" : "VE";
  }
  // Emergency lighting is one system: ELS, CBS and EML alike.
  if (["ELS", "EL", "EML", "ELM", "CBS", "EMERGENCY LIGHTING", "CENTRAL BATTERY SYSTEM", "EMERGENCY LIGHT MONITORING", "MONITORED SELF CONTAINED", "MONITORED SELF CONTAINED SYSTEM", "MONITORED SELF CONTAINED EMERGENCY LIGHTING", "EMERGENCY LIGHTING MONITORING"].includes(code)) return "ELS";
  if (["FRC", "FIRE RATED CABLE", "FIRE RESISTANT CABLE"].includes(code)) return "FRC";
  return value?.trim() ?? "";
}
export function directoryRevision(file: ProjectLogDrawing): LogRevision {
  return { title: file.name, reference: file.reference ?? file.name, revision: file.revision ?? "R0",
    status: file.status ?? "UR", path: file.path, system: file.system_code, updated: file.modified,
    floor: file.floor ?? "Not recorded", page: file.page, evidence: file.reply_text, source: file.source, groupReference: file.group_reference };
}
export function groupRevisions(rows: LogRevision[], voiceEvacuationIntegrated = false): LogDocument[] {
  const groups = new Map<string, LogDocument>();
  for (const row of rows) {
    const base = (row.groupReference ?? row.reference).replace(/[-_ ]+(?:REV[ ._-]*|R)\d+$/i, "");
    // Keyed on the reference *and the system it is for*. A project that
    // numbers every system's submittal with the one reference -- EP-30880
    // for the fire alarm, the emergency lighting and the fire rated cable
    // alike -- has three documents, and keying on the number alone showed
    // one of them and hid the rest. The system code is reduced first, so
    // the same system spelled two ways (FA and FAS, CBS and ELS) is still
    // the one document listed once with its revisions under it.
    const key = `${base.toLowerCase()}|${systemGroup(row.system, voiceEvacuationIntegrated)}`;
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
