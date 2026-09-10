import { Link } from "react-router-dom";

// Phase 2: the opening screen has only these two actions -- see plan.
export function OpeningScreen() {
  return (
    <div className="mx-auto max-w-3xl py-12 text-center">
      <h1 className="text-2xl font-bold text-navy-900">Welcome</h1>
      <p className="mt-1 text-sm text-gray-500">
        Turn engineering documents into structured projects
      </p>

      <div className="mt-10 grid grid-cols-1 gap-6 sm:grid-cols-2">
        <Link
          to="/projects/new"
          className="rounded-2xl border border-gray-200 bg-white p-8 text-left shadow-sm transition hover:border-brand-300 hover:shadow-md"
        >
          <div className="text-lg font-bold text-navy-900">Create Project</div>
          <p className="mt-1 text-sm text-gray-500">
            Start a new project and set up your workspace
          </p>
        </Link>

        <Link
          to="/projects"
          className="rounded-2xl border border-gray-200 bg-white p-8 text-left shadow-sm transition hover:border-brand-300 hover:shadow-md"
        >
          <div className="text-lg font-bold text-navy-900">Open Project</div>
          <p className="mt-1 text-sm text-gray-500">Access an existing project</p>
        </Link>
      </div>
    </div>
  );
}
