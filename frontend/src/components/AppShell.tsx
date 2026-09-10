import { Link, Outlet } from "react-router-dom";
import { branding } from "../branding";
import { useAuth } from "../context/AuthContext";
import { ROLE_LABELS } from "../lib/types";
import { BrandMark } from "./BrandMark";

// Basic app shell for Phase 1. Full left navigation activates once a project
// workspace exists (Phase 3) — see plan.
export function AppShell() {
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="flex items-center justify-between border-b border-gray-200 bg-white px-6 py-3">
        <Link to="/" className="flex items-center gap-2">
          <BrandMark size={28} />
          <span className="text-sm font-bold text-navy-900">{branding.appName}</span>
        </Link>

        <div className="flex items-center gap-4 text-sm">
          {user?.role === "admin" && (
            <Link to="/admin/users" className="text-gray-600 hover:text-brand-600">
              Users
            </Link>
          )}
          {user && (
            <div className="flex items-center gap-3">
              <div className="text-right leading-tight">
                <div className="font-medium text-navy-900">{user.full_name}</div>
                <div className="text-xs text-gray-400">{ROLE_LABELS[user.role]}</div>
              </div>
              <button
                onClick={() => logout()}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-100"
              >
                Log out
              </button>
            </div>
          )}
        </div>
      </header>

      <main className="p-6">
        <Outlet />
      </main>
    </div>
  );
}
