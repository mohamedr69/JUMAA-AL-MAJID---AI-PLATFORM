import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Card, ErrorBox, Spinner, Stat } from './ui'
import { api } from './api'
import type { Comparison, ComparisonFloor } from './types'

/** A difference, IFC less floor wise: a tick where they agree, a signed count where they do not. */
function Diff({ value }: { value: number }) {
  if (value === 0) return <span className="font-medium text-emerald-700">✓</span>
  return (
    <span
      className={`rounded px-1.5 py-0.5 font-semibold tabular-nums ${value > 0 ? 'bg-sky-50 text-sky-800' : 'bg-rose-50 text-rose-800'}`}
      title={value > 0 ? 'More on the IFC drawings than in the floor-wise BOQ' : 'Fewer on the IFC drawings than in the floor-wise BOQ'}
    >
      {value > 0 ? '+' : '−'}
      {Math.abs(value)}
    </span>
  )
}

type View = 'device' | 'floor'

/** The BOQ page's "Comparison" tab: BOQ Floor Wise beside BOQ as per IFC
 *  Drawings, fire alarm, device by device and floor by floor. The IFC side
 *  is the drawings in force, each at its latest revision. */
export default function ComparisonTab({ projectId }: { projectId: number }) {
  const [data, setData] = useState<Comparison | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [view, setView] = useState<View>('device')
  const [onlyDiff, setOnlyDiff] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const load = useCallback(() => {
    setLoading(true)
    setError('')
    api
      .comparison(projectId)
      .then(setData)
      .catch((e) => setError(`The comparison could not be made: ${e.message}`))
      .finally(() => setLoading(false))
  }, [projectId])

  useEffect(() => {
    load()
  }, [load])

  const floorsByKey = useMemo(() => new Map((data?.floors ?? []).map((f) => [f.key, f])), [data])

  const toggle = (key: string) =>
    setExpanded((s) => {
      const next = new Set(s)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  if (loading && !data) {
    return (
      <Card className="p-6">
        <Spinner label="Comparing the floor-wise BOQ with the IFC drawings…" />
      </Card>
    )
  }
  if (!data) return error ? <ErrorBox message={error} /> : null

  const noSchedule = data.schedule === null
  const noIfc = data.drawings.length === 0
  const t = data.totals
  const devices = onlyDiff ? data.devices.filter((d) => d.difference !== 0 || d.floors_differing > 0) : data.devices
  const floors = onlyDiff ? data.floors.filter((f) => f.devices_differing > 0) : data.floors
  const floorLabel = (f: ComparisonFloor) => f.label

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">BOQ Floor Wise vs BOQ as per IFC Drawings</h2>
          <p className="mt-1 text-sm text-slate-600">
            Fire alarm devices, floor by floor. Both are brought to the same device names (a wall and a ceiling speaker are both
            "Speaker") and the same floors ("Level 3" and a typical 3rd-to-16th plan both reach floor 3). Difference is IFC less floor wise.
          </p>
          <p className="mt-1 text-xs text-slate-500">
            Floor wise: {data.schedule ? `${data.schedule.file} (sheet ${data.schedule.sheet})` : 'not read yet'}
            {' · '}IFC:{' '}
            {data.drawings.length ? data.drawings.map((d) => `${d.filename} ${d.revision}`).join(', ') : 'no verified drawing yet'}
          </p>
        </div>
        <Button variant="secondary" onClick={load} disabled={loading}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </Button>
      </div>

      {error && <ErrorBox message={error} onClose={() => setError('')} />}
      {data.schedule_note && <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm text-slate-700">{data.schedule_note}</div>}

      {data.pending.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-900">
          Not in the comparison until its symbols are answered on the As per IFC Drawings tab:{' '}
          {data.pending.map((d) => `${d.filename} ${d.revision} (${d.review_required} to answer)`).join(', ')}.
        </div>
      )}
      {noSchedule && (
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm text-slate-700">
          No floor-wise BOQ has been read for this project: add it on the BOQ Floor Wise tab.
        </div>
      )}
      {noIfc && data.pending.length === 0 && (
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm text-slate-700">
          No IFC drawing has been imported for this project: import it on the As per IFC Drawings tab.
        </div>
      )}
      {data.swaps.length > 0 && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm text-rose-900">
          {data.swaps.map((s) => (
            <div key={`${s.more}-${s.fewer}`}>
              <span className="font-semibold">Looks swapped:</span> {s.more} is {s.qty} over and {s.fewer} {s.qty} under, on the same {s.floors}{' '}
              floor{s.floors > 1 ? 's' : ''}. A {s.fewer.toLowerCase()} symbol was most likely answered as {s.ifc_types.join(' / ') || s.more} on the As per
              IFC Drawings tab.
            </div>
          ))}
        </div>
      )}
      {data.unplaced.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-900">
          {data.unplaced.map((u) => (
            <div key={`${u.drawing}-${u.sheet}`}>
              {u.sheet} ({u.floor_name}) is counted for {u.multiplier} floors by hand, but its title names{' '}
              {u.floors_read.length ? `floors ${u.floors_read.join(', ')}` : 'no floor numbers'}: its {u.qty} devices are in the device totals and
              on no particular floor.
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Floor wise" value={t.schedule} />
        <Stat label="As per IFC" value={t.ifc} tone="blue" />
        <Stat label="Difference" value={t.difference > 0 ? `+${t.difference}` : t.difference} tone={t.difference === 0 ? 'green' : 'red'} />
        <Stat label="Devices agreeing" value={`${t.devices_matching} / ${t.devices}`} tone={t.devices_matching === t.devices ? 'green' : 'amber'} />
        <Stat label="Floors agreeing" value={`${t.floors_matching} / ${t.floors}`} tone={t.floors_matching === t.floors ? 'green' : 'amber'} />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex overflow-hidden rounded-lg border border-slate-300">
          {(['device', 'floor'] as View[]).map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={view === v}
              onClick={() => {
                setView(v)
                setExpanded(new Set())
              }}
              className={`px-4 py-1.5 text-sm font-medium ${view === v ? 'bg-brand-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}
            >
              {v === 'device' ? 'By device' : 'By floor'}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} />
          Only where they differ
        </label>
      </div>

      <Card>
        <div className="overflow-x-auto">
          {view === 'device' ? (
            <table className="min-w-full text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-2.5">Device</th>
                  <th className="px-4 py-2.5 text-right">Floor wise</th>
                  <th className="px-4 py-2.5 text-right">As per IFC</th>
                  <th className="px-4 py-2.5 text-right">Difference</th>
                  <th className="px-4 py-2.5 text-right">Floors differing</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {devices.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-4 py-6 text-center text-slate-500">
                      {onlyDiff ? 'Every device agrees.' : 'Nothing to compare yet.'}
                    </td>
                  </tr>
                )}
                {devices.map((d) => {
                  const open = expanded.has(d.device)
                  const cells = onlyDiff ? d.floors.filter((c) => c.difference !== 0) : d.floors
                  return (
                    <Fragment key={d.device}>
                      <tr className="cursor-pointer hover:bg-slate-50" onClick={() => toggle(d.device)}>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2 font-medium">
                            <span className="w-3 text-slate-400">{open ? '▾' : '▸'}</span>
                            {d.device}
                          </div>
                          <div className="ml-5 text-xs text-slate-500">
                            {d.schedule_lines.length > 0 ? d.schedule_lines.map((l) => l.description).join(' · ') : 'Not in the floor-wise BOQ'}
                            {' | IFC: '}
                            {d.ifc_types.length > 0 ? d.ifc_types.map((x) => x.code).join(', ') : 'none'}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums">{d.schedule_total}</td>
                        <td className="px-4 py-3 text-right tabular-nums">{d.ifc_total}</td>
                        <td className="px-4 py-3 text-right">
                          <Diff value={d.difference} />
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums">{d.floors_differing || <span className="text-slate-400">0</span>}</td>
                      </tr>
                      {open && (
                        <tr className="bg-slate-50/60">
                          <td colSpan={5} className="px-4 pb-4 pt-1">
                            <table className="ml-5 min-w-[28rem] text-xs">
                              <thead className="text-left uppercase tracking-wide text-slate-500">
                                <tr>
                                  <th className="py-1.5 pr-6">Floor</th>
                                  <th className="py-1.5 pr-6 text-right">Floor wise</th>
                                  <th className="py-1.5 pr-6 text-right">As per IFC</th>
                                  <th className="py-1.5 text-right">Difference</th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-slate-100">
                                {cells.map((c) => {
                                  const f = floorsByKey.get(c.floor)
                                  return (
                                    <tr key={c.floor}>
                                      <td className="py-1.5 pr-6" title={f ? [...f.schedule_names, ...f.ifc_names].join(' / ') : ''}>
                                        {f ? floorLabel(f) : c.floor}
                                      </td>
                                      <td className="py-1.5 pr-6 text-right tabular-nums">{c.schedule}</td>
                                      <td className="py-1.5 pr-6 text-right tabular-nums">{c.ifc}</td>
                                      <td className="py-1.5 text-right">
                                        <Diff value={c.difference} />
                                      </td>
                                    </tr>
                                  )
                                })}
                                {d.ifc_unplaced > 0 && (
                                  <tr>
                                    <td className="py-1.5 pr-6 text-amber-800">On no particular floor</td>
                                    <td className="py-1.5 pr-6 text-right">—</td>
                                    <td className="py-1.5 pr-6 text-right tabular-nums">{d.ifc_unplaced}</td>
                                    <td />
                                  </tr>
                                )}
                              </tbody>
                            </table>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          ) : (
            <table className="min-w-full text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-2.5">Floor</th>
                  <th className="px-4 py-2.5">Named</th>
                  <th className="px-4 py-2.5 text-right">Floor wise</th>
                  <th className="px-4 py-2.5 text-right">As per IFC</th>
                  <th className="px-4 py-2.5 text-right">Difference</th>
                  <th className="px-4 py-2.5 text-right">Devices differing</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {floors.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-6 text-center text-slate-500">
                      {onlyDiff ? 'Every floor agrees.' : 'Nothing to compare yet.'}
                    </td>
                  </tr>
                )}
                {floors.map((f) => {
                  const open = expanded.has(f.key)
                  const rows = data.devices
                    .map((d) => ({ device: d.device, cell: d.floors.find((c) => c.floor === f.key) }))
                    .filter((r) => r.cell && (!onlyDiff || r.cell.difference !== 0))
                  return (
                    <Fragment key={f.key}>
                      <tr className="cursor-pointer hover:bg-slate-50" onClick={() => toggle(f.key)}>
                        <td className="px-4 py-3 font-medium">
                          <span className="mr-2 inline-block w-3 text-slate-400">{open ? '▾' : '▸'}</span>
                          {f.label}
                        </td>
                        <td className="px-4 py-3 text-xs text-slate-500">
                          <div>Floor wise: {f.schedule_names.join(', ') || '—'}</div>
                          <div>IFC: {f.ifc_names.join(', ') || '—'}</div>
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums">{f.schedule}</td>
                        <td className="px-4 py-3 text-right tabular-nums">{f.ifc}</td>
                        <td className="px-4 py-3 text-right">
                          <Diff value={f.difference} />
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums">{f.devices_differing || <span className="text-slate-400">0</span>}</td>
                      </tr>
                      {open && (
                        <tr className="bg-slate-50/60">
                          <td colSpan={6} className="px-4 pb-4 pt-1">
                            <table className="ml-5 min-w-[28rem] text-xs">
                              <thead className="text-left uppercase tracking-wide text-slate-500">
                                <tr>
                                  <th className="py-1.5 pr-6">Device</th>
                                  <th className="py-1.5 pr-6 text-right">Floor wise</th>
                                  <th className="py-1.5 pr-6 text-right">As per IFC</th>
                                  <th className="py-1.5 text-right">Difference</th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-slate-100">
                                {rows.map(({ device, cell }) => (
                                  <tr key={device}>
                                    <td className="py-1.5 pr-6">{device}</td>
                                    <td className="py-1.5 pr-6 text-right tabular-nums">{cell!.schedule}</td>
                                    <td className="py-1.5 pr-6 text-right tabular-nums">{cell!.ifc}</td>
                                    <td className="py-1.5 text-right">
                                      <Diff value={cell!.difference} />
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </Card>

      {data.schedule_unnamed.length > 0 && (
        <p className="text-xs text-slate-500">
          Floor-wise lines that name no device the platform knows, and so are in neither side:{' '}
          {data.schedule_unnamed.map((u) => `${u.description} (${u.total})`).join(', ')}.
        </p>
      )}
    </div>
  )
}
