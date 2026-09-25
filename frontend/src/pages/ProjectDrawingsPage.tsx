import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { useAuth } from "../context/AuthContext";
import { PROJECT_EDITOR_ROLES } from "../lib/types";
import { useOnProjectChange } from "../lib/projectChanges";
import { useProject } from "./ProjectWorkspace";
import { RequiredDrawingsTab } from "../components/drawings/RequiredDrawingsTab";
import { DrawingsLogTab } from "../components/drawings/DrawingsLogTab";
import { ReviewIssuesTab } from "../components/drawings/ReviewIssuesTab";
import { ActivityTab } from "../components/drawings/ActivityTab";
import type { DrawingsSummary, SystemSummary } from "../components/drawings/types";
import { when } from "../components/drawings/types";

const TABS = [
  { key: "log", label: "Drawings Log" },
  { key: "required", label: "Actions Required" },
  { key: "issues", label: "Review & Issues" },
  { key: "activity", label: "Activity / History" },
] as const;
type Tab = (typeof TABS)[number]["key"];

const SYSTEM_ICON: Record<string, string> = { FAS: "🔥", ELS: "💡", PAVA: "🔊", VES: "📢" };

/** The Drawings page: the building's floors, from BOQ > As per IFC
 *  Drawings, and each system's shop drawings at every revision, from the
 *  project folder (Sync documents). Fire alarm and emergency lighting share
 *  the floors and nothing else: the system cards choose which one every
 *  tab shows. */
export function ProjectDrawingsPage() {
  const { project } = useProject();
  // Reachable by its address even while the tab is locked, so it says the
  // same thing here rather than loading a log of nothing.
  if (project.drawings_in_scope === false) return <NotOurScope />;
  return <DrawingsWorkspace />;
}

/** The DRF's Systems table says no drawing is required on any of this
 * project's systems: we supply and commission it, someone else draws it. */
function NotOurScope() {
  return (
    <div className="mx-auto max-w-xl rounded-xl border border-gray-200 bg-white p-6 text-center">
      <h1 className="text-lg font-bold text-navy-900">Drawings are not in our scope</h1>
      <p className="mt-2 text-sm text-gray-600">
        The DRF marks no drawing against any system on this project, so the shop drawings are not ours to produce and
        no drawing folders are made for it.
      </p>
      <p className="mt-3 text-xs text-gray-400">
        If that is wrong, tick the drawing column for the system on Project Info and this tab opens.
      </p>
    </div>
  );
}

