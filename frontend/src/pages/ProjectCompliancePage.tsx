import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { ApiError, api, apiUrl } from "../lib/api";
import {
  COMPLIANCE_RESPONSES,
  PROJECT_EDITOR_ROLES,
  type AutofillScope,
  type Compliance,
  type ComplianceSystem,
  type DraftMail,
  type ReferenceIndex,
  type SpecMatch,
  type SpecVerification,
  type Statement,
  type StatementFile,
  type StatementRow,
  type StatementSummary,
  type Suggestion,
} from "../lib/types";
import { useProject } from "./ProjectWorkspace";

// --- helpers -----------------------------------------------------------------------

type SpecRef = { path: string; member: string | null; first_page: number | null };

function specHref(projectId: number, spec: SpecRef, page?: number): string {
  const query = new URLSearchParams({ path: spec.path });
  if (spec.member) query.set("member", spec.member);
  return apiUrl(`/projects/${projectId}/compliance/file?${query}#page=${page ?? spec.first_page ?? 1}`);
}

function specKey(spec: SpecRef & { last_page?: number | null }): string {
  return `${spec.path}|${spec.member ?? ""}|${spec.first_page ?? ""}|${spec.last_page ?? ""}`;
}

/** The API sends naive UTC; without a zone the browser would read it as
 * local time. */
