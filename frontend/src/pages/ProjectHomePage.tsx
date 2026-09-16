import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { JobProgress } from "../components/JobProgress";
import { ReadinessPanel } from "../components/ReadinessPanel";
import { SyncDocumentsCard } from "../components/SyncDocumentsCard";
import { useAuth } from "../context/AuthContext";
import { ApiError, api } from "../lib/api";
import { quantityTotals } from "../lib/boq";
import { useJob } from "../lib/useJob";
import { PROJECT_EDITOR_ROLES, type ProjectBoqItem, type Readiness } from "../lib/types";
import { useProject } from "./ProjectWorkspace";

export function ProjectHomePage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);
  const { state } = useLocation();
  const justCreated = Boolean((state as { justCreated?: boolean } | null)?.justCreated);

  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [readinessError, setReadinessError] = useState<string | null>(null);

  const loadReadiness = useCallback(() => {
    api
      .get<Readiness>(`/projects/${project.id}/readiness`)
      .then((r) => {
        setReadiness(r);
        setReadinessError(null);
      })
      .catch((err) => setReadinessError(err instanceof ApiError ? err.message : "Could not load readiness"));
  }, [project.id]);

  useEffect(() => {
    loadReadiness();
  }, [loadReadiness]);

  const intake = useJob(project.id, "documents_intake", `/projects/${project.id}/jobs/documents-intake`, () => loadReadiness());
  const checking = intake.active;

  async function checkDocuments() {
    await intake.start();
  }

  // A plain read: opening Home must not trigger the Design Sheet extraction,
  // which is the BOQ tab's job.
  const [boq, setBoq] = useState<ProjectBoqItem[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    api
      .get<ProjectBoqItem[]>(`/projects/${project.id}/boq`)
      .then((items) => {
        if (!cancelled) setBoq(items);
      })
      .catch(() => {
        if (!cancelled) setBoq([]);
      });
    return () => {
      cancelled = true;
    };
  }, [project.id]);

  const bySystem = useMemo(() => {
    const groups = new Map<string, ProjectBoqItem[]>();
    for (const item of boq ?? []) {
      const key = item.system_code ?? "Unassigned";
      groups.set(key, [...(groups.get(key) ?? []), item]);
    }
    return [...groups.entries()].map(([system, items]) => ({ system, totals: quantityTotals(items) }));
  }, [boq]);

  const subtitle = [project.client, project.location].filter(Boolean).join(" · ");

  return (
    <div className="max-w-4xl">
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
      {subtitle && <p className="mt-1 text-sm text-gray-500">{subtitle}</p>}

      <div className="mt-6">
        {readiness ? (
          <>
            {/* The project's first sync is its initial processing, started from
                here once; after that the folder is read only on "Sync documents". */}
            <div className="mb-4">
              <SyncDocumentsCard projectId={project.id} canEdit={canEdit} autoStart onSynced={() => loadReadiness()} />
            </div>
            <ReadinessPanel readiness={readiness} onCheckDocuments={canEdit ? checkDocuments : undefined} checking={checking} />
            {intake.job && intake.active && <JobProgress job={intake.job} what="the document check" onCancel={intake.cancel} />}
          </>
        ) : readinessError ? (
          <div role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
            {readinessError}
          </div>
        ) : (
          <div className="rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-400">Checking readiness...</div>
        )}
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card title="Project" link={{ to: "info", label: "Project Info" }}>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
            <Fact label="Client" value={project.client} />
            <Fact label="Consultant" value={project.consultant} />
            <Fact label="Contractor" value={project.contractor} />
            <Fact label="Scope" value={project.scope_of_work} />
          </dl>
        </Card>

        <Card title="Systems" link={{ to: "info", label: "Edit" }}>
          {project.systems.length === 0 ? (
            <p className="text-sm text-gray-400">No systems recorded.</p>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {project.systems.map((system) => (
                <li key={system.id} className="flex flex-wrap items-center gap-x-2">
                  <span className="text-navy-900">{system.name}</span>
                  {system.brand && <span className="text-gray-500">{system.brand}</span>}
                  {system.method_statement && <Tag>MS</Tag>}
                  {system.drawing && <Tag>DWG</Tag>}
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Bill of Quantities" link={{ to: "boq", label: "Open BOQ" }}>
          {boq === null ? (
            <p className="text-sm text-gray-400">Loading...</p>
          ) : bySystem.length === 0 ? (
            <p className="text-sm text-gray-400">
              {project.design_sheets.length > 0
                ? "Not read yet. Opening the BOQ reads the Design Sheets."
                : "No lines yet."}
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-gray-400">
                <tr>
                  <th className="pb-1 font-medium">System</th>
                  <th className="pb-1 text-right font-medium">Lines</th>
                  <th className="pb-1 text-right font-medium">Total qty</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {bySystem.map(({ system, totals }) => (
                  <tr key={system}>
                    <td className="py-1 text-navy-900">{system}</td>
                    <td className="py-1 text-right tabular-nums text-gray-600">{totals.lines}</td>
                    <td className="py-1 text-right tabular-nums text-gray-600">
                      {totals.units.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {project.boq_extraction_warnings && project.boq_extraction_warnings.length > 0 && (
            <p className="mt-2 text-xs text-amber-700">
              {project.boq_extraction_warnings.length} Design Sheet
              {project.boq_extraction_warnings.length > 1 ? "s" : ""} could not be read.
            </p>
          )}
        </Card>

        <Card title="Documents" link={{ to: "documents", label: "Documents" }}>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
            <dt className="text-gray-400">DRF</dt>
            <dd className={project.drf_document_path ? "text-navy-900" : "text-amber-700"}>
              {project.drf_document_path ? "Found" : "Not found"}
            </dd>
            <dt className="text-gray-400">Design Sheets</dt>
            <dd className={project.design_sheets.length > 0 ? "text-navy-900" : "text-amber-700"}>
              {project.design_sheets.length > 0
                ? project.design_sheets.map((sheet) => sheet.system_code ?? "?").join(", ")
                : "Not found"}
            </dd>
          </dl>
        </Card>
      </div>
    </div>
  );
}

function Card({
  title,
  link,
  children,
}: {
  title: string;
  link: { to: string; label: string };
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-400">{title}</h2>
        <Link to={link.to} className="text-xs font-medium text-brand-600 hover:text-brand-700">
          {link.label} &rarr;
        </Link>
      </div>
      {children}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string | null }) {
  return (
    <>
      <dt className="text-gray-400">{label}</dt>
      <dd className="text-navy-900">{value || "—"}</dd>
    </>
  );
}

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500">{children}</span>
  );
}
