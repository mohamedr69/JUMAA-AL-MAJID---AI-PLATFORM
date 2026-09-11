import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useOutletContext, useParams } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import type { Project } from "../lib/types";

export interface ProjectContext {
  project: Project;
  /** Replace the workspace's copy after a save, so the sidebar and the other
   * sections show the new values without a reload. */
  setProject: (project: Project) => void;
}

/** Sections of the project workspace. Only sections that are actually built
 * are listed -- the left nav is the map of the workspace, so a link to
 * nothing reads as a broken feature rather than a planned one. */
const SECTIONS = [
  { to: ".", label: "Home", end: true },
  { to: "info", label: "Project Info", end: false },
  { to: "boq", label: "BOQ", end: false },
  { to: "documents", label: "Documents", end: false },
];

export function ProjectWorkspace() {
  const { id } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setProject(null);
    setError(null);
    api
      .get<Project>(`/projects/${id}`)
      .then((p) => {
        if (!cancelled) setProject(p);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Failed to load project");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (error) {
    return <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>;
  }
  if (!project) return <div className="text-sm text-gray-400">Loading...</div>;

  const context: ProjectContext = { project, setProject };

  return (
    <div className="flex flex-col gap-6 lg:flex-row">
      <aside className="shrink-0 lg:w-60">
        <div className="rounded-xl border border-gray-200 bg-white p-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-400">Project</div>
          <div className="mt-1 font-bold text-navy-900">EP-{project.ep_number}</div>
          {project.project_name && (
            <div className="mt-0.5 text-sm leading-snug text-gray-600">{project.project_name}</div>
          )}
          <span
            className={`mt-2 inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
              project.status === "active"
                ? "bg-green-50 text-green-700"
                : "bg-gray-100 text-gray-500"
            }`}
          >
            {project.status}
          </span>
        </div>

        <nav className="mt-3 flex gap-1 lg:flex-col">
          {SECTIONS.map((section) => (
            <NavLink
              key={section.label}
              to={section.to}
              end={section.end}
              className={({ isActive }) =>
                `rounded-lg px-3 py-2 text-sm font-medium ${
                  isActive
                    ? "bg-brand-50 text-brand-700"
                    : "text-gray-600 hover:bg-gray-100 hover:text-navy-900"
                }`
              }
            >
              {section.label}
            </NavLink>
          ))}
        </nav>

        <Link
          to="/projects"
          className="mt-3 inline-block px-3 text-sm font-medium text-gray-500 hover:text-brand-600"
        >
          &larr; All projects
        </Link>
      </aside>

      <div className="min-w-0 flex-1">
        <Outlet context={context} />
      </div>
    </div>
  );
}

export function useProject(): ProjectContext {
  return useOutletContext<ProjectContext>();
}