function formatWhen(value: string): string {
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function answerable(row: StatementRow): boolean {
  return !row.heading && row.source !== "lead_in";
}

/** How a response reads at a glance: met, met with a qualification, or not. */
type Tone = "good" | "warn" | "bad" | "none";

function toneOf(response: string): Tone {
  if (!response) return "none";
  if (response === "Comply" || response === "Noted") return "good";
  if (response === "Deviation") return "bad";
  return "warn";
}

const TONE_STYLES: Record<Tone, { dot: string; text: string; border: string }> = {
  good: { dot: "bg-green-500", text: "text-green-700", border: "border-green-200" },
  warn: { dot: "bg-amber-500", text: "text-amber-700", border: "border-amber-200" },
  bad: { dot: "bg-red-500", text: "text-red-700", border: "border-red-200" },
  none: { dot: "bg-gray-300", text: "text-gray-500", border: "border-gray-300" },
};

function ToneIcon({ tone, className = "h-4 w-4" }: { tone: Tone; className?: string }) {
  const mark = tone === "good" ? "M5 12l4 4L19 7" : tone === "bad" ? "M7 7l10 10M17 7L7 17" : tone === "warn" ? "M12 7v6M12 16v1" : "";
  return (
    <span className={`inline-flex shrink-0 items-center justify-center rounded-full text-white ${TONE_STYLES[tone].dot} ${className}`}>
      {mark && (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="h-2.5 w-2.5">
          <path d={mark} />
        </svg>
      )}
    </span>
  );
}

function Sparkle({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="m12 3 1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9z" />
      <path d="M18 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z" />
    </svg>
  );
}

function PdfIcon() {
  return (
    <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-red-50 text-red-600">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
        <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
        <path d="M14 3v5h5" />
        <path d="M9 13h6M9 17h6" />
      </svg>
    </span>
  );
}

const btnPrimary =
  "inline-flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50";
const btnSecondary =
  "inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50";

// --- page ----------------------------------------------------------------------------

export function ProjectCompliancePage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);

  const [data, setData] = useState<Compliance | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // The latest statement per system, so the tab pills can say "Draft".
  const [latest, setLatest] = useState<Record<string, StatementSummary | undefined>>({});

  const load = useCallback(
    (refresh = false) => {
      setBusy(true);
      return api
        .get<Compliance>(`/projects/${project.id}/compliance${refresh ? "?refresh=true" : ""}`)
        .then(setData)
        .catch((err) => setError(errorText(err, "Failed to search the project folder")))
        .finally(() => setBusy(false));
    },
    [project.id]
  );

  useEffect(() => {
    setData(null);
    setError(null);
    load();
    api
      .get<StatementSummary[]>(`/projects/${project.id}/compliance/statements`)
      .then((items) => {
        const byCode: Record<string, StatementSummary> = {};
        for (const item of items) if (item.kind === "prepare" && !byCode[item.system_code]) byCode[item.system_code] = item;
        setLatest(byCode);
      })
      .catch(() => setLatest({}));
  }, [load, project.id]);

  const systems = data?.systems ?? [];
  const system = systems.find((s) => s.code === tab) ?? systems[0];

  return (
    <div>
      <div className="text-xs text-gray-400">
        Project workspace <span className="mx-1">/</span> Compliance
      </div>
      <div className="mt-1 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-navy-900">Compliance Statement</h1>
          <p className="mt-1 text-sm text-gray-500">Review and respond to every specification clause.</p>
        </div>
        <button onClick={() => load(true)} disabled={busy} className={btnSecondary}>
          {busy ? "Searching…" : "Search project folder again"}
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
            Searching the project folder for the specifications…
          </div>
        )
      ) : systems.length === 0 ? (
        <div className="mt-6 rounded-xl border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-400">
          This project has no systems yet — add them under Project Info, or import a Design Sheet or BOQ.
        </div>
      ) : (
        <>
          <div className="mt-4 flex flex-wrap gap-1 border-b border-gray-200">
            {systems.map((entry) => {
              const usable = entry.specs.some((s) => s.verification?.project !== "different");
              const draft = latest[entry.code];
              return (
                <button
                  key={entry.code}
                  onClick={() => setTab(entry.code)}
                  className={`-mb-px flex items-center gap-2 rounded-t-lg border-b-2 px-4 py-2 text-sm font-medium ${
                    entry.code === system?.code ? "border-brand-600 text-brand-700" : "border-transparent text-gray-500 hover:text-navy-900"
                  }`}
                >
                  {entry.code}
                  <span className="text-xs text-gray-400">{entry.name}</span>
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                      draft ? "bg-brand-50 text-brand-700" : usable ? "bg-green-50 text-green-700" : entry.specs.length ? "bg-red-50 text-red-700" : "bg-amber-50 text-amber-700"
                    }`}
                  >
                    {draft ? "Draft" : usable ? "Spec found" : entry.specs.length ? "Wrong project" : "No spec"}
                  </span>
                </button>
              );
            })}
          </div>

          {system && (
            <SystemWorkspace
              key={system.code}
              projectId={project.id}
              data={data}
              system={system}
              canEdit={canEdit}
              onSpecsChanged={setData}
              latest={latest[system.code]}
              onLatest={(summary) => setLatest((all) => ({ ...all, [system.code]: summary }))}
            />
          )}
        </>
      )}
    </div>
  );
}

// --- one system --------------------------------------------------------------------

function SystemWorkspace({
  projectId,
  data,
  system,
  canEdit,
  onSpecsChanged,
  latest,
  onLatest,
}: {
  projectId: number;
  data: Compliance;
  system: ComplianceSystem;
  canEdit: boolean;
  onSpecsChanged: (data: Compliance) => void;
  latest: StatementSummary | undefined;
  onLatest: (summary: StatementSummary | undefined) => void;
}) {
  const [selectedSpec, setSelectedSpec] = useState<string | null>(null);
  const [verifications, setVerifications] = useState<Record<string, SpecVerification>>({});
  const [statement, setStatement] = useState<Statement | null>(null);
  const [loadingStatement, setLoadingStatement] = useState(Boolean(latest));
  const [selectedClause, setSelectedClause] = useState<string | null>(null);
  const [check, setCheck] = useState<Statement | null>(null);
  const [error, setError] = useState<string | null>(null);

  const specs = system.specs.map((spec) => ({ ...spec, verification: verifications[specKey(spec)] ?? spec.verification }));
  const chosen =
    specs.find((s) => specKey(s) === selectedSpec) ?? specs.find((s) => s.verification?.project !== "different") ?? specs[0] ?? null;

  // The latest draft opens with the tab.
  useEffect(() => {
    if (!latest) {
      setLoadingStatement(false);
      return;
    }
    let cancelled = false;
    api
      .get<Statement>(`/projects/${projectId}/compliance/statements/${latest.id}`)
      .then((s) => {
        if (!cancelled) setStatement(s);
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setLoadingStatement(false);
      });
    return () => {
      cancelled = true;
    };
    // Only on first open: later statements arrive through replace().
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  function replace(next: Statement | null) {
    setStatement(next);
    onLatest(next ?? undefined);
    if (!next) setSelectedClause(null);
  }

  return (
    <>
      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      <SpecBar
        projectId={projectId}
        system={system}
        specs={specs}
        chosen={chosen}
        statementSpec={statement?.spec ?? null}
        canEdit={canEdit}
        onChoose={setSelectedSpec}
        onVerified={(spec, verdict) => setVerifications((v) => ({ ...v, [specKey(spec)]: verdict }))}
        onSpecsChanged={onSpecsChanged}
        aiAvailable={data.ai_available}
        searched={data.searched}
      />
      <div className="mt-4 grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <ClausesCard
          projectId={projectId}
          system={system}
          spec={chosen}
          statement={statement}
          loading={loadingStatement}
          canEdit={canEdit}
          selectedClause={selectedClause}
          onSelectClause={setSelectedClause}
          onStatement={replace}
          onError={setError}
        />
        <AssistantPanel
          projectId={projectId}
          system={system}
          spec={chosen}
          statement={statement}
          canEdit={canEdit}
          aiAvailable={data.ai_available}
          referenceIndex={data.references}
          selectedClause={selectedClause}
          onStatement={replace}
          onCheck={setCheck}
          onError={setError}
        />
      </div>
      {check && <CheckResults projectId={projectId} statement={check} onClose={() => setCheck(null)} />}
    </>
  );
}

// --- the specification bar -----------------------------------------------------------

function VerificationBadge({ verification }: { verification: SpecVerification | null }) {
  if (!verification) return <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-500">Not verified</span>;
  const { project, system } = verification;
  if (project === "different" || system === "different") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-xs font-semibold text-red-700">
        <ToneIcon tone="bad" className="h-3.5 w-3.5" />
        {project === "different" ? "Another project's specification" : "Another system's specification"}
      </span>
    );
  }
  if (project === "same" && system === "same") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-green-50 px-2 py-0.5 text-xs font-semibold text-green-700">
        <ToneIcon tone="good" className="h-3.5 w-3.5" />
        This project's specification{verification.decided_by === "ai" ? " (AI)" : ""}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-700">
      <ToneIcon tone="warn" className="h-3.5 w-3.5" />
      Project not confirmed
    </span>
  );
}

function SpecBar({
  projectId,
  system,
  specs,
  chosen,
  statementSpec,
  canEdit,
  onChoose,
  onVerified,
  onSpecsChanged,
  aiAvailable,
  searched,
}: {
  projectId: number;
  system: ComplianceSystem;
  specs: SpecMatch[];
  chosen: SpecMatch | null;
  statementSpec: Statement["spec"] | null;
  canEdit: boolean;
  onChoose: (key: string) => void;
  onVerified: (spec: SpecMatch, verdict: SpecVerification) => void;
  onSpecsChanged: (data: Compliance) => void;
  aiAvailable: boolean;
  searched: string | null;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mail, setMail] = useState<DraftMail | null>(null);
  const [copied, setCopied] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function upload(file: File) {
    setBusy("upload");
    setError(null);
    const body = new FormData();
    body.append("file", file);
    body.append("system_code", system.code);
    try {
      onSpecsChanged(await api.upload<Compliance>(`/projects/${projectId}/compliance/specs`, body));
    } catch (err) {
      setError(errorText(err, "Could not upload the specification"));
    } finally {
      setBusy(null);
    }
  }

  async function remove(spec: SpecMatch) {
    if (!window.confirm(`Remove the uploaded file ${spec.filename}?`)) return;
    setBusy("remove");
    setError(null);
    try {
      await api.delete(`/projects/${projectId}/compliance/specs?${new URLSearchParams({ path: spec.path })}`);
      onSpecsChanged(await api.get<Compliance>(`/projects/${projectId}/compliance`));
    } catch (err) {
      setError(errorText(err, "Could not remove the file"));
    } finally {
      setBusy(null);
    }
  }

  async function verifyWithAi(spec: SpecMatch) {
    setBusy("verify");
    setError(null);
    try {
      onVerified(
        spec,
        await api.post<SpecVerification>(`/projects/${projectId}/compliance/verify`, {
          system_code: system.code,
          path: spec.path,
          member: spec.member,
          first_page: spec.first_page,
          last_page: spec.last_page,
        })
      );
    } catch (err) {
      setError(errorText(err, "Could not verify the specification"));
    } finally {
      setBusy(null);
    }
  }

  async function draft() {
    setBusy("mail");
    setError(null);
    try {
      setMail(await api.get<DraftMail>(`/projects/${projectId}/compliance/draft-mail?system_code=${system.code}`));
    } catch (err) {
      setError(errorText(err, "Could not prepare the mail"));
    } finally {
      setBusy(null);
    }
  }

  const fileInput = (
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
  );

  if (!chosen) {
    return (
      <div className="mt-4">
        {error && <div className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
        <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-6">
          <div className="font-semibold text-navy-900">No {system.name} specification is available for this project.</div>
          <p className="mt-1 text-sm text-amber-900">
            Nothing in {searched ? "the project folder" : "the project"} holds one — not as a document, inside an archive, inside a past
            submittal, or by its first page. Ask the contractor for it, or upload the copy you have.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button onClick={draft} disabled={busy !== null} className={btnPrimary}>
              Prepare draft mail to contractor
            </button>
            {canEdit && (
              <button onClick={() => input.current?.click()} disabled={busy !== null} className={btnSecondary}>
                {busy === "upload" ? "Uploading…" : "Upload the specification"}
              </button>
            )}
          </div>
        </div>
        {fileInput}
        {mail && <MailCard mail={mail} copied={copied} onCopied={() => setCopied(true)} onUncopied={() => setCopied(false)} />}
      </div>
    );
  }

  const verification = chosen.verification;
  const unsure = !verification || verification.project === "unknown" || verification.system === "unknown";
  const others = specs.filter((s) => specKey(s) !== specKey(chosen));
  const differentFromStatement = statementSpec !== null && statementSpec.path !== chosen.path;

  return (
    <div className="mt-4">
      {error && <div className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      <div className="rounded-xl border border-gray-200 bg-white px-4 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <PdfIcon />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="font-semibold text-navy-900" title={chosen.path}>
                {chosen.member ? chosen.member.split("/").pop() : chosen.filename}
              </span>
              <span className="text-sm text-gray-400">
                {chosen.section_no ? `Section ${chosen.section_no} · ` : ""}
                {chosen.kind === "section" ? `pages ${chosen.first_page}–${chosen.last_page}` : `${chosen.pages ?? "?"} pages`}
              </span>
              <VerificationBadge verification={verification} />
              {chosen.uploaded && <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs text-brand-700">Uploaded</span>}
              {chosen.matched_on === "submittal" && (
                <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">Inside a past submittal</span>
              )}
              {chosen.matched_on === "content" && (
                <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">Found by its content</span>
              )}
            </div>
            {verification && verification.evidence[0] && (verification.project !== "same" || verification.system !== "same") && (
              <div className="mt-0.5 text-xs text-gray-500">
                {verification.names_in_spec[0] ? `Headed "${verification.names_in_spec[0]}". ` : ""}
                {verification.evidence[0]}
              </div>
            )}
            {differentFromStatement && (
              <div className="mt-0.5 text-xs text-amber-700">
                The open draft was prepared against {statementSpec?.filename}; start a new statement to use this one.
              </div>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {unsure && aiAvailable && (
              <button onClick={() => verifyWithAi(chosen)} disabled={busy !== null} className={btnSecondary}>
                {busy === "verify" ? "Verifying…" : "Verify with AI"}
              </button>
            )}
            <a href={specHref(projectId, chosen)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-sm font-semibold text-brand-700 hover:underline">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
                <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
              View specification
            </a>
          </div>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
          {others.length > 0 && (
            <button onClick={() => setShowAll((v) => !v)} className="font-medium text-brand-600 hover:underline">
              {showAll ? "Hide" : `${others.length} other ${others.length === 1 ? "document" : "documents"} found`}
            </button>
          )}
          {canEdit && (
            <button onClick={() => input.current?.click()} disabled={busy !== null} className="font-medium text-brand-600 hover:underline disabled:opacity-50">
              {busy === "upload" ? "Uploading…" : "Upload another specification"}
            </button>
          )}
          {canEdit && chosen.uploaded && (
            <button onClick={() => remove(chosen)} disabled={busy !== null} className="font-medium text-red-600 hover:underline disabled:opacity-50">
              Remove this upload
            </button>
          )}
          {verification?.project === "different" && (
            <button onClick={draft} disabled={busy !== null} className="font-medium text-brand-600 hover:underline">
              Ask the contractor for this project's specification
            </button>
          )}
        </div>
        {showAll && (
          <ul className="mt-3 space-y-2 border-t border-gray-100 pt-3">
            {others.map((spec) => (
              <li key={specKey(spec)} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <div className="min-w-0">
                  <span className="font-medium text-navy-900">{spec.member ? spec.member.split("/").pop() : spec.filename}</span>
                  <span className="ml-2 text-xs text-gray-400">
                    {spec.kind === "section" ? `pages ${spec.first_page}–${spec.last_page}` : `${spec.pages ?? "?"} pages`}
                  </span>
                  <span className="ml-2">
                    <VerificationBadge verification={spec.verification} />
                  </span>
                  <div className="truncate text-xs text-gray-400" title={spec.path}>
                    {spec.path}
                  </div>
                </div>
                <div className="flex gap-3 text-xs">
                  <a href={specHref(projectId, spec)} target="_blank" rel="noreferrer" className="font-medium text-brand-600 hover:underline">
                    View
                  </a>
                  <button onClick={() => onChoose(specKey(spec))} className="font-medium text-brand-600 hover:underline">
                    Use this one
                  </button>
                  {canEdit && spec.uploaded && (
                    <button onClick={() => remove(spec)} className="font-medium text-red-600 hover:underline">
                      Remove
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
      {fileInput}
      {mail && <MailCard mail={mail} copied={copied} onCopied={() => setCopied(true)} onUncopied={() => setCopied(false)} />}
    </div>
  );
}

function MailCard({ mail, copied, onCopied, onUncopied }: { mail: DraftMail; copied: boolean; onCopied: () => void; onUncopied: () => void }) {
  return (
    <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold text-navy-900">Draft mail to the contractor</h2>
        <div className="flex gap-2">
          <button
            onClick={() => {
              navigator.clipboard?.writeText(`Subject: ${mail.subject}\n\n${mail.body}`);
              onCopied();
              window.setTimeout(onUncopied, 1500);
            }}
            className={btnSecondary}
          >
            {copied ? "Copied" : "Copy"}
          </button>
          <a href={`mailto:${mail.to ?? ""}?subject=${encodeURIComponent(mail.subject)}&body=${encodeURIComponent(mail.body)}`} className={btnPrimary}>
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
      <p className="mt-2 text-xs text-gray-400">A draft: read it over and send it yourself — the platform does not send mail.</p>
    </section>
  );
}

// --- the clauses -----------------------------------------------------------------------

type Filter = "all" | "unanswered" | "review" | "ai";

function ClausesCard({
  projectId,
  system,
  spec,
  statement,
  loading,
  canEdit,
  selectedClause,
  onSelectClause,
  onStatement,
  onError,
}: {
  projectId: number;
  system: ComplianceSystem;
  spec: SpecMatch | null;
  statement: Statement | null;
  loading: boolean;
  canEdit: boolean;
  selectedClause: string | null;
  onSelectClause: (id: string | null) => void;
  onStatement: (statement: Statement | null) => void;
  onError: (message: string | null) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [filter, setFilter] = useState<Filter>("all");
  // Edits the engineer typed but the API has not stored yet.
  const [pending, setPending] = useState<Record<string, { response?: string; remark?: string }>>({});
  const [saveState, setSaveState] = useState<"saved" | "dirty" | "saving" | "failed">("saved");
  const saveTimer = useRef<number | null>(null);
  // The debounced save reads the edits as they are when it fires, not as
  // they were when it was scheduled.
  const pendingRef = useRef(pending);
  useEffect(() => {
    pendingRef.current = pending;
  }, [pending]);

  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    const timer = window.setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);

  const flush = useCallback(async () => {
    const edits = pendingRef.current;
    if (!statement || Object.keys(edits).length === 0) return;
    setSaveState("saving");
    try {
      const updated = await api.patch<Statement>(`/projects/${projectId}/compliance/statements/${statement.id}`, {
        rows: Object.entries(edits).map(([id, change]) => ({ id, ...change })),
      });
      setPending((current) => {
        // Keep only what was typed while the save was in flight.
        const left: typeof current = {};
        for (const [id, change] of Object.entries(current)) if (change !== edits[id]) left[id] = change;
        return left;
      });
      onStatement(updated);
      setSaveState(Object.keys(pendingRef.current).length ? "dirty" : "saved");
    } catch (err) {
      setSaveState("failed");
      onError(errorText(err, "Could not save the changes"));
    }
  }, [onError, onStatement, projectId, statement]);

  function edit(id: string, change: { response?: string; remark?: string }) {
    setPending((all) => ({ ...all, [id]: { ...all[id], ...change } }));
    setSaveState("dirty");
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => void flush(), 1200);
  }

  async function start(useAi: boolean) {
    if (!spec) return;
    if (spec.verification?.project === "different" && !window.confirm("This specification was written for another project. Continue anyway?")) return;
    setBusy(useAi ? "prepare-ai" : "prepare");
    setElapsed(0);
    onError(null);
    try {
      onStatement(
        await api.post<Statement>(`/projects/${projectId}/compliance/prepare`, {
          system_code: system.code,
          path: spec.path,
          member: spec.member,
          first_page: spec.first_page,
          last_page: spec.last_page,
          use_ai: useAi,
        })
      );
      setPending({});
      setSaveState("saved");
    } catch (err) {
      onError(errorText(err, "Could not read the specification"));
    } finally {
      setBusy(null);
    }
  }

  async function exportWorkbook() {
    if (!statement) return;
    setBusy("export");
    onError(null);
    try {
      await flush();
      await api.download(`/projects/${projectId}/compliance/statements/${statement.id}/export`, "Compliance Statement.xlsx");
    } catch (err) {
      onError(errorText(err, "Could not export the workbook"));
    } finally {
      setBusy(null);
    }
  }

  async function discard() {
    if (!statement || !window.confirm("Delete this draft and start again from the specification?")) return;
    try {
      await api.delete(`/projects/${projectId}/compliance/statements/${statement.id}`);
      onStatement(null);
      setPending({});
    } catch (err) {
      onError(errorText(err, "Could not delete the draft"));
    }
  }

  const rows = useMemo(() => {
    if (!statement) return [];
    return statement.rows.map((row) => {
      const change = pending[row.id];
      return change ? { ...row, response: change.response ?? row.response, remark: change.remark ?? row.remark, source: "engineer" } : row;
    });
  }, [pending, statement]);

  const tally = useMemo(() => {
    const clauses = rows.filter(answerable);
    const counts = { good: 0, warn: 0, bad: 0, none: 0 };
    for (const row of clauses) counts[toneOf(row.response)] += 1;
    return { total: clauses.length, ...counts, answered: clauses.length - counts.none };
  }, [rows]);

  const visible = useMemo(() => {
    const keep = (row: StatementRow) => {
      if (!answerable(row)) return true;
      if (filter === "unanswered") return !row.response;
      if (filter === "review") return row.state === "review";
      if (filter === "ai") return row.source === "ai";
      return true;
    };
    const kept = rows.filter(keep);
    // A heading with nothing under it is noise in a filtered view.
    return filter === "all" ? kept : kept.filter((row, i) => answerable(row) || (kept[i + 1] && answerable(kept[i + 1])));
  }, [filter, rows]);

  if (loading) {
    return <div className="rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500">Opening the draft…</div>;
  }

  if (!statement) {
    return (
      <div className="rounded-xl border border-gray-200 bg-white p-6">
        <h2 className="text-lg font-semibold text-navy-900">Specification clauses</h2>
        {spec ? (
          <>
            <p className="mt-1 text-sm text-gray-500">
              Read the specification clause by clause. Each clause is answered from the company's past {system.code} statements and a few
              rules first; what they leave is yours, or the AI assistant's.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <button onClick={() => start(false)} disabled={!canEdit || busy !== null} className={btnPrimary}>
                {busy === "prepare" ? `Reading… ${elapsed}s` : "Start statement"}
              </button>
              <button onClick={() => start(true)} disabled={!canEdit || busy !== null} className={btnSecondary} title="Read the clauses and auto-fill the rest with AI in one go">
                <Sparkle />
                {busy === "prepare-ai" ? `Preparing… ${elapsed}s` : "Start and auto-fill with AI"}
              </button>
            </div>
            {!canEdit && <p className="mt-3 text-xs text-gray-400">Viewers can read statements; an engineer starts them.</p>}
          </>
        ) : (
          <p className="mt-1 text-sm text-gray-500">A specification is needed first — upload one above or ask the contractor.</p>
        )}
      </div>
    );
  }

  const percent = tally.total ? Math.round((tally.answered / tally.total) * 100) : 0;
  const share = (n: number) => (tally.total ? `${(n / tally.total) * 100}%` : "0%");

  return (
    <div className="rounded-xl border border-gray-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-4">
        <div className="flex flex-wrap items-baseline gap-3">
          <h2 className="text-lg font-semibold text-navy-900">Specification clauses</h2>
          <span className="text-sm text-gray-500">
            {tally.answered} of {tally.total} answered
          </span>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex h-2.5 w-56 overflow-hidden rounded-full bg-gray-200" title={`${tally.good} met · ${tally.warn} with remark · ${tally.bad} deviations · ${tally.none} unanswered`}>
            <div className="bg-green-500" style={{ width: share(tally.good) }} />
            <div className="bg-amber-400" style={{ width: share(tally.warn) }} />
            <div className="bg-red-500" style={{ width: share(tally.bad) }} />
          </div>
          <span className="text-sm font-semibold text-navy-900">{percent}%</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 px-5 pb-3 pt-3">
        <div className="flex flex-wrap gap-1">
          {(
            [
              ["all", "All"],
              ["unanswered", `Unanswered (${tally.none})`],
              ["review", `To review (${rows.filter((r) => answerable(r) && r.state === "review").length})`],
              ["ai", `From AI (${rows.filter((r) => r.source === "ai").length})`],
            ] as [Filter, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={`rounded-full px-3 py-1 text-xs font-medium ${filter === key ? "bg-navy-900 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          {canEdit && saveState !== "saved" && (
            <button onClick={() => void flush()} disabled={saveState === "saving"} className={btnSecondary}>
              {saveState === "saving" ? "Saving…" : "Save draft"}
            </button>
          )}
          <button onClick={exportWorkbook} disabled={busy !== null} className={btnPrimary}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
              <path d="M12 3v12M6 11l6 6 6-6M5 21h14" />
            </svg>
            {busy === "export" ? "Exporting…" : "Export Excel"}
          </button>
        </div>
      </div>

      {statement.summary.notes.length > 0 && (
        <div className="mx-5 mb-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">{statement.summary.notes.join(" ")}</div>
      )}

      <div className="overflow-x-auto border-t border-gray-100">
        <table className="w-full min-w-[52rem] text-sm">
          <thead className="bg-gray-50 text-left text-xs font-semibold text-gray-500">
            <tr>
              <th className="w-20 px-4 py-2.5">Clause</th>
              <th className="px-4 py-2.5">Specification requirement</th>
              <th className="w-52 px-4 py-2.5">Compliance</th>
              <th className="w-64 px-4 py-2.5">Remarks</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <ClauseRow
                key={row.id}
                projectId={projectId}
                statement={statement}
                row={row}
                editable={canEdit}
                selected={row.id === selectedClause}
                onSelect={() => onSelectClause(row.id === selectedClause ? null : row.id)}
                onEdit={(change) => edit(row.id, change)}
              />
            ))}
            {visible.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-sm text-gray-400">
                  Nothing here.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-gray-100 px-5 py-3 text-xs text-gray-500">
        <span className="inline-flex items-center gap-1.5">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" className="h-4 w-4">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8h.01M12 11v5" />
          </svg>
          AI suggestions require review · {statement.spec.clauses} clauses read from {statement.spec.filename}
          {canEdit && (
            <>
              {" · "}
              <button onClick={discard} className="text-red-600 hover:underline">
                Start again
              </button>
            </>
          )}
        </span>
        <span className={`inline-flex items-center gap-1.5 ${saveState === "failed" ? "text-red-600" : saveState === "saved" ? "text-green-700" : "text-amber-700"}`}>
          {saveState === "saved" && <ToneIcon tone="good" className="h-3.5 w-3.5" />}
          {saveState === "saved" ? "All changes saved" : saveState === "saving" ? "Saving…" : saveState === "dirty" ? "Unsaved changes" : "Save failed"}
        </span>
      </div>
    </div>
  );
}

