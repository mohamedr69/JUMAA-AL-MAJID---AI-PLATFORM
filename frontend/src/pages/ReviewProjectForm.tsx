import { useState, type FormEvent } from "react";
import { ProjectDetailsFields } from "../components/ProjectDetailsFields";
import { ApiError, api } from "../lib/api";
import { draftFrom, draftToPayload, type DraftTextField } from "../lib/projectDetails";
import {
  DESIGN_SHEET_SYSTEM_CODES,
  type DocumentCandidate,
  type ExtractedFieldName,
  type Project,
  type ProjectCreate,
  type ProjectResolveResponse,
} from "../lib/types";

// The DRF calls it the project title; everywhere else it is the project name.
const DRAFT_FIELD: Record<ExtractedFieldName, DraftTextField> = {
  project_title: "project_name",
  plot_number: "plot_number",
  location: "location",
  client: "client",
  consultant: "consultant",
  contractor: "contractor",
  contact_person: "contact_person",
  contact_phone: "contact_phone",
  contact_email: "contact_email",
};

/** A design sheet the resolver found, as the engineer decides on it: whether
 * to attach it, and which system it is for. The resolver's own notes (a
 * superseded revision, a system inferred from the DRF) stay visible. */
interface SheetChoice {
  path: string;
  filename: string;
  systemCode: string | null;
  selected: boolean;
  revision: number | null;
  note: string;
}

function sheetNote(candidate: DocumentCandidate): string {
  // "folder~scan/commercial, filename~Design; superseded by X" -> what is
  // worth showing is everything after the match rule.
  const parts = candidate.matched_via.split(";").slice(1).map((p) => p.trim());
  return parts.join("; ");
}

