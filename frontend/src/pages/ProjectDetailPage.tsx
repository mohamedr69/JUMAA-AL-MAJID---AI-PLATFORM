import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import type { Project } from "../lib/types";

// Minimal stub -- the full Project Home dashboard (nav, BOQ, etc.) is Phase 3.
export function ProjectDetailPage() {
  const { id } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<Project>(`/projects/${id}`)
      .then(setProject)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Failed to load project"));
  }, [id]);

  if (error) return <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>;
  if (!project) return <div className="text-sm text-gray-400">Loading...</div>;

  const systems = project.systems ? project.systems.split(",") : [];

  return (
    <div className="mx-auto max-w-2xl">
      <div className="flex items-center gap-2 text-sm text-green-700">
        <span className="h-2 w-2 rounded-full bg-green-500" />
        Project created successfully
      </div>
      <h1 className="mt-2 text-2xl font-bold text-navy-900">
        EP-{project.ep_number}
        {project.project_name && ` — ${project.project_name}`}
      </h1>

      <div className="mt-6 grid grid-cols-2 gap-4 rounded-xl border border-gray-200 bg-white p-5 text-sm">
        <InfoRow label="Client" value={project.client} />
        <InfoRow label="Consultant" value={project.consultant} />
        <InfoRow label="Contractor" value={project.contractor} />
        <InfoRow label="Location" value={project.location} />
        <InfoRow label="Plot Number" value={project.plot_number} />
        <InfoRow label="Scope of Work" value={project.scope_of_work} />
        <InfoRow label="Systems" value={systems.length ? systems.join(", ") : null} />
        <InfoRow label="Status" value={project.status} />
      </div>

      {project.other_information && (
        <div className="mt-4 rounded-xl border border-gray-200 bg-white p-4 text-sm">
          <div className="text-xs font-semibold uppercase text-gray-400">Other Information</div>
          <p className="mt-1 whitespace-pre-wrap text-navy-900">{project.other_information}</p>
        </div>
      )}

      <div className="mt-6 rounded-xl border border-dashed border-gray-300 bg-white p-6 text-center text-sm text-gray-400">
        Project Home, BOQ, and the rest of the workspace open up in the next phase.
      </div>

      <Link to="/projects" className="mt-4 inline-block text-sm font-medium text-brand-600">
        &larr; Back to Open Project
      </Link>
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
