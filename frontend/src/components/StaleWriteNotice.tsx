import type { ApiError } from "../lib/api";

/** Shown when a save is refused because someone else saved the same
 * document after this page loaded it. The page's edits are still on screen;
 * reloading shows the other person's version, and the edits are made again
 * over it -- nothing is merged silently. */
export function StaleWriteNotice({
  error,
  what,
  onReload,
  onDismiss,
}: {
  error: ApiError;
  what: string;
  onReload: () => void;
  onDismiss?: () => void;
}) {
  return (
    <div role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
      <div className="font-semibold">Not saved: {what} was changed by someone else</div>
      <p className="mt-1">{error.message}</p>
      <p className="mt-1 text-xs text-red-700">
        Your edits are still on this page. Copy anything you need, then reload to see the current version and make your changes
        again.
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        <button
          onClick={onReload}
          className="rounded-lg bg-red-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:ring-offset-2"
        >
          Reload the current version
        </button>
        {onDismiss && (
          <button
            onClick={onDismiss}
            className="rounded-lg border border-red-300 px-3 py-1.5 text-xs font-semibold text-red-800 hover:bg-red-100"
          >
            Keep my edits on screen
          </button>
        )}
      </div>
    </div>
  );
}
