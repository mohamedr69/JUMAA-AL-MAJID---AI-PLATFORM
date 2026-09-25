import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate, useOutletContext, useParams } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import type { Project } from "../lib/types";

export interface ProjectContext {
  project: Project;
  /** Replace the workspace's copy after a save, so the sidebar and the other
   * sections show the new values without a reload. */
  setProject: (project: Project) => void;
}

/** Sections of the project workspace, in the order the platform's design
 * lays them out. The ones still being built are listed too and say so when
 * opened (`soon`), rather than being hidden: the nav is the map of the
 * workspace, and a map with nothing where a section will be is its own kind
 * of wrong. */
const SECTIONS = [
  { to: ".", label: "Project Home", end: true },
  { to: "info", label: "Project Info", end: false },
  // Every file of the project, read from the OneDrive folder: what the last
  // sync did with each, in one place rather than as warnings on each page.
  { to: "sync", label: "File Sync", end: false },
  { to: "documents", label: "Documents", end: false },
  { to: "boq", label: "BOQ", end: false },
  { to: "materials", label: "Proposed Materials", end: false },
  { to: "calculations", label: "Calculations", end: false },
  { to: "compliance", label: "Compliance Statement", end: false },
  { to: "submittal", label: "Material Submittals", end: false },
  // Locked when the DRF says the shop drawings are not ours: the tab is
  // shown and says so, rather than being hidden or opening an empty page.
  { to: "drawings", label: "Drawings", end: false, needsDrawings: true },
  { to: "logs", label: "Logs", end: false },
  { to: "om-manual", label: "O&M Manual", end: false, soon: true },
  { to: "reports", label: "Reports", end: false, soon: true },
  { to: "team", label: "Project Team", end: false, soon: true },
  { to: "settings", label: "Settings", end: false, soon: true },
];

type Section = (typeof SECTIONS)[number];

export function ProjectWorkspace() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  /** A section that is not work we owe on this project, and so is not
   * part of this job's map at all. Drawings on a project we do not draw
   * is not an empty tab -- there is nothing there to have. */
  const outOfScope = (section: Section) =>
    "needsDrawings" in section && section.needsDrawings === true && project?.drawings_in_scope === false;
  const sections = SECTIONS.filter((section) => !outOfScope(section));

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

        {/* Narrow screens: one picker instead of a strip of thirteen links. */}
        <label className="mt-3 block text-xs font-medium text-gray-500 lg:hidden">
          Section
          <select
            className="input mt-1"
            value={currentSection(location.pathname, project.id)}
            onChange={(e) => navigate(e.target.value === "." ? `/projects/${project.id}` : `/projects/${project.id}/${e.target.value}`)}
          >
            {sections.map((section) => (
              <option key={section.label} value={section.to}>
                {section.label}
                {section.soon ? " (not available yet)" : ""}
              </option>
            ))}
          </select>
        </label>

        <nav aria-label="Project sections" className="mt-3 hidden gap-1 lg:flex lg:flex-col">
          {sections.map((section) => (
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
              <span className="flex items-center justify-between gap-2">
                {section.label}
                {section.soon && (
                  <span className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-semibold text-gray-500">not available yet</span>
                )}
              </span>
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

/** The section a workspace path is in: "boq" for /projects/3/boq/reread. */
function currentSection(pathname: string, projectId: number): string {
  const rest = pathname.replace(new RegExp(`^/projects/${projectId}/?`), "");
  const first = rest.split("/")[0];
  return SECTIONS.some((section) => section.to === first) ? first : ".";
}

export function useProject(): ProjectContext {
  return useOutletContext<ProjectContext>();
}
