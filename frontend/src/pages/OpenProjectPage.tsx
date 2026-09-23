import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ApiError, api } from "../lib/api";
import type { Project, Role } from "../lib/types";

// Mirrors DELETER_ROLES in the projects router. The button is hidden for
// everyone else, but the server is what actually enforces this.
const DELETER_ROLES: Role[] = ["admin", "design_manager"];

export function OpenProjectPage() {
  const { user } = useAuth();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  /* Which projects to list. Not a permission -- anyone may open any
   * project, and two engineers may have the same one open at once -- just
   * which set is shown first: an engineer's own, an admin's all. */
  const [scope, setScope] = useState<"mine" | "all">(user?.role === "admin" ? "all" : "mine");

  const canDelete = user !== null && DELETER_ROLES.includes(user.role);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .get<Project[]>(`/projects?scope=${scope}`)
      .then((rows) => live && setProjects(rows))
      .catch((err) => live && setError(err instanceof ApiError ? err.message : "Failed to load projects"))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [scope]);

  async function removeProject(id: number) {
    setDeletingId(id);
    setError(null);
    try {
      await api.delete(`/projects/${id}`);
      setProjects((prev) => prev.filter((p) => p.id !== id));
      setConfirmingId(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to remove the project");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="mx-auto max-w-3xl">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-navy-900">Open Project</h1>
          <p className="mt-1 text-sm text-gray-500">
            {scope === "mine"
              ? "Projects you created or are assigned to."
              : "Every project on the platform."}
          </p>
        </div>
        <div className="flex overflow-hidden rounded-lg border border-gray-300 text-sm">
          {(["mine", "all"] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setScope(value)}
              aria-pressed={scope === value}
              className={`px-3 py-1.5 font-semibold ${
                scope === value ? "bg-brand-600 text-white" : "bg-white text-gray-600 hover:bg-gray-50"
              }`}
            >
              {value === "mine" ? "Mine" : "All projects"}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {loading && <div className="mt-6 text-sm text-gray-400">Loading...</div>}

      {!loading && projects.length === 0 && !error && (
        <div className="mt-6 rounded-xl border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-400">
          No projects yet. Use Create Project to start one.
        </div>
      )}

      <div className="mt-4 space-y-2">
        {projects.map((p) => {
          const confirming = confirmingId === p.id;
          const busy = deletingId === p.id;
          return (
            <div
              key={p.id}
              className="flex items-center gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 hover:border-brand-300 hover:shadow-sm"
            >
              <Link to={`/projects/${p.id}`} className="min-w-0 flex-1">
                <div className="truncate font-semibold text-navy-900">
                  EP-{p.ep_number} {p.project_name && `— ${p.project_name}`}
                </div>
                <div className="truncate text-xs text-gray-500">
                  {p.client ?? "No client on file"}
                </div>
              </Link>

              <span
                className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${
                  p.status === "active" ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-500"
                }`}
              >
                {p.status}
              </span>

              {canDelete &&
                (confirming ? (
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="text-xs text-gray-500">Remove?</span>
                    <button
                      onClick={() => removeProject(p.id)}
                      disabled={busy}
                      className="rounded-md bg-red-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-60"
                    >
                      {busy ? "Removing..." : "Confirm"}
                    </button>
                    <button
                      onClick={() => setConfirmingId(null)}
                      disabled={busy}
                      className="rounded-md border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-600 hover:bg-gray-100 disabled:opacity-60"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setConfirmingId(p.id)}
                    aria-label={`Remove project EP-${p.ep_number}`}
                    className="shrink-0 rounded-md border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-600 hover:border-red-300 hover:bg-red-50 hover:text-red-700"
                  >
                    Remove
                  </button>
                ))}
            </div>
          );
        })}
      </div>

      {canDelete && projects.length > 0 && (
        <p className="mt-3 text-xs text-gray-400">
          Removing a project deletes only this platform&apos;s record of it. The files in the
          project archive are left untouched.
        </p>
      )}
    </div>
  );
}
