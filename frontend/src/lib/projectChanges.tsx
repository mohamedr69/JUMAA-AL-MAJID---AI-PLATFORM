import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "./api";

/** What a change touched (backend app.services.project_state):
 * "submittal" -- the material submittal register; "documents" -- the
 * document index a folder sync updates (logs, drawings, samples);
 * "action" -- the project's actions. */
export type ChangeScope = "submittal" | "documents" | "action" | "drawing";

export interface ProjectChange {
  id: number;
  project_id: number;
  entity_type: string;
  entity_id: number | null;
  system: string | null;
  change_type: string;
  at: string;
}

interface ChangesOut {
  latest: number;
  changes: ProjectChange[];
}

interface ProjectChangesValue {
  /** How many times each scope has changed since the workspace opened. */
  versions: Record<string, number>;
  /** Ask for changes now rather than at the next poll. */
  refresh: () => void;
}

const ProjectChangesContext = createContext<ProjectChangesValue | null>(null);
const POLL_MS = 5000;
const ALL_SCOPES: ChangeScope[] = ["submittal", "documents", "action", "drawing"];

/** PROJECT_DATA_CHANGED for the open project. Every page of the workspace
 * is a view of the project's records, not the owner of a copy: when a
 * record changes -- on this page, on another, by another user, or by the
 * sync worker in its own process -- the backend writes a change row in the
 * same transaction, this picks it up, and each page showing that scope
 * reads its data again. No page reload. */
export function ProjectChangesProvider({ projectId, children }: { projectId: number; children: ReactNode }) {
  const [versions, setVersions] = useState<Record<string, number>>({});
  const cursor = useRef<number | null>(null);
  const busy = useRef(false);

  const poll = useCallback(async () => {
    if (busy.current || document.visibilityState === "hidden") return;
    busy.current = true;
    try {
      const since = cursor.current;
      const out = await api.get<ChangesOut>(`/projects/${projectId}/changes${since === null ? "" : `?since=${since}`}`);
      if (since !== null && out.latest !== since) {
        // Changes listed: their scopes. A cursor the server no longer has
        // (pruned, or a restored database), or more than one answer holds:
        // every scope, since which ones cannot be told.
        const scopes = out.changes.length > 0 && out.latest === out.changes[out.changes.length - 1].id
          ? [...new Set(out.changes.map((c) => c.entity_type))]
          : ALL_SCOPES;
        setVersions((prev) => {
          const next = { ...prev };
          for (const scope of scopes) next[scope] = (next[scope] ?? 0) + 1;
          return next;
        });
      }
      cursor.current = out.latest;
    } catch {
      /* offline or signed out: the next poll tries again */
    } finally {
      busy.current = false;
    }
  }, [projectId]);

  useEffect(() => {
    cursor.current = null;
    setVersions({});
    void poll();
    const timer = window.setInterval(() => void poll(), POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void poll();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [poll]);

  return (
    <ProjectChangesContext.Provider value={{ versions, refresh: () => void poll() }}>
      {children}
    </ProjectChangesContext.Provider>
  );
}

/** A number that changes whenever any of `scopes` changed in the project. */
export function useProjectVersion(...scopes: ChangeScope[]): number {
  const value = useContext(ProjectChangesContext);
  if (!value) return 0;
  return scopes.reduce((sum, scope) => sum + (value.versions[scope] ?? 0), 0);
}

/** Run `onChange` -- the page's own load -- when any of `scopes` changes
 * after the page opened. The page's first load is its own; this is every
 * load after it, without clearing what is on screen. */
export function useOnProjectChange(scopes: ChangeScope[], onChange: () => void): void {
  const version = useProjectVersion(...scopes);
  const seen = useRef(version);
  const latest = useRef(onChange);
  latest.current = onChange;
  useEffect(() => {
    if (version !== seen.current) {
      seen.current = version;
      latest.current();
    }
  }, [version]);
}

