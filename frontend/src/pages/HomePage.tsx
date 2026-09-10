import { useAuth } from "../context/AuthContext";
import { ROLE_LABELS } from "../lib/types";

export function HomePage() {
  const { user } = useAuth();

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-2xl font-bold text-navy-900">Welcome, {user?.full_name}</h1>
      <p className="mt-1 text-sm text-gray-500">
        Signed in as <span className="font-medium">{ROLE_LABELS[user?.role ?? "viewer"]}</span>
      </p>
      <div className="mt-6 rounded-xl border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-400">
        Project creation and the project workspace open up in the next phase.
      </div>
    </div>
  );
}
