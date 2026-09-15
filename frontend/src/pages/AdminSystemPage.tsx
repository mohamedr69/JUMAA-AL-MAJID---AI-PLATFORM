import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../lib/api";
import { formatApiDate } from "../lib/format";
import type { AiMetrics, BackupRow, BackupVerification } from "../lib/types";

function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 1000) / 10}%`;
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/** For administrators: how good the AI's answers have been against what
 * engineers decided, what it has cost, which tasks are held off until an
 * evaluation passes, and the database backups. */
export function AdminSystemPage() {
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-xl font-bold text-navy-900">AI quality and backups</h1>
        <p className="text-sm text-gray-500">
          AI answers are measured against the decisions engineers made on them. Nothing here changes a project.
        </p>
      </div>
      <AiQualitySection />
      <BackupsSection />
    </div>
  );
}

function AiQualitySection() {
  const [days, setDays] = useState(30);
  const [metrics, setMetrics] = useState<AiMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setMetrics(await api.get<AiMetrics>(`/admin/ai/metrics?days=${days}`));
    } catch (err) {
      setError(errorText(err, "Could not read the AI metrics"));
    }
  }, [days]);

  useEffect(() => {
    void load();
  }, [load]);

  async function buildCases() {
    setBusy(true);
    setNote(null);
    try {
      const result = await api.post<{ added_or_updated: number; total: number }>("/admin/ai/evaluations/cases?task=read_cell");
      setNote(
        `${result.added_or_updated} reviewed cell readings added or updated; the evaluation set now holds ${result.total}. ` +
          "Score a model on it with scripts/ai_eval.py run on the server."
      );
    } catch (err) {
      setError(errorText(err, "Could not build the evaluation set"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-gray-200 bg-white p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-navy-900">AI answers</h2>
        <div className="flex items-center gap-2 text-sm">
          <label htmlFor="ai-days" className="text-gray-500">
            Period
          </label>
          <select id="ai-days" value={days} onChange={(e) => setDays(Number(e.target.value))} className="input py-1 text-sm">
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={365}>Last year</option>
          </select>
          <button
            onClick={buildCases}
            disabled={busy}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-60"
          >
            {busy ? "Building..." : "Add reviewed readings to the evaluation set"}
          </button>
        </div>
      </div>
      {error && <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {note && <div className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{note}</div>}
      {!metrics ? (
        !error && <div className="mt-3 text-sm text-gray-500">Loading...</div>
      ) : (
        <>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Calls in the last 24 h" value={`${metrics.budget.calls_last_24h}`} hint={`limit ${metrics.budget.calls_per_day_limit} per project`} />
            <Stat
              label="Estimated cost, last 24 h"
              value={metrics.budget.priced ? metrics.budget.cost_last_24h.toFixed(4) : "not priced"}
              hint={metrics.budget.cost_per_job_limit !== null ? `limit ${metrics.budget.cost_per_job_limit} per job` : "set AI prices to track cost"}
            />
            <Stat label="Calls per document" value={`${metrics.budget.calls_per_document_limit}`} hint="limit" />
            <Stat label="Time per job" value={`${metrics.budget.elapsed_s_per_job_limit} s`} hint="limit" />
          </div>

          <h3 className="mt-5 text-sm font-semibold text-navy-900">Against engineers' decisions</h3>
          {metrics.proposals.length === 0 ? (
            <p className="mt-1 text-sm text-gray-500">No AI proposals in this period.</p>
          ) : (
            <div className="mt-2 overflow-x-auto">
              <table className="min-w-full text-left text-xs">
                <thead className="text-gray-500">
                  <tr>
                    <th className="py-1 pr-3 font-medium">Task · prompt · model</th>
                    <th className="py-1 pr-3 font-medium">Proposals</th>
                    <th className="py-1 pr-3 font-medium">Decided</th>
                    <th className="py-1 pr-3 font-medium" title="Values taken exactly as proposed, of those decided">Precision</th>
                    <th className="py-1 pr-3 font-medium" title="Validated proposals that engineers kept">Validated precision</th>
                    <th className="py-1 pr-3 font-medium" title="Issues settled with the value the model proposed">Recall</th>
                    <th className="py-1 pr-3 font-medium">Abstained</th>
                    <th className="py-1 pr-3 font-medium">Corrected</th>
                    <th className="py-1 pr-3 font-medium">Flagged text</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {metrics.proposals.map((row) => (
                    <tr key={`${row.task}-${row.prompt_version}-${row.model}`} className="align-top">
                      <td className="py-1.5 pr-3">
                        <div className="font-medium text-navy-900">{row.task}</div>
                        <div className="text-gray-400">
                          {row.prompt_version} · {row.model}
                        </div>
                      </td>
                      <td className="py-1.5 pr-3">{row.proposals}</td>
                      <td className="py-1.5 pr-3">{row.decided}</td>
                      <td className="py-1.5 pr-3">{percent(row.precision)}</td>
                      <td className={`py-1.5 pr-3 ${row.false_validations.length ? "font-semibold text-red-700" : ""}`}>
                        {percent(row.validated_precision)}
                        {row.false_validations.length > 0 && (
                          <div className="font-normal">
                            {row.false_validations.length} false validation{row.false_validations.length === 1 ? "" : "s"}:{" "}
                            {row.false_validations
                              .slice(0, 3)
                              .map((f) => `${f.proposed} → ${f.decided ?? "rejected"}`)
                              .join(", ")}
                          </div>
                        )}
                      </td>
                      <td className="py-1.5 pr-3">{percent(row.recall)}</td>
                      <td className="py-1.5 pr-3">{percent(row.abstention_rate)}</td>
                      <td className="py-1.5 pr-3">{percent(row.correction_rate)}</td>
                      <td className="py-1.5 pr-3">{row.flagged_injection}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h3 className="mt-5 text-sm font-semibold text-navy-900">Usage</h3>
          {metrics.usage.length === 0 ? (
            <p className="mt-1 text-sm text-gray-500">No calls in this period.</p>
          ) : (
            <div className="mt-2 overflow-x-auto">
              <table className="min-w-full text-left text-xs">
                <thead className="text-gray-500">
                  <tr>
                    <th className="py-1 pr-3 font-medium">Task · model</th>
                    <th className="py-1 pr-3 font-medium">Calls</th>
                    <th className="py-1 pr-3 font-medium">Cache hits</th>
                    <th className="py-1 pr-3 font-medium">Tokens in / out</th>
                    <th className="py-1 pr-3 font-medium">Cost</th>
                    <th className="py-1 pr-3 font-medium">Avg latency</th>
                    <th className="py-1 pr-3 font-medium">Errors</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {metrics.usage.map((row) => (
                    <tr key={`${row.task}-${row.model}`}>
                      <td className="py-1.5 pr-3">
                        <span className="font-medium text-navy-900">{row.task}</span> <span className="text-gray-400">{row.model}</span>
                      </td>
                      <td className="py-1.5 pr-3">{row.calls}</td>
                      <td className="py-1.5 pr-3">{row.cache_hits}</td>
                      <td className="py-1.5 pr-3">
                        {row.input_tokens.toLocaleString()} / {row.output_tokens.toLocaleString()}
                      </td>
                      <td className="py-1.5 pr-3">{row.estimated_cost.toFixed(4)}</td>
                      <td className="py-1.5 pr-3">{row.average_latency_ms} ms</td>
                      <td className="py-1.5 pr-3">
                        {Object.entries(row.errors)
                          .map(([kind, count]) => `${kind} ${count}`)
                          .join(", ") || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h3 className="mt-5 text-sm font-semibold text-navy-900">Evaluations and gated tasks</h3>
          <ul className="mt-2 space-y-1 text-xs">
            {metrics.evaluations.map((report) => (
              <li key={report.file} className="rounded-lg border border-gray-100 px-3 py-2">
                <span className="font-medium text-navy-900">{report.task}</span> · {formatApiDate(report.at)} · prompt {report.prompt_version}
                {report.model ? ` · ${report.model}` : ""} — {report.score.scored} cases, precision {percent(report.score.precision)}, recall{" "}
                {percent(report.score.recall)}, abstained {percent(report.score.abstention_rate)},{" "}
                <span className={report.score.false_validations ? "font-semibold text-red-700" : ""}>
                  {report.score.false_validations} false validations
                </span>
              </li>
            ))}
            {metrics.evaluations.length === 0 && <li className="text-gray-500">No evaluation has been run yet.</li>}
            {metrics.disabled_tasks.length > 0 && (
              <li className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900">
                Switched off on this server (AI_DISABLED_TASKS): {metrics.disabled_tasks.join(", ")}
              </li>
            )}
            {metrics.gates.map((gate) => (
              <li key={gate.task} className="rounded-lg border border-gray-100 px-3 py-2">
                <span className="font-medium text-navy-900">{gate.task}</span>:{" "}
                {gate.open ? (
                  <span className="text-emerald-700">on — a passing evaluation exists for the current prompt</span>
                ) : (
                  <span className="text-gray-600">
                    off until an evaluation of at least {gate.min_cases} cases reaches {percent(gate.min_precision)} precision with no false
                    validations{gate.runnable ? "" : " (its evaluation harness is not written yet)"}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className="text-lg font-semibold text-navy-900">{value}</div>
      <div className="text-[11px] text-gray-400">{hint}</div>
    </div>
  );
}

function BackupsSection() {
  const [rows, setRows] = useState<BackupRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [checks, setChecks] = useState<Record<string, BackupVerification>>({});

  const load = useCallback(async () => {
    try {
      setRows(await api.get<BackupRow[]>("/admin/backups"));
    } catch (err) {
      setError(errorText(err, "Could not list the backups"));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function take() {
    setBusy("take");
    setError(null);
    try {
      await api.post("/admin/backups");
      await load();
    } catch (err) {
      setError(errorText(err, "The backup could not be taken"));
    } finally {
      setBusy(null);
    }
  }

  async function verify(name: string) {
    setBusy(name);
    setError(null);
    try {
      const result = await api.post<BackupVerification>(`/admin/backups/${encodeURIComponent(name)}/verify`);
      setChecks((current) => ({ ...current, [name]: result }));
    } catch (err) {
      setError(errorText(err, "The backup could not be verified"));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="rounded-xl border border-gray-200 bg-white p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-navy-900">Database backups</h2>
          <p className="text-xs text-gray-500">
            A backup is taken automatically before every schema change. Verify restores a copy into a scratch database and reads it.
          </p>
        </div>
        <button
          onClick={take}
          disabled={busy !== null}
          className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
        >
          {busy === "take" ? "Taking backup..." : "Take a backup now"}
        </button>
      </div>
      {error && <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {rows === null ? (
        !error && <div className="mt-3 text-sm text-gray-500">Loading...</div>
      ) : rows.length === 0 ? (
        <p className="mt-3 text-sm text-gray-500">No backups yet.</p>
      ) : (
        <ul className="mt-3 divide-y divide-gray-100 text-xs">
          {rows.map((row) => {
            const check = checks[row.name];
            return (
              <li key={row.name} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <div className="min-w-0">
                  <div className="break-all font-medium text-navy-900">{row.name}</div>
                  <div className="text-gray-500">
                    {formatApiDate(row.created_at)} · {(row.size / 1_048_576).toFixed(1)} MB
                  </div>
                  {check && (
                    <div className={check.restores ? "text-emerald-700" : "text-red-700"}>
                      {check.restores ? "Restores" : "Does NOT restore"}: integrity {check.integrity}; {check.counts.projects} projects,{" "}
                      {check.counts.project_boq_items} BOQ lines, {check.counts.project_boq_revisions} revisions
                      {check.schema_version ? `; schema ${check.schema_version}` : ""}
                    </div>
                  )}
                </div>
                <button
                  onClick={() => verify(row.name)}
                  disabled={busy !== null}
                  className="rounded-lg border border-gray-300 px-2.5 py-1 text-gray-700 hover:bg-gray-50 disabled:opacity-60"
                >
                  {busy === row.name ? "Verifying..." : "Verify restore"}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