export function ReviewProjectForm({
  epNumber,
  sourceFolderPath,
  resolution,
  onCreated,
}: {
  epNumber: string;
  sourceFolderPath: string;
  resolution: ProjectResolveResponse;
  onCreated: (project: Project) => void;
}) {
  const extracted = Object.entries(resolution.extracted_fields) as [
    ExtractedFieldName,
    NonNullable<ProjectResolveResponse["extracted_fields"][ExtractedFieldName]>,
  ][];
  const hasExtraction = extracted.length > 0;
  const confidence = Object.fromEntries(
    extracted.map(([name, field]) => [DRAFT_FIELD[name], field.confidence])
  );

  const [draft, setDraft] = useState(() =>
    draftFrom(
      {
        ...Object.fromEntries(extracted.map(([name, field]) => [DRAFT_FIELD[name], field.value])),
        scope_of_work: resolution.extracted_scope_of_work,
        other_information: resolution.extracted_other_information,
      },
      resolution.extracted_systems
    )
  );

  const [sheets, setSheets] = useState<SheetChoice[]>(() =>
    resolution.design_sheet_candidates.map((d) => ({
      path: d.path,
      filename: d.filename,
      systemCode: d.system_guess,
      selected: d.selected,
      revision: d.revision,
      note: sheetNote(d),
    }))
  );

  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function updateSheet(path: string, patch: Partial<SheetChoice>) {
    setSheets((current) => current.map((s) => (s.path === path ? { ...s, ...patch } : s)));
  }

  const unlabelled = sheets.filter((s) => s.selected && !s.systemCode);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);

    const payload: ProjectCreate = {
      ...draftToPayload(draft),
      ep_number: epNumber,
      // The folder the documents came from -- the one the engineer chose
      // where the EP number matched more than one -- not the first match.
      source_folder_path: resolution.source_folder ?? sourceFolderPath,
      drf_document_path: resolution.drf_candidates[0]?.path ?? null,
      design_sheets: sheets
        .filter((s) => s.selected)
        .map((s) => ({ system_code: s.systemCode, document_path: s.path })),
    };

    try {
      const created = await api.post<Project>("/projects", payload);
      onCreated(created);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create project");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="text-xl font-bold text-navy-900">Review Project Information</h1>
      <p className="mt-1 text-sm text-gray-500">
        {hasExtraction
          ? "Fields below were read from the DRF automatically. Please review and correct anything before creating the project."
          : "Documents were located automatically. Enter the project details below, then create the project."}
      </p>

      <div className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
        <div className="text-xs font-semibold uppercase text-gray-400">Source Documents</div>
        <div className="mt-2 truncate text-xs text-gray-400" title={resolution.source_folder ?? sourceFolderPath}>
          {resolution.source_folder ?? sourceFolderPath}
        </div>

        <DocList label="DRF" items={resolution.drf_candidates.map((d) => d.filename)} />
        {resolution.drf_candidates.length > 1 && (
          <div className="mt-1 text-xs text-gray-500">
            The first is read; the others are copies of the same form filed beside it.
          </div>
        )}

        <div className="mt-3">
          <div className="text-xs font-medium text-gray-500">Design Sheet(s)</div>
          {sheets.length === 0 ? (
            <div className="text-xs text-amber-600">Not found &mdash; you can attach it later</div>
          ) : (
            <table className="mt-1 w-full text-xs">
              <thead>
                <tr className="text-left text-gray-400">
                  <th className="w-6 py-1"></th>
                  <th className="py-1">File</th>
                  <th className="w-36 py-1">System</th>
                </tr>
              </thead>
              <tbody>
                {sheets.map((sheet) => (
                  <tr key={sheet.path} className={sheet.selected ? "" : "text-gray-400"}>
                    <td className="py-1 align-top">
                      <input
                        type="checkbox"
                        checked={sheet.selected}
                        onChange={(e) => updateSheet(sheet.path, { selected: e.target.checked })}
                        aria-label={`Attach ${sheet.filename}`}
                      />
                    </td>
                    <td className="py-1 align-top">
                      <div className="text-navy-900">
                        {sheet.filename}
                        {sheet.revision !== null && (
                          <span className="ml-1 rounded bg-gray-100 px-1 text-[10px] text-gray-600">R{sheet.revision}</span>
                        )}
                      </div>
                      {sheet.note && <div className="text-[11px] text-amber-700">{sheet.note}</div>}
                    </td>
                    <td className="py-1 align-top">
                      <select
                        value={sheet.systemCode ?? ""}
                        onChange={(e) => updateSheet(sheet.path, { systemCode: e.target.value || null })}
                        className="input py-1 text-xs"
                        aria-label={`System of ${sheet.filename}`}
                      >
                        <option value="">Unassigned</option>
                        {DESIGN_SHEET_SYSTEM_CODES.map((code) => (
                          <option key={code} value={code}>
                            {code}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {unlabelled.length > 0 && (
            <div className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
              {unlabelled.length === 1
                ? `${unlabelled[0].filename} has no system: its BOQ lines will be "Unassigned" until one is chosen.`
                : `${unlabelled.length} sheets have no system: their BOQ lines will be "Unassigned" until one is chosen.`}
            </div>
          )}
        </div>
      </div>

      {resolution.ai_suggestions.length > 0 && (
        <div className="mt-4 rounded-xl border border-brand-200 bg-brand-50/50 p-4 text-xs">
          <div className="text-xs font-semibold uppercase text-brand-700">AI suggestions</div>
          <p className="mt-1 text-gray-600">
            Read by the model from the scanned cell or the sheet&apos;s own words. Nothing below has been applied:
            check the source and type what you accept into the field or choose the system yourself.
          </p>
          <ul className="mt-2 space-y-1">
            {resolution.ai_suggestions.map((s, i) => (
              <li key={i} className="text-navy-900">
                {s.kind === "drf_field" && (
                  <>
                    <span className="font-medium">{s.target.replace("drf_field:", "").replace(/_/g, " ")}</span>
                    {s.value ? <> &rarr; <span className="font-semibold">{s.value}</span></> : null}
                  </>
                )}
                {s.kind === "sheet_system" && (
                  <>
                    <span className="font-medium">{s.target.split(/[\\/]/).pop()}</span>
                    {s.value ? <> &rarr; system <span className="font-semibold">{s.value}</span></> : null}
                  </>
                )}
                {s.kind === "error" && <span className="font-medium">AI assistance did not complete</span>}
                <span className={`ml-2 ${s.state === "validated" ? "text-emerald-700" : "text-amber-700"}`}>
                  {s.state === "validated" ? "independent OCR agrees" : s.state.replace(/_/g, " ")}
                  {s.reason && s.state !== "validated" ? ` — ${s.reason}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <form onSubmit={handleSubmit} className="mt-4 space-y-4 rounded-xl border border-gray-200 bg-white p-4">
        <ProjectDetailsFields
          epNumber={`EP-${epNumber}`}
          draft={draft}
          onChange={setDraft}
          confidence={confidence}
        />

        {error && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
        >
          {submitting ? "Creating..." : "Create Project"}
        </button>
      </form>
    </div>
  );
}

function DocList({ label, items }: { label: string; items: string[] }) {
  return (
    <div className="mt-3">
      <div className="text-xs font-medium text-gray-500">{label}</div>
      {items.length === 0 ? (
        <div className="text-xs text-amber-600">Not found &mdash; you can attach it later</div>
      ) : (
        <ul className="mt-1 space-y-0.5">
          {items.map((name) => (
            <li key={name} className="text-xs text-navy-900">
              {name}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
