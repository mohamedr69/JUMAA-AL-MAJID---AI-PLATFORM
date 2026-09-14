import { useMemo, useState, type FormEvent } from "react";
import { DetailsCheckPanel } from "../components/DetailsCheckPanel";
import { ProjectDetailsFields } from "../components/ProjectDetailsFields";
import { useAuth } from "../context/AuthContext";
import { ApiError, api } from "../lib/api";
import { draftFrom, draftToPayload } from "../lib/projectDetails";
import { PROJECT_EDITOR_ROLES, type Project } from "../lib/types";
import { useProject } from "./ProjectWorkspace";

export function ProjectInfoPage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold text-navy-900">Project Info</h1>
      <p className="mt-1 text-sm text-gray-500">
        {canEdit
          ? "Read from the DRF when the project was created. Correct anything that was read wrongly or has changed since."
          : "Read from the DRF when the project was created."}
      </p>
      {canEdit ? <EditProjectInfo /> : <ProjectInfoReadOnly project={project} />}
    </div>
  );
}

function EditProjectInfo() {
  const { project, setProject } = useProject();
  const saved = useMemo(() => draftFrom(project, project.systems), [project]);
  const [draft, setDraft] = useState(saved);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [propagated, setPropagated] = useState<string[]>([]);

  const dirty = JSON.stringify(draftToPayload(draft)) !== JSON.stringify(draftToPayload(saved));

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const updated = await api.put<Project>(`/projects/${project.id}`, draftToPayload(draft));
      setProject(updated);
      setDraft(draftFrom(updated, updated.systems));
      setSavedAt(new Date().toLocaleTimeString());
      setPropagated(updated.propagated ?? []);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save the project information");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="mt-6 space-y-4 rounded-xl border border-gray-200 bg-white p-5">
      {project.drf_document_path ? (
        <DetailsCheckPanel endpoint={`/projects/${project.id}/details-check`} draft={draft} onApply={setDraft} disabled={saving} />
      ) : (
        <p className="rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-500">
          Attach the DRF under Documents to check these details against it with AI.
        </p>
      )}
      <ProjectDetailsFields epNumber={`EP-${project.ep_number}`} draft={draft} onChange={setDraft} />

      {error && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

      {savedAt && !dirty && propagated.length > 0 && (
        <div className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
          Saved and carried across the project: {propagated.join("; ")}.
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        {savedAt && !dirty && <span className="text-xs text-gray-400">Saved {savedAt} · every tab now shows these details</span>}
        <button
          type="button"
          onClick={() => setDraft(saved)}
          disabled={!dirty || saving}
          className="rounded-lg border border-gray-300 px-4 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50"
        >
          Discard changes
        </button>
        <button
          type="submit"
          disabled={!dirty || saving}
          className="rounded-lg bg-brand-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
        >
          {saving ? "Saving..." : "Save"}
        </button>
      </div>
    </form>
  );
}

function ProjectInfoReadOnly({ project }: { project: Project }) {
  return (
    <>
      <section className="mt-6 grid grid-cols-1 gap-4 rounded-xl border border-gray-200 bg-white p-5 text-sm sm:grid-cols-2">
        <InfoRow label="EP Number" value={`EP-${project.ep_number}`} />
        <InfoRow label="Project Name" value={project.project_name} />
        <InfoRow label="Client" value={project.client} />
        <InfoRow label="Consultant" value={project.consultant} />
        <InfoRow label="Contractor" value={project.contractor} />
        <InfoRow label="Location" value={project.location} />
        <InfoRow label="Plot Number" value={project.plot_number} />
        <InfoRow label="Scope of Work" value={project.scope_of_work} />
        <InfoRow label="Contact Person" value={project.contact_person} />
        <InfoRow label="Contact Phone" value={project.contact_phone} />
        <InfoRow label="Contact Email" value={project.contact_email} />
      </section>

      <section className="mt-4 rounded-xl border border-gray-200 bg-white p-5">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-400">Systems</h2>
        {project.systems.length === 0 ? (
          <p className="mt-2 text-sm text-gray-400">No systems recorded for this project.</p>
        ) : (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-gray-400">
                <tr>
                  <th className="py-1.5 pr-4 font-medium">System</th>
                  <th className="py-1.5 pr-4 font-medium">Brand</th>
                  <th className="w-20 py-1.5 text-center font-medium">MS</th>
                  <th className="w-20 py-1.5 text-center font-medium">DWG</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {project.systems.map((system) => (
                  <tr key={system.id}>
                    <td className="py-1.5 pr-4 text-navy-900">{system.name}</td>
                    <td className="py-1.5 pr-4 text-gray-600">{system.brand || "—"}</td>
                    <td className="py-1.5 text-center">{system.method_statement ? "✓" : "—"}</td>
                    <td className="py-1.5 text-center">{system.drawing ? "✓" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {project.other_information && (
        <section className="mt-4 rounded-xl border border-gray-200 bg-white p-5 text-sm">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-400">Other Information</h2>
          <p className="mt-1 whitespace-pre-wrap text-navy-900">{project.other_information}</p>
        </section>
      )}
    </>
  );
}

function InfoRow({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <div className="text-xs text-gray-400">{label}</div>
      <div className="text-navy-900">{value || "—"}</div>
    </div>
  );
}
