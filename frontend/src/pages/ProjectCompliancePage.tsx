import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useAuth } from "../context/AuthContext";
import { ApiError, api, apiUrl } from "../lib/api";
import {
  COMPLIANCE_RESPONSES,
  PROJECT_EDITOR_ROLES,
  TECHNICAL_LABELS,
  WORKFLOW_LABELS,
  type Compliance,
  type ComplianceSystem,
  type DraftMail,
  type KnowledgeCandidate,
  type AiFillClass,
  type KnowledgeMatch,
  type KnowledgeStatus,
  type SpecMatch,
  type SpecVerification,
  type Statement,
  type StatementFile,
  type StatementRow,
  type StatementSummary,
  type TechnicalStatus,
  type WorkflowStatus,
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
function formatWhen(value: string | null | undefined): string {
  if (!value) return "never";
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function answerable(row: StatementRow): boolean {
  return !row.heading && row.source !== "lead_in";
}

function newRequestId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
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

// What an AI fill decided about a row, and how loudly the table says it.
const AI_CLASS_STYLES: Record<AiFillClass, { badge: string; label: string; row: string }> = {
  filled: { badge: "bg-purple-50 text-purple-700", label: "AI answered", row: "" },
  confirmed: { badge: "bg-green-50 text-green-700", label: "AI agrees with the database", row: "" },
  needs_review: { badge: "bg-amber-100 text-amber-900", label: "AI answer needs your review", row: "bg-amber-50/60" },
  conflict: { badge: "bg-red-100 text-red-800", label: "AI disagrees with the draft", row: "bg-red-50/60" },
};

const WORKFLOW_STYLES: Record<WorkflowStatus, string> = {
  unfilled: "bg-gray-100 text-gray-600",
  autofilled: "bg-blue-50 text-brand-700",
  candidate: "bg-amber-50 text-amber-800",
  ai_pending: "bg-purple-50 text-purple-700",
  reviewed: "bg-green-50 text-green-700",
  recheck: "bg-red-50 text-red-700",
};

function matchLabelOf(match: KnowledgeMatch): string {
  if (match.result === "candidate" && match.same_wording) return "Same requirement — past answer not reusable as recorded";
  return MATCH_LABELS[match.result];
}

const MATCH_LABELS: Record<KnowledgeMatch["result"], string> = {
  eligible: "Eligible match",
  flagged: "Same requirement — filled from a flagged record, verify the source",
  candidate: "Similar requirement — candidate requires review",
  missing_model: "Missing model — BOQ clarification required",
  scope: "Missing scope condition — scope verification required",
  conflict: "Conflicting candidates — conflict requires review",
  none: "No database match",
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

function Icon({ path, className = "h-4 w-4" }: { path: string; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d={path} />
    </svg>
  );
}

const ICONS = {
  search: "M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16zM21 21l-4.3-4.3",
  trash: "M4 7h16M10 11v6M14 11v6M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2l1-12M9 7V4h6v3",
  info: "M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18zM12 8h.01M11 12h1v4h1",
  alert: "M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18zM12 7v6M12 16.5v.5",
  chevron: "m6 15 6-6 6 6",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
};

/** A side-panel section that folds away, so the panel stays short. */
function PanelCard({ title, defaultOpen = true, children }: { title: string; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="rounded-xl border border-gray-200 bg-white shadow-sm">
      <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left">
        <h2 className="text-base font-semibold text-navy-900">{title}</h2>
        <Icon path={open ? ICONS.chevron : "m6 9 6 6 6-6"} className="h-4 w-4 text-gray-500" />
      </button>
      {open && <div className="px-4 pb-4">{children}</div>}
    </section>
  );
}

const btnPrimary =
  "inline-flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50";
const btnSecondary =
  "inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50";
const btnSmall =
  "inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-semibold text-navy-900 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50";

function WorkflowBadge({ workflow }: { workflow: WorkflowStatus | undefined }) {
  const key = workflow ?? "unfilled";
  return <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${WORKFLOW_STYLES[key]}`}>{WORKFLOW_LABELS[key]}</span>;
}

function TechnicalChip({ technical }: { technical: StatementRow["technical"] }) {
  if (!technical?.status) return null;
  const label = TECHNICAL_LABELS[technical.status];
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${technical.verified ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-600"}`}
      title={technical.verified ? "Confirmed by the engineer" : "Proposed — not verified for this project"}
    >
      {label}
      {technical.verified ? " ✓" : " (proposed)"}
    </span>
  );
}

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
          <Icon path={ICONS.search} />
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
                  className={`-mb-px flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm ${
                    entry.code === system?.code ? "border-brand-600 text-brand-700" : "border-transparent text-gray-500 hover:text-navy-900"
                  }`}
                >
                  <span className="font-semibold">{entry.code}</span>
                  <span className={entry.code === system?.code ? "text-brand-600/80" : "text-gray-400"}>{entry.name}</span>
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
  // Rows with a review call in flight, so a double-click is one request and
  // the row itself says it is waiting.
  const [reviewing, setReviewing] = useState<Record<string, boolean>>({});

  const specs = system.specs.map((spec) => ({ ...spec, verification: verifications[specKey(spec)] ?? spec.verification }));
  const chosen =
    specs.find((s) => specKey(s) === selectedSpec) ?? specs.find((s) => s.verification?.project !== "different") ?? specs[0] ?? null;

  // The latest draft opens with the tab -- whenever the list of statements
  // arrives, which can be after the specifications when their search is
  // cached. Statements made here arrive through replace(), not a fetch.
  const loadedId = useRef<number | null>(null);
  useEffect(() => {
    if (!latest) {
      setLoadingStatement(false);
      return;
    }
    if (loadedId.current === latest.id) return;
    loadedId.current = latest.id;
    let cancelled = false;
    let settled = false;
    setLoadingStatement(true);
    api
      .get<Statement>(`/projects/${projectId}/compliance/statements/${latest.id}`)
      .then((s) => {
        if (!cancelled) setStatement(s);
      })
      .catch(() => undefined)
      .finally(() => {
        settled = true;
        if (!cancelled) setLoadingStatement(false);
      });
    return () => {
      // A fetch cancelled before it settled (StrictMode's second mount, a
      // tab change) has not loaded anything: let the next run fetch again.
      cancelled = true;
      if (!settled) loadedId.current = null;
    };
  }, [projectId, latest]);

  function replace(next: Statement | null) {
    loadedId.current = next?.id ?? null;
    setStatement(next);
    onLatest(next ?? undefined);
    if (!next) setSelectedClause(null);
  }

  /** One row came back changed (a review): keep the rest as they are. */
  function replaceRow(row: StatementRow) {
    setStatement((current) => (current ? { ...current, rows: current.rows.map((r) => (r.id === row.id ? { ...r, ...row } : r)) } : current));
  }

  async function reviewWithAi(clauseId: string, instruction: string) {
    if (!statement || reviewing[clauseId]) return;
    setReviewing((all) => ({ ...all, [clauseId]: true }));
    setError(null);
    try {
      const row = await api.post<StatementRow>(`/projects/${projectId}/compliance/statements/${statement.id}/rows/${clauseId}/review`, {
        instruction: instruction.trim() || null,
        request_id: newRequestId(),
      });
      replaceRow(row);
    } catch (err) {
      setError(errorText(err, "The review could not be run"));
    } finally {
      setReviewing((all) => {
        const rest = { ...all };
        delete rest[clauseId];
        return rest;
      });
    }
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
      <div className="mt-4 grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_24rem]">
        <ClausesCard
          projectId={projectId}
          system={system}
          spec={chosen}
          statement={statement}
          loading={loadingStatement}
          canEdit={canEdit}
          aiAvailable={data.ai_available}
          selectedClause={selectedClause}
          reviewing={reviewing}
          onSelectClause={setSelectedClause}
          onStatement={replace}
          onReview={(id) => void reviewWithAi(id, "")}
          onError={setError}
        />
        <SidePanel
          projectId={projectId}
          system={system}
          spec={chosen}
          statement={statement}
          canEdit={canEdit}
          aiAvailable={data.ai_available}
          knowledge={data.knowledge}
          selectedClause={selectedClause}
          reviewing={reviewing}
          onStatement={replace}
          onReview={reviewWithAi}
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
      <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
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
              <div className="mt-0.5 text-xs text-red-600">
                The open draft was prepared against {statementSpec?.filename}; start a new statement to use this one.
              </div>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {unsure && aiAvailable && (
              <button onClick={() => verifyWithAi(chosen)} disabled={busy !== null} className={btnSecondary}>
                <Sparkle />
                {busy === "verify" ? "Verifying…" : "Verify with AI"}
              </button>
            )}
            <a href={specHref(projectId, chosen)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 px-2 text-sm font-semibold text-brand-700 hover:underline">
              <Icon path={ICONS.eye} />
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

type Filter = "all" | "unfilled" | "review" | "autofilled" | "reviewed";

function inFilter(row: StatementRow, filter: Filter): boolean {
  const workflow = row.workflow ?? "unfilled";
  if (filter === "unfilled") return !row.response;
  if (filter === "review") return workflow === "candidate" || workflow === "ai_pending" || workflow === "recheck";
  if (filter === "autofilled") return workflow === "autofilled" && row.origin === "database";
  if (filter === "reviewed") return workflow === "reviewed";
  return true;
}

function ClausesCard({
  projectId,
  system,
  spec,
  statement,
  loading,
  canEdit,
  aiAvailable,
  selectedClause,
  reviewing,
  onSelectClause,
  onStatement,
  onReview,
  onError,
}: {
  projectId: number;
  system: ComplianceSystem;
  spec: SpecMatch | null;
  statement: Statement | null;
  loading: boolean;
  canEdit: boolean;
  aiAvailable: boolean;
  selectedClause: string | null;
  reviewing: Record<string, boolean>;
  onSelectClause: (id: string | null) => void;
  onStatement: (statement: Statement | null) => void;
  onReview: (clauseId: string) => void;
  onError: (message: string | null) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [filter, setFilter] = useState<Filter>("all");
  const [clearing, setClearing] = useState(false);
  // Edits the engineer typed but the API has not stored yet.
  const [pending, setPending] = useState<Record<string, { response?: string; remark?: string }>>({});
  const [saveState, setSaveState] = useState<"saved" | "dirty" | "saving" | "failed">("saved");
  const saveTimer = useRef<number | null>(null);
  // The edits as they are right now. Kept in step with `pending` by hand, not
  // by an effect: a save that finishes must see what is left at once, or it
  // would count the edits it just stored as unsaved and hold approval shut.
  const pendingRef = useRef(pending);
  function setPendingNow(next: typeof pending) {
    pendingRef.current = next;
    setPending(next);
  }

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
      // Keep only what was typed while the save was in flight.
      const left: typeof edits = {};
      for (const [id, change] of Object.entries(pendingRef.current)) if (change !== edits[id]) left[id] = change;
      setPendingNow(left);
      onStatement(updated);
      setSaveState(Object.keys(left).length ? "dirty" : "saved");
    } catch (err) {
      setSaveState("failed");
      onError(errorText(err, "Could not save the changes"));
    }
  }, [onError, onStatement, projectId, statement]);

  function edit(id: string, change: { response?: string; remark?: string }) {
    const all = pendingRef.current;
    setPendingNow({ ...all, [id]: { ...all[id], ...change } });
    setSaveState("dirty");
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => void flush(), 1200);
  }

  async function start() {
    if (!spec) return;
    if (spec.verification?.project === "different" && !window.confirm("This specification was written for another project. Continue anyway?")) return;
    setBusy("prepare");
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
        })
      );
      setPendingNow({});
      setSaveState("saved");
    } catch (err) {
      onError(errorText(err, "Could not read the specification"));
    } finally {
      setBusy(null);
    }
  }

  async function exportStatement(format: "xlsx" | "pdf") {
    if (!statement) return;
    setBusy(format === "pdf" ? "export-pdf" : "export");
    onError(null);
    try {
      await flush();
      if (format === "pdf") {
        await api.download(`/projects/${projectId}/compliance/statements/${statement.id}/export.pdf`, "Compliance Statement.pdf");
      } else {
        await api.download(`/projects/${projectId}/compliance/statements/${statement.id}/export`, "Compliance Statement.xlsx");
      }
    } catch (err) {
      onError(errorText(err, format === "pdf" ? "Could not export the PDF" : "Could not export the workbook"));
    } finally {
      setBusy(null);
    }
  }

  async function approve() {
    if (!statement) return;
    const unreviewed = statement.rows.filter((row) => answerable(row) && inFilter(row, "autofilled")).length;
    const message =
      unreviewed > 0
        ? `Approve this compliance statement?\n\n${unreviewed} draft answer${unreviewed === 1 ? " was" : "s were"} not individually marked reviewed. Approving signs off every answer as it stands and opens export.`
        : "Approve this compliance statement? Approving signs off every answer as it stands and opens export.";
    if (!window.confirm(message)) return;
    setBusy("approve");
    onError(null);
    try {
      await flush();
      onStatement(await api.post<Statement>(`/projects/${projectId}/compliance/statements/${statement.id}/approval`));
    } catch (err) {
      onError(errorText(err, "Could not approve the statement"));
    } finally {
      setBusy(null);
    }
  }

  async function withdrawApproval() {
    if (!statement || !window.confirm("Withdraw the approval? Export stays closed until the statement is approved again.")) return;
    setBusy("approve");
    onError(null);
    try {
      onStatement(await api.delete<Statement>(`/projects/${projectId}/compliance/statements/${statement.id}/approval`));
    } catch (err) {
      onError(errorText(err, "Could not withdraw the approval"));
    } finally {
      setBusy(null);
    }
  }

  async function clearAll(keepManualRemarks: boolean) {
    if (!statement) return;
    setBusy("clear");
    onError(null);
    try {
      await flush();
      onStatement(
        await api.post<Statement>(`/projects/${projectId}/compliance/statements/${statement.id}/clear`, { keep_manual_remarks: keepManualRemarks })
      );
      setFilter("all");
      setClearing(false);
    } catch (err) {
      onError(errorText(err, "Could not clear the statement"));
    } finally {
      setBusy(null);
    }
  }

  async function discard() {
    if (!statement || !window.confirm("Delete this draft and start again from the specification?")) return;
    try {
      await api.delete(`/projects/${projectId}/compliance/statements/${statement.id}`);
      onStatement(null);
      setPendingNow({});
    } catch (err) {
      onError(errorText(err, "Could not delete the draft"));
    }
  }

  async function markReviewed(row: StatementRow, reviewed: boolean) {
    if (!statement) return;
    onError(null);
    try {
      await flush();
      onStatement(await api.post<Statement>(`/projects/${projectId}/compliance/statements/${statement.id}/rows/${row.id}/reviewed`, { reviewed }));
    } catch (err) {
      onError(errorText(err, "Could not update the review status"));
    }
  }

  const rows = useMemo(() => {
    if (!statement) return [];
    return statement.rows.map((row) => {
      const change = pending[row.id];
      return change ? { ...row, response: change.response ?? row.response, remark: change.remark ?? row.remark, source: "engineer", origin: "manual" as const } : row;
    });
  }, [pending, statement]);

  const tally = useMemo(() => {
    const clauses = rows.filter(answerable);
    const counts = { good: 0, warn: 0, bad: 0, none: 0 };
    for (const row of clauses) counts[toneOf(row.response)] += 1;
    return { total: clauses.length, ...counts, answered: clauses.length - counts.none };
  }, [rows]);

  const workflowCounts = useMemo(() => {
    const counts: Record<Filter, number> = { all: 0, unfilled: 0, review: 0, autofilled: 0, reviewed: 0 };
    for (const row of rows.filter(answerable)) for (const key of Object.keys(counts) as Filter[]) if (inFilter(row, key)) counts[key] += 1;
    return counts;
  }, [rows]);

  const visible = useMemo(() => {
    const kept = rows.filter((row) => !answerable(row) || inFilter(row, filter));
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
              Read the specification clause by clause. Definitions, references and related sections are noted by rule; every other clause
              waits for Auto-fill from the knowledge base, or for you.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <button onClick={start} disabled={!canEdit || busy !== null} className={btnPrimary}>
                {busy === "prepare" ? `Reading… ${elapsed}s` : "Start statement"}
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

  const exportable = statement.approved && saveState === "saved" && busy === null;
  const exportHint = statement.approved
    ? saveState === "saved"
      ? ""
      : "Wait for your changes to save"
    : "An engineer must approve the statement before it can be exported";
  const blockers = saveState === "saved" ? statement.approval_blockers : ["Your latest changes are not saved yet."];
  const percent = tally.total ? Math.round((tally.answered / tally.total) * 100) : 0;
  const share = (n: number) => (tally.total ? `${(n / tally.total) * 100}%` : "0%");

  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-4">
        <div className="flex flex-wrap items-baseline gap-3">
          <h2 className="text-xl font-bold text-navy-900">Specification clauses</h2>
          <span className="text-sm text-gray-500">
            {tally.answered} of {tally.total} answered · {workflowCounts.reviewed} reviewed
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

      <div className="flex flex-wrap items-start justify-between gap-3 px-5 pb-3 pt-4">
        <div className="flex flex-wrap gap-1.5">
          {(
            [
              ["all", "All"],
              ["unfilled", `Unfilled (${workflowCounts.unfilled})`],
              ["review", `To review (${workflowCounts.review})`],
              ["autofilled", `Auto-filled (${workflowCounts.autofilled})`],
              ["reviewed", `Reviewed (${workflowCounts.reviewed})`],
            ] as [Filter, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={`rounded-full px-3.5 py-1.5 text-xs font-medium ${filter === key ? "bg-navy-900 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-start gap-2">
          {canEdit && (
            <div className="hidden items-center gap-1.5 self-center border-l border-gray-200 pl-3 pr-1 text-[11px] leading-tight text-gray-500 lg:flex">
              <Icon path={ICONS.info} className="h-4 w-4 text-brand-600" />
              <span>
                Clearing applies to:
                <br />
                <span className="font-medium text-navy-900">
                  {system.code} – {system.name}
                </span>
              </span>
            </div>
          )}
          {canEdit && saveState !== "saved" && (
            <button onClick={() => void flush()} disabled={saveState === "saving"} className={btnSecondary}>
              {saveState === "saving" ? "Saving…" : "Save draft"}
            </button>
          )}
          <button onClick={() => void exportStatement("pdf")} disabled={!exportable} className={btnPrimary} title={exportHint}>
            <Icon path="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M9 13h6M9 17h4" />
            {busy === "export-pdf" ? "Exporting…" : "Export PDF"}
          </button>
          <button onClick={() => void exportStatement("xlsx")} disabled={!exportable} className={btnSecondary} title={exportHint}>
            <Icon path="M12 3v12M6 11l6 6 6-6M5 21h14" />
            {busy === "export" ? "Exporting…" : "Export Excel"}
          </button>
          {canEdit && (
            <div className="relative">
              <button
                onClick={() => setClearing((v) => !v)}
                disabled={busy !== null}
                className="inline-flex items-center gap-2 rounded-lg border border-red-300 bg-red-50/60 px-4 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Icon path={ICONS.trash} />
                {busy === "clear" ? "Clearing…" : "Clear all compliance"}
              </button>
              <div className="mt-1 text-center text-[11px] text-gray-400">Clears answers, remarks and review status</div>
              {clearing && (
                <ClearPopover systemLabel={system.code} busy={busy === "clear"} onCancel={() => setClearing(false)} onConfirm={(keep) => void clearAll(keep)} />
              )}
            </div>
          )}
        </div>
      </div>

      <div className="px-5 pb-3">
        {statement.approved ? (
          <div className="inline-flex items-center gap-1.5 rounded-full bg-green-50 px-3 py-1 text-xs font-medium text-green-800">
            <ToneIcon tone="good" className="h-3.5 w-3.5" />
            Approved by {statement.approved_by_name} on {formatApproval(statement.approved_at)}
          </div>
        ) : (
          <div className="inline-flex items-center gap-1.5 rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-600">
            Awaiting engineer approval · export opens once the statement is approved at the end of the list
          </div>
        )}
      </div>

      {statement.summary.notes.length > 0 && (
        <div className="mx-5 mb-3 flex items-start gap-2.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-900">
          <span className="mt-px inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-amber-500 text-[10px] font-bold text-white">!</span>
          <span>{statement.summary.notes.join(" ")}</span>
        </div>
      )}

      <div className="overflow-x-auto border-t border-gray-100">
        <table className="w-full min-w-[52rem] text-sm">
          <thead className="bg-gray-50 text-left text-xs font-semibold text-gray-500">
            <tr>
              <th className="w-20 px-4 py-2.5">Clause</th>
              <th className="px-4 py-2.5">Specification requirement</th>
              <th className="w-56 px-4 py-2.5">Compliance</th>
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
                aiAvailable={aiAvailable}
                reviewing={Boolean(reviewing[row.id])}
                selected={row.id === selectedClause}
                onSelect={() => onSelectClause(row.id === selectedClause ? null : row.id)}
                onEdit={(change) => edit(row.id, change)}
                onReview={() => onReview(row.id)}
                onReviewed={(reviewed) => void markReviewed(row, reviewed)}
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

      <div className="border-t border-gray-200 px-5 py-4">
        {statement.approved ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-green-200 bg-green-50 px-4 py-3">
            <div>
              <p className="flex items-center gap-1.5 text-sm font-semibold text-green-800">
                <ToneIcon tone="good" className="h-4 w-4" />
                Approved by {statement.approved_by_name}
              </p>
              <p className="mt-0.5 text-xs text-green-700">
                {formatApproval(statement.approved_at)} · Export PDF and Export Excel are open at the top. Changing any answer withdraws the approval.
              </p>
            </div>
            {canEdit && (
              <button onClick={() => void withdrawApproval()} disabled={busy !== null} className={btnSecondary}>
                Withdraw approval
              </button>
            )}
          </div>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
            <div>
              <p className="text-sm font-semibold text-navy-900">Engineer approval</p>
              {blockers.length > 0 ? (
                <ul className="mt-1 list-disc pl-4 text-xs text-amber-800">
                  {blockers.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              ) : (
                <p className="mt-0.5 text-xs text-gray-600">Every clause is answered. Approve to sign the statement off and open export.</p>
              )}
              {!canEdit && <p className="mt-1 text-xs text-gray-400">Only an engineer can approve a statement.</p>}
            </div>
            {canEdit && (
              <button onClick={() => void approve()} disabled={busy !== null || blockers.length > 0} className={btnPrimary}>
                {busy === "approve" ? "Approving…" : "Approve statement"}
              </button>
            )}
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-gray-100 px-5 py-3 text-xs text-gray-500">
        <span className="inline-flex items-center gap-1.5">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" className="h-4 w-4">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8h.01M12 11v5" />
          </svg>
          Drafts and suggestions are proposals until you mark a row reviewed · {statement.spec.clauses} clauses read from {statement.spec.filename}
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

function ClearPopover({
  systemLabel,
  busy,
  onCancel,
  onConfirm,
}: {
  systemLabel: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (keepManualRemarks: boolean) => void;
}) {
  const [keep, setKeep] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.parentElement?.contains(e.target as Node)) onCancel();
    };
    const escape = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [onCancel]);
  return (
    <div ref={box} role="dialog" aria-label="Clear all compliance" className="absolute right-0 z-30 mt-2 w-72 rounded-xl border border-gray-200 bg-white p-4 shadow-xl">
      <div className="flex items-center gap-2">
        <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-red-50 text-red-600">
          <Icon path={ICONS.trash} />
        </span>
        <h3 className="font-semibold text-navy-900">Clear all compliance?</h3>
      </div>
      <p className="mt-2 text-xs text-gray-600">
        This will remove all compliance answers, remarks and review status for {systemLabel}. This action cannot be undone.
      </p>
      <label className="mt-3 flex items-center gap-2 text-xs text-gray-700">
        <input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} className="h-4 w-4 rounded border-gray-300" />
        Keep manually entered remarks
      </label>
      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onCancel} disabled={busy} className={btnSecondary}>
          Cancel
        </button>
        <button
          onClick={() => onConfirm(keep)}
          disabled={busy}
          className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
        >
          {busy ? "Clearing…" : "Clear all"}
        </button>
      </div>
    </div>
  );
}

function formatApproval(at: string | null): string {
  if (!at) return "";
  // The API sends UTC without a zone designator.
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(at) ? at : `${at}Z`);
  return date.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

const SOURCE_LABELS: Record<string, string> = {
  rule: "By rule",
  database: "From the knowledge base",
  learned: "From an engineer-approved answer",
  ai: "From an AI suggestion",
  engineer: "Answered by you",
  none: "Not answered yet",
};

function ClauseRow({
  projectId,
  statement,
  row,
  editable,
  aiAvailable,
  reviewing,
  selected,
  onSelect,
  onEdit,
  onReview,
  onReviewed,
}: {
  projectId: number;
  statement: Statement;
  row: StatementRow;
  editable: boolean;
  aiAvailable: boolean;
  reviewing: boolean;
  selected: boolean;
  onSelect: () => void;
  onEdit: (change: { response?: string; remark?: string }) => void;
  onReview: () => void;
  onReviewed: (reviewed: boolean) => void;
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
  const workflow = row.workflow ?? "unfilled";
  const review = row.ai_review;
  const matchLabel = row.match && row.match.result !== "eligible" ? matchLabelOf(row.match) : null;
  const aiClass = row.ai_class ? AI_CLASS_STYLES[row.ai_class] : null;
  return (
    <tr
      onClick={onSelect}
      className={`cursor-pointer border-t border-gray-100 align-top ${
        selected
          ? "bg-brand-50/70"
          : aiClass?.row
            ? aiClass.row
            : workflow === "recheck"
              ? "bg-red-50/40"
              : workflow === "candidate" || workflow === "ai_pending"
                ? "bg-amber-50/40"
                : "hover:bg-gray-50/60"
      }`}
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
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-gray-400">
            <WorkflowBadge workflow={workflow} />
            {aiClass && (
              <span className={`rounded-full px-1.5 py-0.5 font-medium ${aiClass.badge}`}>{aiClass.label}</span>
            )}
            <span>{SOURCE_LABELS[row.source] ?? row.source}</span>
            {matchLabel && <span className="text-amber-700">· {matchLabel}</span>}
            {row.note ? <span>· {row.note}</span> : null}
            {reviewing && <span className="text-purple-700">· Reviewing with AI…</span>}
            {!reviewing && review?.status === "failed" && <span className="text-red-600">· AI review failed: {review.error}</span>}
            {!reviewing && review?.status === "done" && !review.decision && <span className="text-purple-700">· Suggestion waiting</span>}
          </div>
        )}
        {!leadIn && editable && (
          <div className="mt-1.5 flex flex-wrap gap-1.5" onClick={(e) => e.stopPropagation()}>
            {aiAvailable && (
              <button onClick={onReview} disabled={reviewing} className={btnSmall} title="Ask the AI about this clause only">
                <Sparkle className="h-3 w-3" />
                {reviewing ? "Reviewing…" : review ? "Review again" : "Review with AI"}
              </button>
            )}
            {workflow === "reviewed" ? (
              <button onClick={() => onReviewed(false)} className={btnSmall}>
                Unmark reviewed
              </button>
            ) : (
              <button onClick={() => onReviewed(true)} disabled={!row.response} className={btnSmall} title={row.response ? "" : "Give the clause a response first"}>
                Mark reviewed
              </button>
            )}
          </div>
        )}
      </td>
      <td className="px-4 py-2" onClick={(e) => e.stopPropagation()}>
        {leadIn ? (
          <span className="text-xs text-gray-400">Answered by its items</span>
        ) : (
          <>
            <ResponseSelect value={row.response} disabled={!editable} onChange={(response) => onEdit({ response })} />
            <div className="mt-1">
              <TechnicalChip technical={row.technical} />
            </div>
          </>
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

// --- the side panel: knowledge base, auto-fill, the selected clause ------------------------

function SidePanel({
  projectId,
  system,
  spec,
  statement,
  canEdit,
  aiAvailable,
  knowledge,
  selectedClause,
  reviewing,
  onStatement,
  onReview,
  onCheck,
  onError,
}: {
  projectId: number;
  system: ComplianceSystem;
  spec: SpecMatch | null;
  statement: Statement | null;
  canEdit: boolean;
  aiAvailable: boolean;
  knowledge: KnowledgeStatus | null;
  selectedClause: string | null;
  reviewing: Record<string, boolean>;
  onStatement: (statement: Statement | null) => void;
  onReview: (clauseId: string, instruction: string) => Promise<void>;
  onCheck: (statement: Statement | null) => void;
  onError: (message: string | null) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [aiScope, setAiScope] = useState<"unanswered" | "review" | "all">("unanswered");
  const [instruction, setInstruction] = useState("");
  const [editing, setEditing] = useState<{ response: string; remark: string } | null>(null);
  const [showCheck, setShowCheck] = useState(false);
  const [files, setFiles] = useState<StatementFile[] | null>(null);
  const [statementPath, setStatementPath] = useState("");
  const upload = useRef<HTMLInputElement>(null);

  const clause = statement?.rows.find((r) => r.id === selectedClause) ?? null;
  const review = clause?.ai_review ?? null;
  const inFlight = clause ? Boolean(reviewing[clause.id]) : false;

  useEffect(() => {
    setInstruction("");
    setEditing(null);
  }, [selectedClause]);

  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    const timer = window.setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);

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
      onStatement(await api.post<Statement>(`/projects/${projectId}/compliance/statements/${statement.id}/autofill`));
    } catch (err) {
      onError(errorText(err, "Could not fill the statement"));
    } finally {
      setBusy(null);
    }
  }

  async function aiAutofill() {
    if (!statement) return;
    setBusy("aifill");
    onError(null);
    try {
      onStatement(
        await api.post<Statement>(`/projects/${projectId}/compliance/statements/${statement.id}/ai-autofill`, {
          scope: aiScope,
        }),
      );
    } catch (err) {
      onError(errorText(err, "Could not fill the statement with AI"));
    } finally {
      setBusy(null);
    }
  }

  // The fill runs on the server; while it does, the statement is fetched
  // again every few seconds so answers appear batch by batch.
  const aiJob = statement?.summary.ai_job ?? null;
  const aiRunning = Boolean(aiJob?.running);
  const statementId = statement?.id;
  // The latest callback, so a re-render (every keystroke) does not restart the timer.
  const onStatementRef = useRef(onStatement);
  useEffect(() => {
    onStatementRef.current = onStatement;
  });
  useEffect(() => {
    if (!aiRunning || statementId === undefined) return;
    const timer = window.setInterval(() => {
      api
        .get<Statement>(`/projects/${projectId}/compliance/statements/${statementId}`)
        .then((next) => onStatementRef.current(next))
        .catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [aiRunning, statementId, projectId]);

  async function useAnswer(responseId: string) {
    if (!statement || !clause) return;
    setBusy("use");
    onError(null);
    try {
      onStatement(
        await api.post<Statement>(`/projects/${projectId}/compliance/statements/${statement.id}/rows/${clause.id}/use-answer`, {
          response_id: responseId,
        })
      );
    } catch (err) {
      onError(errorText(err, "Could not use that answer"));
    } finally {
      setBusy(null);
    }
  }

  async function decide(action: "accept" | "edit" | "reject") {
    if (!statement || !clause) return;
    setBusy("decide");
    onError(null);
    try {
      onStatement(
        await api.post<Statement>(`/projects/${projectId}/compliance/statements/${statement.id}/rows/${clause.id}/suggestion`, {
          action,
          response: action === "edit" ? editing?.response : undefined,
          remark: action === "edit" ? editing?.remark : undefined,
        })
      );
      setEditing(null);
    } catch (err) {
      onError(errorText(err, "Could not apply the decision"));
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
      if (file) body.append("file", file);
      else body.append("statement_path", statementPath);
      onCheck(await api.upload<Statement>(`/projects/${projectId}/compliance/check`, body));
    } catch (err) {
      onError(errorText(err, "Could not check the statement"));
    } finally {
      setBusy(null);
    }
  }

  // The statement as it stands, not the last run: a second Auto-fill finds
  // nothing new to fill, and the rows it filled before are still filled.
  const counts = useMemo(() => {
    const rows = (statement?.rows ?? []).filter((r) => answerable(r) && r.match);
    if (rows.length === 0) return null;
    const result = (r: StatementRow) => r.match?.result;
    return {
      filled: rows.filter((r) => r.origin === "database" && r.match?.result !== "flagged").length,
      flagged: rows.filter((r) => r.origin === "database" && r.match?.result === "flagged").length,
      candidates: rows.filter((r) => !r.response && result(r) === "candidate").length,
      blocked: rows.filter((r) => !r.response && ["missing_model", "scope", "conflict"].includes(result(r) ?? "")).length,
      unmatched: rows.filter((r) => !r.response && result(r) === "none").length,
    };
  }, [statement]);
  const aiCounts = useMemo(() => {
    const rows = (statement?.rows ?? []).filter((r) => answerable(r) && r.ai_class);
    if (rows.length === 0) return null;
    const of = (name: AiFillClass) => rows.filter((r) => r.ai_class === name).length;
    return { filled: of("filled"), confirmed: of("confirmed"), needs_review: of("needs_review"), conflict: of("conflict") };
  }, [statement]);
  const eligible = knowledge?.records.eligible_responses ?? 0;
  // The knowledge base's labels for this system: FA for the fire alarm family, CBS and EML for emergency lighting.
  const knowledgeLabels = system.code === "ELS" ? ["CBS", "EML"] : system.code === "FAS" || system.code === "VES" ? ["FA"] : [system.code];
  const systemStats = knowledge ? Object.entries(knowledge.by_system).filter(([label]) => knowledgeLabels.some((l) => label.startsWith(l))) : [];

  return (
    <aside className="flex flex-col gap-4 xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)] xl:self-start xl:overflow-y-auto">
      <PanelCard title="Knowledge base">
      <p className="text-xs text-gray-500">
        The company's past compliance responses. Auto-fill writes in only answers to the same wording, for the manufacturer and models this
        project's BOQ proposes — as drafts for you to review. It never calls the AI.
      </p>
      {counts && (
        <div className="mt-3 grid grid-cols-5 overflow-hidden rounded-lg border border-gray-200 text-center">
          {(
            [
              ["Filled", counts.filled, "text-brand-700"],
              ["To verify", counts.flagged, "text-amber-600"],
              ["Candidates", counts.candidates, "text-amber-600"],
              ["Blocked", counts.blocked, "text-red-600"],
              ["No match", counts.unmatched, "text-navy-900"],
            ] as [string, number, string][]
          ).map(([label, value, color], i) => (
            <div key={label} className={`py-2 ${i ? "border-l border-gray-200" : ""}`}>
              <div className={`text-base font-semibold ${color}`}>{value}</div>
              <div className="text-[10px] text-gray-500">{label}</div>
            </div>
          ))}
        </div>
      )}
      <button onClick={autofill} disabled={!statement || !canEdit || busy !== null} className={`${btnPrimary} mt-3 w-full justify-center py-2.5`}>
        {busy === "fill" ? `Filling… ${elapsed}s` : "Auto-fill from knowledge base"}
      </button>
      {!statement && <p className="mt-1.5 text-xs text-gray-400">Start the statement first.</p>}
      <div className="mt-2 text-[11px] text-gray-500">
        {knowledge && knowledge.records.responses > 0 ? (
          <>
            <div>
              <span className="font-semibold text-navy-900">{eligible.toLocaleString()}</span> of {knowledge.records.responses.toLocaleString()} past
              responses eligible
              {systemStats.length > 0 && ` · ${systemStats.map(([label, n]) => `${label}: ${n.eligible.toLocaleString()}`).join(", ")}`}
            </div>
            <div className="text-gray-400">Last refreshed {formatWhen(knowledge.last_refreshed_at)} · updated by an administrator</div>
          </>
        ) : (
          <div className="text-amber-700">No knowledge has been imported yet. An administrator updates it under Knowledge base.</div>
        )}
      </div>
      </PanelCard>

      <PanelCard title="Auto-fill with AI">
        <p className="text-xs text-gray-500">
          Answers the clauses the knowledge base could not, reading the project's facts, its scope of work, its BOQ and the nearest past
          answers. Every row it writes is a draft; the ones that turn on this project, or that disagree with the draft already there, come
          back highlighted for you.
        </p>
        <label className="mt-3 block text-xs font-semibold text-navy-900">
          Fill
          <select
            value={aiScope}
            onChange={(event) => setAiScope(event.target.value as "unanswered" | "review" | "all")}
            disabled={!statement || !canEdit || busy !== null}
            className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-sm font-normal text-navy-900 disabled:bg-gray-50"
          >
            <option value="unanswered">Clauses with no answer yet</option>
            <option value="review">Clauses waiting on review</option>
            <option value="all">Every clause not answered by me</option>
          </select>
        </label>
        <button
          onClick={aiAutofill}
          disabled={!statement || !canEdit || !aiAvailable || busy !== null || aiRunning}
          className={`${btnPrimary} mt-3 w-full justify-center py-2.5`}
        >
          <Sparkle />
          {aiRunning ? `Asking the AI… ${aiJob?.done ?? 0} of ${aiJob?.total ?? 0}` : busy === "aifill" ? "Starting…" : "Auto-fill with AI"}
        </button>
        {aiRunning && aiJob && (
          <div className="mt-2">
            <div className="h-1.5 overflow-hidden rounded-full bg-gray-200">
              <div className="h-full bg-brand-600 transition-all" style={{ width: `${aiJob.total ? (aiJob.done / aiJob.total) * 100 : 0}%` }} />
            </div>
            <p className="mt-1 text-[11px] text-gray-500">
              Runs on the server, paced to the AI provider's limit. You can keep reviewing; answers appear as each batch returns.
            </p>
          </div>
        )}
        {!aiRunning && aiJob?.interrupted && (
          <p className="mt-1.5 text-xs text-amber-700">The last fill was interrupted (the server restarted). Run it again to answer the rest.</p>
        )}
        {!aiAvailable && <p className="mt-1.5 text-xs text-amber-700">AI is not configured on this server.</p>}
        {aiCounts && (
          <div className="mt-1.5 grid grid-cols-4 gap-1 text-center text-[11px]">
            {(
              [
                ["Answered", aiCounts.filled, "text-purple-700"],
                ["Confirmed", aiCounts.confirmed, "text-green-700"],
                ["To review", aiCounts.needs_review, "text-amber-700"],
                ["Disagrees", aiCounts.conflict, "text-red-700"],
              ] as [string, number, string][]
            ).map(([label, value, color]) => (
              <div key={label} className="rounded-lg bg-gray-50 py-1">
                <div className={`text-sm font-semibold ${color}`}>{value}</div>
                <div className="text-gray-500">{label}</div>
              </div>
            ))}
          </div>
        )}
      </PanelCard>

      <PanelCard title={clause ? `Clause ${clause.ref}` : "Selected clause"}>
        {!clause && <p className="text-xs text-gray-500">Click a clause in the table to see where its answer came from and to review it.</p>}
        {clause && (
          <>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <WorkflowBadge workflow={clause.workflow} />
              <TechnicalChip technical={clause.technical} />
            </div>
            <MatchDetails key={clause.id} match={clause.match ?? null} onUse={canEdit ? (id) => void useAnswer(id) : undefined} busy={busy === "use"} />

            <div className="mt-3 rounded-xl border border-gray-200 bg-gray-50/60 p-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-navy-900">
                <Sparkle className="h-4 w-4 text-brand-600" />
                Review with AI
                <span className={`ml-auto rounded-full px-2 py-0.5 text-[10px] font-semibold ${aiAvailable ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-500"}`}>
                  {aiAvailable ? "Ready" : "Off"}
                </span>
              </div>
              <p className="mt-0.5 text-[11px] text-gray-500">
                Sends this clause, its BOQ lines, the scope and its past answers — nothing else — and only when you click.
              </p>
              {canEdit && (
                <>
                  <textarea
                    value={instruction}
                    onChange={(e) => setInstruction(e.target.value)}
                    disabled={!aiAvailable || inFlight}
                    rows={2}
                    placeholder="Optional instruction, e.g. “Check the standby duration against the BOQ batteries.”"
                    className="mt-2 w-full rounded-lg border border-gray-300 px-3 py-2 text-xs outline-none placeholder:text-gray-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-100 disabled:bg-gray-50"
                  />
                  <button
                    onClick={() => void onReview(clause.id, instruction)}
                    disabled={!aiAvailable || inFlight || busy !== null}
                    className={`${btnSecondary} mt-2 w-full justify-center`}
                  >
                    {inFlight ? "Reviewing…" : review ? "Review again" : "Review this clause"}
                  </button>
                </>
              )}

              {review && !inFlight && (
                <div className="mt-3 border-t border-gray-200 pt-2 text-xs">
                  {review.status === "failed" ? (
                    <div className="rounded-lg bg-red-50 px-2 py-1.5 text-red-700">
                      The review failed: {review.error}. Nothing was retried — press Review again when ready.
                    </div>
                  ) : review.suggestion ? (
                    <SuggestionCard
                      review={review}
                      current={clause}
                      editing={editing}
                      canEdit={canEdit}
                      busy={busy === "decide"}
                      onEditStart={() => setEditing({ response: review.suggestion!.suggested_response, remark: review.suggestion!.suggested_remark })}
                      onEditChange={setEditing}
                      onDecide={decide}
                    />
                  ) : null}
                </div>
              )}
            </div>
          </>
        )}
      </PanelCard>

      <section className="rounded-xl border border-gray-200 bg-white shadow-sm">
        <button onClick={() => setShowCheck((v) => !v)} className="flex w-full items-center justify-between px-4 py-3 text-base font-semibold text-navy-900">
          Check an existing statement
          <span className="text-xl leading-none text-gray-500">{showCheck ? "−" : "+"}</span>
        </button>
        {showCheck && (
          <div className="px-4 pb-4">
            <p className="text-xs text-gray-500">
              Lay a statement (.xlsx, .xls, .docx) against this specification: missing and unanswered clauses, and another project's or
              manufacturer's name.
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
      </section>
    </aside>
  );
}

function SourceLine({ source }: { source: KnowledgeCandidate["sources"][number] }) {
  return (
    <li>
      <span className="font-mono text-[10px] text-gray-500">{source.source_id}</span> {source.filename}
      {source.project ? ` · ${source.project}` : ""}
      {source.page ? ` · p${source.page}` : ""}
      {source.document_revision ? ` · ${source.document_revision}` : ""}
      {source.superseded && <span className="text-amber-700"> · superseded</span>}
      {source.review_status && <span className="text-gray-400"> · {source.review_status.slice(0, 60)}</span>}
    </li>
  );
}

/** Where a row's answer came from, or why the knowledge base gave none. */
function MatchDetails({ match, onUse, busy }: { match: KnowledgeMatch | null; onUse?: (responseId: string) => void; busy?: boolean }) {
  // Until toggled: open when the past answers are what there is to look at.
  const [toggled, setToggled] = useState<boolean | null>(null);
  if (!match) return <p className="mt-2 text-xs text-gray-500">Not looked up yet — run Auto-fill.</p>;
  const tone = match.result === "eligible" ? "text-green-700" : match.result === "none" ? "text-gray-500" : "text-amber-700";
  const open = toggled ?? (match.result !== "eligible" && match.result !== "flagged");
  return (
    <div className="mt-2 text-xs">
      <div className={`font-semibold ${tone}`}>{matchLabelOf(match)}</div>
      <p className="mt-0.5 text-gray-600">{match.explanation}</p>
      {match.response_id && (
        <div className="mt-2 rounded-lg border border-gray-200 bg-white p-2">
          <div className="text-gray-500">
            Record <span className="font-mono text-[10px]">{match.response_id}</span>
            {match.requirement_id && (
              <>
                {" "}
                · requirement <span className="font-mono text-[10px]">{match.requirement_id}</span>
              </>
            )}
            {match.equivalence && " · via a validated equivalence"}
          </div>
          <div className="mt-1 text-gray-800">“{match.historical_response}”</div>
          <div className="mt-1 text-gray-500">
            Historical status: {match.historical_status ?? "unknown"} (proposed) · {match.manufacturer ?? "manufacturer unconfirmed"}
            {match.brand && match.brand !== match.manufacturer ? ` (${match.brand})` : ""}
            {match.models ? ` · models ${match.models}` : ""}
          </div>
          {match.remarks && <div className="mt-1 text-gray-600">Remarks: {match.remarks}</div>}
          {match.boq_item && (
            <div className="mt-1 text-gray-600">
              BOQ: {match.boq_item.manufacturer ?? "—"} {match.boq_item.model ?? ""} — {match.boq_item.description}
              {match.boq_item.quantity ? ` (${match.boq_item.quantity} ${match.boq_item.unit ?? ""})` : ""}
            </div>
          )}
          {match.sources && match.sources.length > 0 && (
            <ul className="mt-1 space-y-0.5 text-gray-600">
              {match.sources.map((s) => (
                <SourceLine key={`${s.source_id}-${s.page}`} source={s} />
              ))}
            </ul>
          )}
        </div>
      )}
      {match.unresolved.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-4 text-amber-800">
          {match.unresolved.map((u) => (
            <li key={u}>{u}</li>
          ))}
        </ul>
      )}
      {match.candidates.length > 0 && (
        <div className="mt-2">
          <button onClick={() => setToggled(!open)} className="font-medium text-brand-600 hover:underline">
            {open ? "Hide" : "Show"} {match.candidates.length} past {match.candidates.length === 1 ? "answer" : "answers"}
          </button>
          {open && (
            <ul className="mt-1 space-y-2">
              {match.candidates.map((c, i) => (
                <li key={c.response_id ?? `approved-${i}`} className="rounded-lg border border-gray-200 bg-white p-2">
                  <div className="text-gray-500">
                    <span className="font-mono text-[10px]">{c.response_id ?? "Engineer-approved"}</span> · {c.manufacturer ?? "unconfirmed"} · {c.historical_status ?? "?"}
                    {c.similarity !== null && c.similarity < 1 ? ` · ${Math.round(c.similarity * 100)}% alike` : ""}
                    {c.eligibility === "blocked" && <span className="text-amber-700"> · not eligible: {c.eligibility_reasons}</span>}
                  </div>
                  {c.requirement_text && c.similarity !== null && c.similarity < 1 && <div className="mt-0.5 text-gray-500">Their clause: “{c.requirement_text}”</div>}
                  <div className="mt-0.5 text-gray-800">“{c.historical_response}”</div>
                  {c.remarks && <div className="mt-0.5 text-gray-600">Remarks: {c.remarks}</div>}
                  {onUse && c.response && c.response_id && (
                    <button onClick={() => onUse(c.response_id!)} disabled={busy} className={`${btnSmall} mt-1 border-brand-200 text-brand-700`}>
                      {busy ? "Using…" : `Use this answer (${c.response})`}
                    </button>
                  )}
                  {c.sources.length > 0 && (
                    <ul className="mt-0.5 text-gray-500">
                      {c.sources.slice(0, 2).map((s) => (
                        <SourceLine key={`${s.source_id}-${s.page}`} source={s} />
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function SuggestionCard({
  review,
  current,
  editing,
  canEdit,
  busy,
  onEditStart,
  onEditChange,
  onDecide,
}: {
  review: NonNullable<StatementRow["ai_review"]>;
  current: StatementRow;
  editing: { response: string; remark: string } | null;
  canEdit: boolean;
  busy: boolean;
  onEditStart: () => void;
  onEditChange: (value: { response: string; remark: string } | null) => void;
  onDecide: (action: "accept" | "edit" | "reject") => void;
}) {
  const s = review.suggestion!;
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold text-navy-900">Suggestion</span>
        <span className="text-[10px] text-gray-400">
          {formatWhen(review.at)} · {review.model}
          {review.decision ? ` · ${review.decision}` : ""}
        </span>
      </div>
      <div className="mt-1.5 grid grid-cols-2 gap-2">
        <div className="rounded-lg border border-gray-200 bg-white p-2">
          <div className="text-[10px] uppercase text-gray-400">Current</div>
          <div className={`mt-0.5 font-medium ${TONE_STYLES[toneOf(current.response)].text}`}>{current.response || "—"}</div>
          <div className="text-gray-600">{current.remark || <span className="text-gray-400">no remark</span>}</div>
        </div>
        <div className="rounded-lg border border-brand-200 bg-brand-50/50 p-2">
          <div className="text-[10px] uppercase text-brand-700">Suggested</div>
          {editing ? (
            <>
              <select
                value={editing.response}
                onChange={(e) => onEditChange({ ...editing, response: e.target.value })}
                className="mt-0.5 w-full rounded border border-gray-300 px-1 py-0.5 text-xs"
              >
                {COMPLIANCE_RESPONSES.map((option) => (
                  <option key={option}>{option}</option>
                ))}
              </select>
              <input
                value={editing.remark}
                onChange={(e) => onEditChange({ ...editing, remark: e.target.value })}
                className="mt-1 w-full rounded border border-gray-300 px-1 py-0.5 text-xs"
                placeholder="Remark"
              />
            </>
          ) : (
            <>
              <div className={`mt-0.5 font-medium ${TONE_STYLES[toneOf(s.suggested_response)].text}`}>{s.suggested_response}</div>
              <div className="text-gray-700">{s.suggested_remark || <span className="text-gray-400">no remark</span>}</div>
            </>
          )}
          <div className="mt-1 text-gray-500">Status: {TECHNICAL_LABELS[s.proposed_compliance_status as TechnicalStatus]} (proposed)</div>
        </div>
      </div>
      {s.review_notes && <p className="mt-1.5 text-gray-700">{s.review_notes}</p>}
      {s.evidence_references.length > 0 && <div className="mt-1 text-gray-500">Evidence: {s.evidence_references.join("; ")}</div>}
      {s.deviations.length > 0 && (
        <div className="mt-1 text-red-700">
          Deviations: {s.deviations.join("; ")}
        </div>
      )}
      {s.missing_information.length > 0 && (
        <div className="mt-1 text-amber-800">
          Missing: {s.missing_information.join("; ")}
        </div>
      )}
      {canEdit && !review.decision && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {editing ? (
            <>
              <button onClick={() => onDecide("edit")} disabled={busy} className={`${btnSmall} border-brand-200 text-brand-700`}>
                Apply edit
              </button>
              <button onClick={() => onEditChange(null)} disabled={busy} className={btnSmall}>
                Cancel
              </button>
            </>
          ) : (
            <>
              <button onClick={() => onDecide("accept")} disabled={busy} className={`${btnSmall} border-brand-200 text-brand-700`}>
                Accept
              </button>
              <button onClick={onEditStart} disabled={busy} className={btnSmall}>
                Edit
              </button>
              <button onClick={() => onDecide("reject")} disabled={busy} className={`${btnSmall} text-red-700`}>
                Reject
              </button>
            </>
          )}
        </div>
      )}
      {review.decision && <p className="mt-1.5 text-[11px] text-gray-400">Accepting a suggestion makes it the draft; mark the row reviewed once you have checked it.</p>}
    </div>
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
    ["Rows not in spec", summary.rows_not_in_spec ?? 0],
  ];

  return (
    <section className="mt-5 rounded-xl border border-gray-200 bg-white">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 p-4">
        <div>
          <h2 className="font-semibold text-navy-900">Check of {statement.statement_name}</h2>
          <div className="mt-1 text-xs text-gray-500">
            Against {statement.spec.filename} · {formatWhen(statement.created_at)} · coverage and identity only
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
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
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
