import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import FloorWiseTable from './FloorWiseTable'
import FloorsTab from './FloorsTab'
import ReviewTab from './ReviewTab'
import { DeviceTypeSelect, type NewTypeHint } from './DeviceTypeSelect'
import { Button, Card, ErrorBox, Spinner, Stat, StatusBadge, SymbolThumb, shortBlockName } from './ui'
import { api } from './api'
import type { Category, CategoryBoq, DeviceType, DeviceTypeRef, Drawing, FloorInfo, ReviewAnswer, ReviewInfo, SheetInfo, SymbolGroup } from './types'
import { withType } from './types'

type Tab = 'fire_alarm' | 'emergency_light' | 'other' | 'floors' | 'verify' | 'ignored'
const QUANTITY_TABS: Tab[] = ['fire_alarm', 'emergency_light', 'other', 'floors']

/** One IFC drawing, in the BOQ page's "As per IFC Drawings" tab: verify
 *  its symbols, then its quantities. The original tool's drawing page; here
 *  the drawing is one of a project's, and only fire alarm is counted for
 *  now -- emergency lighting is the next step. A viewer sees it all and
 *  answers nothing. */
export default function DrawingView({
  projectId,
  drawingId: id,
  canEdit,
  onBack,
  onOpen,
}: {
  projectId: number
  drawingId: number
  canEdit: boolean
  onBack: () => void
  /** Open another drawing: the revision in force, from a superseded one. */
  onOpen?: (id: number) => void
}) {
  const [drawing, setDrawing] = useState<Drawing | null>(null)
  const [types, setTypes] = useState<DeviceType[]>([])
  const [tab, setTab] = useState<Tab>('verify')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')

  useEffect(() => {
    // a load that finishes after the page moved on (or React's second dev-mode run) must not reset the tab the user picked
    let live = true
    Promise.all([api.getDrawing(projectId, id), api.listDeviceTypes()])
      .then(([d, t]) => {
        if (!live) return
        setDrawing(d)
        setTypes(t)
        // the quantities come once the symbols are verified
        setTab(d.review.ready ? 'fire_alarm' : 'verify')
      })
      .catch((e) => live && setError(e.message))
    return () => {
      live = false
    }
  }, [projectId, id])

  const run = useCallback(
    async (fn: () => Promise<Drawing | void>) => {
      setBusy(true)
      setError('')
      try {
        const d = await fn()
        setDrawing(d ?? (await api.getDrawing(projectId, id)))
      } catch (e) {
        setError((e as Error).message)
      } finally {
        setBusy(false)
      }
    },
    [projectId, id],
  )

  const verify = useCallback(
    (signatures: string[], deviceTypeId: number | null, ignore = false) =>
      run(() => api.verify(projectId, { drawing_id: id, signatures, device_type_id: deviceTypeId, ignore })),
    [projectId, id, run],
  )
  const unverify = useCallback((signatures: string[]) => run(async () => void (await api.unverify(signatures))), [run])
  const setFloors = useCallback((sheet: string, n: number | null) => run(() => api.setFloors(projectId, id, sheet, n)), [projectId, id, run])
  // a device type added from any dropdown joins every dropdown on the page
  const typeAdded = useCallback((t: DeviceType) => setTypes((ts) => withType(ts, t)), [])
  // step 2: each answer goes to the library as it is given. Requests run one after another, so the
  // last response is the latest state and the page never freezes while they do.
  const queue = useRef<Promise<unknown>>(Promise.resolve())
  const inQueue = useCallback(<T,>(fn: () => Promise<T>): Promise<T> => {
    const job = queue.current.then(fn)
    queue.current = job.catch(() => undefined)
    return job
  }, [])
  const saveReview = useCallback(
    async (answers: ReviewAnswer[]) => {
      setError('')
      try {
        const d = await inQueue(() => api.saveReview(projectId, id, answers))
        setDrawing(d)
        // step 3: nothing left to answer, the quantities are given
        if (d.review.ready) {
          setTab('fire_alarm')
          setNotice('Every symbol on the floor plans is answered and saved to the symbol library. The quantities are ready.')
          window.scrollTo({ top: 0, behavior: 'smooth' })
        }
        return true
      } catch (e) {
        setError((e as Error).message)
        return false
      }
    },
    [projectId, id, inQueue],
  )
  // take answers back: saved symbols leave the library (and are asked again), a skip is lifted
  const undoReview = useCallback(
    async (answers: ReviewAnswer[]) => {
      setError('')
      const saved = answers.filter((a) => a.device_type_id || a.ignore).map((a) => a.signature)
      const other = answers
        .filter((a) => !a.device_type_id && !a.ignore)
        .map((a) => (a.skip ? { signature: a.signature } : { signature: a.signature, skip: true }))
      try {
        const d = await inQueue(async () => {
          if (saved.length) await api.unverify(saved)
          return other.length ? api.saveReview(projectId, id, other) : api.getDrawing(projectId, id)
        })
        setDrawing(d)
        return true
      } catch (e) {
        setError((e as Error).message)
        return false
      }
    },
    [projectId, id, inQueue],
  )

  if (error && !drawing) return <ErrorBox message={error} />
  if (!drawing) return <Spinner label="Loading drawing…" />

  const t = drawing.totals
  const groups = drawing.groups
  const otherCount = groups.filter((g) => g.status === 'verified' && g.device_type?.category === 'other').length
  const review = drawing.review
  const ready = review.ready
  // no quantities until every symbol on the floor plans is answered and saved
  const view: Tab = !ready && QUANTITY_TABS.includes(tab) ? 'verify' : tab

  const tabs: { key: Tab; label: string; count: number | string; show: boolean; locked?: boolean }[] = [
    { key: 'verify', label: 'Verify Symbols', count: ready ? '✓' : review.required, show: true },
    { key: 'fire_alarm', label: 'Fire Alarm', count: t.fire_alarm, show: true, locked: !ready },
    { key: 'emergency_light', label: 'Emergency Lighting', count: t.emergency_light, show: false, locked: !ready },
    { key: 'other', label: 'Other', count: t.other, show: otherCount > 0, locked: !ready },
    { key: 'floors', label: 'Floors', count: drawing.sheets.filter((s) => s.kind === 'plan').length, show: true, locked: !ready },
    { key: 'ignored', label: 'Not Devices', count: t.ignored_symbols, show: true },
  ]

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <button type="button" onClick={onBack} className="text-sm text-brand-700 hover:underline">
            ← Drawings &amp; revisions
          </button>
          <h1 className="mt-1 flex flex-wrap items-center gap-2 break-words text-xl font-semibold tracking-tight">
            {drawing.filename}
            <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs font-semibold text-white">{drawing.revision}</span>
            {drawing.superseded_by ? (
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-300">Superseded</span>
            ) : (
              <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 ring-1 ring-inset ring-emerald-600/25">In force</span>
            )}
          </h1>
          {drawing.superseded_by && (
            <p className="mt-1 text-sm text-amber-800">
              This is an earlier revision: {drawing.superseded_by.revision} replaced it.{' '}
              {onOpen && (
                <button type="button" onClick={() => onOpen(drawing.superseded_by!.id)} className="font-medium text-brand-700 hover:underline">
                  Open {drawing.superseded_by.revision}
                </button>
              )}
            </p>
          )}
          {drawing.carried_over && (drawing.carried_over.floor_overrides > 0 || drawing.carried_over.review_skipped > 0) && (
            <p className="mt-0.5 text-xs text-slate-500">
              Kept from {drawing.carried_over.from}:{' '}
              {[
                drawing.carried_over.floor_overrides > 0 && `${drawing.carried_over.floor_overrides} sheet floor count${drawing.carried_over.floor_overrides > 1 ? 's' : ''}`,
                drawing.carried_over.review_skipped > 0 && `${drawing.carried_over.review_skipped} skipped symbol${drawing.carried_over.review_skipped > 1 ? 's' : ''}`,
              ]
                .filter(Boolean)
                .join(', ')}
              . Symbols answered before are recognised from the library.
            </p>
          )}
          <p className="mt-0.5 text-xs text-slate-500">
            {groups.length} distinct symbols · units {drawing.units} · read in {drawing.seconds.toFixed(1)} s
            {drawing.containers.length > 0 && ` · ${drawing.containers.length} container blocks opened`}
            {drawing.conversion && ` · converted from DWG by ${drawing.conversion.converter} in ${drawing.conversion.seconds.toFixed(1)} s`}
          </p>
          {drawing.archive_path ? (
            <p className="mt-0.5 text-xs text-slate-500">Filed in the project folder: {drawing.archive_path}</p>
          ) : (
            drawing.filed_note && <p className="mt-0.5 text-xs text-amber-700">{drawing.filed_note}</p>
          )}
        </div>
        {ready ? (
          <a href={api.exportUrl(projectId, drawing.id)} className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700">
            Export BOQ to Excel
          </a>
        ) : (
          <span
            title="Verify the symbols and save them to the library first"
            className="inline-flex cursor-not-allowed items-center gap-1.5 rounded-md bg-slate-200 px-3 py-1.5 text-sm font-medium text-slate-500"
          >
            <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className="h-3.5 w-3.5"><path fillRule="evenodd" d="M10 1a4.5 4.5 0 0 0-4.5 4.5V9H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6a2 2 0 0 0-2-2h-.5V5.5A4.5 4.5 0 0 0 10 1Zm3 8V5.5a3 3 0 1 0-6 0V9h6Z" clipRule="evenodd" /></svg>
            Export BOQ to Excel
          </span>
        )}
      </div>

      <FloorSummary drawing={drawing} />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Stat label="Fire alarm devices" value={ready ? t.fire_alarm : '—'} tone="blue" />
        <Stat label="Symbols to answer" value={review.required} tone={review.required ? 'amber' : 'green'} />
        <Stat label="Not asked (not counted)" value={review.optional + review.skipped} tone="slate" />
      </div>

      <ReviewSteps review={review} />
      {notice && <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-800">{notice}</div>}

      {ready && t.not_counted_instances > 0 && (
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm text-slate-700">
          {t.not_counted_instances} verified symbols are not counted: they are on riser, schematic or detail sheets, not placed on the
          architecture, or outside every sheet. The Floors tab lists them.
        </div>
      )}

      {error && <ErrorBox message={error} onClose={() => setError('')} />}

      {drawing.review.conflicts > 0 && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          <div className="font-semibold">
            {drawing.review.conflicts === 1 ? 'An answer disagrees' : `${drawing.review.conflicts} answers disagree`} with the symbol's own name. Check before using the quantities:
          </div>
          <ul className="mt-1.5 space-y-1">
            {groups
              .filter((g) => g.conflict && g.on_plans > 0)
              .map((g) => (
                <li key={g.signature} className="flex flex-wrap items-center gap-2">
                  <SymbolThumb svg={g.svg} size={28} />
                  <span>
                    <span className="font-medium">{g.label || shortBlockName(Object.keys(g.block_names)[0] ?? '', 40)}</span> ({g.boq_qty} in the BOQ): {g.conflict}.
                  </span>
                  {canEdit && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => unverify([g.signature]).then(() => setTab('verify'))}
                      className="rounded border border-rose-300 bg-white px-2 py-0.5 text-xs font-medium text-rose-800 hover:bg-rose-100"
                    >
                      Answer it again
                    </button>
                  )}
                </li>
              ))}
          </ul>
        </div>
      )}
      {!canEdit && (
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm text-slate-600">
          You can see this drawing's symbols and quantities. Verifying symbols is for the project's engineers.
        </div>
      )}

      <div role="tablist" className="flex flex-wrap gap-1 border-b border-slate-200">
        {tabs
          .filter((x) => x.show)
          .map((x) => (
            <button
              key={x.key}
              role="tab"
              aria-selected={view === x.key}
              disabled={x.locked}
              title={x.locked ? 'Verify the symbols and save them to the library first' : undefined}
              onClick={() => setTab(x.key)}
              className={`-mb-px inline-flex items-center gap-1 border-b-2 px-4 py-2 text-sm font-medium transition ${
                x.locked
                  ? 'cursor-not-allowed border-transparent text-slate-300'
                  : view === x.key
                    ? 'border-brand-600 text-brand-700'
                    : 'border-transparent text-slate-500 hover:text-slate-800'
              }`}
            >
              {x.locked && <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className="h-3.5 w-3.5"><path fillRule="evenodd" d="M10 1a4.5 4.5 0 0 0-4.5 4.5V9H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6a2 2 0 0 0-2-2h-.5V5.5A4.5 4.5 0 0 0 10 1Zm3 8V5.5a3 3 0 1 0-6 0V9h6Z" clipRule="evenodd" /></svg>}
              {x.label}
              {!x.locked && (
                <span className={`ml-1 rounded-full px-2 py-0.5 text-xs ${view === x.key ? 'bg-brand-100 text-brand-700' : 'bg-slate-100 text-slate-600'}`}>
                  {x.count}
                </span>
              )}
            </button>
          ))}
      </div>

      <fieldset disabled={!canEdit} className={busy ? 'pointer-events-none opacity-60' : ''}>
        {(view === 'fire_alarm' || view === 'emergency_light' || view === 'other') && (
          <QuantityTab
            category={view}
            groups={groups}
            types={types}
            floor={drawing.floor_info}
            boq={drawing.floor_boq[view]}
            sheets={drawing.sheets}
            onVerify={verify}
            onUnverify={unverify}
            onTypeAdded={typeAdded}
          />
        )}
        {view === 'floors' && <FloorsTab drawing={drawing} onSetFloors={setFloors} />}
        {view === 'verify' && (
          <ReviewTab key={drawing.id} drawing={drawing} types={types} onSave={saveReview} onUndo={undoReview} onTypeAdded={typeAdded} />
        )}
        {view === 'ignored' && <IgnoredTab groups={groups.filter((g) => g.status === 'ignored')} onUnverify={unverify} />}
      </fieldset>
    </div>
  )
}

