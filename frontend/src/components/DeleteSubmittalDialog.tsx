/** The warning before a material submittal is deleted for good: the
 * register row and its history, the log entry, and -- after this
 * confirmation -- the filed forms themselves in the project folder on
 * OneDrive. Nothing is deleted until "Delete permanently" is pressed. */
export function DeleteSubmittalDialog({
  title,
  reference,
  revision,
  files,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  title: string;
  reference: string | null;
  revision: string | null;
  files: string[];
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-navy-950/40 p-6" role="dialog" aria-modal="true" aria-labelledby="delete-submittal-title">
      <div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl">
        <h2 id="delete-submittal-title" className="text-lg font-bold text-red-700">Delete this material submittal permanently?</h2>
        <p className="mt-2 text-sm text-gray-700">
          <span className="font-semibold">{title}</span>
          {revision ? ` ${revision}` : ""}
          {reference ? ` (${reference})` : ""} will be removed from the register, the map and the log, with its history.
        </p>
        {files.length > 0 ? (
          <div className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-800">
            <div className="font-semibold">The file{files.length === 1 ? "" : "s"} below will be deleted from the project folder on OneDrive:</div>
            <ul className="mt-1 list-disc pl-5">
              {files.map((f) => (
                <li key={f} className="break-all">{f}</li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="mt-3 text-sm text-gray-500">No filed form is known for it; only the record is removed.</p>
        )}
        <p className="mt-3 text-xs text-gray-500">This cannot be undone.</p>
        {error && <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
        <div className="mt-5 flex justify-end gap-3">
          <button onClick={onCancel} disabled={busy} className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-navy-900">Cancel</button>
          <button onClick={onConfirm} disabled={busy} className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60">
            {busy ? "Deleting..." : "Delete permanently"}
          </button>
        </div>
      </div>
    </div>
  );
}
