import { useLocation } from "react-router-dom";
import { useProject } from "./ProjectWorkspace";

export function ProjectHomePage() {
  const { project } = useProject();
  const { state } = useLocation();
  const justCreated = Boolean((state as { justCreated?: boolean } | null)?.justCreated);

  return (
    <div className="max-w-3xl">
      {justCreated && (
        <div className="mb-3 flex items-center gap-2 text-sm text-green-700">
          <span className="h-2 w-2 rounded-full bg-green-500" />
          Project created successfully
        </div>
      )}

      <h1 className="text-2xl font-bold text-navy-900">
        EP-{project.ep_number}
        {project.project_name && ` — ${project.project_name}`}
      </h1>

      <section className="mt-6 grid grid-cols-1 gap-4 rounded-xl border border-gray-200 bg-white p-5 text-sm sm:grid-cols-2">
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
          <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-400">
            Other Information
          </h2>
          <p className="mt-1 whitespace-pre-wrap text-navy-900">{project.other_information}</p>
        </section>
      )}
    </div>
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
