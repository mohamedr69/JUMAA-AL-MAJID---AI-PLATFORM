import { useState, type FormEvent } from "react";
import { ApiError, api } from "../lib/api";
import {
  SCOPE_OF_WORK_OPTIONS,
  SYSTEM_OPTIONS,
  type Project,
  type ProjectCreate,
  type ProjectResolveResponse,
} from "../lib/types";

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
  const [projectName, setProjectName] = useState("");
  const [plotNumber, setPlotNumber] = useState("");
  const [location, setLocation] = useState("");
  const [client, setClient] = useState("");
  const [consultant, setConsultant] = useState("");
  const [contractor, setContractor] = useState("");
  const [contactPerson, setContactPerson] = useState("");
  const [contactPhone, setContactPhone] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [scopeOfWork, setScopeOfWork] = useState<string>("");
  const [systems, setSystems] = useState<string[]>([]);
  const [otherInformation, setOtherInformation] = useState("");

  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function toggleSystem(system: string) {
    setSystems((prev) =>
      prev.includes(system) ? prev.filter((s) => s !== system) : [...prev, system]
    );
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);

    const payload: ProjectCreate = {
      ep_number: epNumber,
      project_name: projectName || null,
      plot_number: plotNumber || null,
      location: location || null,
      client: client || null,
      consultant: consultant || null,
      contractor: contractor || null,
      contact_person: contactPerson || null,
      contact_phone: contactPhone || null,
      contact_email: contactEmail || null,
      scope_of_work: scopeOfWork || null,
      systems,
      other_information: otherInformation || null,
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
        Documents were located automatically. Enter the project details below, then create the
        project.
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
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="EP Number">
            <input disabled value={epNumber} className="input bg-gray-50 text-gray-500" />
          </Field>
          <Field label="Project Name">
            <input value={projectName} onChange={(e) => setProjectName(e.target.value)} className="input" />
          </Field>
          <Field label="Plot Number">
            <input value={plotNumber} onChange={(e) => setPlotNumber(e.target.value)} className="input" />
          </Field>
          <Field label="Location">
            <input value={location} onChange={(e) => setLocation(e.target.value)} className="input" />
          </Field>
          <Field label="Client">
            <input value={client} onChange={(e) => setClient(e.target.value)} className="input" />
          </Field>
          <Field label="Consultant">
            <input value={consultant} onChange={(e) => setConsultant(e.target.value)} className="input" />
          </Field>
          <Field label="Contractor">
            <input value={contractor} onChange={(e) => setContractor(e.target.value)} className="input" />
          </Field>
          <Field label="Scope of Work">
            <select value={scopeOfWork} onChange={(e) => setScopeOfWork(e.target.value)} className="input">
              <option value="">Select...</option>
              {SCOPE_OF_WORK_OPTIONS.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Contact Person">
            <input value={contactPerson} onChange={(e) => setContactPerson(e.target.value)} className="input" />
          </Field>
          <Field label="Contact Phone">
            <input value={contactPhone} onChange={(e) => setContactPhone(e.target.value)} className="input" />
          </Field>
          <Field label="Contact Email" className="sm:col-span-2">
            <input
              type="email"
              value={contactEmail}
              onChange={(e) => setContactEmail(e.target.value)}
              className="input"
            />
          </Field>
        </div>

        <div>
          <div className="mb-1 text-sm font-medium text-gray-700">Systems</div>
          <div className="flex flex-wrap gap-x-4 gap-y-2">
            {SYSTEM_OPTIONS.map((system) => (
              <label key={system} className="flex items-center gap-1.5 text-sm text-gray-600">
                <input
                  type="checkbox"
                  checked={systems.includes(system)}
                  onChange={() => toggleSystem(system)}
                  className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500"
                />
                {system}
              </label>
            ))}
          </div>
        </div>

        <Field label="Other Information">
          <textarea
            value={otherInformation}
            onChange={(e) => setOtherInformation(e.target.value)}
            rows={3}
            className="input"
          />
        </Field>

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

function Field({
  label,
  children,
  className = "",
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label className={`block text-sm font-medium text-gray-700 ${className}`}>
      {label}
      <div className="mt-1">{children}</div>
    </label>
  );
}