const SOURCE_LABELS: Record<string, string> = {
  rule: "By rule",
  reference: "From a past statement",
  ai: "Suggested by AI",
  engineer: "Answered by you",
  none: "Not answered yet",
};

function ClauseRow({
  projectId,
  statement,
  row,
  editable,
  selected,
  onSelect,
  onEdit,
}: {
  projectId: number;
  statement: Statement;
  row: StatementRow;
  editable: boolean;
  selected: boolean;
  onSelect: () => void;
  onEdit: (change: { response?: string; remark?: string }) => void;
}) {
  if (row.heading) {
    return (
      <tr className={row.level === 0 ? "bg-blue-50/60" : "bg-gray-50/70"}>
        <td className="px-4 py-1.5 text-xs font-semibold text-navy-900">{row.level === 0 ? "" : row.label}</td>
        <td colSpan={3} className="px-4 py-1.5 text-xs font-semibold uppercase tracking-wide text-navy-900">
          {row.level === 0 ? `${row.label} — ${row.text}` : row.text}
        </td>
      </tr>
    );
  }
  const leadIn = row.source === "lead_in";
  const reference = row.reference;
  return (
    <tr
      onClick={onSelect}
      className={`cursor-pointer border-t border-gray-100 align-top ${selected ? "bg-brand-50/70" : row.state === "review" && row.source !== "engineer" ? "bg-amber-50/40" : "hover:bg-gray-50/60"}`}
    >
      <td className="px-4 py-2.5 text-xs text-gray-500">
        <a
          href={specHref(projectId, statement.spec, row.page)}
          target="_blank"
          rel="noreferrer"
          onClick={(e) => e.stopPropagation()}
          className="hover:text-brand-700"
          title={`Open the specification at page ${row.page}`}
        >
          {row.ref}
        </a>
      </td>
      <td className="px-4 py-2.5 text-gray-800" style={{ paddingLeft: `${1 + Math.max(0, row.level - 2) * 1}rem` }}>
        {row.text}
        {!leadIn && (
          <div className="mt-1 text-[11px] text-gray-400" title={reference?.path}>
            {SOURCE_LABELS[row.source] ?? row.source}
            {row.source === "reference" && reference && ` · ${Math.round(reference.similarity * 100)}% the same clause`}
            {row.note ? ` · ${row.note}` : ""}
          </div>
        )}
      </td>
      <td className="px-4 py-2" onClick={(e) => e.stopPropagation()}>
        {leadIn ? (
          <span className="text-xs text-gray-400">Answered by its items</span>
        ) : (
          <ResponseSelect value={row.response} disabled={!editable} onChange={(response) => onEdit({ response })} />
        )}
      </td>
      <td className="px-4 py-2" onClick={(e) => e.stopPropagation()}>
        {leadIn ? null : editable ? (
          <input
            value={row.remark}
            onChange={(e) => onEdit({ remark: e.target.value })}
            placeholder="Add remarks…"
            className="w-full rounded-lg border border-gray-300 px-3 py-1.5 text-sm outline-none placeholder:text-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
          />
        ) : (
          <span className="text-sm text-gray-600">{row.remark}</span>
        )}
      </td>
    </tr>
  );
}

