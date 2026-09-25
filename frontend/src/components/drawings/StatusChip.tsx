import type { LogCell, Status } from "./types";
import { STATUS_LABEL } from "./types";

/** How each status reads: an icon and a label, colour third, so it reads without colour. */
export const STATUS: Record<Status, { help: string; chip: string; dot: string }> = {
  approved: { help: "Approved by the consultant", chip: "bg-emerald-50 text-emerald-800 ring-emerald-200", dot: "bg-emerald-600" },
  approved_as_noted: { help: "Approved with comments to incorporate", chip: "bg-cyan-50 text-cyan-800 ring-cyan-200", dot: "bg-cyan-600" },
  under_review: { help: "Submitted and under review", chip: "bg-amber-50 text-amber-800 ring-amber-200", dot: "bg-amber-500" },
  not_approved: { help: "Returned with comments", chip: "bg-rose-50 text-rose-700 ring-rose-200", dot: "bg-rose-600" },
  reply_not_found: {
    help: "A later revision was submitted, so this one was answered; its reply is not in the project folder",
    chip: "bg-orange-50 text-orange-800 ring-orange-200", dot: "bg-orange-500",
  },
  not_submitted: { help: "No submitted revision", chip: "bg-slate-100 text-slate-600 ring-slate-200", dot: "bg-slate-400" },
};

export function StatusIcon({ status, className = "h-4 w-4" }: { status: Status; className?: string }) {
  const common = { className, viewBox: "0 0 20 20", "aria-hidden": true } as const;
  if (status === "approved" || status === "approved_as_noted")
    return (
      <svg {...common}>
        <circle cx="10" cy="10" r="9" className={status === "approved" ? "fill-emerald-600" : "fill-cyan-600"} />
        <path d="M6 10.5l2.5 2.5L14 7.5" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  if (status === "not_approved")
    return (
      <svg {...common}>
        <circle cx="10" cy="10" r="9" className="fill-rose-600" />
        <path d="M7 7l6 6M13 7l-6 6" stroke="white" strokeWidth="2" strokeLinecap="round" />
      </svg>
    );
  if (status === "under_review")
    return (
      <svg {...common}>
        <circle cx="10" cy="10" r="8" fill="none" className="stroke-amber-500" strokeWidth="2" />
        <path d="M10 5.5V10l3 2" fill="none" className="stroke-amber-500" strokeWidth="2" strokeLinecap="round" />
      </svg>
    );
  if (status === "reply_not_found")
    return (
      <svg {...common}>
        <circle cx="10" cy="10" r="8" fill="none" className="stroke-orange-500" strokeWidth="2" />
        <path d="M10 6v5" fill="none" className="stroke-orange-500" strokeWidth="2" strokeLinecap="round" />
        <circle cx="10" cy="14" r="1.2" className="fill-orange-500" />
      </svg>
    );
  return (
    <svg {...common}>
      <circle cx="10" cy="10" r="6" className="fill-slate-400" />
    </svg>
  );
}

/** One official status. A cell that is no official revision -- a file
 *  found, or nothing at all -- reads as a dash: never a status it has not got. */
export function Chip({ cell, blank = false }: { cell: LogCell; blank?: boolean }) {
  if (cell.candidate) {
    return (
      <span
        className="inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-dashed border-indigo-300 bg-white px-2 py-0.5 text-[11px] font-medium text-indigo-700"
        title={cell.candidate.evidence?.note ?? `${cell.candidate.revision} found in the folder; not taken as submitted`}
      >
        ⓘ {cell.candidate.label}
      </span>
    );
  }
  if (blank || (cell.status === "not_submitted" && !cell.path)) {
    return <span className="text-slate-300" title="No revision">—</span>;
  }
  const s = STATUS[cell.status];
  const title = [cell.reference, cell.remarks, cell.note, cell.confirmed ? "Confirmed by an engineer" : null,
    cell.source_missing ? "Its file is no longer in the project folder" : null]
    .filter(Boolean).join("\n") || s.help;
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${s.chip}`} title={title}>
      <StatusIcon status={cell.status} className="h-3.5 w-3.5" />
      {STATUS_LABEL[cell.status]}
      {cell.confirmed && <span aria-label="Confirmed by an engineer" title="Confirmed by an engineer">✎</span>}
      {cell.source_missing && <span aria-label="File missing" title="Its file is no longer in the project folder">⚠</span>}
    </span>
  );
}

export function StatusBadge({ status }: { status: Status }) {
  const s = STATUS[status];
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${s.chip}`} title={s.help}>
      <StatusIcon status={status} className="h-3.5 w-3.5" />
      {STATUS_LABEL[status]}
    </span>
  );
}

export function HintBadge({ hint }: { hint: { kind: string; label: string; severity: "info" | "warning" | "error"; source?: string; note?: string | null } }) {
  const tone = hint.kind === "revision_candidate"
    ? "border-indigo-300 text-indigo-700 bg-indigo-50"
    : hint.severity === "error"
      ? "border-rose-300 text-rose-700 bg-rose-50"
      : hint.severity === "warning"
        ? "border-amber-300 text-amber-800 bg-amber-50"
        : "border-slate-300 text-slate-600 bg-slate-50";
  const mark = hint.source === "ai" ? "✨" : hint.kind === "revision_candidate" ? "ⓘ" : hint.severity === "error" ? "✕" : "⚠";
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-2 py-0.5 text-[11px] font-medium ${tone}`} title={hint.note ?? hint.label}>
      {mark} {hint.label}
    </span>
  );
}

export const EyeIcon = () => (
  <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);

export const FolderIcon = () => (
  <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
  </svg>
);
