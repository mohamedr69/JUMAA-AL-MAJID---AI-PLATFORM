import { useEffect, useRef, useState } from "react";
import { API_BASE_URL, ApiError, api } from "../lib/api";
import type { ChecklistRead, PackagePlan, Project } from "../lib/types";

/** Creating a material submittal package.
 *
 * Three steps, in the platform owner's order: pick the **system**, choose how
 * the **index** is decided, then assemble. A system is offered whether or not
 * the register already holds a submittal for it -- a package is rebuilt
 * often, for a revision or to check what it would contain, and refusing to
 * build one that already exists would make that impossible.
 *
 * The index comes either from ticking the sections, or from the company's
 * checklist: the form the team already fills in and signs, read here rather
 * than asked for a second time.
 *
 * The finished package is a PDF the browser holds, so it can be opened and
 * looked at before anyone decides to keep it.
 */

type Step = "system" | "sections";
type Mode = "custom" | "checklist";

export function SubmittalPackageBuilder({
  project,
  systems,
  onClose,
}: {
  project: Project;
  systems: string[];
  onClose: () => void;
}) {
  const [step, setStep] = useState<Step>("system");
  const [mode, setMode] = useState<Mode>("custom");
  const [system, setSystem] = useState(systems[0] ?? "FAS");
  const [revision, setRevision] = useState("R0");
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [plan, setPlan] = useState<PackagePlan | null>(null);
  const [checklist, setChecklist] = useState<ChecklistRead | null>(null);
  const [loading, setLoading] = useState(false);
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ url: string; name: string; pages: string; missing: string; filed: string | null } | null>(null);
  // A revision already filed for this system: the window that offers the
  // next revision, or replacing what is filed.
  const [prepared, setPrepared] = useState<{ reference: string; revision: string; filed: string; filed_at: string | null; next_revision: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  // The blob outlives the fetch, so it has to be released by hand.
  useEffect(() => () => { if (result) URL.revokeObjectURL(result.url); }, [result]);

  // The section list and what each would contribute. Asked for without
  // naming sections, which answers for all of them, so ticking a box never
  // needs another round trip.
  useEffect(() => {
    if (step !== "sections") return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .get<PackagePlan>(`/projects/${project.id}/submittal/package/plan?system_code=${encodeURIComponent(system)}`)
      .then((result) => {
        if (cancelled) return;
        setPlan(result);
        setChosen(new Set(
          checklist
            ? checklist.sections
            : result.sections.filter((s) => s.found > 0).map((s) => s.number),
        ));
      })
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Could not read the submittal builder"))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [step, project.id, system, checklist]);

  const toggle = (number: number) =>
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(number)) next.delete(number); else next.add(number);
      return next;
    });

  async function attachChecklist(file: File) {
    setLoading(true);
    setError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const read = await api.upload<ChecklistRead>(`/projects/${project.id}/submittal/package/checklist`, body);
      setChecklist(read);
      setMode("checklist");
      setStep("sections");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The checklist could not be read");
    } finally {
      setLoading(false);
    }
  }

  async function create(options: { revision?: string; replace?: boolean } = {}) {
    const wanted = options.revision ?? revision;
    if (options.revision) setRevision(options.revision);
    setPrepared(null);
    setBuilding(true);
    setError(null);
    if (result) URL.revokeObjectURL(result.url);
    setResult(null);
    try {
      const res = await fetch(`${API_BASE_URL}/projects/${project.id}/submittal/package`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sections: [...chosen].sort((a, b) => a - b), system_code: system, revision: wanted, replace: Boolean(options.replace) }),
      });
      if (!res.ok) {
        let detail: unknown = res.statusText;
        try { detail = (await res.json()).detail ?? detail; } catch { /* no JSON body */ }
        if (res.status === 409 && typeof detail === "object" && detail !== null && (detail as { code?: string }).code === "already_prepared") {
          setPrepared(detail as { reference: string; revision: string; filed: string; filed_at: string | null; next_revision: string });
          return;
        }
        throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      setResult({
        url: URL.createObjectURL(await res.blob()),
        name: `EP-${project.ep_number} - Material Submittal - ${wanted}.pdf`,
        pages: res.headers.get("X-Package-Pages") ?? "?",
        missing: res.headers.get("X-Package-Warnings") ?? "0",
        filed: res.headers.get("X-Package-Filed") ? decodeURIComponent(res.headers.get("X-Package-Filed") ?? "") : null,
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The package could not be built");
    } finally {
      setBuilding(false);
    }
  }

  const selected = plan?.sections.filter((s) => chosen.has(s.number)) ?? [];
  const totalMissing = selected.reduce((sum, s) => sum + s.missing, 0);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-navy-950/40 p-6">
      <div className="w-full max-w-3xl rounded-2xl bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-gray-200 px-6 py-4">
          <div>
            <h2 className="text-lg font-bold text-navy-900">Create Material Submittal</h2>
            <p className="text-sm text-gray-500">
              EP-{project.ep_number}
              {step === "sections" && ` · ${system} · ${revision}`}
            </p>
          </div>
          <button onClick={onClose} className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50">
            Close
          </button>
        </div>

        {/* step 1 -- the system, then how the index is decided */}
        {step === "system" && (
          <div className="p-6">
            <div className="flex flex-wrap items-end gap-4">
              <label className="text-sm">
                System
                <select className="input mt-1 w-52" value={system} onChange={(e) => setSystem(e.target.value)}>
                  {systems.map((code) => <option key={code}>{code}</option>)}
                </select>
              </label>
              <label className="text-sm">
                Revision
                <input className="input mt-1 w-28" value={revision} onChange={(e) => setRevision(e.target.value)} />
              </label>
            </div>
            <p className="mt-2 text-xs text-gray-500">
              Every system the BOQ covers is offered, including ones the register already has a submittal for.
            </p>

            <h3 className="mt-6 text-sm font-semibold text-navy-900">How is the index decided?</h3>
            <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <button
                onClick={() => { setChecklist(null); setMode("custom"); setStep("sections"); }}
                className="rounded-xl border border-gray-200 p-5 text-left transition hover:border-brand-300 hover:shadow-md"
              >
                <div className="font-bold text-navy-900">Custom index</div>
                <p className="mt-1 text-sm text-gray-500">
                  Tick the sections this submittal carries. Starts from the ones the platform can fill.
                </p>
              </button>
              <button
                onClick={() => fileInput.current?.click()}
                disabled={loading}
                className="rounded-xl border border-gray-200 p-5 text-left transition hover:border-brand-300 hover:shadow-md disabled:opacity-50"
              >
                <div className="font-bold text-navy-900">{loading ? "Reading checklist..." : "Attach checklist"}</div>
                <p className="mt-1 text-sm text-gray-500">
                  Attach the signed Material Submittal Checklist. Its Yes / No / N/A ticks decide the index.
                </p>
              </button>
            </div>
            <input
              ref={fileInput}
              type="file"
              accept="application/pdf,.pdf"
              className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) attachChecklist(f); }}
            />
            {error && <p className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}
          </div>
        )}

        {/* step 2 -- the sections */}
        {step === "sections" && (
          <div className="p-6">
            <div className="mb-4 flex flex-wrap items-center gap-3">
              <button onClick={() => setStep("system")} className="text-sm text-brand-600 hover:underline">
                &larr; System and index
              </button>
              {mode === "checklist" && checklist && (
                <span className="rounded-full bg-brand-50 px-2.5 py-1 text-xs font-semibold text-brand-700">
                  From the attached checklist · {checklist.sections.length} ticked Yes
                </span>
              )}
            </div>

            {loading && <p className="py-8 text-center text-sm text-gray-500">Reading the submittal builder and the datasheet library...</p>}

            {!loading && plan && !plan.library_found && (
              <p className="mb-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                The submittal builder folder was not found, so the cover, the dividers and the company documents cannot be collected.
              </p>
            )}

            {!loading && plan?.battery_calculation?.note && (
              <p className="mb-4 rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-600">{plan.battery_calculation.note}</p>
            )}

            {!loading && plan && (
              <div className="max-h-[42vh] overflow-y-auto rounded-lg border border-gray-200">
                <table className="w-full text-left text-sm">
                  <thead className="sticky top-0 bg-gray-50">
                    <tr>
                      {["", "No.", "Section", "What goes in", ...(checklist ? ["Checklist"] : [])].map((h) => (
                        <th key={h} className="border-b border-gray-200 px-3 py-2.5 font-semibold">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {plan.sections.map((section) => (
                      <tr key={section.number} className="border-b border-gray-100 last:border-0">
                        <td className="px-3 py-2.5">
                          <input
                            type="checkbox"
                            aria-label={section.name}
                            checked={chosen.has(section.number)}
                            onChange={() => toggle(section.number)}
                          />
                        </td>
                        <td className="px-3 py-2.5 text-gray-500">{String(section.number).padStart(2, "0")}</td>
                        <td className="px-3 py-2.5 font-medium text-navy-900">{section.name}</td>
                        <td className="px-3 py-2.5 text-gray-500">
                          {section.found > 0 && (
                            <span className="font-medium text-green-700">
                              {section.found} document{section.found === 1 ? "" : "s"}
                            </span>
                          )}
                          {section.missing > 0 && (
                            <span className="ml-2 font-medium text-amber-700">{section.missing} missing</span>
                          )}
                          {section.found === 0 && section.missing === 0 && (
                            <span className="text-gray-400">{section.note ?? "Nothing found"}</span>
                          )}
                        </td>
                        {checklist && (
                          <td className="px-3 py-2.5 text-xs font-semibold uppercase text-gray-500">
                            {checklist.answers[section.number] ?? "—"}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {prepared && (
              <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4" role="alertdialog" aria-labelledby="already-prepared-title">
                <h3 id="already-prepared-title" className="text-sm font-bold text-amber-900">Material submittal is already prepared</h3>
                <p className="mt-1 text-sm text-amber-900">
                  {prepared.reference} {prepared.revision} is filed as <span className="break-all font-medium">{prepared.filed}</span>
                  {prepared.filed_at ? ` (${new Date(prepared.filed_at).toLocaleString()})` : ""}. Create the next revision, or replace what is filed.
                </p>
                <div className="mt-3 flex flex-wrap gap-3">
                  <button onClick={() => void create({ revision: prepared.next_revision })} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white">
                    Create {prepared.next_revision}
                  </button>
                  <button onClick={() => void create({ revision: prepared.revision, replace: true })} className="rounded-lg border border-amber-400 px-4 py-2 text-sm font-semibold text-amber-900">
                    Replace {prepared.revision}
                  </button>
                  <button onClick={() => setPrepared(null)} className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-navy-900">Cancel</button>
                </div>
              </div>
            )}

            {result && (
              <div className="mt-4 rounded-lg bg-green-50 p-4">
                <p className="text-sm font-semibold text-green-800">
                  {result.pages} pages assembled
                  {result.missing !== "0" && ` · ${result.missing} document${result.missing === "1" ? "" : "s"} still to add`}
                </p>
                <p className="mt-1 text-xs text-green-800">
                  {result.filed
                    ? `Filed in the project folder as ${result.filed} and entered in the register and the log.`
                    : "Not filed: the project folder is not reachable on this PC, or the system has no folder. Save the download into it."}
                </p>
                <div className="mt-3 flex flex-wrap gap-3">
                  <a
                    href={result.url}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white"
                  >
                    View PDF
                  </a>
                  <a
                    href={result.url}
                    download={result.name}
                    className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-white"
                  >
                    Download
                  </a>
                </div>
              </div>
            )}
            {error && <p className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}

            <div className="mt-5 flex flex-wrap items-center gap-3">
              <button
                // Not `onClick={create}`: React would hand the click event
                // in as `options`, and the build would go out with whatever
                // a MouseEvent happens to have on it rather than with none.
                onClick={() => void create()}
                disabled={building || chosen.size === 0 || loading}
                className="rounded-lg bg-brand-600 px-5 py-2 font-semibold text-white disabled:opacity-40"
              >
                {building ? "Assembling..." : result ? "Rebuild" : "Create Material Submittal"}
              </button>
              <span className="text-sm text-gray-500">
                {chosen.size} section{chosen.size === 1 ? "" : "s"}
                {totalMissing > 0 && ` · ${totalMissing} document${totalMissing === 1 ? "" : "s"} missing`}
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