/* ------------------------------------------------------------------ quantities */

function QuantityTab({
  category,
  groups,
  types,
  floor,
  boq,
  sheets,
  onVerify,
  onUnverify,
  onTypeAdded,
}: {
  category: Category
  groups: SymbolGroup[]
  types: DeviceType[]
  floor: FloorInfo
  boq: CategoryBoq
  sheets: SheetInfo[]
  onVerify: (sigs: string[], typeId: number | null, ignore?: boolean) => void
  onUnverify: (sigs: string[]) => void
  onTypeAdded: (t: DeviceType) => void
}) {
  const [open, setOpen] = useState<number | null>(null)
  // several floors: list the quantities floor by floor (default), or the building total with its symbols to check
  const [view, setView] = useState<'floors' | 'building'>('floors')
  const [only, setOnly] = useState('')
  const rows = useMemo(() => {
    const m = new Map<number, { dt: DeviceTypeRef; qty: number; groups: SymbolGroup[] }>()
    for (const g of groups) {
      if (g.status !== 'verified' || !g.device_type || g.device_type.category !== category) continue
      const r = m.get(g.device_type.id) ?? { dt: g.device_type, qty: 0, groups: [] }
      r.qty += g.boq_qty ?? g.count
      r.groups.push(g)
      m.set(g.device_type.id, r)
    }
    const order = new Map(types.map((t) => [t.id, t.sort_order]))
    // a device type whose symbols sit only on schematic sheets or off the plan has no quantity: no BOQ line
    return [...m.values()].filter((r) => r.qty > 0).sort((a, b) => (order.get(a.dt.id) ?? 0) - (order.get(b.dt.id) ?? 0))
  }, [groups, category, types])

  if (!rows.length) {
    return (
      <Card className="p-6 text-sm text-slate-500">
        Nothing verified in this category yet. Open the Verify Symbols tab and assign device types to the symbols on this drawing.
      </Card>
    )
  }
  const total = rows.reduce((s, r) => s + r.qty, 0)
  const multiple = floor.mode === 'multiple'
  const plans = boq.floors.filter((f) => f.rows.length > 0)
  return (
    <div className="space-y-3">
      {multiple && (
        <Card className="flex flex-wrap items-center justify-between gap-3 p-3">
          {view === 'floors' ? (
            <label className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
              <span className="font-medium text-slate-700">Floor</span>
              <select value={only} onChange={(e) => setOnly(e.target.value)} className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm">
                <option value="">All floors</option>
                {boq.floors.map((f) => (
                  <option key={f.sheet} value={f.sheet}>
                    {f.floor_name} · {f.sheet}
                    {f.multiplier !== 1 ? ` (${f.multiplier} floors)` : ''}
                  </option>
                ))}
              </select>
              <span className="text-xs text-slate-500">
                {plans.length} floor plan{plans.length > 1 ? 's' : ''} from the title blocks · a plan for several floors is multiplied
              </span>
            </label>
          ) : (
            <div className="text-sm text-slate-600">Each floor plan's count × the floors it stands for. Open a line to check its symbols.</div>
          )}
          <div className="flex gap-1 rounded-lg bg-slate-100 p-1">
            {(
              [
                ['floors', 'Floor by floor'],
                ['building', 'Building total'],
              ] as const
            ).map(([k, label]) => (
              <button
                key={k}
                onClick={() => setView(k)}
                aria-pressed={view === k}
                className={`rounded-md px-3 py-1 text-sm font-medium ${view === k ? 'bg-white text-brand-700 shadow-sm' : 'text-slate-600'}`}
              >
                {label}
              </button>
            ))}
          </div>
        </Card>
      )}
      {multiple && view === 'floors' ? (
        <FloorWiseTable category={category} boq={boq} buildingFloors={floor.floors} only={only} />
      ) : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="w-12 px-4 py-2.5">#</th>
                  {floor.mode === 'single' && <th className="px-4 py-2.5">Floor</th>}
                  <th className="px-4 py-2.5">Symbol</th>
                  <th className="px-4 py-2.5">Device</th>
                  <th className="px-4 py-2.5">Code</th>
                  <th className="px-4 py-2.5 text-right">{floor.mode === 'single' ? 'Qty' : `Qty (${floor.floors} floors)`}</th>
                  <th className="px-4 py-2.5">Unit</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {rows.map((r, i) => (
                  <QuantityRow
                    key={r.dt.id}
                    index={i + 1}
                    floorName={floor.mode === 'single' ? floor.floor_name ?? '' : null}
                    row={r}
                    open={open === r.dt.id}
                    onToggle={() => setOpen(open === r.dt.id ? null : r.dt.id)}
                    types={types}
                    sheets={sheets}
                    onVerify={onVerify}
                    onUnverify={onUnverify}
                    onTypeAdded={onTypeAdded}
                  />
                ))}
                <tr className="bg-slate-50 font-semibold">
                  <td className="px-4 py-2.5" colSpan={floor.mode === 'single' ? 5 : 4}>
                    Total
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{total}</td>
                  <td colSpan={2} />
                </tr>
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}

function QuantityRow({
  index,
  floorName,
  row,
  open,
  onToggle,
  types,
  sheets,
  onVerify,
  onUnverify,
  onTypeAdded,
}: {
  index: number
  floorName: string | null
  row: { dt: DeviceTypeRef; qty: number; groups: SymbolGroup[] }
  open: boolean
  onToggle: () => void
  types: DeviceType[]
  sheets: SheetInfo[]
  onVerify: (sigs: string[], typeId: number | null, ignore?: boolean) => void
  onUnverify: (sigs: string[]) => void
  onTypeAdded: (t: DeviceType) => void
}) {
  return (
    <>
      <tr className="cursor-pointer hover:bg-slate-50" onClick={onToggle}>
        <td className="px-4 py-2.5 text-slate-500">{index}</td>
        {floorName !== null && <td className="whitespace-nowrap px-4 py-2.5 font-medium text-slate-700">{floorName}</td>}
        <td className="px-4 py-2">
          <div className="flex gap-1">
            {row.groups.slice(0, 3).map((g) => (
              <SymbolThumb key={g.signature} svg={g.svg} size={36} />
            ))}
          </div>
        </td>
        <td className="px-4 py-2.5 font-medium">{row.dt.name}</td>
        <td className="px-4 py-2.5 font-mono text-xs text-slate-600">{row.dt.code}</td>
        <td className="px-4 py-2.5 text-right text-base font-semibold tabular-nums">{row.qty}</td>
        <td className="px-4 py-2.5 text-slate-500">{row.dt.unit}</td>
        <td className="px-4 py-2.5 text-right text-xs text-brand-700">{open ? 'Hide' : `${row.groups.length} symbol${row.groups.length > 1 ? 's' : ''}`}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={floorName !== null ? 8 : 7} className="bg-slate-50/70 px-4 py-3">
            <div className="space-y-2">
              {row.groups.map((g) => {
                const fromLibrary = g.match?.kind === 'library' || g.match?.kind === 'family'
                return (
                  <GroupLine key={g.signature} g={g} sheets={sheets}>
                    {fromLibrary && g.device_type && (
                      <Button variant="success" onClick={() => onVerify([g.signature], g.device_type!.id)} title="Save this exact drawing to the library as the same device">
                        Confirm
                      </Button>
                    )}
                    <ReassignControl
                      types={types}
                      current={g.device_type?.id}
                      onAssign={(tid) => onVerify([g.signature], tid)}
                      onTypeAdded={onTypeAdded}
                      hint={{ code: g.label, category: g.device_type?.category, svg: g.svg }}
                    />
                    {fromLibrary ? (
                      <Button variant="secondary" onClick={() => onVerify([g.signature], null, true)} title="This is not a device: remember it so it is not matched again">
                        Not a device
                      </Button>
                    ) : (
                      <Button variant="danger" onClick={() => onUnverify([g.signature])} title="Remove from library and send back for verification">
                        Unverify
                      </Button>
                    )}
                  </GroupLine>
                )
              })}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

function ReassignControl({
  types,
  current,
  onAssign,
  onTypeAdded,
  hint,
}: {
  types: DeviceType[]
  current?: number
  onAssign: (id: number) => void
  onTypeAdded: (t: DeviceType) => void
  hint: NewTypeHint
}) {
  const [v, setV] = useState<number | ''>('')
  return (
    <div className="flex items-center gap-1.5">
      <DeviceTypeSelect
        types={types.filter((t) => t.id !== current)}
        value={v}
        onChange={setV}
        placeholder="Change to…"
        className="max-w-56"
        onTypeAdded={onTypeAdded}
        hint={hint}
      />
      <Button variant="secondary" disabled={v === ''} onClick={() => v !== '' && onAssign(v)}>
        Change
      </Button>
    </div>
  )
}

/** One symbol group: preview, letters, count, where it came from. */
function GroupLine({
  g,
  sheets,
  children,
  leading,
}: {
  g: SymbolGroup
  /** given in the quantity tabs: the line then says what is counted and where the rest are */
  sheets?: SheetInfo[]
  children?: React.ReactNode
  leading?: React.ReactNode
}) {
  const [details, setDetails] = useState(false)
  const names = Object.entries(g.block_names).sort((a, b) => b[1] - a[1])
  const layers = Object.keys(g.layers)
  const left = sheets ? notCounted(g, sheets) : []
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="flex flex-wrap items-center gap-3">
        {leading}
        <SymbolThumb svg={g.svg} size={64} />
        <div className="min-w-0 flex-1 basis-64">
          <div className="flex flex-wrap items-center gap-2">
            {sheets ? (
              <span className="flex items-baseline gap-1.5">
                <span className="text-lg font-semibold tabular-nums">{g.boq_qty}</span>
                <span className="text-sm text-slate-600">
                  counted
                  {g.boq_qty !== g.on_plans && ` (${g.on_plans} on the floor plans × the floors each stands for)`}
                </span>
                {g.count !== g.on_plans && <span className="text-xs text-slate-400">of {g.count} in the drawing file</span>}
              </span>
            ) : (
              <span className="text-lg font-semibold tabular-nums">{g.count}×</span>
            )}
            {g.label ? (
              <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-xs text-white">{g.label}</span>
            ) : (
              <span className="text-xs italic text-slate-400">no letters</span>
            )}
            <StatusBadge status={g.status} />
            {g.match?.kind === 'library' && (
              <span
                className="inline-flex items-center rounded-full bg-sky-50 px-2 py-0.5 text-xs font-medium text-sky-700 ring-1 ring-inset ring-sky-600/20"
                title="Counted because it matches a verified library symbol with the same letters. Confirm it to save this exact drawing."
              >
                Library match {Math.round(g.match.score * 100)}%
              </span>
            )}
            {g.match?.kind === 'family' && (
              <span
                className="inline-flex items-center rounded-full bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-700 ring-1 ring-inset ring-violet-600/20"
                title="Counted because it is the same Revit family and type as a verified device, with the same letters, and its drawing lies on that device's drawing (part of it may be clipped). Confirm it to save this exact drawing."
              >
                Revit family match
              </span>
            )}
            {g.device_type && <span className="text-sm font-medium text-slate-700">{g.device_type.name}</span>}
          </div>
          {left.length > 0 && (
            <div className="mt-1 text-xs text-amber-800">
              <span className="font-medium">Not counted:</span> {left.join(' · ')}
            </div>
          )}
          <div className="mt-1 truncate text-xs text-slate-600" title={names.map((n) => n[0]).join('\n')}>
            <span className="text-slate-400">Block: </span>
            {shortBlockName(names[0]?.[0] ?? '', 90)}
            {names.length > 1 && <span className="text-slate-400"> +{names.length - 1} more names</span>}
          </div>
          <div className="text-xs text-slate-500">
            <span className="text-slate-400">Layer: </span>
            {layers.slice(0, 3).join(', ')}
            {g.direct_count < g.count && <span className="text-slate-400"> · {g.count - g.direct_count} inside container blocks</span>}
            <button onClick={() => setDetails(!details)} className="ml-2 text-brand-700 hover:underline">
              {details ? 'hide details' : 'details'}
            </button>
          </div>
        </div>
        {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
      </div>
      {details && (
        <div className="mt-3 grid gap-3 border-t border-slate-100 pt-3 text-xs md:grid-cols-2">
          <div>
            <div className="mb-1 font-medium text-slate-700">Block names ({names.length})</div>
            <ul className="max-h-40 space-y-0.5 overflow-auto font-mono text-slate-600">
              {names.map(([n, c]) => (
                <li key={n} className="break-all">
                  {c}× {n}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <div className="mb-1 font-medium text-slate-700">Made of</div>
            <div className="text-slate-600">
              {Object.entries(g.entity_counts)
                .map(([k, v]) => `${v} ${k}`)
                .join(', ')}
            </div>
            <div className="mb-1 mt-2 font-medium text-slate-700">First positions</div>
            <ul className="max-h-28 space-y-0.5 overflow-auto font-mono text-slate-600">
              {g.occurrences.slice(0, 12).map((o) => (
                <li key={o.handle + o.x}>
                  ({o.x.toFixed(0)}, {o.y.toFixed(0)}) {o.layer}
                  {o.rotation ? ` rot ${o.rotation}°` : ''}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ ignored */

function IgnoredTab({ groups, onUnverify }: { groups: SymbolGroup[]; onUnverify: (sigs: string[]) => void }) {
  if (!groups.length) return <Card className="p-6 text-sm text-slate-500">No symbols marked as not a device.</Card>
  return (
    <div className="space-y-2">
      {groups.map((g) => (
        <GroupLine key={g.signature} g={g}>
          <Button variant="secondary" onClick={() => onUnverify([g.signature])}>
            Send back to verify
          </Button>
        </GroupLine>
      ))}
    </div>
  )
}

/** What the drawing covers: one floor (named from its title block) or
 *  several floors, and what was left out. */
function FloorSummary({ drawing }: { drawing: Drawing }) {
  const f = drawing.floor_info
  return (
    <Card className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 text-sm">
      {f.mode === 'single' ? (
        <div>
          <span className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700 ring-1 ring-inset ring-emerald-600/20">Single floor</span>
          <span className="ml-2 font-semibold">{f.floor_name}</span>
          <span className="ml-1 text-slate-500">(from the title block)</span>
        </div>
      ) : (
        <div>
          <span className="rounded-full bg-brand-50 px-2.5 py-0.5 text-xs font-semibold text-brand-700 ring-1 ring-inset ring-brand-600/20">Multiple floors</span>
          <span className="ml-2 font-semibold">
            {f.plans === 1 ? `${f.floor_name}: one plan for ${f.floors} floors` : `${f.plans} floor plans, ${f.floors} floors`}
          </span>
          <span className="ml-1 text-slate-500">(quantities multiplied by the floors each plan stands for)</span>
        </div>
      )}
      {f.excluded_sheets.length > 0 && (
        <div className="text-slate-600">
          <span className="font-medium">Excluded:</span> {f.excluded_sheets.join(' · ')}
        </div>
      )}
      <div className={f.architecture_found ? 'text-slate-600' : 'text-amber-700'}>
        {f.architecture_found
          ? 'Only devices placed on the architecture are counted.'
          : 'No architecture found in this drawing: every device on the plan is counted.'}
      </div>
    </Card>
  )
}

/** Verify first, then quantities: where this drawing is. */
function ReviewSteps({ review }: { review: ReviewInfo }) {
  const steps = review.ready
    ? [
        { title: 'Verify symbols', text: 'Every symbol on the floor plans is answered' },
        { title: 'Save to library', text: 'Saved: the next drawing recognises them' },
        { title: 'Quantities', text: 'Ready in Fire Alarm and Floors, and in Excel' },
      ]
    : [
        { title: 'Verify symbols', text: `${review.required} symbol${review.required === 1 ? '' : 's'} on the floor plans to answer` },
        { title: 'Save to library', text: 'Each answer is saved the moment you give it' },
        { title: 'Quantities', text: 'Given once the answers are saved' },
      ]
  return (
    <ol className="grid gap-2 sm:grid-cols-3">
      {steps.map((s, i) => {
        const state = review.ready ? 'done' : i === 0 ? 'current' : 'todo'
        return (
          <li
            key={s.title}
            className={`flex items-center gap-3 rounded-xl border px-3 py-2.5 ${
              state === 'done' ? 'border-emerald-200 bg-emerald-50' : state === 'current' ? 'border-brand-300 bg-brand-50' : 'border-slate-200 bg-white'
            }`}
          >
            <span
              className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${
                state === 'done' ? 'bg-emerald-600 text-white' : state === 'current' ? 'bg-brand-600 text-white' : 'bg-slate-200 text-slate-500'
              }`}
            >
              {state === 'done' ? '✓' : i + 1}
            </span>
            <div className="min-w-0">
              <div className={`text-sm font-semibold ${state === 'todo' ? 'text-slate-500' : 'text-slate-800'}`}>{s.title}</div>
              <div className="text-xs text-slate-500">{s.text}</div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}

/** Where a symbol's copies are that the quantity leaves out, one phrase each. */
function notCounted(g: SymbolGroup, sheets: SheetInfo[]): string[] {
  const byName = new Map(sheets.map((s) => [s.name, s]))
  const on = Object.entries(g.by_sheet ?? {})
  const sum = (list: [string, number][]) => list.reduce((a, [, n]) => a + n, 0)
  const out: string[] = []
  const diagrams = on.filter(([s]) => byName.get(s)?.kind === 'diagram')
  if (diagrams.length) out.push(`${sum(diagrams)} on the schematic sheet${diagrams.length > 1 ? 's' : ''} ${diagrams.map(([s]) => s).join(', ')}`)
  const zero = on.filter(([s]) => byName.get(s)?.kind === 'plan' && byName.get(s)!.multiplier === 0)
  if (zero.length) out.push(`${sum(zero)} on plans set to 0 floors (${zero.map(([s]) => s).join(', ')})`)
  const offArch = g.by_sheet?.['(not on the architecture)'] ?? 0
  if (offArch) out.push(`${offArch} beside the plan, off the architecture (legend or stray copies)`)
  const outside = g.by_sheet?.['(outside sheets)'] ?? 0
  if (outside) out.push(`${outside} outside every sheet, in parts of the model that no layout shows`)
  return out
}
