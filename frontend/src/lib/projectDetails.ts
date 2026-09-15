import { SYSTEM_OPTIONS, type ProjectDetailsInput, type ProjectSystemInput } from "./types";

/** Form state for the project information: text fields as strings (blank
 * means not set), systems keyed by name so the DRF's rows and the template's
 * rows line up regardless of the order either lists them in. */
export interface ProjectDetailsDraft {
  project_name: string;
  plot_number: string;
  location: string;
  client: string;
  consultant: string;
  contractor: string;
  contact_person: string;
  contact_phone: string;
  contact_email: string;
  scope_of_work: string;
  other_information: string;
  systems: Record<string, ProjectSystemInput>;
  separate_ve_panel: boolean;
  /** Whether the project's documents may be sent to an AI provider. */
  ai_policy: "allowed" | "blocked";
}

export type DraftTextField = Exclude<keyof ProjectDetailsDraft, "systems" | "separate_ve_panel" | "ai_policy">;

const TEXT_FIELDS: DraftTextField[] = [
  "project_name",
  "plot_number",
  "location",
  "client",
  "consultant",
  "contractor",
  "contact_person",
  "contact_phone",
  "contact_email",
  "scope_of_work",
  "other_information",
];

export function draftFrom(
  source: Partial<Record<DraftTextField, string | null>> & { separate_ve_panel?: boolean; ai_policy?: string },
  systems: ProjectSystemInput[]
): ProjectDetailsDraft {
  const text = Object.fromEntries(TEXT_FIELDS.map((field) => [field, source[field] ?? ""])) as Record<
    DraftTextField,
    string
  >;
  return {
    ...text,
    separate_ve_panel: Boolean(source.separate_ve_panel),
    ai_policy: source.ai_policy === "blocked" ? "blocked" : "allowed",
    systems: Object.fromEntries(
      systems.map((s) => [
        s.name,
        { name: s.name, brand: s.brand ?? null, method_statement: s.method_statement, drawing: s.drawing },
      ])
    ),
  };
}

export function draftToPayload(draft: ProjectDetailsDraft): ProjectDetailsInput {
  const text = Object.fromEntries(
    TEXT_FIELDS.map((field) => [field, draft[field].trim() || null])
  ) as Record<DraftTextField, string | null>;

  // Template order first, then anything the template does not list.
  const known = SYSTEM_OPTIONS as readonly string[];
  const names = [
    ...known.filter((name) => name in draft.systems),
    ...Object.keys(draft.systems).filter((name) => !known.includes(name)),
  ];
  return {
    ...text,
    separate_ve_panel: draft.separate_ve_panel,
    ai_policy: draft.ai_policy,
    systems: names.map((name) => draft.systems[name]),
  };
}

/** Whether Voice Evacuation is part of the fire alarm system, as the backend
 * decides it (app/services/system_rules.py): the fire alarm is Edwards, the
 * voice evacuation carries no other brand, and no separate panel is ticked. */
export function voiceEvacuationIntegrated(draft: ProjectDetailsDraft): boolean {
  if (draft.separate_ve_panel) return false;
  const edwards = (brand: string | null | undefined) => /edwards|\best\d?\b/i.test(brand ?? "");
  const fireAlarm = draft.systems["Fire Alarm"];
  if (!fireAlarm || !edwards(fireAlarm.brand)) return false;
  const voice = draft.systems["Voice Evacuation"]?.brand;
  return !voice || edwards(voice);
}