function DrawingsWorkspace() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);
  const [summary, setSummary] = useState<DrawingsSummary | null>(null);
  const [error, setError] = useState("");
  const tabKey = `drawings.tab.${project.id}`;
  const systemKey = `drawings.system.${project.id}`;
  const [tab, setTab] = useState<Tab>(() => {
    try {
      const saved = localStorage.getItem(tabKey);
      return TABS.some((t) => t.key === saved) ? (saved as Tab) : "log";
    } catch {
      return "log";
    }
  });
  const [system, setSystem] = useState<string | null>(() => {
    try {
      return localStorage.getItem(systemKey);
    } catch {
      return null;
    }
  });
  // A row of the log or an issue asks to open one drawing's details.
  const [openDrawing, setOpenDrawing] = useState<number | null>(null);

  const loadSummary = useCallback(() => {
    api
      .get<DrawingsSummary>(`/projects/${project.id}/drawings/summary`)
      .then((s) => {
        setSummary(s);
        setError("");
      })
      .catch((e) => setError(`The drawings could not be loaded: ${e.message}`));
  }, [project.id]);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);
  // The records are the sync's (documents) and the engineer's (drawing): either changing reaches the cards.
  useOnProjectChange(["documents", "drawing"], loadSummary);

  const systems = summary?.systems ?? [];
  // The system on show: the remembered one if the project has it, else its first.
  const shown: SystemSummary | null = systems.find((s) => s.code === system) ?? systems[0] ?? null;
  const shownCode = shown?.code ?? system ?? "FAS";

  const choose = (key: Tab) => {
    setTab(key);
    try {
      localStorage.setItem(tabKey, key);
    } catch {
      /* storage blocked: the tab is not remembered */
    }
  };
  const chooseSystem = (code: string) => {
    setSystem(code);
    setOpenDrawing(null);
    try {
      localStorage.setItem(systemKey, code);
    } catch {
      /* storage blocked: the system is not remembered */
    }
  };
  const goToIssues = () => choose("issues");
  const goToDrawing = (id: number) => {
    setOpenDrawing(id);
    choose("log");
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">Drawings</h1>
          <p className="mt-2 text-sm text-gray-500">Manage shop drawings and track revision status per floor and system.</p>
        </div>
        <div className="grid grid-cols-3 divide-x divide-gray-200 rounded-xl border border-gray-200 bg-white text-sm">
          <div className="px-5 py-3">
            <div className="text-gray-500">Project</div>
            <div className="mt-1 font-semibold text-navy-900">{project.project_name}</div>
          </div>
          <div className="px-5 py-3">
            <div className="text-gray-500">Project No.</div>
            <div className="mt-1 font-semibold text-navy-900">EP-{project.ep_number}</div>
          </div>
          <div className="px-5 py-3">
            <div className="text-gray-500">Last Sync</div>
            <div className="mt-1 font-semibold text-navy-900">
              <span className={`mr-1.5 inline-block h-2 w-2 rounded-full ${summary?.synced_at ? "bg-emerald-500" : "bg-gray-300"}`} />
              {summary?.synced_at ? when(summary.synced_at) : "Not synced yet"}
            </div>
          </div>
        </div>
      </div>

      {error && <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm text-rose-800">{error}</div>}

      {/* The systems, side by side: both are seen without switching, and the selected one is every tab's. */}
      {systems.length > 0 && (
        <div className={`grid gap-3 ${systems.length > 1 ? "md:grid-cols-2" : ""} ${systems.length > 2 ? "xl:grid-cols-3" : ""}`}>
          {systems.map((s) => (
            <SystemCard key={s.code} system={s} selected={s.code === shownCode} onSelect={() => chooseSystem(s.code)} onIssues={() => { chooseSystem(s.code); goToIssues(); }} />
          ))}
        </div>
      )}

      <div className="flex flex-wrap gap-1 rounded-xl border border-gray-200 bg-white p-1">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => choose(t.key)}
            aria-pressed={t.key === tab}
            className={`flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold ${
              t.key === tab ? "bg-brand-50 text-brand-700" : "text-gray-600 hover:bg-gray-50"
            }`}
          >
            {t.label}
            {t.key === "issues" && shown && shown.review_items > 0 && (
              <span className="rounded-full bg-rose-600 px-1.5 py-0.5 text-[11px] font-semibold text-white">{shown.review_items}</span>
            )}
          </button>
        ))}
      </div>

      {tab === "required" && <RequiredDrawingsTab projectId={project.id} canEdit={canEdit} system={shownCode} onSystem={chooseSystem} />}
      {tab === "log" && (
        <DrawingsLogTab
          projectId={project.id}
          canEdit={canEdit}
          system={shownCode}
          summary={shown}
          openDrawing={openDrawing}
          onOpenDrawing={setOpenDrawing}
          onSystemsKnown={(codes) => { if (system && !codes.includes(system) && codes[0]) chooseSystem(codes[0]); }}
          onChanged={loadSummary}
          onReview={goToIssues}
        />
      )}
      {tab === "issues" && <ReviewIssuesTab projectId={project.id} canEdit={canEdit} system={shownCode} onOpenDrawing={goToDrawing} onChanged={loadSummary} />}
      {tab === "activity" && <ActivityTab projectId={project.id} system={shownCode} />}
    </div>
  );
}

/** One system, at a glance: approved of floors, and what needs attention. */
function SystemCard({ system, selected, onSelect, onIssues }: { system: SystemSummary; selected: boolean; onSelect: () => void; onIssues: () => void }) {
  const share = system.floors ? Math.round((system.approved_total / system.floors) * 100) : 0;
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect()}
      aria-pressed={selected}
      className={`flex cursor-pointer items-center gap-4 rounded-xl border-2 bg-white px-5 py-4 text-left transition ${
        selected ? "border-brand-600 shadow-sm" : "border-gray-200 hover:border-gray-300"
      }`}
    >
      <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-gray-50 text-2xl" aria-hidden="true">
        {SYSTEM_ICON[system.code] ?? "📐"}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <div className="text-base font-bold text-navy-900">
            {system.name} <span className="font-normal text-gray-500">({system.code})</span>
          </div>
          <span className={`h-5 w-5 rounded-full border-2 ${selected ? "border-brand-600 bg-brand-600" : "border-gray-300"}`} aria-hidden="true">
            {selected && <svg viewBox="0 0 20 20" className="h-4 w-4"><path d="M6 10.5l2.5 2.5L14 7.5" fill="none" stroke="white" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>}
          </span>
        </div>
        <div className="mt-0.5 text-sm text-gray-700">
          <span className="text-lg font-semibold text-navy-900">{system.approved_total} / {system.floors}</span> Approved
          <span className="ml-3 text-gray-500">{share}%</span>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-gray-100">
          <div className="h-full rounded-full bg-emerald-500" style={{ width: `${share}%` }} />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-600">
          {system.review_items > 0 ? (
            <button type="button" onClick={(e) => { e.stopPropagation(); onIssues(); }} className="font-medium text-rose-700 hover:underline">
              ⚠ {system.review_items} Review {system.review_items === 1 ? "Item" : "Items"}
            </button>
          ) : (
            <span className="text-emerald-700">✓ Nothing to review</span>
          )}
          {system.candidates > 0 && <span className="text-indigo-700">ⓘ {system.candidates} newer {system.candidates === 1 ? "revision" : "revisions"} available</span>}
        </div>
      </div>
    </div>
  );
}
