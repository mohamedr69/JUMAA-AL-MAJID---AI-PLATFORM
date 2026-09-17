import type { ChangeEvent, ReactNode } from "react";
import { voiceEvacuationIntegrated, type DraftTextField, type ProjectDetailsDraft } from "../lib/projectDetails";
import { SCOPE_OF_WORK_OPTIONS, SYSTEM_OPTIONS, type ProjectSystemInput } from "../lib/types";

/** The project information fields and the Systems table, shared by the
 * create-time review and the Project Info page. */
export function ProjectDetailsFields({
  epNumber,
  draft,
  onChange,
  confidence = {},
}: {
  epNumber: string;
  draft: ProjectDetailsDraft;
  onChange: (next: ProjectDetailsDraft) => void;
  /** Read confidence per field, shown as a badge where a reader gave one (the model gives none). */
  confidence?: Partial<Record<DraftTextField, number>>;
}) {
  const bind = (field: DraftTextField) => ({
    value: draft[field],
    onChange: (e: ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
      onChange({ ...draft, [field]: e.target.value }),
  });

  // Systems the template lists, plus any the project has that it does not.
  const systemNames = [
    ...SYSTEM_OPTIONS,
    ...Object.keys(draft.systems).filter((name) => !(SYSTEM_OPTIONS as readonly string[]).includes(name)),
  ];

  function toggleSystem(name: string) {
    const systems = { ...draft.systems };
    if (name in systems) {
      delete systems[name];
    } else {
      systems[name] = { name, brand: null, method_statement: false, drawing: false };
    }
    onChange({ ...draft, systems });
  }

  function updateSystem(name: string, patch: Partial<ProjectSystemInput>) {
    const current = draft.systems[name];
    if (current) onChange({ ...draft, systems: { ...draft.systems, [name]: { ...current, ...patch } } });
  }

  return (
    <>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="EP Number">
          <input disabled value={epNumber} className="input bg-gray-50 text-gray-500" />
        </Field>
        <Field label="Project Name" confidence={confidence.project_name}>
          <input {...bind("project_name")} className="input" />
        </Field>
        <Field label="Plot Number" confidence={confidence.plot_number}>
          <input {...bind("plot_number")} className="input" />
        </Field>
        <Field label="Location" confidence={confidence.location}>
          <input {...bind("location")} className="input" />
        </Field>
        <Field label="Client" confidence={confidence.client}>
          <input {...bind("client")} className="input" />
        </Field>
        <Field label="Consultant" confidence={confidence.consultant}>
          <input {...bind("consultant")} className="input" />
        </Field>
        <Field label="Contractor" confidence={confidence.contractor}>
          <input {...bind("contractor")} className="input" />
        </Field>
        <Field label="Scope of Work">
          <select {...bind("scope_of_work")} className="input">
            <option value="">Select...</option>
            {SCOPE_OF_WORK_OPTIONS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Contact Person" confidence={confidence.contact_person}>
          <input {...bind("contact_person")} className="input" />
        </Field>
        <Field label="Contact Phone" confidence={confidence.contact_phone}>
          <input {...bind("contact_phone")} className="input" />
        </Field>
        <Field label="Contact Email" className="sm:col-span-2" confidence={confidence.contact_email}>
          <input type="email" {...bind("contact_email")} className="input" />
        </Field>
      </div>

      <div>
        <div className="mb-1 text-sm font-medium text-gray-700">Systems</div>
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-3 py-2 font-medium">System</th>
                <th className="px-3 py-2 font-medium">Brand</th>
                <th className="w-16 px-3 py-2 text-center font-medium">MS</th>
                <th className="w-16 px-3 py-2 text-center font-medium">DWG</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {systemNames.map((system) => {
                const selected = draft.systems[system];
                return (
                  <tr key={system} className={selected ? "bg-white" : "bg-gray-50/40"}>
                    <td className="px-3 py-1.5">
                      <label className="flex items-center gap-2 text-gray-700">
                        <input
                          type="checkbox"
                          checked={Boolean(selected)}
                          onChange={() => toggleSystem(system)}
                          className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500"
                        />
                        {system}
                      </label>
                    </td>
                    <td className="px-3 py-1.5">
                      <input
                        value={selected?.brand ?? ""}
                        disabled={!selected}
                        onChange={(e) => updateSystem(system, { brand: e.target.value || null })}
                        className="input py-1 disabled:bg-gray-50"
                      />
                    </td>
                    <td className="px-3 py-1.5 text-center">
                      <input
                        type="checkbox"
                        checked={selected?.method_statement ?? false}
                        disabled={!selected}
                        onChange={(e) => updateSystem(system, { method_statement: e.target.checked })}
                        className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500"
                      />
                    </td>
                    <td className="px-3 py-1.5 text-center">
                      <input
                        type="checkbox"
                        checked={selected?.drawing ?? false}
                        disabled={!selected}
                        onChange={(e) => updateSystem(system, { drawing: e.target.checked })}
                        className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500"
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/edwards|\best\d?\b/i.test(draft.systems["Fire Alarm"]?.brand ?? "") && (
        <div className="rounded-lg border border-blue-100 bg-blue-50/60 px-3 py-2 text-sm">
          <label className="flex items-center gap-2 font-medium text-navy-900">
            <input
              type="checkbox"
              checked={draft.separate_ve_panel}
              onChange={(e) => onChange({ ...draft, separate_ve_panel: e.target.checked })}
              className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500"
            />
            Separate voice evacuation panel
          </label>
          <p className="mt-1 text-xs text-gray-600">
            {voiceEvacuationIntegrated(draft)
              ? "Edwards fire alarm: Fire Alarm, Voice Evacuation and Fire Telephone are one integrated system (FAS) with one Design Sheet, BOQ, compliance statement and submittal."
              : "Voice Evacuation is a system of its own, with its own Design Sheet."}
          </p>
        </div>
      )}

      <fieldset className="rounded-lg border border-gray-200 px-3 py-2 text-sm">
        <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-gray-500">AI use</legend>
        <label className="flex items-start gap-2">
          <input
            type="radio"
            name="ai_policy"
            checked={draft.ai_policy === "allowed"}
            onChange={() => onChange({ ...draft, ai_policy: "allowed" })}
            className="mt-0.5 h-4 w-4"
          />
          <span>
            <span className="font-medium text-navy-900">Allowed</span>
            <span className="block text-xs text-gray-500">
              Crops of unreadable cells, DRF fields and specification clauses may be sent to the AI provider for a suggestion an
              engineer then accepts or rejects.
            </span>
          </span>
        </label>
        <label className="mt-2 flex items-start gap-2">
          <input
            type="radio"
            name="ai_policy"
            checked={draft.ai_policy === "blocked"}
            onChange={() => onChange({ ...draft, ai_policy: "blocked" })}
            className="mt-0.5 h-4 w-4"
          />
          <span>
            <span className="font-medium text-navy-900">Not allowed</span>
            <span className="block text-xs text-gray-500">
              Nothing from this project is sent to an AI provider. AI features are switched off here; everything else works the same.
            </span>
          </span>
        </label>
      </fieldset>

      <Field label="Other Information">
        <textarea {...bind("other_information")} rows={3} className="input" />
      </Field>
    </>
  );
}

function Field({
  label,
  children,
  className = "",
  confidence,
}: {
  label: string;
  children: ReactNode;
  className?: string;
  confidence?: number;
}) {
  return (
    <label className={`block text-sm font-medium text-gray-700 ${className}`}>
      <span className="flex items-center gap-1.5">
        {label}
        <ConfidenceBadge confidence={confidence} />
      </span>
      <div className="mt-1">{children}</div>
    </label>
  );
}

function ConfidenceBadge({ confidence }: { confidence?: number | null }) {
  if (confidence === undefined || confidence === null) return null;

  const tier =
    confidence >= 85
      ? { label: "auto-filled", classes: "bg-green-50 text-green-700" }
      : confidence >= 60
        ? { label: "check this", classes: "bg-amber-50 text-amber-700" }
        : { label: "low confidence", classes: "bg-red-50 text-red-700" };

  return (
    <span
      className={`rounded-full px-1.5 py-0.5 text-[10px] font-normal normal-case ${tier.classes}`}
      title={`Read confidence: ${confidence.toFixed(0)}%`}
    >
      {tier.label}
    </span>
  );
}
