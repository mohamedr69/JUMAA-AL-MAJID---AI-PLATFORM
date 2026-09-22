import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { api, ApiError } from "../lib/api";
import { DIVISIONS, type Division } from "../lib/divisions";

interface EstimationProject {
  id: number;
  reference: string;
  title: string;
  client: string;
  created_at: string;
}

const inputClass = "mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm";
const buttonClass = "rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60";

export function EstimationDashboard({ division = "estimation" }: { division?: Division }) {
  const label = DIVISIONS[division].label;
  const base = `/${division}/projects`;
  const { user } = useAuth();
  return (
    <div className="mx-auto max-w-5xl py-10">
      <section className="rounded-2xl bg-navy-950 p-8 text-white">
        <p className="text-sm font-semibold uppercase tracking-widest text-white/60">{label} Team</p>
        <h1 className="mt-3 text-3xl font-bold">{label} Dashboard</h1>
        <p className="mt-3 text-white/70">Welcome, {user?.full_name}. Create a project or open an existing {label} project.</p>
      </section>
      <div className="mt-6 grid gap-5 sm:grid-cols-2">
        {[{ title: "Open Project", to: base, description: `Browse and open your team's ${label} projects.` },
          { title: "Create New Project", to: `${base}/new`, description: `Start a new ${label} project.` }].map(item => (
          <Link key={item.to} to={item.to} className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm transition hover:border-brand-500 hover:shadow-md">
            <h2 className="text-xl font-bold text-navy-900">{item.title} <span aria-hidden="true" className="text-brand-600">→</span></h2>
            <p className="mt-2 text-sm text-gray-500">{item.description}</p>
          </Link>
        ))}
      </div>
    </div>
  );
}

export function EstimationCreatePage({ division = "estimation" }: { division?: Division }) {
  const label = DIVISIONS[division].label;
  const base = `/${division}/projects`;
  const navigate = useNavigate();
  const [reference, setReference] = useState("");
  const [title, setTitle] = useState("");
  const [client, setClient] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const project = await api.post<EstimationProject>(base, { reference, title, client });
      navigate(`${base}/${project.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create project");
    } finally { setSaving(false); }
  }
  return (
    <div className="mx-auto max-w-2xl">
      <Link to="/" className="text-sm text-brand-600">← {label} Dashboard</Link>
      <h1 className="my-5 text-2xl font-bold text-navy-900">Create New Project</h1>
      <form onSubmit={submit} className="space-y-5 rounded-xl border border-gray-200 bg-white p-6">
        {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
        <label className="block text-sm font-medium">Project reference
          <input required maxLength={100} value={reference} onChange={e => setReference(e.target.value)} className={inputClass} />
        </label>
        <label className="block text-sm font-medium">Project title
          <input required maxLength={255} value={title} onChange={e => setTitle(e.target.value)} className={inputClass} />
        </label>
        <label className="block text-sm font-medium">Client (optional)
          <input maxLength={255} value={client} onChange={e => setClient(e.target.value)} className={inputClass} />
        </label>
        <button disabled={saving || !reference.trim() || !title.trim()} className={buttonClass}>{saving ? "Creating…" : "Create Project"}</button>
      </form>
    </div>
  );
}

export function EstimationOpenPage({ division = "estimation" }: { division?: Division }) {
  const label = DIVISIONS[division].label;
  const base = `/${division}/projects`;
  const [projects, setProjects] = useState<EstimationProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    let active = true;
    api.get<EstimationProject[]>(base)
      .then(data => { if (active) setProjects(data); })
      .catch(err => { if (active) setError(err instanceof ApiError ? err.message : "Failed to load projects"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [base]);
  const filtered = projects.filter(p => `${p.reference} ${p.title} ${p.client}`.toLowerCase().includes(query.toLowerCase()));
  return (
    <div className="mx-auto max-w-4xl">
      <Link to="/" className="text-sm text-brand-600">← {label} Dashboard</Link>
      <div className="my-5 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-navy-900">Open Project</h1>
        <Link to={`${base}/new`} className={buttonClass}>Create New Project</Link>
      </div>
      <input aria-label={`Search ${label} projects`} placeholder="Search by reference, title or client" value={query} onChange={e => setQuery(e.target.value)} className={inputClass} />
      {error && <p role="alert" className="mt-4 text-red-700">{error}</p>}
      {loading ? <p className="mt-6 text-gray-500">Loading projects…</p> : !error && (
        <div className="mt-5 space-y-3">
          {filtered.map(project => (
            <Link key={project.id} to={`${base}/${project.id}`} className="block rounded-xl border border-gray-200 bg-white p-5 hover:border-brand-500">
              <p className="text-xs font-semibold text-brand-600">{project.reference}</p>
              <h2 className="mt-1 font-bold text-navy-900">{project.title}</h2>
              {project.client && <p className="mt-1 text-sm text-gray-500">{project.client}</p>}
            </Link>
          ))}
          {!filtered.length && <p className="rounded-xl border border-dashed border-gray-300 p-8 text-center text-gray-500">{projects.length ? "No matching projects." : `No ${label} projects yet. Create a new project to get started.`}</p>}
        </div>
      )}
    </div>
  );
}

export function EstimationProjectPage({ division = "estimation" }: { division?: Division }) {
  const label = DIVISIONS[division].label;
  const base = `/${division}/projects`;
  const { id } = useParams();
  const [project, setProject] = useState<EstimationProject | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setProject(null);
    setError("");
    api.get<EstimationProject>(`${base}/${id}`)
      .then(data => { if (active) setProject(data); })
      .catch(err => { if (active) setError(err instanceof ApiError ? err.message : "Failed to open project"); });
    return () => { active = false; };
  }, [id, base]);
  return (
    <div className="mx-auto max-w-3xl">
      <Link to={base} className="text-sm text-brand-600">← Open Project</Link>
      {error ? <p role="alert" className="mt-5 text-red-700">{error}</p> : !project ? <p className="mt-5">Loading project…</p> : (
        <section className="mt-5 rounded-2xl border border-gray-200 bg-white p-8">
          <p className="text-sm font-semibold text-brand-600">{label} · {project.reference}</p>
          <h1 className="mt-2 text-2xl font-bold text-navy-900">{project.title}</h1>
          <dl className="mt-6 space-y-3 text-sm">
            <div><dt className="text-gray-500">Client</dt><dd>{project.client || "—"}</dd></div>
            <div><dt className="text-gray-500">Created</dt><dd>{new Date(project.created_at).toLocaleDateString()}</dd></div>
          </dl>
        </section>
      )}
    </div>
  );
}
