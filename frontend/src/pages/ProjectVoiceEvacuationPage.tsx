import { useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { ApiError, api } from "../lib/api";
import {
  PROJECT_EDITOR_ROLES,
  type VeChannelStatus,
  type VeDesign,
  type VoiceEvacuationResponse,
  type WorkbookCandidate,
} from "../lib/types";
import { formatPercent, formatWatts, mergeChannelAt, splitChannelAt } from "../lib/ve";
import { useProject } from "./ProjectWorkspace";

/** The API sends naive UTC; without a zone the browser would read it as
 * local time. */
function formatWhen(value: string): string {
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const STATUS: Record<VeChannelStatus, { label: string; badge: string; bar: string }> = {
  ok: { label: "Within limit", badge: "bg-green-50 text-green-700", bar: "bg-green-500" },
  over_limit: { label: "Over limit", badge: "bg-amber-50 text-amber-800", bar: "bg-amber-500" },
  over_rating: { label: "Over rating", badge: "bg-red-50 text-red-700", bar: "bg-red-500" },
  no_rating: { label: "No amplifier", badge: "bg-gray-100 text-gray-500", bar: "bg-gray-300" },
};

function numberOrNull(value: string): number | null {
  if (value.trim() === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function ProjectVoiceEvacuationPage() {
  const { project } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);

  const [data, setData] = useState<VoiceEvacuationResponse | null>(null);
  const [draft, setDraft] = useState<VeDesign | null>(null);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [importOpen, setImportOpen] = useState(false);
  const [candidates, setCandidates] = useState<WorkbookCandidate[] | null>(null);
  const [candidatesError, setCandidatesError] = useState<string | null>(null);
  const [selectedPath, setSelectedPath] = useState("");
  const [selectedSheet, setSelectedSheet] = useState("");
  const [importing, setImporting] = useState(false);

  function accept(response: VoiceEvacuationResponse) {
    setData(response);
    setDraft(response.design);
    setDirty(false);
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .get<VoiceEvacuationResponse>(`/projects/${project.id}/design/ve`)
      .then((response) => {
        if (cancelled) return;
        accept(response);
        // Nothing imported yet: the import is the only thing to do here.
        if (!response.design && canEdit) setImportOpen(true);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load the design");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [project.id, canEdit]);

  // The folder listing walks the project's archive folder, which can be
  // slow on a synced drive -- so only when the import panel is open.
  useEffect(() => {
    if (!importOpen || candidates !== null) return;
    let cancelled = false;
    api
      .get<WorkbookCandidate[]>(`/projects/${project.id}/design/ve/workbooks`)
      .then((list) => {
        if (cancelled) return;
        setCandidates(list);
        const current = data?.design?.source?.path;
        setSelectedPath(list.find((c) => c.path === current)?.path ?? list.find((c) => c.likely)?.path ?? "");
      })
      .catch((err) => {
        if (!cancelled) setCandidatesError(err instanceof ApiError ? err.message : "Failed to list the workbooks");
      });
    return () => {
      cancelled = true;
    };
  }, [importOpen, candidates, project.id, data]);

  async function runImport() {
    if (!selectedPath) return;
    if (
      data?.design &&
      !window.confirm("Replace the current design, and any edits made to it, with what this workbook says?")
    ) {
      return;
    }
    setImporting(true);
    setError(null);
    try {
      const sheet = selectedPath === data?.design?.source?.path && selectedSheet ? selectedSheet : null;
      accept(
        await api.post<VoiceEvacuationResponse>(`/projects/${project.id}/design/ve/import`, {
          path: selectedPath,
          sheet,
        })
      );
      setImportOpen(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Import failed");
    } finally {
      setImporting(false);
    }
  }

  async function save() {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      accept(await api.put<VoiceEvacuationResponse>(`/projects/${project.id}/design/ve`, draft));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  function edit(next: VeDesign) {
    setDraft(next);
    setDirty(true);
  }

  const design = draft;
  const result = data?.result ?? null;
  const source = data?.design?.source ?? null;
  const limit = data?.design?.max_load_fraction ?? (data?.rule?.data.fraction as number | undefined) ?? null;
  const multipleSheets = source !== null && source.sheets.length > 1 && selectedPath === source.path;
  const startsOf = (d: VeDesign | null | undefined) => d?.channels.map((c) => c.first_zone).join(",");
  const boundariesChanged = startsOf(design) !== startsOf(data?.design);

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-2xl font-bold text-navy-900">Voice Evacuation Amplifiers</h1>
          <p className="mt-1 text-sm text-gray-500">
            Amplifier loading from the engineer&rsquo;s zone schedule, recalculated from the speaker counts.
          </p>
        </div>
        {canEdit && design && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => setImportOpen((open) => !open)}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
            >
              Re-import
            </button>
            <button
              onClick={save}
              disabled={saving || !dirty}
              className="rounded-lg bg-brand-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
            >
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        )}
      </div>

      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

      {canEdit && importOpen && (
        <div className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
          <div className="text-sm font-semibold text-navy-900">Import the amplifier calculation workbook</div>
          <p className="mt-1 text-xs text-gray-500">
            Pick the engineer&rsquo;s amplifier workbook from the project folder. The zones, speaker counts, taps and
            the grouping onto amplifiers are read from it; the loads are then worked out here.
          </p>
          {candidatesError ? (
            <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{candidatesError}</div>
          ) : candidates === null ? (
            <div className="mt-3 text-sm text-gray-400">Looking through the project folder...</div>
          ) : candidates.length === 0 ? (
            <div className="mt-3 text-sm text-amber-700">No Excel workbooks (.xlsx) in the project folder.</div>
          ) : (
            <div className="mt-3 flex flex-wrap items-end gap-3">
              <label className="min-w-0 flex-1">
                <span className="text-xs font-medium text-gray-500">Workbook</span>
                <select
                  value={selectedPath}
                  onChange={(e) => setSelectedPath(e.target.value)}
                  className="input mt-1 py-1.5"
                >
                  <option value="">Choose a workbook...</option>
                  {candidates.some((c) => c.likely) && (
                    <optgroup label="Looks like an amplifier calculation">
                      {candidates
                        .filter((c) => c.likely)
                        .map((c) => (
                          <option key={c.path} value={c.path}>
                            {c.path}
                          </option>
                        ))}
                    </optgroup>
                  )}
                  <optgroup label="Other workbooks">
                    {candidates
                      .filter((c) => !c.likely)
                      .map((c) => (
                        <option key={c.path} value={c.path}>
                          {c.path}
                        </option>
                      ))}
                  </optgroup>
                </select>
              </label>
              {multipleSheets && (
                <label>
                  <span className="text-xs font-medium text-gray-500">Sheet</span>
                  <select
                    value={selectedSheet || source.sheet}
                    onChange={(e) => setSelectedSheet(e.target.value)}
                    className="input mt-1 py-1.5"
                  >
                    {source.sheets.map((sheet) => (
                      <option key={sheet} value={sheet}>
                        {sheet}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <button
                onClick={runImport}
                disabled={!selectedPath || importing}
                className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
              >
                {importing ? "Reading..." : "Import"}
              </button>
            </div>
          )}
        </div>
      )}

      {loading ? (
        <div className="mt-6 rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500">
          Loading...
        </div>
      ) : !design || !result ? (
        !importOpen && (
          <div className="mt-6 rounded-xl border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-400">
            No amplifier calculation has been imported for this project yet.
          </div>
        )
      ) : (
        <>
          <div className="mt-4 space-y-1 text-xs text-gray-500">
            {source && (
              <div>
                From <span className="font-medium text-gray-700">{source.path}</span>, sheet{" "}
                <span className="font-medium text-gray-700">{source.sheet}</span> &middot; imported{" "}
                {formatWhen(source.imported_at)}
                {data?.updated_at && data.updated_by && (
                  <>
                    {" "}
                    &middot; last saved {formatWhen(data.updated_at)} by {data.updated_by}
                  </>
                )}
              </div>
            )}
            {limit !== null && (
              <div title={data?.rule?.source ?? undefined}>
                Design limit: a channel may carry up to{" "}
                <span className="font-medium text-gray-700">{formatPercent(limit)}</span> of its amplifier&rsquo;s
                rating{data?.rule && <> (rule v{data.rule.version})</>}.
              </div>
            )}
          </div>

          {source && source.warnings.length > 0 && (
            <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
              <div className="font-medium">Check these against the workbook:</div>
              <ul className="mt-1 list-disc pl-5 text-xs">
                {source.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          )}
          {dirty && (
            <div className="mt-3 rounded-lg bg-blue-50 px-3 py-2 text-sm text-blue-800">
              Unsaved edits. The loads below are from the last save and update when you save.
            </div>
          )}

          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Total load" value={formatWatts(result.total_required_watts)} />
            <Stat label="Channels" value={String(result.channels.length)} />
            <Stat
              label="Over the limit"
              value={String(result.channels_failing)}
              tone={result.channels_failing > 0 ? "bad" : "good"}
            />
            <Stat
              label="Sheet disagrees"
              value={String(result.sheet_mismatches)}
              tone={result.sheet_mismatches > 0 ? "warn" : undefined}
              hint="Rows or channels where the workbook's own figure differs from its counts x taps"
            />
          </div>
          {result.unassigned_zones.length > 0 && (
            <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
              {result.unassigned_zones.length} zone{result.unassigned_zones.length > 1 ? "s are" : " is"} on no
              amplifier channel: {result.unassigned_zones.map((i) => design.zones[i]?.name).join(", ")}.
            </div>
          )}

          <h2 className="mt-8 text-lg font-semibold text-navy-900">Channels</h2>
          <div className="mt-2 overflow-x-auto rounded-xl border border-gray-200 bg-white">
            <table className="w-full min-w-[960px] text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-3 py-2 font-medium">#</th>
                  <th className="px-3 py-2 font-medium">Label</th>
                  <th className="px-3 py-2 font-medium">Zones</th>
                  <th className="px-3 py-2 text-right font-medium">Required</th>
                  <th className="w-32 px-3 py-2 text-right font-medium">Amplifier (W)</th>
                  <th className="px-3 py-2 text-right font-medium">Limit</th>
                  <th className="w-44 px-3 py-2 font-medium">Load</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 text-right font-medium">Sheet</th>
                  <th className="px-3 py-2 font-medium">Rack</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {design.channels.map((draftChannel, index) => {
                  // The rows are the draft's; the figures are the last save's,
                  // and only line up with the draft while its boundaries do.
                  const channel = boundariesChanged ? null : result.channels[index];
                  const status = channel ? STATUS[channel.status] : null;
                  const lastZone = (design.channels[index + 1]?.first_zone ?? design.zones.length) - 1;
                  const first = design.zones[draftChannel.first_zone]?.name;
                  const last = design.zones[lastZone]?.name;
                  return (
                    <tr key={index} className={dirty ? "opacity-70" : undefined}>
                      <td className="px-3 py-1.5 tabular-nums text-gray-400">{index + 1}</td>
                      <td className="px-3 py-1.5">
                        <input
                          value={draftChannel.label ?? ""}
                          disabled={!canEdit}
                          placeholder="—"
                          onChange={(e) =>
                            edit({
                              ...design,
                              channels: design.channels.map((c, i) =>
                                i === index ? { ...c, label: e.target.value || null } : c
                              ),
                            })
                          }
                          className="input w-40 py-1 disabled:bg-gray-50"
                        />
                      </td>
                      <td className="px-3 py-1.5 text-gray-700">
                        {first === last ? first : `${first} – ${last}`}
                        <span className="ml-1 text-xs text-gray-400">({lastZone - draftChannel.first_zone + 1})</span>
                      </td>
                      <td className="px-3 py-1.5 text-right font-medium tabular-nums">
                        {formatWatts(channel?.required_watts)}
                      </td>
                      <td className="px-3 py-1.5 text-right">
                        <input
                          type="number"
                          min={0}
                          step="any"
                          value={draftChannel.amplifier_watts ?? ""}
                          disabled={!canEdit}
                          placeholder="—"
                          onChange={(e) =>
                            edit({
                              ...design,
                              channels: design.channels.map((c, i) =>
                                i === index ? { ...c, amplifier_watts: numberOrNull(e.target.value) } : c
                              ),
                            })
                          }
                          className="input w-24 py-1 text-right disabled:bg-gray-50"
                        />
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-gray-500">
                        {formatWatts(channel?.limit_watts)}
                      </td>
                      <td className="px-3 py-1.5">
                        {channel && status ? (
                          <LoadBar fraction={channel.load_fraction} limit={result.max_load_fraction} bar={status.bar} />
                        ) : (
                          <span className="text-xs text-gray-400">—</span>
                        )}
                      </td>
                      <td className="px-3 py-1.5">
                        {status ? (
                          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${status.badge}`}>
                            {status.label}
                          </span>
                        ) : (
                          <span className="text-xs text-gray-400">Save to calculate</span>
                        )}
                      </td>
                      <td
                        className={`px-3 py-1.5 text-right tabular-nums ${
                          channel?.sheet_mismatch ? "font-medium text-amber-700" : "text-gray-400"
                        }`}
                        title={
                          channel?.sheet_mismatch
                            ? `The workbook says ${formatWatts(channel.sheet_required_watts)}; its counts give ${formatWatts(channel.required_watts)}`
                            : undefined
                        }
                      >
                        {formatWatts(draftChannel.sheet_required_watts)}
                      </td>
                      <td className="px-3 py-1.5 text-gray-600">
                        {channel && channel.rack !== null ? result.racks[channel.rack]?.name : "—"}
                      </td>
                    </tr>
                  );
                })}
                {design.channels.length === 0 && (
                  <tr>
                    <td colSpan={10} className="px-3 py-6 text-center text-sm text-gray-400">
                      No channels. Tick &ldquo;Starts channel&rdquo; on a zone below to start one.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {result.racks.length > 0 && (
            <>
              <h2 className="mt-8 text-lg font-semibold text-navy-900">Racks</h2>
              <div className="mt-2 overflow-x-auto rounded-xl border border-gray-200 bg-white">
                <table className="w-full min-w-[640px] text-sm">
                  <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                    <tr>
                      <th className="px-3 py-2 font-medium">Rack</th>
                      <th className="px-3 py-2 font-medium">Location</th>
                      <th className="px-3 py-2 font-medium">Channels</th>
                      <th className="px-3 py-2 text-right font-medium">Required</th>
                      <th className="px-3 py-2 text-right font-medium">Amplifiers</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {result.racks.map((rack) => (
                      <tr key={rack.index} className={dirty ? "opacity-70" : undefined}>
                        <td className="px-3 py-1.5 font-medium text-navy-900">{rack.name}</td>
                        <td className="px-3 py-1.5 text-gray-600">{rack.location ?? "—"}</td>
                        <td className="px-3 py-1.5 tabular-nums text-gray-600">
                          {rack.first_channel === rack.last_channel
                            ? rack.first_channel + 1
                            : `${rack.first_channel + 1} – ${rack.last_channel + 1}`}
                        </td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{formatWatts(rack.required_watts)}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{formatWatts(rack.amplifier_watts)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <h2 className="mt-8 text-lg font-semibold text-navy-900">Zone schedule</h2>
          <p className="mt-1 text-xs text-gray-500">
            Watts per zone = speaker count &times; tap, summed over the speaker types. A channel runs from the zone
            that starts it to the zone before the next one.
          </p>
          <div className="mt-2 overflow-x-auto rounded-xl border border-gray-200 bg-white">
            <table className="w-full min-w-[800px] text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-3 py-2 font-medium">Zone</th>
                  {design.speaker_types.map((speaker, s) => (
                    <th key={speaker.key} className="px-2 py-2 text-right font-medium normal-case">
                      <div className="max-w-40 truncate" title={speaker.model ? `${speaker.name} (${speaker.model})` : speaker.name}>
                        {speaker.name}
                      </div>
                      <label className="mt-1 flex items-center justify-end gap-1 font-normal text-gray-400">
                        tap
                        <input
                          type="number"
                          min={0}
                          step="any"
                          value={speaker.tap_watts}
                          disabled={!canEdit}
                          onChange={(e) => {
                            const tap = numberOrNull(e.target.value);
                            if (tap === null) return;
                            edit({
                              ...design,
                              speaker_types: design.speaker_types.map((t, i) =>
                                i === s ? { ...t, tap_watts: tap } : t
                              ),
                            });
                          }}
                          className="input w-16 py-0.5 text-right text-xs disabled:bg-gray-50"
                        />
                        W
                      </label>
                    </th>
                  ))}
                  <th className="px-3 py-2 text-right font-medium">Watts</th>
                  <th className="px-3 py-2 text-right font-medium">Sheet</th>
                  <th className="px-3 py-2 font-medium">Channel</th>
                  {canEdit && <th className="px-3 py-2 font-medium">Starts channel</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {design.zones.map((zone, z) => {
                  const zoneResult = result.zones[z];
                  const starts = design.channels.some((c) => c.first_zone === z);
                  return (
                    <tr key={z} className={starts ? "border-t-2 border-t-gray-200" : undefined}>
                      <td className="px-3 py-1 text-gray-800">{zone.name}</td>
                      {design.speaker_types.map((speaker) => (
                        <td key={speaker.key} className="px-2 py-1 text-right">
                          <input
                            type="number"
                            min={0}
                            step={1}
                            value={zone.counts[speaker.key] ?? ""}
                            disabled={!canEdit}
                            onChange={(e) => {
                              const count = numberOrNull(e.target.value);
                              const counts = { ...zone.counts };
                              if (count === null || count <= 0) delete counts[speaker.key];
                              else counts[speaker.key] = Math.round(count);
                              edit({
                                ...design,
                                zones: design.zones.map((zz, i) => (i === z ? { ...zz, counts } : zz)),
                              });
                            }}
                            className="input w-16 py-0.5 text-right disabled:bg-gray-50"
                          />
                        </td>
                      ))}
                      <td className={`px-3 py-1 text-right tabular-nums ${dirty ? "text-gray-400" : ""}`}>
                        {zoneResult ? formatWatts(zoneResult.watts) : "—"}
                      </td>
                      <td
                        className={`px-3 py-1 text-right tabular-nums ${
                          zoneResult?.sheet_mismatch ? "font-medium text-amber-700" : "text-gray-400"
                        }`}
                        title={
                          zoneResult?.sheet_mismatch
                            ? `The workbook says ${formatWatts(zone.sheet_watts)}; its counts give ${formatWatts(zoneResult.watts)}`
                            : undefined
                        }
                      >
                        {formatWatts(zone.sheet_watts)}
                      </td>
                      <td className="px-3 py-1 tabular-nums text-gray-500">
                        {zoneResult && zoneResult.channel !== null && !dirty ? zoneResult.channel + 1 : ""}
                      </td>
                      {canEdit && (
                        <td className="px-3 py-1">
                          <input
                            type="checkbox"
                            checked={starts}
                            onChange={(e) =>
                              edit(e.target.checked ? splitChannelAt(design, z) : mergeChannelAt(design, z))
                            }
                            aria-label={`Channel starts at ${zone.name}`}
                            className="h-4 w-4 rounded border-gray-300"
                          />
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad" | "warn";
  hint?: string;
}) {
  const color =
    tone === "bad" ? "text-red-700" : tone === "warn" ? "text-amber-700" : tone === "good" ? "text-green-700" : "text-navy-900";
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-3" title={hint}>
      <div className="text-xs font-medium uppercase tracking-wide text-gray-400">{label}</div>
      <div className={`mt-1 text-xl font-bold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}

/** The channel's load against its rating, with the design limit marked. */
function LoadBar({ fraction, limit, bar }: { fraction: number | null; limit: number; bar: string }) {
  if (fraction === null) return <span className="text-xs text-gray-400">—</span>;
  return (
    <div className="flex items-center gap-2">
      <div className="relative h-2 w-24 overflow-hidden rounded-full bg-gray-100">
        <div className={`h-full ${bar}`} style={{ width: `${Math.min(fraction, 1) * 100}%` }} />
        <div className="absolute inset-y-0 w-px bg-gray-700" style={{ left: `${limit * 100}%` }} />
      </div>
      <span className="text-xs tabular-nums text-gray-600">{formatPercent(fraction)}</span>
    </div>
  );
}
