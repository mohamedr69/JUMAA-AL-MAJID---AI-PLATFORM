import { useState } from "react";
import { ApiError, api } from "../lib/api";
import { draftToPayload, type DraftTextField, type ProjectDetailsDraft } from "../lib/projectDetails";
import type { DetailsCheck, FieldSuggestion, SystemSuggestion } from "../lib/types";

/** The AI check of the project details against the DRF page.
 *
 * Claude reads the DRF beside the values the form holds and suggests what
 * the form actually shows. Nothing is applied until the engineer clicks
 * Apply; applying changes only the form, which is then saved (or the project
 * created) the ordinary way. */
export function DetailsCheckPanel({
  endpoint,
  drfPath,
  draft,
  onApply,
  disabled,
}: {
  /** `/projects/{id}/details-check`, or `/projects/details-check` at creation. */
  endpoint: string;
  drfPath?: string | null;
  draft: ProjectDetailsDraft;
  onApply: (next: ProjectDetailsDraft) => void;
  disabled?: boolean;
}) {
  const [result, setResult] = useState<DetailsCheck | null>(null);
  const [applied, setApplied] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.post<DetailsCheck>(endpoint, { details: draftToPayload(draft), drf_path: drfPath ?? null }));
      setApplied(new Set());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The AI check did not complete");
    } finally {
      setBusy(false);
    }
  }

  function withField(current: ProjectDetailsDraft, s: FieldSuggestion): ProjectDetailsDraft {
    return { ...current, [s.field as DraftTextField]: s.suggested };
  }

  function withSystem(current: ProjectDetailsDraft, s: SystemSuggestion): ProjectDetailsDraft {
    const systems = { ...current.systems };
    if (s.change === "remove") delete systems[s.name];
    else if (s.suggested) systems[s.name] = s.suggested;
    return { ...current, systems };
  }

  function apply(key: string, next: ProjectDetailsDraft) {
    onApply(next);
    setApplied((all) => new Set(all).add(key));
  }

  function applyAll() {
    if (!result) return;
    let next = draft;
    const keys = new Set(applied);
    for (const s of result.fields) {
      if (keys.has(`f:${s.field}`)) continue;
      next = withField(next, s);
      keys.add(`f:${s.field}`);
    }
    for (const s of result.systems) {
      if (keys.has(`s:${s.name}`)) continue;
      next = withSystem(next, s);
      keys.add(`s:${s.name}`);
    }
    onApply(next);
    setApplied(keys);
  }

  const total = result ? result.fields.length + result.systems.length : 0;
  const left = result ? total - applied.size : 0;

  return (
    <div className="rounded-xl border border-brand-200 bg-brand-50/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-navy-900">AI check against the DRF</div>
          <p className="text-xs text-gray-600">
            Claude reads the DRF page and compares it with the values below. Suggestions change nothing until you apply them
            {endpoint.includes("/details-check") && !drfPath ? " and save" : ""}.
          </p>
        </div>
        <button
          type="button"
          onClick={run}
          disabled={busy || disabled}
          className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
        >
          {busy ? "Claude is reading the DRF…" : result ? "Check again" : "Check with AI"}
        </button>
      </div>

      {error && <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}

      {result && (
        <div className="mt-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className={total ? "text-amber-800" : "text-emerald-700"}>
              {total === 0
                ? `Everything matches the DRF (${result.confirmed} checked).`
                : `${total} difference${total === 1 ? "" : "s"} found · ${result.confirmed} confirmed`}
              {result.unreadable.length > 0 && ` · could not read: ${result.unreadable.join(", ")}`}
            </span>
            {left > 0 && (
              <button type="button" onClick={applyAll} className="font-semibold text-brand-700 hover:underline">
                Apply all ({left})
              </button>
            )}
          </div>

          <ul className="mt-2 space-y-1.5">
            {result.fields.map((s) => {
              const key = `f:${s.field}`;
              return (
                <Suggestion
                  key={key}
                  title={s.label}
                  from={s.current || "(blank)"}
                  to={s.suggested || "(blank)"}
                  reason={s.reason}
                  applied={applied.has(key)}
                  onApply={() => apply(key, withField(draft, s))}
                />
              );
            })}
            {result.systems.map((s) => {
              const key = `s:${s.name}`;
              return (
                <Suggestion
                  key={key}
                  title={`${s.name} — ${s.change === "add" ? "add system" : s.change === "remove" ? "remove system" : "update"}`}
                  from={systemText(s.current)}
                  to={systemText(s.suggested)}
                  reason={s.reason}
                  applied={applied.has(key)}
                  onApply={() => apply(key, withSystem(draft, s))}
                />
              );
            })}
          </ul>
          {result.notes.length > 0 && <p className="mt-2 text-gray-500">{result.notes.join(" ")}</p>}
          <p className="mt-2 text-[11px] text-gray-400">
            {result.model}
            {result.from_cache ? " · from an earlier identical check" : ""}
          </p>
        </div>
      )}
    </div>
  );
}

function systemText(system: SystemSuggestion["current"]): string {
  if (!system) return "(not recorded)";
  const ticks = [system.method_statement && "MS", system.drawing && "DWG"].filter(Boolean).join(" + ");
  return [system.brand || "no brand", ticks || "no ticks"].join(" · ");
}

function Suggestion({
  title,
  from,
  to,
  reason,
  applied,
  onApply,
}: {
  title: string;
  from: string;
  to: string;
  reason: string;
  applied: boolean;
  onApply: () => void;
}) {
  return (
    <li className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="font-semibold text-navy-900">{title}</div>
        <div className="mt-0.5 text-gray-600">
          <span className="text-red-700 line-through decoration-red-300">{from}</span>
          <span className="mx-1.5 text-gray-400">→</span>
          <span className="font-medium text-emerald-700">{to}</span>
        </div>
        {reason && <div className="mt-0.5 text-gray-500">{reason}</div>}
      </div>
      {applied ? (
        <span className="rounded-full bg-emerald-50 px-2 py-0.5 font-semibold text-emerald-700">Applied</span>
      ) : (
        <button
          type="button"
          onClick={onApply}
          className="rounded-md border border-brand-200 bg-white px-2.5 py-1 font-semibold text-brand-700 hover:bg-brand-50"
        >
          Apply
        </button>
      )}
    </li>
  );
}
