import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { branding } from "../branding";
import { BrandMark } from "../components/BrandMark";
import { CitySkylineBackdrop } from "../components/CitySkylineBackdrop";
import { useAuth } from "../context/AuthContext";
import { DESIGN_ROLES, ROLE_LABELS } from "../lib/types";
import { divisionForRole } from "../lib/divisions";
import { EstimationDashboard } from "./EstimationPages";

/** The home page, built to the platform owner's design.
 *
 * Everything the design shows is laid out, and everything the build has not
 * reached is shown **disabled with a "Soon" badge** rather than hidden: the
 * home page is the map of the platform, and a map with nothing where a
 * section will be is its own kind of wrong. A tile that looks ready and does
 * nothing is worse still.
 *
 * The design fills Announcements and Manufacturer Monitoring with sample
 * rows -- brands, update counts, dates. None of that is invented here.
 * Nothing behind those panels exists yet, and a made-up "3 new updates since
 * 12 Sep" would read as a fact about the project. They state what they will
 * carry instead.
 */

const CREATOR_ROLES = ["admin", "design_manager", ...DESIGN_ROLES];

function Soon() {
  return (
    <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-700">
      Soon
    </span>
  );
}

/* --- icons (line style, matching the design) ------------------------------ */

const icon = (path: ReactNode) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
    {path}
  </svg>
);
const IconHome = () => icon(<><path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h14V9.5" /></>);
const IconPlus = () => icon(<><path d="M12 5v14" /><path d="M5 12h14" /></>);
const IconFolder = () => icon(<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />);
const IconClipboard = () => icon(<><rect x="5" y="4" width="14" height="17" rx="2" /><path d="M9 4h6v3H9z" /><path d="M9 12h6M9 16h4" /></>);
const IconSheet = () => icon(<><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /><path d="M9 13h6M9 17h4" /></>);
const IconTemplate = () => icon(<><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h5" /></>);
const IconBook = () => icon(<><path d="M4 5a2 2 0 0 1 2-2h12v18H6a2 2 0 0 1-2-2z" /><path d="M8 3v18" /></>);
const IconFactory = () => icon(<><path d="M3 21V10l5 3V10l5 3V8l6 3v10z" /><path d="M7 17h.01M12 17h.01M17 17h.01" /></>);
const IconChart = () => icon(<><path d="M4 20h16" /><rect x="6" y="11" width="3" height="6" /><rect x="11" y="7" width="3" height="10" /><rect x="16" y="13" width="3" height="4" /></>);
const IconGear = () => icon(<><circle cx="12" cy="12" r="3" /><path d="M19.4 14a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2v.2a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 7 18.5l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.9H2.8a2 2 0 0 1 0-4h.1A1.7 1.7 0 0 0 4.1 7L4 6.9a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V2.8a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1h.2a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.6 1z" /></>);
const IconArrow = () => icon(<><path d="M5 12h13" /><path d="m13 6 6 6-6 6" /></>);
const IconMegaphone = () => icon(<><path d="m3 11 14-6v14L3 13z" /><path d="M3 11v2a2 2 0 0 0 2 2h1v4h3v-4" /></>);
const IconLink = () => icon(<><path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1" /><path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1" /></>);

/* --- destinations --------------------------------------------------------- */

interface Destination {
  label: string;
  description?: string;
  to?: string;
  icon: () => ReactNode;
  /** No such page yet: rendered disabled with a Soon badge. */
  soon?: boolean;
  adminOnly?: boolean;
  creatorOnly?: boolean;
  /** The design manager's own tools. */
  managerOnly?: boolean;
}

const PRIMARY: Destination[] = [
  { label: "Design Dashboard", to: "/", icon: IconHome },
  { label: "Create New Project", to: "/projects/new", icon: IconPlus, creatorOnly: true },
  { label: "Open Project", to: "/projects", icon: IconFolder },
];

const TOOLS: Destination[] = [
  { label: "Project Register", description: "Every job and the engineer designing it.", icon: IconClipboard, to: "/register", managerOnly: true },
  { label: "Datasheets", description: "Search the manufacturers' datasheet library.", icon: IconSheet, to: "/datasheets" },
  { label: "Templates", description: "Standard documents and drawing templates.", icon: IconTemplate, soon: true },
  { label: "Knowledge Base", description: "Standards, codes, templates and guides.", icon: IconBook, soon: true },
  { label: "Manufacturer Monitoring", description: "Track latest updates from manufacturers.", icon: IconFactory, soon: true },
  { label: "Reports", description: "View consolidated reports and insights.", icon: IconChart, soon: true },
  // Only the user-management half of Settings exists, and only for admins.
  { label: "Settings", description: "Manage users, preferences and system configuration.", icon: IconGear, to: "/admin/users", adminOnly: true },
];

/** External references the design offers as shortcuts. No URL has been
 *  confirmed for any of them, and guessing one would send an engineer to a
 *  page that is not the authority they think it is. */
const RESOURCES = ["UAE Fire Code", "NFPA", "DCD Portal", "SIRA Portal", "Product Databases", "Online Calculators", "Industry News"];

export function OpeningScreen() {
  const { user } = useAuth();
  const firstName = (user?.full_name ?? "").trim().split(/\s+/)[0] || "there";
  const isAdmin = user?.role === "admin";
  const canCreate = !!user && CREATOR_ROLES.includes(user.role);

  const division = divisionForRole(user?.role);
  if (division) return <EstimationDashboard division={division} />;

  const isManager = user?.role === "design_manager";
  const available = (item: Destination) =>
    !item.soon && !!item.to && (!item.adminOnly || isAdmin) && (!item.creatorOnly || canCreate)
    // The register is the manager's review of the engineers' work. An
    // admin may open it by its address; it is not on their rail.
    && (!item.managerOnly || isManager);

  return (
    <div className="-m-6 flex min-h-[calc(100vh-57px)] bg-[#f5f7fb] text-navy-900">
      {/* --- left rail ----------------------------------------------------- */}
      <aside className="hidden w-60 shrink-0 flex-col bg-navy-950 px-3 py-5 text-white lg:flex">
        <div className="flex items-center gap-2.5 px-2">
          <BrandMark size={30} />
          <div className="leading-tight">
            <div className="text-sm font-bold">{branding.appName}</div>
            <div className="text-[10px] uppercase tracking-wider text-white/50">Design Team</div>
          </div>
        </div>

        <nav className="mt-7 flex flex-col gap-1">
          {PRIMARY.map((item) => <RailItem key={item.label} item={item} active={item.to === "/"} enabled={available(item)} />)}
          <div className="my-3 border-t border-white/10" />
          {TOOLS.map((item) => <RailItem key={item.label} item={item} enabled={available(item)} />)}
        </nav>

        <div className="mt-auto">
          <div className="relative overflow-hidden rounded-xl bg-navy-900 p-4">
            <CitySkylineBackdrop />
            <div className="relative">
              <div className="text-base font-bold leading-snug">Engineering<br />Smarter Together</div>
              <p className="mt-2 text-[11px] leading-relaxed text-white/60">{branding.companyTagline.split("|").join(" · ")}</p>
            </div>
          </div>
          <p className="mt-3 px-2 text-[11px] text-white/40">{branding.version}</p>
        </div>
      </aside>

      {/* --- page ---------------------------------------------------------- */}
      <div className="min-w-0 flex-1 p-6">
        {/* hero */}
        <section className="relative overflow-hidden rounded-2xl border border-gray-200 bg-white">
          <div className="grid grid-cols-1 lg:grid-cols-[1.35fr_1fr]">
            <div className="p-8">
              <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-brand-600">Design Team</p>
              <h1 className="text-3xl font-bold">Design Team Dashboard</h1>
              <p className="mt-2 text-sm text-gray-500">Welcome back, {firstName}. Your workspace for design projects, calculations, drawings and compliance.</p>
              <p className="mt-4 border-l-2 border-brand-600 pl-3 text-sm italic text-gray-600">{branding.heroSubtext}</p>
            </div>
            <div className="relative min-h-40 bg-navy-950 text-white">
              <CitySkylineBackdrop />
              <div className="relative flex h-full flex-col justify-center p-8">
                <div className="text-2xl font-bold leading-tight">From<br />Concept<br />to Completion</div>
                <div className="mt-4 h-0.5 w-14 bg-brand-500" />
                <div className="mt-3 text-xs text-white/60">{branding.tagline.split("|").join(" | ")}</div>
              </div>
            </div>
          </div>

          {/* the two actions that exist today */}
          <div className="grid grid-cols-1 gap-4 border-t border-gray-100 p-6 sm:grid-cols-2">
            <ActionCard
              to="/projects/new"
              enabled={canCreate}
              tint="bg-brand-600"
              title="Create New Project"
              description={canCreate ? "Start a new project and set up your workspace." : "Your role cannot create projects."}
            />
            <ActionCard to="/projects" enabled tint="bg-emerald-600" title="Open Project" description="Access your existing projects and recent work." />
          </div>
        </section>

        {/* global tools */}
        <section className="mt-5 rounded-2xl border border-gray-200 bg-white p-6">
          <h2 className="text-lg font-bold">Global Tools</h2>
          <p className="mt-0.5 text-sm text-gray-500">Tools available across all projects</p>
          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
            {TOOLS.map((item) => <ToolCard key={item.label} item={item} enabled={available(item)} isAdmin={isAdmin} />)}
          </div>
        </section>

        {/* announcements + manufacturer monitoring */}
        <div className="mt-5 grid grid-cols-1 gap-5 xl:grid-cols-2">
          <Panel icon={<IconMegaphone />} title="Latest Announcements" subtitle="Platform, standards and manufacturer notices">
            <Pending>
              Announcements are not published yet. This panel will carry new templates, manufacturer notices, standards
              revisions and planned maintenance, newest first.
            </Pending>
          </Panel>

          <Panel icon={<IconFactory />} title="Manufacturers Monitoring" subtitle="Latest updates from your key manufacturers">
            <Pending>
              No manufacturer feed is connected yet. This panel will show each manufacturer you follow with what has
              changed since you last looked, and a link straight to the update.
            </Pending>
          </Panel>
        </div>

        {/* helpful resources */}
        <section className="mt-5 rounded-2xl border border-gray-200 bg-white p-6">
          <div className="flex items-center gap-2">
            <span className="text-brand-600"><IconLink /></span>
            <h2 className="text-lg font-bold">Helpful Resources</h2>
            <Soon />
          </div>
          <p className="mt-0.5 text-sm text-gray-500">
            Quick access to commonly used external links. Each one opens once its official address is confirmed.
          </p>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-7">
            {RESOURCES.map((label) => (
              <div
                key={label}
                aria-disabled="true"
                className="flex cursor-not-allowed items-center gap-2 rounded-xl border border-dashed border-gray-200 bg-gray-50 px-3 py-3 text-sm font-medium text-gray-400"
              >
                <span className="text-gray-300"><IconLink /></span>
                <span className="truncate">{label}</span>
              </div>
            ))}
          </div>
        </section>

        {user && (
          <p className="mt-5 text-center text-xs text-gray-400">
            Signed in as {user.full_name} · {ROLE_LABELS[user.role]}
          </p>
        )}
      </div>
    </div>
  );
}

/* --- pieces --------------------------------------------------------------- */

function RailItem({ item, active, enabled }: { item: Destination; active?: boolean; enabled: boolean }) {
  const Icon = item.icon;
  const body = (
    <span className="flex w-full items-center gap-3">
      <span className={enabled ? "text-white/70" : "text-white/25"}><Icon /></span>
      <span className="flex-1 truncate">{item.label}</span>
      {item.soon && <Soon />}
    </span>
  );
  if (!enabled) {
    return (
      <span aria-disabled="true" title={item.soon ? "Being built" : "Not available for your role"}
        className="cursor-not-allowed rounded-lg px-3 py-2.5 text-sm font-medium text-white/30">
        {body}
      </span>
    );
  }
  return (
    <Link to={item.to!} className={`rounded-lg px-3 py-2.5 text-sm font-medium transition ${active ? "bg-brand-600 text-white" : "text-white/80 hover:bg-white/10"}`}>
      {body}
    </Link>
  );
}

function ActionCard({ to, enabled, tint, title, description }: { to: string; enabled: boolean; tint: string; title: string; description: string }) {
  const inner = (
    <>
      <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-white ${enabled ? tint : "bg-gray-300"}`}>
        <IconPlus />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block font-bold">{title}</span>
        <span className="mt-0.5 block text-sm text-gray-500">{description}</span>
      </span>
      <span className={enabled ? "text-brand-600" : "text-gray-300"}><IconArrow /></span>
    </>
  );
  if (!enabled) {
    return (
      <span aria-disabled="true" className="flex cursor-not-allowed items-center gap-4 rounded-xl border border-gray-200 bg-gray-50 p-5 text-gray-400">
        {inner}
      </span>
    );
  }
  return (
    <Link to={to} className="flex items-center gap-4 rounded-xl border border-gray-200 bg-white p-5 transition hover:border-brand-300 hover:shadow-md">
      {inner}
    </Link>
  );
}

function ToolCard({ item, enabled, isAdmin }: { item: Destination; enabled: boolean; isAdmin: boolean }) {
  const Icon = item.icon;
  const inner = (
    <>
      <span className={`flex h-11 w-11 items-center justify-center rounded-xl ${enabled ? "bg-brand-50 text-brand-600" : "bg-gray-100 text-gray-300"}`}>
        <Icon />
      </span>
      <span className="mt-3 flex items-center gap-2">
        <span className="font-bold">{item.label}</span>
        {item.soon && <Soon />}
      </span>
      <span className="mt-1 block text-xs leading-relaxed text-gray-500">{item.description}</span>
      {enabled && <span className="mt-3 inline-flex text-brand-600"><IconArrow /></span>}
    </>
  );
  if (!enabled) {
    return (
      <span
        aria-disabled="true"
        title={item.soon ? "Being built" : "Admins only"}
        className="flex cursor-not-allowed flex-col rounded-xl border border-dashed border-gray-200 bg-gray-50 p-4 text-gray-400"
      >
        {inner}
        {/* Settings is built but role-gated; that is not the same as unbuilt. */}
        {!item.soon && !isAdmin && <span className="mt-3 text-[11px] font-semibold uppercase tracking-wide text-gray-400">Admins only</span>}
      </span>
    );
  }
  return (
    <Link to={item.to!} className="flex flex-col rounded-xl border border-gray-200 bg-white p-4 transition hover:border-brand-300 hover:shadow-md">
      {inner}
    </Link>
  );
}

function Panel({ icon, title, subtitle, children }: { icon: ReactNode; title: string; subtitle: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border border-gray-200 bg-white p-6">
      <div className="flex items-center gap-2">
        <span className="text-brand-600">{icon}</span>
        <h2 className="text-lg font-bold">{title}</h2>
        <Soon />
      </div>
      <p className="mt-0.5 text-sm text-gray-500">{subtitle}</p>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Pending({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 p-6 text-sm leading-relaxed text-gray-500">
      {children}
    </div>
  );
}
