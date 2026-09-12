import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { ApiError, api, apiUrl } from "../lib/api";
import { PROJECT_EDITOR_ROLES, type Compliance, type ComplianceSystem, type DraftMail, type SpecMatch } from "../lib/types";
import { useProject } from "./ProjectWorkspace";

function specHref(projectId: number, spec: SpecMatch): string {
  const query = new URLSearchParams({ path: spec.path });
  if (spec.member) query.set("member", spec.member);
  return apiUrl(`/projects/${projectId}/compliance/file?${query}#page=${spec.first_page ?? 1}`);
}

export function ProjectCompliancePage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);

  const [data, setData] = useState<Compliance | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    (refresh = false) => {
      setBusy(true);
      return api
        .get<Compliance>(`/projects/${project.id}/compliance${refresh ? "?refresh=true" : ""}`)
        .then(setData)
        .catch((err) => setError(err instanceof ApiError ? err.message : "Failed to search the project folder"))
        .finally(() => setBusy(false));
    },
    [project.id]
  );

  useEffect(() => {
    setData(null);
    setError(null);
    load();
  }, [load]);

  const systems = data?.systems ?? [];
  const system = systems.find((s) => s.code === tab) ?? systems[0];

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="text-2xl font-bold text-navy-900">Compliance Statement</h1>
          <p className="mt-1 text-sm text-gray-500">
            The specification each system must be answered against, as the project folder holds it.
          </p>
        </div>
        <button
          onClick={() => load(true)}
          disabled={busy}
          className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
        >
          {busy ? "Searching..." : "Search again"}
        </button>
      </div>

      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {data?.warnings.map((warning) => (
        <div key={warning} className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {warning}
        </div>
      ))}

      {!data ? (
        !error && (
          <div className="mt-6 rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500">
            Searching the project folder for the specifications...
          </div>
        )
      ) : systems.length === 0 ? (
        <div className="mt-6 rounded-xl border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-400">
          This project has no systems yet — they come from the DRF, the Design Sheets and the BOQ.
        </div>
      ) : (
        <>
          <div className="mt-4 flex flex-wrap gap-1 border-b border-gray-200">
            {systems.map((entry) => (
              <button
                key={entry.code}
                onClick={() => setTab(entry.code)}
                className={`-mb-px flex items-center gap-2 rounded-t-lg border-b-2 px-4 py-2 text-sm font-medium ${
                  entry.code === system?.code
                    ? "border-brand-600 text-brand-700"
                    : "border-transparent text-gray-500 hover:text-navy-900"
                }`}
              >
                {entry.code}
                <span className="text-xs text-gray-400">{entry.name}</span>
                <span
                  className={`h-2 w-2 rounded-full ${entry.specs.length ? "bg-green-500" : "bg-amber-500"}`}
                  title={entry.specs.length ? "Specification found" : "No specification found"}
                />
              </button>
            ))}
          </div>

          {system && (
            <SystemPanel
              key={system.code}
              projectId={project.id}
              searched={data.searched}
              system={system}
              canEdit={canEdit}
              onUploaded={setData}
            />
          )}
        </>
      )}
    </div>
  );
}

