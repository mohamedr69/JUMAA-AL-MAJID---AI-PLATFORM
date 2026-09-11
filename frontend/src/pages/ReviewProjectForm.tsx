import { useState, type FormEvent } from "react";
import { ProjectDetailsFields } from "../components/ProjectDetailsFields";
import { ApiError, api } from "../lib/api";
import { draftFrom, draftToPayload, type DraftTextField } from "../lib/projectDetails";
import type { ExtractedFieldName, Project, ProjectCreate, ProjectResolveResponse } from "../lib/types";

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
      },
      resolution.extracted_systems
    )
  );

  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);

    const payload: ProjectCreate = {
      ...draftToPayload(draft),
      ep_number: epNumber,
      source_folder_path: sourceFolderPath,
      drf_document_path: resolution.drf_candidates[0]?.path ?? null,
      design_sheets: resolution.design_sheet_candidates.map((d) => ({
        system_code: d.system_guess,
        document_path: d.path,
      })),
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
        <div className="mt-2 truncate text-xs text-gray-400" title={sourceFolderPath}>
          {sourceFolderPath}
        </div>

        <DocList label="DRF" items={resolution.drf_candidates.map((d) => d.filename)} />
        <DocList
          label="Design Sheet(s)"
          items={resolution.design_sheet_candidates.map(
            (d) => d.filename + (d.system_guess ? ` (${d.system_guess})` : "")
          )}
        />
      </div>

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
