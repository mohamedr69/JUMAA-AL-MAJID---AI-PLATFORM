import { useState } from "react";
import { Link } from "react-router-dom";
import type { CheckStatus, Readiness } from "../lib/types";

export const STATUS_STYLE: Record<CheckStatus, { label: string; badge: string; dot: string }> = {
  ok: { label: "Done", badge: "bg-emerald-50 text-emerald-800 ring-emerald-200", dot: "bg-emerald-500" },
  warning: { label: "Check", badge: "bg-amber-50 text-amber-900 ring-amber-200", dot: "bg-amber-500" },
  blocked: { label: "Blocked", badge: "bg-red-50 text-red-800 ring-red-200", dot: "bg-red-600" },
  unknown: { label: "Not checked", badge: "bg-gray-100 text-gray-700 ring-gray-300", dot: "bg-gray-400" },
};

export function StatusPill({ status }: { status: CheckStatus }) {
  const style = STATUS_STYLE[status];
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${style.badge}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} aria-hidden="true" />
      {style.label}
    </span>
  );
}

/** Everything that stands between the project and an issue, in order: the
 * documents, the reading of them, the BOQ, the calculations, compliance. */
export function ReadinessPanel({ readiness, onCheckDocuments, checking }: {
  readiness: Readiness;
  onCheckDocuments?: () => void;
  checking?: boolean;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const blocked = readiness.checks.filter((c) => c.status === "blocked").length;

  return (
    <section aria-labelledby="readiness-title" className="rounded-xl border border-gray-200 bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="readiness-title" className="text-xs font-semibold uppercase tracking-wide text-gray-400">
            Readiness
          </h2>
          <p className={`mt-1 text-sm font-semibold ${readiness.boq_ready_for_issue ? "text-emerald-800" : "text-red-800"}`}>
            {readiness.boq_ready_for_issue
              ? "The BOQ can be issued."
              : `The BOQ cannot be issued yet: ${readiness.boq_blockers.length} thing${readiness.boq_blockers.length === 1 ? "" : "s"} to settle.`}
          </p>
          {blocked > 0 && <p className="text-xs text-gray-500">{blocked} check{blocked === 1 ? " is" : "s are"} blocked in total.</p>}
        </div>
        {onCheckDocuments && (
          <button
            onClick={onCheckDocuments}
            disabled={checking}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-60"
          >
            {checking ? "Checking documents..." : "Check documents now"}
          </button>
        )}
      </div>

      <ul className="mt-3 divide-y divide-gray-100">
        {readiness.checks.map((check) => (
          <li key={check.key} className="py-2">
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill status={check.status} />
              <span className="text-sm font-medium text-navy-900">{check.label}</span>
              <span className="text-xs text-gray-400">{check.scope === "boq" ? "BOQ" : check.scope}</span>
              <span className="min-w-0 flex-1 text-sm text-gray-600">{check.summary}</span>
              {check.items.length > 0 && (
                <button
                  onClick={() => setOpen(open === check.key ? null : check.key)}
                  aria-expanded={open === check.key}
                  className="text-xs font-semibold text-brand-700 hover:underline"
                >
                  {open === check.key ? "Hide" : `Details (${check.items.length})`}
                </button>
              )}
              {check.link && (
                <Link to={check.link} className="text-xs font-semibold text-brand-700 hover:underline">
                  Open &rarr;
                </Link>
              )}
            </div>
            {open === check.key && (
              <ul className="mt-2 max-h-64 list-disc space-y-0.5 overflow-auto pl-8 text-xs text-gray-600">
                {check.items.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