function SystemPanel({
  projectId,
  system,
  searched,
  canEdit,
  onUploaded,
}: {
  projectId: number;
  system: ComplianceSystem;
  searched: string | null;
  canEdit: boolean;
  onUploaded: (data: Compliance) => void;
}) {
  const [mail, setMail] = useState<DraftMail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function draft() {
    setBusy(true);
    setError(null);
    try {
      setMail(await api.get<DraftMail>(`/projects/${projectId}/compliance/draft-mail?system_code=${system.code}`));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not prepare the mail");
    } finally {
      setBusy(false);
    }
  }

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    const body = new FormData();
    body.append("file", file);
    body.append("system_code", system.code);
    try {
      onUploaded(await api.upload<Compliance>(`/projects/${projectId}/compliance/specs`, body));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not upload the specification");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-4">
      {error && <div className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

      {system.specs.length > 0 ? (
        <>
          <div className="text-sm text-gray-600">
            {system.specs.length} specification{system.specs.length === 1 ? "" : "s"} found for {system.name}.
          </div>
          <ul className="mt-3 space-y-3">
            {system.specs.map((spec, i) => (
              <li key={i} className="rounded-xl border border-gray-200 bg-white p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="font-semibold text-navy-900">
                      {spec.section_no && <span className="mr-2 rounded-md bg-blue-50 px-2 py-0.5 text-xs text-brand-700">{spec.section_no}</span>}
                      {spec.heading ?? spec.filename}
                    </div>
                    <div className="mt-1 break-all text-xs text-gray-500" title={spec.path}>
                      {spec.uploaded ? "Uploaded to the platform · " : ""}
                      {spec.path}
                      {spec.member && <span className="text-gray-400"> → {spec.member}</span>}
                    </div>
                    <div className="mt-1 text-xs text-gray-500">
                      {spec.kind === "section"
                        ? `Section of a larger specification, pages ${spec.first_page}–${spec.last_page} (${spec.pages} pages)`
                        : `${spec.pages ?? "?"} pages`}
                      {spec.matched_on === "heading" ? " · found by its heading" : " · found by its title"}
                    </div>
                  </div>
                  <a
                    href={specHref(projectId, spec)}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-700"
                  >
                    Open
                  </a>
                </div>
                {spec.snippet && (
                  <p className="mt-3 border-t border-gray-100 pt-3 text-xs leading-relaxed text-gray-600">{spec.snippet}…</p>
                )}
              </li>
            ))}
          </ul>
          {canEdit && (
            <div className="mt-3 text-xs text-gray-500">
              Not the right document?{" "}
              <button onClick={() => input.current?.click()} className="font-medium text-brand-600 hover:underline">
                Upload the specification
              </button>
              .
            </div>
          )}
        </>
      ) : (
        <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-6">
          <div className="font-semibold text-navy-900">No {system.name} specification is available for this project.</div>
          <p className="mt-1 text-sm text-amber-900">
            Nothing in {searched ? "the project folder" : "the project"} holds a specification for {system.code}. Ask the
            contractor for it, or upload the copy you have.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              onClick={draft}
              disabled={busy}
              className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
            >
              No specs available — prepare draft mail to contractor
            </button>
            {canEdit && (
              <button
                onClick={() => input.current?.click()}
                disabled={busy}
                className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-60"
              >
                Upload the specification
              </button>
            )}
          </div>
        </div>
      )}

      <input
        ref={input}
        type="file"
        accept=".pdf"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) upload(file);
          e.target.value = "";
        }}
      />

      {mail && (
        <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-semibold text-navy-900">Draft mail to the contractor</h2>
            <div className="flex gap-2">
              <button
                onClick={() => {
                  navigator.clipboard?.writeText(`Subject: ${mail.subject}\n\n${mail.body}`);
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1500);
                }}
                className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                {copied ? "Copied" : "Copy"}
              </button>
              <a
                href={`mailto:${mail.to ?? ""}?subject=${encodeURIComponent(mail.subject)}&body=${encodeURIComponent(mail.body)}`}
                className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-700"
              >
                Open in mail
              </a>
            </div>
          </div>
          <div className="mt-2 text-xs text-gray-500">
            To: {mail.to_name ?? "the contractor"}
            {mail.to ? ` <${mail.to}>` : " (no address on the project — fill it in Project Info)"}
          </div>
          <div className="mt-1 text-sm font-medium text-navy-900">{mail.subject}</div>
          <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-gray-50 p-3 text-sm text-gray-800">{mail.body}</pre>
          <p className="mt-2 text-xs text-gray-400">
            A draft: read it over and send it yourself — the platform does not send mail.
          </p>
        </section>
      )}
    </div>
  );
}
