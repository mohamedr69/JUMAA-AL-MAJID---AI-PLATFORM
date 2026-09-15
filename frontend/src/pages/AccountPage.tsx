import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import { ROLE_LABELS, type Account, type ActivityEvent, type ActivityPage } from "../lib/types";

/** The API sends naive UTC; without a zone the browser would read it as
 * local time. */
function formatWhen(value: string | null): string {
  if (!value) return "—";
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const TABS = ["Overview", "Projects", "Activity", "Submittals", "BOQ revisions", "Compliance"] as const;
type Tab = (typeof TABS)[number];

const ACTION_GROUPS: { value: string; label: string }[] = [
  { value: "", label: "All activity" },
  { value: "auth", label: "Sign-ins" },
  { value: "project", label: "Projects" },
  { value: "boq", label: "BOQ" },
  { value: "submittal", label: "Submittals" },
  { value: "document", label: "Documents" },
  { value: "design", label: "Calculations" },
  { value: "compliance", label: "Compliance" },
  { value: "user", label: "User accounts" },
];

const COUNT_LABELS: [string, string][] = [
  ["projects", "Projects"],
  ["projects_created", "Projects created"],
  ["projects_opened", "Projects opened"],
  ["logins", "Sign-ins"],
  ["boq_saves", "BOQ saves"],
  ["boq_revisions_issued", "BOQ revisions issued"],
  ["submittals", "Submittals"],
  ["compliance_statements", "Compliance statements"],
  ["compliance_approvals", "Statements approved"],
  ["compliance_clause_changes", "Clause answers changed"],
  ["activity_events", "Actions recorded"],
];

const PAGE_SIZE = 100;

/** One user's record: their account, the projects they worked on, what they
 * changed and made. `/account` is the signed-in user's own; an admin opens
 * anyone's under `/admin/users/:userId`. */
export function AccountPage() {
  const { userId } = useParams();
  const base = userId ? `/users/${userId}` : "/auth/me";

  const [account, setAccount] = useState<Account | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("Overview");

  useEffect(() => {
    let cancelled = false;
    setAccount(null);
    api
      .get<Account>(`${base}/account`)
      .then((data) => !cancelled && setAccount(data))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Failed to load the account"));
    return () => {
      cancelled = true;
    };
  }, [base]);

  async function exportRecord() {
    if (!account) return;
    try {
      await api.download(`${base}/account/export.xlsx`, `User record - ${account.user.full_name}.xlsx`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Export failed");
    }
  }

  if (error) return <div className="mx-auto max-w-6xl rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>;
  if (!account) return <div className="mx-auto max-w-6xl text-sm text-gray-400">Loading...</div>;

  const { user } = account;
  return (
    <div className="mx-auto max-w-6xl">
      {userId && (
        <Link to="/admin/users" className="mb-2 inline-block text-xs text-brand-600 hover:underline">
          ← All users
        </Link>
      )}
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-navy-900">{user.full_name}</h1>
          <div className="text-sm text-gray-500">
            {user.email} · {ROLE_LABELS[user.role]} ·{" "}
            <span className={user.is_active ? "text-green-700" : "text-gray-400"}>
              {user.is_active ? "Active" : "Disabled"}
            </span>
          </div>
        </div>
        <button
          onClick={exportRecord}
          className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100"
        >
          Export to Excel
        </button>
      </div>

      <div className="mb-4 flex flex-wrap gap-1 border-b border-gray-200">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
              tab === t ? "border-brand-600 text-brand-700" : "border-transparent text-gray-500 hover:text-navy-900"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "Overview" && <Overview account={account} />}
      {tab === "Projects" && <ProjectsTab account={account} />}
      {tab === "Activity" && <ActivityTab base={base} account={account} />}
      {tab === "Submittals" && <SubmittalsTab account={account} />}
      {tab === "BOQ revisions" && <RevisionsTab account={account} />}
      {tab === "Compliance" && <ComplianceTab account={account} />}
    </div>
  );
}

function Card({ children }: { children: ReactNode }) {
  return <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white">{children}</div>;
}

function Empty({ text }: { text: string }) {
  return <div className="px-4 py-6 text-center text-sm text-gray-400">{text}</div>;
}

function Th({ children }: { children?: ReactNode }) {
  return <th className="whitespace-nowrap px-4 py-3">{children}</th>;
}

function ProjectLink({ id, label, deleted }: { id: number | null; label: string; deleted?: boolean }) {
  if (id === null || deleted) {
    return (
      <span className="text-gray-500">
        {label}
        {deleted && <span className="ml-2 rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-500">deleted</span>}
      </span>
    );
  }
  return (
    <Link to={`/projects/${id}`} className="font-medium text-brand-600 hover:underline">
      {label}
    </Link>
  );
}

function Overview({ account }: { account: Account }) {
  const { user, counts } = account;
  return (
    <div className="space-y-4">
      <Card>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-3 p-4 text-sm sm:grid-cols-2">
          {[
            ["Name", user.full_name],
            ["Email", user.email],
            ["Role", ROLE_LABELS[user.role]],
            ["Status", user.is_active ? "Active" : "Disabled"],
            ["Account created", formatWhen(user.created_at)],
            ["Last sign-in", formatWhen(user.last_login_at)],
          ].map(([label, value]) => (
            <div key={label}>
              <dt className="text-xs uppercase text-gray-400">{label}</dt>
              <dd className="font-medium text-navy-900">{value}</dd>
            </div>
          ))}
        </dl>
      </Card>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {COUNT_LABELS.map(([key, label]) => (
          <div key={key} className="rounded-xl border border-gray-200 bg-white p-4">
            <div className="text-2xl font-semibold text-navy-900">{(counts[key] ?? 0).toLocaleString()}</div>
            <div className="text-xs text-gray-500">{label}</div>
          </div>
        ))}
      </div>
      <div>
        <h2 className="mb-2 text-sm font-semibold text-navy-900">Latest activity</h2>
        <Card>
          <EventTable events={account.activity.slice(0, 10)} />
        </Card>
      </div>
    </div>
  );
}

function ProjectsTab({ account }: { account: Account }) {
  return (
    <Card>
      {account.projects.length === 0 ? (
        <Empty text="No projects yet." />
      ) : (
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <Th>Project</Th>
              <Th>Status</Th>
              <Th>Role in project</Th>
              <Th>Times opened</Th>
              <Th>Changes</Th>
              <Th>Last activity</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {account.projects.map((p) => (
              <tr key={`${p.id}-${p.label}`}>
                <td className="px-4 py-3">
                  <ProjectLink id={p.id} label={p.label} deleted={p.deleted} />
                </td>
                <td className="px-4 py-3 capitalize text-gray-500">{p.status ?? "—"}</td>
                <td className="px-4 py-3 text-gray-500">
                  {[p.created && "Created", p.assigned && "Design engineer"].filter(Boolean).join(", ") || "Worked on"}
                </td>
                <td className="px-4 py-3 text-gray-500">{p.opened_count}</td>
                <td className="px-4 py-3 text-gray-500">{p.changes}</td>
                <td className="whitespace-nowrap px-4 py-3 text-gray-500">{formatWhen(p.last_activity_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function detailText(detail: ActivityEvent["detail"]): string {
  if (!detail) return "";
  return Object.entries(detail)
    .filter(([, value]) => value !== null && value !== "")
    .map(([key, value]) => `${key.replace(/_/g, " ")}: ${String(value)}`)
    .join(" · ");
}

function EventTable({ events }: { events: ActivityEvent[] }) {
  if (events.length === 0) return <Empty text="Nothing recorded yet." />;
  return (
    <table className="w-full text-sm">
      <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
        <tr>
          <Th>When</Th>
          <Th>Project</Th>
          <Th>What</Th>
        </tr>
      </thead>
      <tbody className="divide-y divide-gray-100">
        {events.map((e) => (
          <tr key={e.id} className="align-top">
            <td className="whitespace-nowrap px-4 py-3 text-gray-500">{formatWhen(e.at)}</td>
            <td className="px-4 py-3">
              {e.project_label ? <ProjectLink id={e.project_id} label={e.project_label} /> : <span className="text-gray-400">—</span>}
            </td>
            <td className="px-4 py-3">
              <div className="text-navy-900">{e.summary}</div>
              {detailText(e.detail) && <div className="mt-0.5 text-xs text-gray-400">{detailText(e.detail)}</div>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ActivityTab({ base, account }: { base: string; account: Account }) {
  const [action, setAction] = useState("");
  const [projectId, setProjectId] = useState("");
  const [page, setPage] = useState<ActivityPage | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
    if (action) params.set("action", action);
    if (projectId) params.set("project_id", projectId);
    return params;
  }, [action, projectId]);

  useEffect(() => {
    let cancelled = false;
    setPage(null);
    api
      .get<ActivityPage>(`${base}/activity?${query}`)
      .then((data) => !cancelled && setPage(data))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Failed to load activity"));
    return () => {
      cancelled = true;
    };
  }, [base, query]);

  async function loadMore() {
    if (!page) return;
    setLoadingMore(true);
    try {
      const params = new URLSearchParams(query);
      params.set("offset", String(page.events.length));
      const next = await api.get<ActivityPage>(`${base}/activity?${params}`);
      setPage({ ...next, events: [...page.events, ...next.events] });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load activity");
    } finally {
      setLoadingMore(false);
    }
  }

  const projects = account.projects.filter((p) => p.id !== null);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <select value={action} onChange={(e) => setAction(e.target.value)} className="rounded-lg border border-gray-300 px-3 py-2 text-sm">
          {ACTION_GROUPS.map((g) => (
            <option key={g.value} value={g.value}>
              {g.label}
            </option>
          ))}
        </select>
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)} className="rounded-lg border border-gray-300 px-3 py-2 text-sm">
          <option value="">All projects</option>
          {projects.map((p) => (
            <option key={p.id} value={String(p.id)}>
              {p.label}
            </option>
          ))}
        </select>
        {page && <span className="self-center text-xs text-gray-400">{page.total.toLocaleString()} recorded</span>}
      </div>
      {error && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      <Card>{page ? <EventTable events={page.events} /> : <Empty text="Loading..." />}</Card>
      {page && page.events.length < page.total && (
        <button
          onClick={loadMore}
          disabled={loadingMore}
          className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-60"
        >
          {loadingMore ? "Loading..." : `Show more (${page.total - page.events.length} older)`}
        </button>
      )}
    </div>
  );
}

function SubmittalsTab({ account }: { account: Account }) {
  return (
    <Card>
      {account.submittals.length === 0 ? (
        <Empty text="No submittals created or changed." />
      ) : (
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <Th>Project</Th>
              <Th>Submittal</Th>
              <Th>System</Th>
              <Th>Revision</Th>
              <Th>Status</Th>
              <Th>By this user</Th>
              <Th>Updated</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {account.submittals.map((s) => (
              <tr key={s.id}>
                <td className="px-4 py-3">
                  <ProjectLink id={s.project_id} label={s.project_label} />
                </td>
                <td className="px-4 py-3">
                  <div className="text-navy-900">{s.title}</div>
                  {s.reference && <div className="text-xs text-gray-400">{s.reference}</div>}
                </td>
                <td className="px-4 py-3 text-gray-500">{s.system_code ?? "—"}</td>
                <td className="px-4 py-3 text-gray-500">{s.revision}</td>
                <td className="px-4 py-3 capitalize text-gray-500">
                  {s.status.replace(/_/g, " ")}
                  {s.reply_code && ` (${s.reply_code})`}
                </td>
                <td className="px-4 py-3 text-gray-500">
                  {[s.created_by_user && "Created", s.changes_by_user > 0 && `${s.changes_by_user} change${s.changes_by_user === 1 ? "" : "s"}`]
                    .filter(Boolean)
                    .join(", ")}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-gray-500">{formatWhen(s.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function RevisionsTab({ account }: { account: Account }) {
  return (
    <Card>
      {account.boq_revisions.length === 0 ? (
        <Empty text="No BOQ revisions issued." />
      ) : (
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <Th>Project</Th>
              <Th>Revision</Th>
              <Th>Lines</Th>
              <Th>Note</Th>
              <Th>Issued</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {account.boq_revisions.map((r) => (
              <tr key={`${r.project_id}-${r.number}`}>
                <td className="px-4 py-3">
                  <ProjectLink id={r.project_id} label={r.project_label} />
                </td>
                <td className="px-4 py-3">
                  <Link to={`/projects/${r.project_id}/boq/revisions`} className="text-brand-600 hover:underline">
                    {r.label}
                  </Link>
                </td>
                <td className="px-4 py-3 text-gray-500">{r.lines}</td>
                <td className="px-4 py-3 text-gray-500">{r.note ?? "—"}</td>
                <td className="whitespace-nowrap px-4 py-3 text-gray-500">{formatWhen(r.issued_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function ComplianceTab({ account }: { account: Account }) {
  return (
    <Card>
      {account.compliance_statements.length === 0 ? (
        <Empty text="No compliance statements prepared, changed or approved." />
      ) : (
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr>
              <Th>Project</Th>
              <Th>System</Th>
              <Th>Kind</Th>
              <Th>Clauses</Th>
              <Th>By this user</Th>
              <Th>Approved</Th>
              <Th>Updated</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {account.compliance_statements.map((s) => (
              <tr key={s.id}>
                <td className="px-4 py-3">
                  <ProjectLink id={s.project_id} label={s.project_label} />
                </td>
                <td className="px-4 py-3 text-gray-500">{s.system_code}</td>
                <td className="px-4 py-3 capitalize text-gray-500">{s.kind === "prepare" ? "Prepared" : "Checked"}</td>
                <td className="px-4 py-3 text-gray-500">{s.clauses}</td>
                <td className="px-4 py-3 text-gray-500">
                  {[
                    s.created_by_user && "Created",
                    s.approved_by_user && "Approved",
                    s.clause_changes_by_user > 0 && `${s.clause_changes_by_user} clause change${s.clause_changes_by_user === 1 ? "" : "s"}`,
                  ]
                    .filter(Boolean)
                    .join(", ")}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-gray-500">{formatWhen(s.approved_at)}</td>
                <td className="whitespace-nowrap px-4 py-3 text-gray-500">{formatWhen(s.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
