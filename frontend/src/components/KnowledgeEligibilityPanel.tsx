import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../lib/api";

interface EligibilitySummary {
  responses: number;
  eligible: number;
  blocked: number;
  blocked_only_by_pairing: number;
  reasons: { reason: string; count: number }[];
  by_manufacturer: { key: string; eligible: number; blocked: number }[];
  by_system: { key: string; eligible: number; blocked: number }[];
  mappings_by_method: { method: string; confidence: string; count: number }[];
  mapping_reviews: { verified: number; rejected: number };
}

interface QueueItem {
  mapping_id: string;
  method: string;
  confidence: string;
  clause: string | null;
  section: string | null;
  requirement: string;
  source: { filename: string | null; project: string | null; manufacturer: string | null; revision: string | null; pdf_page: string | null };
  responses: { response_id: string; response: string; status: string | null; eligibility: string; reasons: string | null }[];
}

/** Why the knowledge base's answers are blocked, and a queue of source
 * pairings for an administrator to check against the source PDFs. A verified
 * pairing counts as high confidence; everything else the policy requires
 * still applies. */
export function KnowledgeEligibilityPanel() {
  const [summary, setSummary] = useState<EligibilitySummary | null>(null);
  const [queue, setQueue] = useState<{ total: number; items: QueueItem[] } | null>(null);
  const [method, setMethod] = useState("A_table");
  const [confidence, setConfidence] = useState("medium");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [note, setNote] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, q] = await Promise.all([
        api.get<EligibilitySummary>("/admin/knowledge/eligibility"),
        api.get<{ total: number; items: QueueItem[] }>(
          `/admin/knowledge/eligibility/review-queue?${new URLSearchParams({ method, confidence, limit: "20" })}`
        ),
      ]);
      setSummary(s);
      setQueue(q);
      setSelected(new Set());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the eligibility summary");
    }
  }, [method, confidence]);

  useEffect(() => {
    void load();
  }, [load]);

  async function review(verdict: "verified" | "rejected") {
    if (selected.size === 0) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ reviewed: number; now_eligible: number; now_blocked: number }>(
        "/admin/knowledge/eligibility/reviews",
        { mapping_ids: [...selected], verdict, note: note.trim() || null }
      );
      setMessage(
        `${result.reviewed} pairing${result.reviewed === 1 ? "" : "s"} marked ${verdict}: ${result.now_eligible} response${
          result.now_eligible === 1 ? "" : "s"
        } now eligible, ${result.now_blocked} now blocked.`
      );
      setNote("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The review could not be saved");
    } finally {
      setBusy(false);
    }
  }

  if (!summary) return error ? <div className="mt-4 text-sm text-red-700">{error}</div> : null;

  return (
    <section aria-labelledby="eligibility-title" className="mt-4 rounded-xl border border-gray-200 bg-white p-4 text-sm">
      <h2 id="eligibility-title" className="font-semibold text-navy-900">
        Why answers are not used by Auto-fill
      </h2>
      <p className="mt-1 text-xs text-gray-500">
        {summary.eligible.toLocaleString()} of {summary.responses.toLocaleString()} responses are eligible.{" "}
        {summary.blocked_only_by_pairing.toLocaleString()} are blocked only because their answer's pairing with its clause was not
        read with high confidence — checking those pairings is what can make them usable. {summary.mapping_reviews.verified} verified,{" "}
        {summary.mapping_reviews.rejected} rejected so far.
      </p>

      <div className="mt-3 grid gap-4 md:grid-cols-2">
        <div>
          <h3 className="text-xs font-semibold uppercase text-gray-500">Blocking reasons</h3>
          <ul className="mt-1 space-y-0.5">
            {summary.reasons.slice(0, 10).map((r) => (
              <li key={r.reason} className="flex justify-between gap-2">
                <span className="text-gray-700">{r.reason}</span>
                <span className="tabular-nums text-gray-500">{r.count.toLocaleString()}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="text-xs font-semibold uppercase text-gray-500">Source pairings by method</h3>
          <ul className="mt-1 space-y-0.5">
            {summary.mappings_by_method.slice(0, 10).map((m) => (
              <li key={`${m.method}-${m.confidence}`} className="flex justify-between gap-2">
                <button
                  className="text-left text-brand-700 hover:underline"
                  onClick={() => {
                    setMethod(m.method);
                    setConfidence(m.confidence);
                  }}
                  title="Show these in the review queue"
                >
                  {m.method} · {m.confidence}
                </button>
                <span className="tabular-nums text-gray-500">{m.count.toLocaleString()}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="mt-4 border-t border-gray-100 pt-3">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <h3 className="font-semibold text-navy-900">
            Review queue: {method} · {confidence} ({queue?.total.toLocaleString() ?? "…"} unreviewed)
          </h3>
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-xs text-gray-600">
              Note
              <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} className="input mt-0.5 w-56 py-1 text-xs" />
            </label>
            <button
              onClick={() => review("verified")}
              disabled={busy || selected.size === 0}
              className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50"
            >
              Verified ({selected.size})
            </button>
            <button
              onClick={() => review("rejected")}
              disabled={busy || selected.size === 0}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-50"
            >
              Wrong pairing ({selected.size})
            </button>
          </div>
        </div>
        <p className="mt-1 text-xs text-gray-500">
          Open the source document at the page shown and confirm that the answer below belongs to the clause. Only pairings you have
          checked should be marked verified.
        </p>
        {message && (
          <div role="status" className="mt-2 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
            {message}
          </div>
        )}
        {error && (
          <div role="alert" className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
            {error}
          </div>
        )}
        <ul className="mt-2 space-y-2">
          {queue?.items.map((item) => (
            <li key={item.mapping_id} className="rounded-lg border border-gray-200 p-3">
              <label className="flex items-start gap-3">
                <input
                  type="checkbox"
                  className="mt-1 h-4 w-4"
                  checked={selected.has(item.mapping_id)}
                  onChange={(e) =>
                    setSelected((prev) => {
                      const next = new Set(prev);
                      if (e.target.checked) next.add(item.mapping_id);
                      else next.delete(item.mapping_id);
                      return next;
                    })
                  }
                />
                <span className="min-w-0 flex-1">
                  <span className="block text-xs text-gray-500">
                    {item.source.filename} · page {item.source.pdf_page ?? "?"} · {item.source.manufacturer ?? "maker unknown"}
                    {item.source.revision ? ` · ${item.source.revision}` : ""} · clause {item.clause ?? "?"}
                  </span>
                  <span className="mt-1 block text-navy-900">{item.requirement}</span>
                  {item.responses.map((r) => (
                    <span key={r.response_id} className="mt-1 block rounded bg-gray-50 px-2 py-1 text-xs text-gray-700">
                      <strong>{r.status ?? "—"}:</strong> {r.response}
                    </span>
                  ))}
                </span>
              </label>
            </li>
          ))}
          {queue && queue.items.length === 0 && <li className="text-xs text-gray-400">Nothing left to review for this method.</li>}
        </ul>
      </div>
    </section>
  );
}
