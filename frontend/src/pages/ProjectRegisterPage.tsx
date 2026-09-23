import { useEffect, useState } from "react";
import { ApiError, api } from "../lib/api";
import { formatApiDate } from "../lib/format";
import type {
  EngineerLoad,
  RegisterDivision,
  RegisterPage,
  RegisterProject,
  RegisterRevision,
} from "../lib/types";

/** The design manager's review of the work: every job in the company's
 * register, by division and by engineer, newest first.
 *
 * The register is a workbook kept in the archive, outside the platform,
 * and read here rather than copied in -- it is edited daily, and a copy
 * would only be a staler answer. Read-only for the same reason: a job is
 * added by editing the workbook, not here.
 *
 * A division is the engineers in it, not the systems on a job: the
 * register's System column runs to eighty-odd spellings and a fire alarm
 * engineer picks up the occasional CCTV job, which put the same person in
 * both tables. So a job counts once per engineer carrying it, the jobs
 * shared by two engineers count for both, and the division totals are
 * larger than the number of jobs. The page says so where it shows them.
 */
/** How often the open page asks whether the workbook has changed. Short
 * enough that a manager sees an edit while they are still looking, long
 * enough that it is a handful of `stat` calls a minute. */
const WATCH_SECONDS = 20;

export function ProjectRegisterPage() {
  const [page, setPage] = useState<RegisterPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  /** Which division's latest job is open below the overview. */
  const [open, setOpen] = useState<string | null>(null);
  /** The engineer whose jobs are being listed, when one was clicked. */
  const [engineer, setEngineer] = useState<string | null>(null);
  /** Narrowed to one status, from the filter or a status tile. */
  const [status, setStatus] = useState<string | null>(null);
  /** Bumped when the workbook changes under us, to read it again. */
  const [reloads, setReloads] = useState(0);
  /** Shown briefly after the page has picked a change up. */
  const [justUpdated, setJustUpdated] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setSearch(query), 250);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    let live = true;
    const params = new URLSearchParams();
    if (search.trim()) params.set("q", search.trim());
    if (engineer) params.set("engineer", engineer);
    if (status) params.set("status", status);
    api
      .get<RegisterPage>(`/register/projects?${params}`)
      .then((rows) => live && setPage(rows))
      .catch((err) => live && setError(err instanceof ApiError ? err.message : "The register could not be read"));
    return () => {
      live = false;
    };
  }, [search, engineer, status, reloads]);

  /* The register is a workbook in a synced folder and is edited while
   * this page is open. Watching it is a `stat` every WATCH_SECONDS --
   * the timestamp and size, nothing parsed -- and the register is read
   * again only when that differs from what is being shown. Polling the
   * whole thing on a timer would cost two thousand rows every time for
   * nothing. */
  const revision = page?.revision ?? null;
  useEffect(() => {
    if (!revision) return;
    let live = true;
    const timer = setInterval(() => {
      api
        .get<RegisterRevision>("/register/revision")
        .then((now) => {
          if (!live || !now.revision || now.revision === revision) return;
          setJustUpdated(true);
          setReloads((n) => n + 1);
        })
        // A check that fails is not worth a message: the next one is
        // WATCH_SECONDS away, and the page is still showing real data.
        .catch(() => undefined);
    }, WATCH_SECONDS * 1000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [revision]);

  useEffect(() => {
    if (!justUpdated) return;
    const timer = setTimeout(() => setJustUpdated(false), 6000);
    return () => clearTimeout(timer);
  }, [justUpdated, page]);

  const divisions = page?.divisions ?? [];
  const searching = search.trim() !== "";

  return (
    <div className="mx-auto max-w-[96rem]">
      {/* --- header ------------------------------------------------------- */}
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-brand-50 text-brand-600">
            <ClipboardIcon />
          </span>
          <div>
            <h1 className="text-2xl font-bold text-navy-900">Project Register</h1>
            <p className="mt-0.5 text-sm text-gray-500">
              Every job designed by our engineers, with its status and who is carrying it.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <div className="relative">
            <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">
              <SearchIcon />
            </span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by EP number, project name, client..."
              className="w-80 max-w-full rounded-lg border border-gray-300 py-2 pl-9 pr-3 text-sm focus:border-brand-500 focus:outline-none"
            />
          </div>
          {page?.available && (
            <label>
              <span className="block text-xs font-semibold text-gray-500">Status</span>
              <select
                value={status ?? ""}
                onChange={(event) => setStatus(event.target.value || null)}
                className="mt-1 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
              >
                <option value="">All statuses</option>
                {page.statuses.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {page?.updated_at && (
            <div className="text-right">
              <p className="text-xs text-gray-500">Register last edited</p>
              <p className="text-sm font-semibold text-navy-900">{formatApiDate(page.updated_at)}</p>
              {justUpdated ? (
                <p className="text-xs font-semibold text-emerald-700">Just updated</p>
              ) : (
                <p className="text-[11px] text-gray-400">Watching for changes</p>
              )}
            </div>
          )}
        </div>
      </header>

      {/* The register is maintained outside the platform, so there is no
       * "add project" here: a new job is added by editing the workbook. */}
      <p className="mt-2 text-xs text-gray-400">
        Read-only. The register is kept in the archive and edited there.
      </p>

      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {page && !page.available && (
        <div className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {page.unavailable_reason ?? "The register could not be read."}
        </div>
      )}
      {page === null && !error && <div className="mt-6 text-sm text-gray-400">Reading the register...</div>}

      {page?.available && (
        <>
          {engineer ? (
            <EngineerProjects
              page={page}
              engineer={engineer}
              status={status}
              onStatus={setStatus}
              onClear={() => setEngineer(null)}
            />
          ) : searching || status ? (
            <SearchResults
              page={page}
              status={status}
              onClear={() => {
                setQuery("");
                setStatus(null);
              }}
            />
          ) : (
            <>
              <div className="mt-5 grid gap-4 xl:grid-cols-2">
                {divisions.map((division) => (
                  <DivisionCard
                    key={division.key}
                    division={division}
                    tiles={page.tile_statuses}
                    onOpen={() => setOpen(open === division.key ? null : division.key)}
                    isOpen={open === division.key}
                    onEngineer={setEngineer}
                  />
                ))}
              </div>
              {divisions.map((division) =>
                open === division.key ? (
                  <LatestPanel key={division.key} division={division} />
                ) : null,
              )}
              <p className="mt-4 text-xs text-gray-400">
                {page.total} jobs in the register. A division is the engineers in it, so a job shared by two engineers
                counts for both — and these totals are larger than the number of jobs.
              </p>
            </>
          )}
        </>
      )}
    </div>
  );
}

/* --- one division ----------------------------------------------------------- */

const TINTS: Record<string, { ring: string; tile: string; head: string; accent: string }> = {
  fire_alarm: { ring: "border-red-100", tile: "bg-red-50 text-red-600", head: "bg-red-50/40", accent: "text-red-700" },
  elv: { ring: "border-sky-100", tile: "bg-sky-50 text-sky-600", head: "bg-sky-50/40", accent: "text-sky-700" },
  other: { ring: "border-gray-200", tile: "bg-gray-100 text-gray-600", head: "bg-gray-50", accent: "text-gray-700" },
};

const STATUS_STYLES: Record<string, string> = {
  "On Going": "bg-emerald-50 text-emerald-700",
  "Testing & Commissioning": "bg-blue-50 text-blue-700",
  Completed: "bg-indigo-50 text-indigo-700",
  "On Hold": "bg-amber-50 text-amber-700",
};

function DivisionCard({
  division,
  tiles,
  onOpen,
  isOpen,
  onEngineer,
}: {
  division: RegisterDivision;
  tiles: string[];
  onOpen: () => void;
  isOpen: boolean;
  onEngineer: (name: string) => void;
}) {
  /* People who have left are kept out of the table by default -- a
   * manager reviewing who is carrying what wants the people who are here
   * -- but their jobs are still in the division's totals, so the count is
   * stated rather than quietly dropped. */
  const [withLeavers, setWithLeavers] = useState(false);
  const tint = TINTS[division.key] ?? TINTS.other;
  const active = division.engineers.filter((e) => !e.resigned);
  const leavers = division.engineers.length - active.length;
  const shown = withLeavers ? division.engineers : active;
  // Statuses with no tile are real jobs; naming them is what keeps the
  // tiles from looking like they should add up to the total.
  const untiled = Object.entries(division.by_status).filter(([name]) => !tiles.includes(name));

  return (
    <section className={`overflow-hidden rounded-xl border bg-white ${tint.ring}`}>
      <div className={`flex flex-wrap items-start justify-between gap-3 p-4 ${tint.head}`}>
        <div className="flex items-start gap-3">
          <span className={`grid h-11 w-11 shrink-0 place-items-center rounded-xl ${tint.tile}`}>
            {division.key === "elv" ? <NetworkIcon /> : <FlameIcon />}
          </span>
          <div>
            <h2 className="text-lg font-bold text-navy-900">{division.label}</h2>
            <p className="text-xs text-gray-500">
              {active.length} engineer{active.length === 1 ? "" : "s"} · {division.total} job
              {division.total === 1 ? "" : "s"}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onOpen}
          className={`rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold ${tint.accent} hover:bg-gray-50`}
        >
          {isOpen ? "Hide latest" : "Latest project"}
        </button>
      </div>

      <div className="grid grid-cols-2 gap-2 px-4 pb-1 sm:grid-cols-4">
        {tiles.map((name) => (
          <div key={name} className="rounded-lg border border-gray-100 px-3 py-2">
            <p className="truncate text-[11px] font-medium text-gray-500" title={name}>
              {name}
            </p>
            <p className="text-xl font-bold leading-tight text-navy-900">{division.by_status[name] ?? 0}</p>
          </div>
        ))}
      </div>
      {untiled.length > 0 && (
        <p className="px-4 pt-1 text-[11px] text-gray-400">
          Also {untiled.map(([name, count]) => `${count} ${name.toLowerCase()}`).join(", ")}.
        </p>
      )}
      {leavers > 0 && (
        <p className="px-4 pt-1 text-[11px] text-amber-700">
          {division.resigned_total} of these {division.total} jobs {division.resigned_total === 1 ? "is" : "are"} against{" "}
          {leavers} engineer{leavers === 1 ? "" : "s"} who {leavers === 1 ? "has" : "have"} left.{" "}
          <button
            type="button"
            onClick={() => setWithLeavers((v) => !v)}
            className="font-semibold underline underline-offset-2 hover:no-underline"
          >
            {withLeavers ? "Hide them" : "Show them"}
          </button>
        </p>
      )}

      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-y border-gray-100 text-left text-[11px] font-semibold text-gray-500">
              <th className="px-4 py-2">Engineer</th>
              <th className="px-2 py-2 text-right">Jobs</th>
              {tiles.map((name) => (
                <th key={name} className="px-2 py-2 text-right" title={name}>
                  {SHORT[name] ?? name}
                </th>
              ))}
              <th className="px-2 py-2">Latest project</th>
              <th className="px-4 py-2 whitespace-nowrap">Last action</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((engineer) => (
              <EngineerRow key={engineer.name} engineer={engineer} tiles={tiles} onOpen={onEngineer} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** The tiles are named in full; the per-engineer columns cannot be, and a
 * truncated heading with a tooltip is clearer than a wrapped one. */
const SHORT: Record<string, string> = {
  "On Going": "Going",
  "Testing & Commissioning": "T&C",
  Completed: "Done",
  "On Hold": "Hold",
};

function EngineerRow({
  engineer,
  tiles,
  onOpen,
}: {
  engineer: EngineerLoad;
  tiles: string[];
  onOpen: (name: string) => void;
}) {
  const latest = engineer.latest;
  return (
    <tr
      onClick={() => onOpen(engineer.name)}
      title={`Show ${engineer.name}'s jobs`}
      className="cursor-pointer border-b border-gray-50 last:border-0 hover:bg-brand-50/50"
    >
      <td className="px-4 py-2">
        <span className="flex items-center gap-2">
          <span
            className={`grid h-7 w-7 shrink-0 place-items-center rounded-full text-[11px] font-bold text-white ${
              engineer.resigned ? "bg-gray-400" : "bg-navy-900"
            }`}
          >
            {engineer.name.slice(0, 1).toUpperCase()}
          </span>
          <span className="font-medium text-navy-900">{engineer.name}</span>
          {engineer.resigned && (
            <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">left</span>
          )}
        </span>
      </td>
      <td className="px-2 py-2 text-right font-semibold text-navy-900">{engineer.total}</td>
      {tiles.map((name) => {
        const count = engineer.by_status[name] ?? 0;
        return (
          <td key={name} className="px-2 py-2 text-right">
            {count === 0 ? (
              <span className="text-gray-300">0</span>
            ) : (
              <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${STATUS_STYLES[name] ?? "bg-gray-100 text-gray-700"}`}>
                {count}
              </span>
            )}
          </td>
        );
      })}
      <td className="max-w-[16rem] px-2 py-2">
        {latest ? (
          <>
            <span className="block truncate font-medium text-navy-900">{latest.ep_number}</span>
            <span className="block truncate text-xs text-gray-500">{latest.project_name ?? "—"}</span>
          </>
        ) : (
          <span className="text-gray-400">—</span>
        )}
      </td>
      <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-600">
        {latest?.last_action ? formatApiDate(latest.last_action, "short") : "—"}
      </td>
    </tr>
  );
}

/** The division's newest job, opened from its card. */
function LatestPanel({ division }: { division: RegisterDivision }) {
  const project = division.latest;
  if (!project) return null;
  const tint = TINTS[division.key] ?? TINTS.other;
  return (
    <section className={`mt-4 rounded-xl border bg-white p-4 ${tint.ring}`}>
      <h3 className={`text-sm font-bold ${tint.accent}`}>{division.label} — latest job</h3>
      <div className="mt-2 flex flex-wrap items-start gap-x-8 gap-y-3">
        <div className="min-w-[14rem]">
          <p className="text-xl font-bold text-navy-900">{project.ep_number}</p>
          <p className="text-sm text-gray-700">{project.project_name ?? "—"}</p>
          {project.client && <p className="text-xs text-gray-500">{project.client}</p>}
        </div>
        <Fact label="Design engineer" value={project.engineers.join(", ") || "—"} />
        <Fact label="Status" value={project.statuses.join(" · ") || "—"} />
        <Fact label="Systems" value={project.systems.join(", ") || "—"} />
        <Fact label="Brand" value={project.products.join(", ") || "—"} />
        <Fact
          label="Last action"
          value={project.last_action ? formatApiDate(project.last_action, "short") : "—"}
        />
        {project.scope && <Fact label="Scope" value={project.scope} />}
      </div>
    </section>
  );
}

/** The manufacturers on a job. A job with two systems often has two --
 * the fire alarm's and the emergency lighting's -- and both belong here.
 * The register spells a brand several ways; one spelling is settled on
 * the way in (app/services/brands.py). */
function Brands({ brands }: { brands: string[] }) {
  if (brands.length === 0) return <span className="text-gray-300">—</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {brands.map((brand) => (
        <span key={brand} className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] font-medium text-gray-700">
          {brand}
        </span>
      ))}
    </span>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-[8rem]">
      <p className="text-[11px] font-medium text-gray-500">{label}</p>
      <p className="text-sm font-semibold text-navy-900">{value}</p>
    </div>
  );
}

/* --- one engineer's jobs ---------------------------------------------------- */

/** Every job in the register against one engineer, newest first.
 *
 * Reached by clicking their row. A job they share with someone else is
 * theirs too and is listed here, with the other name shown beside it --
 * the point of opening an engineer is to see everything on their plate,
 * not only what is theirs alone. */
function EngineerProjects({
  page,
  engineer,
  status,
  onStatus,
  onClear,
}: {
  page: RegisterPage;
  engineer: string;
  status: string | null;
  onStatus: (name: string | null) => void;
  onClear: () => void;
}) {
  const load = page.divisions
    .flatMap((d) => d.engineers.map((e) => ({ ...e, division: d.label })))
    .find((e) => e.name === engineer);

  return (
    <section className="mt-5 rounded-xl border border-gray-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-navy-900 text-sm font-bold text-white">
            {engineer.slice(0, 1).toUpperCase()}
          </span>
          <div>
            <h2 className="flex items-center gap-2 font-bold text-navy-900">
              {engineer}
              {load?.resigned && (
                <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">left</span>
              )}
            </h2>
            <p className="text-xs text-gray-500">
              {page.total} job{page.total === 1 ? "" : "s"}
              {status ? ` · ${status}` : ""}
              {load?.division ? ` · ${load.division}` : ""}
              {page.total > page.projects.length ? ` · showing the newest ${page.projects.length}` : ""}
            </p>
          </div>
        </div>
        <button type="button" onClick={onClear} className="text-sm font-semibold text-brand-600 hover:underline">
          ← All engineers
        </button>
      </div>

      {load && (
        /* The counts are this engineer's whole load, not the filtered
         * list, so a tile still says how many they have of each while one
         * of them is being read. */
        <div className="grid grid-cols-2 gap-2 px-4 py-3 sm:grid-cols-4">
          {page.tile_statuses.map((name) => {
            const on = status === name;
            return (
              <button
                key={name}
                type="button"
                onClick={() => onStatus(on ? null : name)}
                aria-pressed={on}
                className={`rounded-lg border px-3 py-2 text-left transition ${
                  on ? "border-brand-400 bg-brand-50" : "border-gray-100 hover:border-brand-300"
                }`}
              >
                <p className="truncate text-[11px] font-medium text-gray-500" title={name}>
                  {name}
                </p>
                <p className="text-xl font-bold leading-tight text-navy-900">{load.by_status[name] ?? 0}</p>
              </button>
            );
          })}
        </div>
      )}

      {page.projects.length === 0 ? (
        <p className="px-4 py-6 text-sm text-gray-500">
          {status
            ? `No ${status} jobs against this engineer.`
            : "No jobs in the register against this engineer."}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-y border-gray-100 text-left text-[11px] font-semibold text-gray-500">
                <th className="px-4 py-2">EP number</th>
                <th className="px-2 py-2">Project</th>
                <th className="px-2 py-2">Shared with</th>
                <th className="px-2 py-2">System</th>
                <th className="px-2 py-2">Brand</th>
                <th className="px-2 py-2">Status</th>
                <th className="px-4 py-2 whitespace-nowrap">Last action</th>
              </tr>
            </thead>
            <tbody>
              {page.projects.map((project) => (
                <tr key={project.ep_number} className="border-b border-gray-50 last:border-0 hover:bg-gray-50">
                  <td className="whitespace-nowrap px-4 py-2 font-medium text-navy-900">{project.ep_number}</td>
                  <td className="max-w-[22rem] px-2 py-2">
                    <span className="block truncate text-gray-800">{project.project_name ?? "—"}</span>
                    {project.client && <span className="block truncate text-xs text-gray-500">{project.client}</span>}
                  </td>
                  <td className="px-2 py-2 text-xs">
                    {/* Only the others: their own name on every row would
                     * tell the reader nothing. */}
                    {project.engineers.filter((n) => n.toLowerCase() !== engineer.toLowerCase()).join(", ") || (
                      <span className="text-gray-300">—</span>
                    )}
                  </td>
                  <td className="px-2 py-2 text-xs text-gray-600">{project.systems.join(", ") || "—"}</td>
                  <td className="px-2 py-2">
                    <Brands brands={project.products} />
                  </td>
                  <td className="px-2 py-2">
                    <span
                      className={
                        project.statuses.length > 1
                          ? "text-xs font-medium text-amber-700"
                          : "text-xs text-gray-600"
                      }
                    >
                      {project.statuses.join(" · ") || "—"}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-600">
                    {project.last_action ? formatApiDate(project.last_action, "short") : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/* --- searching across the whole register ------------------------------------ */

function SearchResults({
  page,
  status,
  onClear,
}: {
  page: RegisterPage;
  status: string | null;
  onClear: () => void;
}) {
  return (
    <section className="mt-5 rounded-xl border border-gray-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-4 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          {page.total} job{page.total === 1 ? "" : "s"}
          {status ? ` · ${status}` : ""}
          {page.total > page.projects.length && ` (showing the newest ${page.projects.length})`}
        </h2>
        <button type="button" onClick={onClear} className="text-xs font-semibold text-brand-600 hover:underline">
          Clear filters
        </button>
      </div>
      {page.projects.length === 0 ? (
        <p className="px-4 py-6 text-sm text-gray-500">Nothing in the register matches that.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-left text-[11px] font-semibold text-gray-500">
                <th className="px-4 py-2">EP number</th>
                <th className="px-2 py-2">Project</th>
                <th className="px-2 py-2">Design engineer</th>
                <th className="px-2 py-2">System</th>
                <th className="px-2 py-2">Brand</th>
                <th className="px-2 py-2">Status</th>
                <th className="px-4 py-2 whitespace-nowrap">Last action</th>
              </tr>
            </thead>
            <tbody>
              {page.projects.map((project) => (
                <SearchRow key={project.ep_number} project={project} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function SearchRow({ project }: { project: RegisterProject }) {
  return (
    <tr className="border-b border-gray-50 last:border-0 hover:bg-gray-50">
      <td className="whitespace-nowrap px-4 py-2 font-medium text-navy-900">{project.ep_number}</td>
      <td className="max-w-[22rem] px-2 py-2">
        <span className="block truncate text-gray-800">{project.project_name ?? "—"}</span>
        {project.client && <span className="block truncate text-xs text-gray-500">{project.client}</span>}
      </td>
      <td className="px-2 py-2">
        {/* Two names means the job is split between them, which is the kind
         * of thing this page exists to surface. */}
        <span className={project.engineers.length > 1 ? "font-medium text-amber-700" : "text-gray-800"}>
          {project.engineers.join(", ") || "—"}
        </span>
      </td>
      <td className="px-2 py-2 text-xs text-gray-600">{project.systems.join(", ") || "—"}</td>
      <td className="px-2 py-2">
        <Brands brands={project.products} />
      </td>
      <td className="px-2 py-2">
        <span className={project.statuses.length > 1 ? "text-xs font-medium text-amber-700" : "text-xs text-gray-600"}>
          {project.statuses.join(" · ") || "—"}
        </span>
      </td>
      <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-600">
        {project.last_action ? formatApiDate(project.last_action, "short") : "—"}
      </td>
    </tr>
  );
}

/* --- icons ------------------------------------------------------------------ */

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const ClipboardIcon = () => (
  <svg viewBox="0 0 24 24" className="h-6 w-6" {...stroke}>
    <rect x="5" y="4" width="14" height="17" rx="2" />
    <path d="M9 4h6v3H9zM9 12h6M9 16h4" />
  </svg>
);
const SearchIcon = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" {...stroke}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </svg>
);
const FlameIcon = () => (
  <svg viewBox="0 0 24 24" className="h-6 w-6" {...stroke}>
    <path d="M12 2.5S7 7 7 13a5 5 0 0 0 10 0c0-2-1-3.5-2-5-1 1.5-1.5 2-2.5 2 .5-3-.5-5.5 0-7.5z" />
  </svg>
);
const NetworkIcon = () => (
  <svg viewBox="0 0 24 24" className="h-6 w-6" {...stroke}>
    <circle cx="6" cy="18" r="2.5" />
    <circle cx="18" cy="18" r="2.5" />
    <circle cx="12" cy="5" r="2.5" />
    <path d="M12 7.5v4M12 11.5 7 15.8M12 11.5l5 4.3" />
  </svg>
);
