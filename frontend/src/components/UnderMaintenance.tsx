/** Shown wherever the platform's design has a section the build has not
 * reached yet. A page that says plainly it is being worked on is honest;
 * buttons that look ready and do nothing are not. */
export function UnderMaintenance({ title, note }: { title: string; note?: string }) {
  return (
    <div className="rounded-xl border border-dashed border-amber-300 bg-amber-50/60 p-10 text-center">
      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-amber-100 text-amber-700">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-7 w-7">
          <path d="M14.7 6.3a4 4 0 0 1-5.4 5.4L5 16v3h3l4.3-4.3a4 4 0 0 0 5.4-5.4l-2.2 2.2-2.1-2.1z" />
        </svg>
      </div>
      <h1 className="mt-4 text-xl font-bold text-navy-900">{title}</h1>
      <p className="mx-auto mt-2 max-w-md text-sm text-amber-900">
        This part of the platform is under maintenance while it is being built.
      </p>
      {note && <p className="mx-auto mt-1 max-w-md text-sm text-amber-800">{note}</p>}
    </div>
  );
}
