import { Link } from "react-router-dom";

export function AccessDeniedPage() {
  return (
    <div className="mx-auto max-w-md py-20 text-center">
      <h1 className="text-xl font-bold text-navy-900">Access Denied</h1>
      <p className="mt-2 text-sm text-gray-500">
        Your role doesn&apos;t have permission to view this module.
      </p>
      <Link to="/" className="mt-6 inline-block text-sm font-medium text-brand-600">
        Back to home
      </Link>
    </div>
  );
}
