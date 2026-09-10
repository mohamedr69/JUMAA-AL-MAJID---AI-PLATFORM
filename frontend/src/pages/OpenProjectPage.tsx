import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import type { Project } from "../lib/types";

export function OpenProjectPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<Project[]>("/projects")
      .then(setProjects)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Failed to load projects"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-xl font-bold text-navy-900">Open Project</h1>
      <p className="mt-1 text-sm text-gray-500">Projects previously created in this platform.</p>

      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {loading && <div className="mt-6 text-sm text-gray-400">Loading...</div>}

      {!loading && projects.length === 0 && !error && (
        <div className="mt-6 rounded-xl border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-400">
          No projects yet. Use Create Project to start one.
        </div>
      )}

      <div className="mt-4 space-y-2">
        {projects.map((p) => (
          <Link
            key={p.id}
            to={`/projects/${p.id}`}
            className="block rounded-xl border border-gray-200 bg-white px-4 py-3 hover:border-brand-300 hover:shadow-sm"
          >
            <div className="flex items-center justify-between">
              <div>
                <div className="font-semibold text-navy-900">
                  EP-{p.ep_number} {p.project_name && `— ${p.project_name}`}
                </div>
                <div className="text-xs text-gray-500">{p.client ?? "No client on file"}</div>
              </div>
              <span
                className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                  p.status === "active" ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-500"
                }`}
              >
                {p.status}
              </span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
