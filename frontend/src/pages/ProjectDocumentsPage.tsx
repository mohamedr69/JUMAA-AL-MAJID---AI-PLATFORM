import { useProject } from "./ProjectWorkspace";

export function ProjectDocumentsPage() {
  const { project } = useProject();

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold text-navy-900">Documents</h1>
      <p className="mt-1 text-sm text-gray-500">
        Files matched in the project archive when this project was created.
      </p>

      <section className="mt-6 rounded-xl border border-gray-200 bg-white p-5">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-400">
          Design Request Form
        </h2>
        <DocumentPath path={project.drf_document_path} />
      </section>

      <section className="mt-4 rounded-xl border border-gray-200 bg-white p-5">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-400">
          Design Sheets
        </h2>
        {project.design_sheets.length === 0 ? (
          <p className="mt-2 text-sm text-gray-400">No Design Sheets matched.</p>
        ) : (
          <ul className="mt-2 divide-y divide-gray-100">
            {project.design_sheets.map((sheet) => (
              <li key={sheet.id} className="py-2">
                <div className="text-sm font-medium text-navy-900">
                  {sheet.system_code ?? "Unlabelled system"}
                </div>
                <div className="break-all text-xs text-gray-500">{sheet.document_path}</div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-4 rounded-xl border border-gray-200 bg-white p-5">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-400">
          Source Folder
        </h2>
        <DocumentPath path={project.source_folder_path} />
      </section>
    </div>
  );
}

function DocumentPath({ path }: { path: string | null }) {
  if (!path) return <p className="mt-2 text-sm text-gray-400">Not recorded.</p>;
  return <p className="mt-2 break-all text-sm text-navy-900">{path}</p>;
}