function ResponseSelect({ value, disabled, onChange }: { value: string; disabled: boolean; onChange: (value: string) => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const tone = toneOf(value);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const options = (COMPLIANCE_RESPONSES as readonly string[]).includes(value) || !value ? [...COMPLIANCE_RESPONSES] : [value, ...COMPLIANCE_RESPONSES];

  return (
    <div ref={box} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={`flex w-full items-center justify-between gap-2 rounded-lg border bg-white px-2.5 py-1.5 text-left text-sm ${TONE_STYLES[tone].border} ${
          value ? "text-navy-900" : "text-gray-400"
        } ${disabled ? "cursor-default" : "hover:bg-gray-50"} ${open ? "ring-2 ring-brand-100" : ""}`}
      >
        <span className="flex min-w-0 items-center gap-2">
          <ToneIcon tone={tone} />
          <span className="truncate">{value || "Select response"}</span>
        </span>
        {!disabled && (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5 shrink-0 text-gray-400">
            <path d="m6 9 6 6 6-6" />
          </svg>
        )}
      </button>
      {open && (
        <ul className="absolute left-0 z-20 mt-1 w-56 overflow-hidden rounded-xl border border-gray-200 bg-white py-1 shadow-lg">
          {options.map((option) => (
            <li key={option}>
              <button
                type="button"
                onClick={() => {
                  onChange(option);
                  setOpen(false);
                }}
                className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-gray-50 ${option === value ? "bg-brand-50/70 font-medium" : ""}`}
              >
                <ToneIcon tone={toneOf(option)} />
                {option}
              </button>
            </li>
          ))}
          {value && (
            <li className="border-t border-gray-100">
              <button
                type="button"
                onClick={() => {
                  onChange("");
                  setOpen(false);
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-gray-500 hover:bg-gray-50"
              >
                Clear response
              </button>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

// --- the assistant ---------------------------------------------------------------------

function AssistantPanel({
  projectId,
  system,
  spec,
  statement,
  canEdit,
  aiAvailable,
  referenceIndex,
  selectedClause,
  onStatement,
  onCheck,
  onError,
}: {
  projectId: number;
  system: ComplianceSystem;
  spec: SpecMatch | null;
  statement: Statement | null;
  canEdit: boolean;
  aiAvailable: boolean;
  referenceIndex: ReferenceIndex | null;
  selectedClause: string | null;
  onStatement: (statement: Statement | null) => void;
  onCheck: (statement: Statement | null) => void;
  onError: (message: string | null) => void;
}) {
  const [scope, setScope] = useState<AutofillScope>("unanswered");
  const [busy, setBusy] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<{ clause: string | null; text: string } | null>(null);
  const [index, setIndex] = useState<ReferenceIndex | null>(referenceIndex);
  const [showCheck, setShowCheck] = useState(false);
  const [files, setFiles] = useState<StatementFile[] | null>(null);
  const [statementPath, setStatementPath] = useState("");
  const upload = useRef<HTMLInputElement>(null);

  const clause = statement?.rows.find((r) => r.id === selectedClause) ?? null;

  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    const timer = window.setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);

  useEffect(() => {
    if (!index?.running) return;
    const timer = window.setInterval(() => {
      api.get<ReferenceIndex>(`/projects/compliance/references`).then(setIndex).catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [index?.running]);

  useEffect(() => {
    if (!showCheck || files !== null) return;
    api
      .get<StatementFile[]>(`/projects/${projectId}/compliance/statement-files`)
      .then(setFiles)
      .catch(() => setFiles([]));
  }, [files, projectId, showCheck]);

  async function autofill() {
    if (!statement) return;
    setBusy("fill");
    setElapsed(0);
    onError(null);
    try {
      onStatement(await api.post<Statement>(`/projects/${projectId}/compliance/statements/${statement.id}/autofill`, { scope }));
    } catch (err) {
      onError(errorText(err, "Could not fill the statement"));
    } finally {
      setBusy(null);
    }
  }

  async function suggest() {
    if (!statement || !clause) return;
    setBusy("suggest");
    setElapsed(0);
    onError(null);
    try {
      setSuggestion(await api.post<Suggestion>(`/projects/${projectId}/compliance/statements/${statement.id}/suggest`, { clause_id: clause.id }));
    } catch (err) {
      onError(errorText(err, "Could not get a suggestion"));
    } finally {
      setBusy(null);
    }
  }

  async function apply() {
    if (!statement || !suggestion) return;
    setBusy("apply");
    onError(null);
    try {
      onStatement(
        await api.patch<Statement>(`/projects/${projectId}/compliance/statements/${statement.id}`, {
          rows: [{ id: suggestion.id, response: suggestion.response, remark: suggestion.remark }],
        })
      );
      setSuggestion(null);
    } catch (err) {
      onError(errorText(err, "Could not apply the suggestion"));
    } finally {
      setBusy(null);
    }
  }

  async function ask() {
    if (!statement || !question.trim()) return;
    setBusy("ask");
    setElapsed(0);
    onError(null);
    try {
      const reply = await api.post<{ answer: string }>(`/projects/${projectId}/compliance/statements/${statement.id}/ask`, {
        clause_id: clause?.id ?? null,
        question: question.trim(),
      });
      setAnswer({ clause: clause?.ref ?? null, text: reply.answer });
      setQuestion("");
    } catch (err) {
      onError(errorText(err, "The assistant could not answer"));
    } finally {
      setBusy(null);
    }
  }

  async function runCheck(file?: File) {
    if (!spec) return;
    setBusy("check");
    setElapsed(0);
    onError(null);
    try {
      const body = new FormData();
      body.append("system_code", system.code);
      body.append("path", spec.path);
      if (spec.member) body.append("member", spec.member);
      if (spec.first_page) body.append("first_page", String(spec.first_page));
      if (spec.last_page) body.append("last_page", String(spec.last_page));
      body.append("use_ai", String(aiAvailable));
      if (file) body.append("file", file);
      else body.append("statement_path", statementPath);
      onCheck(await api.upload<Statement>(`/projects/${projectId}/compliance/check`, body));
    } catch (err) {
      onError(errorText(err, "Could not check the statement"));
    } finally {
      setBusy(null);
    }
  }

  async function rescan() {
    try {
      setIndex(await api.post<ReferenceIndex>(`/projects/compliance/references/scan`));
    } catch (err) {
      onError(errorText(err, "Could not start the index"));
    }
  }

  const ready = aiAvailable && statement !== null;
  const indexed = index?.by_system?.[system.code] ?? 0;
  const unanswered = statement ? statement.rows.filter((r) => answerable(r) && !r.response).length : 0;

  return (
    <aside className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-navy-900">
          <Sparkle className="h-5 w-5 text-brand-600" />
          AI Assistant
        </h2>
        <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold ${aiAvailable ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-500"}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${aiAvailable ? "bg-green-500" : "bg-gray-400"}`} />
          {aiAvailable ? "Ready" : "Off"}
        </span>
      </div>

      <h3 className="mt-4 font-semibold text-navy-900">Fill your statement faster</h3>
      <p className="mt-0.5 text-xs text-gray-500">
        Draft clause responses from the specification, the project's BOQ and the company's past statements. Everything the AI writes is a
        suggestion for you to review.
      </p>

      <div className="mt-4 text-xs font-semibold text-gray-700">Using these sources</div>
      <ul className="mt-1.5 space-y-1.5">
        <SourceLine
          label={statement?.spec.filename ?? spec?.filename ?? "No specification yet"}
          detail={statement ? `${statement.spec.clauses} clauses` : spec ? `${spec.pages ?? "?"} pages` : ""}
        />
        <SourceLine
          label={`Past ${system.code} compliance statements`}
          detail={index?.running ? `indexing… ${index.indexed}` : `${indexed} indexed`}
          action={canEdit ? { label: index?.running ? "Indexing…" : "Update", onClick: rescan, disabled: Boolean(index?.running) } : undefined}
        />
        <SourceLine label="Project facts & BOQ" detail="Project Info, BOQ" />
      </ul>
      {index?.message && <div className="mt-1 text-xs text-amber-700">{index.message}</div>}

      <div className="mt-4 text-xs font-semibold text-gray-700">Fill scope</div>
      <select value={scope} onChange={(e) => setScope(e.target.value as AutofillScope)} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm">
        <option value="unanswered">Unanswered clauses only{statement ? ` (${unanswered})` : ""}</option>
        <option value="review">Clauses marked for review</option>
        <option value="all">All clauses (keeps your own answers)</option>
      </select>
      <button onClick={autofill} disabled={!ready || !canEdit || busy !== null} className={`${btnPrimary} mt-3 w-full justify-center`}>
        <Sparkle />
        {busy === "fill" ? `Filling… ${elapsed}s` : "Auto-fill with AI"}
      </button>
      {!statement && <p className="mt-1.5 text-xs text-gray-400">Start the statement first; the assistant works on its clauses.</p>}
      {busy && elapsed > 20 && (
        <p className="mt-1.5 text-xs text-amber-700">Calls are paced to the AI provider's per-minute allowance; a long specification can take a few minutes.</p>
      )}

      <div className="mt-4 rounded-xl border border-gray-200 bg-gray-50/60 p-3">
        <div className="flex items-center justify-between gap-2">
          <div className="text-sm font-semibold text-navy-900">Suggested response</div>
          {suggestion && (
            <span className={`inline-flex items-center gap-1 rounded-full bg-white px-2 py-0.5 text-xs font-semibold ${TONE_STYLES[toneOf(suggestion.response)].text}`}>
              <ToneIcon tone={toneOf(suggestion.response)} className="h-3.5 w-3.5" />
              {suggestion.response}
            </span>
          )}
        </div>
        {clause ? (
          <>
            <div className="mt-0.5 text-xs text-gray-500">Clause {clause.ref}</div>
            {suggestion && suggestion.id === clause.id ? (
              <>
                <p className="mt-2 text-sm text-gray-700">{suggestion.remark || "No remark needed."}</p>
                {canEdit && (
                  <button onClick={apply} disabled={busy !== null} className={`${btnSecondary} mt-3 w-full justify-center border-brand-200 text-brand-700 hover:bg-brand-50`}>
                    {busy === "apply" ? "Applying…" : "Apply suggestion"}
                  </button>
                )}
              </>
            ) : (
              <button onClick={suggest} disabled={!ready || busy !== null} className={`${btnSecondary} mt-2 w-full justify-center`}>
                {busy === "suggest" ? `Thinking… ${elapsed}s` : "Suggest a response"}
              </button>
            )}
          </>
        ) : (
          <p className="mt-1 text-xs text-gray-500">Click a clause in the table to get a suggestion for it.</p>
        )}
      </div>
      <p className="mt-1.5 text-[11px] text-gray-400">Review suggestions before export.</p>

      {answer && (
        <div className="mt-3 rounded-xl border border-brand-100 bg-brand-50/50 p-3 text-sm text-gray-800">
          {answer.clause && <div className="text-xs text-gray-500">About clause {answer.clause}</div>}
          <p className="mt-0.5 whitespace-pre-wrap">{answer.text}</p>
        </div>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void ask();
        }}
        className="mt-3 flex items-center gap-2"
      >
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          disabled={!ready || busy !== null}
          placeholder={clause ? `Ask AI about clause ${clause.ref}…` : "Ask AI about a clause…"}
          className="min-w-0 flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none placeholder:text-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-100 disabled:bg-gray-50"
        />
        <button type="submit" disabled={!ready || busy !== null || !question.trim()} className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50" title="Ask">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
            <path d="M22 2 11 13M22 2l-7 20-4-9-9-4z" />
          </svg>
        </button>
      </form>

      <div className="mt-5 border-t border-gray-100 pt-4">
        <button onClick={() => setShowCheck((v) => !v)} className="flex w-full items-center justify-between text-sm font-semibold text-navy-900">
          Check an existing statement
          <span className="text-gray-400">{showCheck ? "−" : "+"}</span>
        </button>
        {showCheck && (
          <div className="mt-2">
            <p className="text-xs text-gray-500">
              Lay a statement (.xlsx, .xls, .docx) against this specification: missing and unanswered clauses, another project's or
              manufacturer's name, and answers that contradict the BOQ.
            </p>
            <select value={statementPath} onChange={(e) => setStatementPath(e.target.value)} className="mt-2 w-full rounded-lg border border-gray-300 px-2 py-1.5 text-xs">
              <option value="">{files === null ? "Looking for statements…" : files.length ? "Choose a statement in the project" : "No statement in the project folder"}</option>
              {files?.map((file) => (
                <option key={file.path} value={file.path}>
                  {file.path}
                </option>
              ))}
            </select>
            <div className="mt-2 flex gap-2">
              <button onClick={() => runCheck()} disabled={!spec || !canEdit || !statementPath || busy !== null} className={`${btnPrimary} flex-1 justify-center`}>
                {busy === "check" ? `Checking… ${elapsed}s` : "Check"}
              </button>
              <button onClick={() => upload.current?.click()} disabled={!spec || !canEdit || busy !== null} className={btnSecondary}>
                Upload…
              </button>
            </div>
            <input
              ref={upload}
              type="file"
              accept=".xlsx,.xlsm,.xls,.docx"
              hidden
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) runCheck(file);
                e.target.value = "";
              }}
            />
          </div>
        )}
      </div>
    </aside>
  );
}

function SourceLine({ label, detail, action }: { label: string; detail: string; action?: { label: string; onClick: () => void; disabled?: boolean } }) {
  return (
    <li className="flex items-center gap-2 rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 shrink-0 text-gray-400">
        <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
        <path d="M14 3v5h5" />
      </svg>
      <span className="min-w-0 flex-1 truncate font-medium text-gray-700" title={label}>
        {label}
      </span>
      <span className="shrink-0 text-gray-400">{detail}</span>
      {action && (
        <button onClick={action.onClick} disabled={action.disabled} className="shrink-0 font-medium text-brand-600 hover:underline disabled:opacity-50">
          {action.label}
        </button>
      )}
    </li>
  );
}

// --- a check's findings ----------------------------------------------------------------

function CheckResults({ projectId, statement, onClose }: { projectId: number; statement: Statement; onClose: () => void }) {
  const [all, setAll] = useState(false);
  const { summary, verification } = statement;
  const rows = statement.rows.filter((row) => {
    if (row.heading) return true;
    if (all) return true;
    return (row.findings ?? []).some((f) => f.severity !== "info");
  });
  const visible = rows.filter((row, i) => !row.heading || (rows[i + 1] && !rows[i + 1].heading));
  const counts: [string, number][] = [
    ["Clauses", summary.clauses],
    ["Missing", summary.finding_counts?.missing ?? 0],
    ["Unanswered", summary.finding_counts?.unanswered ?? 0],
    ["Contradict BOQ", summary.finding_counts?.ai_conflict ?? 0],
    ["Rows not in spec", summary.rows_not_in_spec ?? 0],
  ];

  return (
    <section className="mt-5 rounded-xl border border-gray-200 bg-white">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 p-4">
        <div>
          <h2 className="font-semibold text-navy-900">Check of {statement.statement_name}</h2>
          <div className="mt-1 text-xs text-gray-500">
            Against {statement.spec.filename} · {formatWhen(statement.created_at)} · {statement.ai_calls} AI call{statement.ai_calls === 1 ? "" : "s"}
            {summary.reviewed_by_ai ? ` · ${summary.reviewed_by_ai} answers reviewed against the BOQ` : ""}
          </div>
        </div>
        <button onClick={onClose} className={btnSecondary}>
          Close
        </button>
      </div>
      <div className="space-y-2 p-4">
        {(verification.project === "different" || verification.system === "different") && (
          <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">
            <span className="font-semibold">Not this project's specification.</span> {verification.evidence[0]}
          </div>
        )}
        {(summary.general ?? []).map((finding) => (
          <div key={finding.code + finding.message} className={`rounded-lg px-3 py-2 text-sm ${finding.severity === "error" ? "bg-red-50 text-red-800" : "bg-amber-50 text-amber-800"}`}>
            {finding.message}
          </div>
        ))}
        {summary.notes.map((note) => (
          <div key={note} className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {note}
          </div>
        ))}
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
          {counts.map(([label, value]) => (
            <div key={label} className="rounded-lg bg-gray-50 px-3 py-2">
              <div className="text-lg font-semibold text-navy-900">{value}</div>
              <div className="text-xs text-gray-500">{label}</div>
            </div>
          ))}
        </div>
        <div className="flex gap-1 pt-1">
          {[
            [false, "Findings"],
            [true, "All clauses"],
          ].map(([value, label]) => (
            <button
              key={String(value)}
              onClick={() => setAll(value as boolean)}
              className={`rounded-full px-3 py-1 text-xs font-medium ${all === value ? "bg-navy-900 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}
            >
              {label as string}
            </button>
          ))}
        </div>
      </div>
      <div className="overflow-x-auto border-t border-gray-100">
        <table className="w-full min-w-[52rem] text-sm">
          <thead className="bg-gray-50 text-left text-xs font-semibold text-gray-500">
            <tr>
              <th className="w-20 px-4 py-2.5">Clause</th>
              <th className="px-4 py-2.5">Specification requirement</th>
              <th className="w-44 px-4 py-2.5">In the statement</th>
              <th className="w-72 px-4 py-2.5">Findings</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) =>
              row.heading ? (
                <tr key={row.id} className={row.level === 0 ? "bg-blue-50/60" : "bg-gray-50/70"}>
                  <td className="px-4 py-1.5 text-xs font-semibold text-navy-900">{row.level === 0 ? "" : row.label}</td>
                  <td colSpan={3} className="px-4 py-1.5 text-xs font-semibold uppercase tracking-wide text-navy-900">
                    {row.level === 0 ? `${row.label} — ${row.text}` : row.text}
                  </td>
                </tr>
              ) : (
                <tr key={row.id} className="border-t border-gray-100 align-top">
                  <td className="px-4 py-2.5 text-xs text-gray-500">
                    <a href={specHref(projectId, statement.spec, row.page)} target="_blank" rel="noreferrer" className="hover:text-brand-700">
                      {row.ref}
                    </a>
                  </td>
                  <td className="px-4 py-2.5 text-gray-800">{row.text}</td>
                  <td className="px-4 py-2.5">
                    {row.source === "lead_in" ? (
                      <span className="text-xs text-gray-400">Answered by its items</span>
                    ) : (
                      <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${TONE_STYLES[toneOf(row.response)].text}`}>
                        <ToneIcon tone={toneOf(row.response)} className="h-3.5 w-3.5" />
                        {row.response || (row.source === "missing" ? "Missing" : "No answer")}
                      </span>
                    )}
                    {row.remark && <div className="mt-0.5 text-xs text-gray-500">{row.remark}</div>}
                  </td>
                  <td className="px-4 py-2.5">
                    <ul className="space-y-1">
                      {(row.findings ?? []).map((finding) => (
                        <li
                          key={finding.code + finding.message}
                          className={`text-xs ${finding.severity === "error" ? "text-red-700" : finding.severity === "warning" ? "text-amber-700" : "text-gray-500"}`}
                        >
                          {finding.code.startsWith("ai_") ? "AI: " : ""}
                          {finding.message}
                        </li>
                      ))}
                    </ul>
                  </td>
                </tr>
              )
            )}
            {visible.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-sm text-gray-400">
                  No findings — every clause is answered.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
