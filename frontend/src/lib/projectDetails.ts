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
}

export type DraftTextField = Exclude<keyof ProjectDetailsDraft, "systems">;

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
  source: Partial<Record<DraftTextField, string | null>>,
  systems: ProjectSystemInput[]
): ProjectDetailsDraft {
  const text = Object.fromEntries(TEXT_FIELDS.map((field) => [field, source[field] ?? ""])) as Record<
    DraftTextField,
    string
  >;
  return {
    ...text,
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
  return { ...text, systems: names.map((name) => draft.systems[name]) };
}
