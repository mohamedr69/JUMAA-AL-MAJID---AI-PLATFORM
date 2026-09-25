import { useEffect, useMemo, useRef, useState } from 'react'
import type { DeviceType, Drawing, ReviewAnswer, ReviewKind, SymbolGroup } from './types'
import { DeviceTypeSelect } from './DeviceTypeSelect'
import { Button, Card, SymbolThumb, shortBlockName } from './ui'

const SECTIONS: { kind: ReviewKind; title: string; note: string; all?: string }[] = [
  {
    kind: 'answer',
    title: 'Needs your answer',
    note: 'No letters confirm a guess. A guess from the shape alone is shown, and one click puts it in the list. Pick the device and press Verify, or press Not a device.',
  },
  {
    kind: 'suggested',
    title: 'Suggested by the app',
    note: "The letters agree with a library symbol, so the app's guess is filled in. Press Verify to accept one, or change it first.",
    all: 'Accept all',
  },
  {
    kind: 'confirm',
    title: 'Matched to the library by resemblance',
    note: 'They look like a verified symbol with the same letters, so its device is filled in. Press Verify to confirm one, or change it first.',
    all: 'Confirm all',
  },
  {
    kind: 'architecture',
    title: 'Looks like architecture',
    note: "The architect's model: on its xref layers, or furniture and fittings by their Revit family name (sofa, bed, fridge, lift…). Press Not a device, or pick the device if one is real.",
    all: 'Mark all Not a device',
  },
  { kind: 'skipped', title: 'Skipped', note: 'Left out of the quantities and not saved to the library. Answer one to count it, or ask about it again.' },
  {
    kind: 'optional',
    title: 'Not asked',
    note: 'Not on a floor plan (riser, schematic, outside the sheets, off the architecture) or unlikely to be a device (no letters, not on a device layer). They are not counted either way. Answer any you like.',
  },
]

/** The device filled in for a symbol: the app's guess or the library match. */
function guessedDevice(g: SymbolGroup): number | '' {
  if (g.review === 'confirm') return g.device_type?.id ?? ''
  if (g.review === 'suggested' && !g.suggestion?.is_ignored) return g.suggestion?.device_type?.id ?? ''
  return ''
}

/** The filled-in answer a section's "accept all" saves; null when there is none. */
function filledIn(g: SymbolGroup, pick: number | ''): ReviewAnswer | null {
  if (g.review === 'architecture' || (g.review === 'suggested' && g.suggestion?.is_ignored)) return { signature: g.signature, ignore: true }
  if ((g.review === 'suggested' || g.review === 'confirm') && pick !== '') return { signature: g.signature, device_type_id: pick }
  return null
}

/** Step 1 and 2 before the quantities: every symbol on the floor plans that
 *  the library does not know exactly is asked. Each answer is saved to the
 *  library the moment it is given, and the row leaves the list; the last
 *  one opens the quantities. */
export default function ReviewTab({
  drawing,
  types,
  onSave,
  onUndo,
  onTypeAdded,
}: {
  drawing: Drawing
  types: DeviceType[]
  onSave: (answers: ReviewAnswer[]) => Promise<boolean>
  onUndo: (answers: ReviewAnswer[]) => Promise<boolean>
  onTypeAdded: (t: DeviceType) => void
}) {
  const asked = useMemo(() => drawing.groups.filter((g) => g.review), [drawing.groups])
  const [picks, setPicks] = useState<Record<string, number | ''>>({})
  const [saving, setSaving] = useState<Set<string>>(new Set())
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')
  const [bulkType, setBulkType] = useState<number | ''>('')
  const [open, setOpen] = useState<Partial<Record<ReviewKind, boolean>>>({})
  const [toast, setToast] = useState<{ text: string; answers: ReviewAnswer[] } | null>(null)
  const typeName = (id: number) => types.find((t) => t.id === id)?.code ?? 'device'

  const live = asked.filter((g) => !saving.has(g.signature)) // an answer being saved has left the list
  // progress since the page opened: the most there was to answer, and what is left
  const left = live.filter((g) => ['answer', 'suggested', 'confirm', 'architecture'].includes(g.review!)).length
  const start = useRef(left)
  start.current = Math.max(start.current, left)

  // answers used to wait in this browser until a Save button; they are saved at once now
  useEffect(() => {
    try {
      localStorage.removeItem(`boq-review-${drawing.id}`)
    } catch {
      /* storage blocked: nothing was kept */
    }
  }, [drawing.id])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 9000)
    return () => clearTimeout(t)
  }, [toast])

  const pick = (g: SymbolGroup) => (g.signature in picks ? picks[g.signature] : guessedDevice(g))
  const name = (g: SymbolGroup) => g.label || shortBlockName(Object.keys(g.block_names)[0] ?? 'symbol', 40)

  /** Save answers now: the rows leave the list at once, the server catches up in order. */
  const commit = async (answers: ReviewAnswer[], text: string) => {
    if (!answers.length) return
    // An answer against the symbol's own words ("M + S" on block MSS as a call point) is asked about first.
    const against = answers
      .map((a) => ({ g: asked.find((x) => x.signature === a.signature), t: types.find((t) => t.id === a.device_type_id) }))
      .filter(({ g, t }) => g && t && disagrees(g, t))
    if (
      against.length &&
      !confirm(
        `${against.length === 1 ? 'This answer disagrees' : `${against.length} answers disagree`} with the symbol's own name:\n\n` +
          against
            .slice(0, 8)
            .map(({ g, t }) => `• ${name(g!)} as ${t!.code} (${t!.family}), but its ${g!.name_hint!.reason} reads as ${g!.name_hint!.device_type.code} (${g!.name_hint!.family})`)
            .join('\n') +
          '\n\nSave anyway?',
      )
    )
      return
    const sigs = answers.map((a) => a.signature)
    setSaving((s) => new Set([...s, ...sigs]))
    setSelected((s) => new Set([...s].filter((x) => !sigs.includes(x))))
    const ok = await onSave(answers)
    setSaving((s) => new Set([...s].filter((x) => !sigs.includes(x))))
    if (ok) setToast({ text, answers })
  }
  const answerText = (a: ReviewAnswer) => (a.device_type_id ? `verified as ${typeName(a.device_type_id)}` : a.ignore ? 'Not a device' : a.skip ? 'skipped' : 'asked again')
  const one = (g: SymbolGroup, a: ReviewAnswer) =>
    commit([a], a.skip || (!a.device_type_id && !a.ignore) ? `${name(g)}: ${answerText(a)}.` : `${name(g)} saved to the library: ${answerText(a)}.`)

  const q = query.trim().toUpperCase()
  const matches = (g: SymbolGroup) =>
    !q ||
    g.label.includes(q) ||
    Object.keys(g.block_names).some((n) => n.toUpperCase().includes(q)) ||
    Object.keys(g.layers).some((n) => n.toUpperCase().includes(q))
  const sections = SECTIONS.map((s) => ({
    ...s,
    groups: live.filter((g) => g.review === s.kind && matches(g)).sort((a, b) => b.on_plans - a.on_plans || b.count - a.count),
    total: live.filter((g) => g.review === s.kind).length,
  })).filter((s) => s.total > 0)
  const isOpen = (kind: ReviewKind, n: number) => open[kind] ?? (kind === 'optional' ? false : kind === 'architecture' ? n <= 30 : true)
  const filled = live.filter((g) => ['suggested', 'confirm', 'architecture'].includes(g.review!)).map((g) => filledIn(g, pick(g))).filter((a): a is ReviewAnswer => a !== null)
  const needs = live.filter((g) => g.review === 'answer').length
  const sel = [...selected].filter((x) => live.some((g) => g.signature === x))

  if (!asked.length) {
    return <Card className="p-6 text-sm text-emerald-700">Every symbol on this drawing is in the library. The quantities are ready.</Card>
  }

  const done = start.current - left
  return (
    <div className="space-y-4">
      <Card className="sticky top-2 z-10 space-y-3 p-4 shadow-md">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0 flex-1 basis-72">
            {left > 0 ? (
              <>
                <div className="text-sm font-semibold">
                  {left} symbol{left === 1 ? '' : 's'} left to answer
                  <span className="font-normal text-slate-500">
                    {' '}
                    · {needs} need{needs === 1 ? 's' : ''} your answer, {left - needs} filled in for you to check
                    {saving.size > 0 && ` · saving ${saving.size}…`}
                  </span>
                </div>
                <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-slate-100">
                  <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${start.current ? (100 * done) / start.current : 0}%` }} />
                </div>
              </>
            ) : (
              <div className="text-sm text-emerald-700">Every symbol on the floor plans is answered and saved. The quantities are ready.</div>
            )}
            <div className="mt-1 text-xs text-slate-500">Each answer is saved to the symbol library the moment you give it.</div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search letters, block, layer…"
              className="w-56 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            />
            {filled.length > 0 && (
              <Button
                variant="success"
                onClick={() => commit(filled, `${filled.length} filled-in answer${filled.length === 1 ? '' : 's'} saved to the library.`)}
                title="Save every filled-in answer (the app's guesses, the library matches and the architecture) as it stands"
              >
                Save all {filled.length} filled-in answers
              </Button>
            )}
          </div>
        </div>
        {sel.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
            <span className="text-sm font-medium">{sel.length} selected:</span>
            <DeviceTypeSelect types={types} value={bulkType} onChange={setBulkType} className="max-w-72" onTypeAdded={onTypeAdded} />
            <Button
              disabled={bulkType === ''}
              onClick={() => bulkType !== '' && commit(sel.map((signature) => ({ signature, device_type_id: bulkType })), `${sel.length} symbols saved to the library as ${typeName(bulkType)}.`)}
            >
              Verify as this device
            </Button>
            <Button variant="secondary" onClick={() => commit(sel.map((signature) => ({ signature, ignore: true })), `${sel.length} symbols saved to the library as Not a device.`)}>
              Not a device
            </Button>
            <Button variant="secondary" onClick={() => commit(sel.map((signature) => ({ signature, skip: true })), `${sel.length} symbols skipped.`)}>
              Skip
            </Button>
            <Button variant="ghost" onClick={() => setSelected(new Set())}>
              Unselect
            </Button>
          </div>
        )}
      </Card>

      {sections.map((s) => {
        const shown = isOpen(s.kind, s.total)
        const sigs = s.groups.map((g) => g.signature)
        const all = sigs.length > 0 && sigs.every((x) => selected.has(x))
        const sectionFilled = s.all ? s.groups.map((g) => filledIn(g, pick(g))).filter((a): a is ReviewAnswer => a !== null) : []
        return (
          <section key={s.kind} className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={all}
                  disabled={!shown || !sigs.length}
                  onChange={() => {
                    const n = new Set(selected)
                    for (const x of sigs) {
                      if (all) n.delete(x)
                      else n.add(x)
                    }
                    setSelected(n)
                  }}
                  aria-label={`Select all in ${s.title}`}
                  className="h-4 w-4"
                />
                <h3 className="text-sm font-semibold text-slate-800">{s.title}</h3>
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{s.total}</span>
              </label>
              <div className="flex items-center gap-3">
                {sectionFilled.length > 0 && (
                  <Button
                    variant="secondary"
                    onClick={() => commit(sectionFilled, `${sectionFilled.length} symbol${sectionFilled.length === 1 ? '' : 's'} saved to the library.`)}
                  >
                    {s.all} {sectionFilled.length}
                  </Button>
                )}
                <button className="text-sm text-brand-700 hover:underline" onClick={() => setOpen({ ...open, [s.kind]: !shown })}>
                  {shown ? 'Hide' : 'Show'}
                </button>
              </div>
            </div>
            <p className="text-xs text-slate-500">{s.note}</p>
            {shown &&
              s.groups.map((g) => (
                <ReviewRow
                  key={g.signature}
                  g={g}
                  pick={pick(g)}
                  checked={selected.has(g.signature)}
                  onCheck={() => {
                    const n = new Set(selected)
                    if (n.has(g.signature)) n.delete(g.signature)
                    else n.add(g.signature)
                    setSelected(n)
                  }}
                  onPick={(id) => setPicks((p) => ({ ...p, [g.signature]: id }))}
                  onAnswer={(a) => one(g, a)}
                  types={types}
                  onTypeAdded={onTypeAdded}
                />
              ))}
            {shown && s.groups.length === 0 && <p className="text-xs italic text-slate-400">None match the search.</p>}
          </section>
        )
      })}

      {toast && (
        <div
          role="status"
          className="fixed bottom-4 left-1/2 z-20 flex max-w-[92vw] -translate-x-1/2 items-center gap-3 rounded-lg bg-slate-900 px-4 py-2.5 text-sm text-white shadow-lg"
          style={{ marginBottom: 'env(safe-area-inset-bottom, 0px)' }}
        >
          <span className="min-w-0 truncate">✓ {toast.text}</span>
          <button
            className="shrink-0 font-semibold text-sky-300 hover:text-sky-200"
            onClick={() => {
              const answers = toast.answers
              setToast(null)
              onUndo(answers)
            }}
          >
            Undo
          </button>
          <button className="shrink-0 text-slate-400 hover:text-white" onClick={() => setToast(null)} aria-label="Close">
            ✕
          </button>
        </div>
      )}
    </div>
  )
}

/** Why a symbol is in the engineer's queue, and what the AI said of it:
 *  its classification, confidence and reason code -- never its reasoning. */
function QueueReason({ g, types, pick, onPick }: { g: SymbolGroup; types: DeviceType[]; pick: number | ''; onPick: (id: number | '') => void }) {
  const ai = g.queue!.ai
  const said = ai?.decision === 'device' && ai.device_type_id ? types.find((t) => t.id === ai.device_type_id) : undefined
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-slate-600">
      <span className="rounded bg-amber-50 px-1.5 py-0.5 font-medium text-amber-800 ring-1 ring-inset ring-amber-600/20">{g.queue!.label}</span>
      {ai && ai.decision === 'uncertain' && <span>AI: not sure{ai.reason_code ? ` (${ai.reason_code.toLowerCase().replace(/_/g, ' ')})` : ''}</span>}
      {ai && ai.decision === 'not_device' && (
        <span>
          AI: not a device{ai.confidence != null && ` · ${Math.round(ai.confidence * 100)}%`}
          {ai.validation_reason && ` · ${ai.validation_reason}`}
        </span>
      )}
      {said && (
        <span>
          AI: {said.code} · {said.name}
          {ai!.confidence != null && ` · ${Math.round(ai!.confidence * 100)}%`}
          {ai!.validation_reason && ` · ${ai!.validation_reason}`}
          {pick !== said.id && (
            <button
              type="button"
              onClick={() => onPick(said.id)}
              className="ml-2 rounded border border-indigo-300 bg-indigo-50 px-1.5 py-0.5 font-medium text-indigo-900 hover:bg-indigo-100"
            >
              Use the AI's answer
            </button>
          )}
        </span>
      )}
    </div>
  )
}

/** The picked type is of another family than the symbol's own words name. */
function disagrees(g: SymbolGroup, t: DeviceType | undefined): boolean {
  return !!(g.name_hint && t?.family && t.family !== g.name_hint.family)
}

function ReviewRow({
  g,
  pick,
  checked,
  onCheck,
  onPick,
  onAnswer,
  types,
  onTypeAdded,
}: {
  g: SymbolGroup
  pick: number | ''
  checked: boolean
  onCheck: () => void
  onPick: (id: number | '') => void
  onAnswer: (a: ReviewAnswer) => void
  types: DeviceType[]
  onTypeAdded: (t: DeviceType) => void
}) {
  const names = Object.keys(g.block_names)
  const s = g.suggestion
  const architecture = g.review === 'architecture' || (g.review === 'suggested' && s?.is_ignored)
  const edge = g.review === 'answer' ? 'border-l-amber-400' : g.review === 'optional' || g.review === 'skipped' ? 'border-l-slate-200' : 'border-l-sky-400'
  return (
    <div className={`rounded-lg border border-l-4 border-slate-200 bg-white p-3 ${edge}`}>
      <div className="flex flex-wrap items-center gap-3">
        <input type="checkbox" checked={checked} onChange={onCheck} aria-label="Select symbol" className="h-4 w-4" />
        <SymbolThumb svg={g.svg} size={56} />
        <div className="min-w-0 flex-1 basis-60">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-lg font-semibold tabular-nums">{g.on_plans || g.count}×</span>
            <span className="text-xs text-slate-500">
              {g.on_plans ? (g.on_plans < g.count ? `on the floor plans (${g.count} in all)` : 'on the floor plans') : 'none on the floor plans'}
            </span>
            {g.label ? (
              <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-xs text-white">{g.label}</span>
            ) : (
              <span className="text-xs italic text-slate-400">no letters</span>
            )}
          </div>
          <div className="mt-0.5 truncate text-xs text-slate-600" title={names.join('\n')}>
            <span className="text-slate-400">Block: </span>
            {shortBlockName(names[0] ?? '', 80)}
            {names.length > 1 && <span className="text-slate-400"> +{names.length - 1} more names</span>}
          </div>
          <div className="truncate text-xs text-slate-500">
            <span className="text-slate-400">Layer: </span>
            {Object.keys(g.layers).slice(0, 2).join(', ')}
          </div>
          {g.review === 'confirm' && g.match && g.device_type && (
            <div className="mt-1 text-xs text-sky-800">
              Looks like the library's {g.device_type.code} symbol ({g.match.kind === 'family' ? 'same Revit family' : `${Math.round(g.match.score * 100)}% alike`})
            </div>
          )}
          {g.name_hint && !(s && !s.is_ignored && s.device_type?.id === g.name_hint.device_type.id) && (
            <div className="mt-1 text-xs text-sky-800">
              Its own name reads as {g.name_hint.device_type.code} · {g.name_hint.device_type.name} ({g.name_hint.reason})
              {pick !== g.name_hint.device_type.id && (
                <button
                  onClick={() => onPick(g.name_hint!.device_type.id)}
                  className="ml-2 rounded border border-sky-300 bg-sky-50 px-1.5 py-0.5 font-medium text-sky-900 hover:bg-sky-100"
                >
                  Use {g.name_hint.device_type.code}
                </button>
              )}
            </div>
          )}
          {g.queue && g.queue.reason !== 'resemblance_match' && <QueueReason g={g} types={types} pick={pick} onPick={onPick} />}
          {disagrees(g, types.find((t) => t.id === pick)) && (
            <div className="mt-1 rounded bg-rose-50 px-2 py-1 text-xs font-medium text-rose-800">
              Check: {types.find((t) => t.id === pick)?.code} is a {types.find((t) => t.id === pick)?.family}, but this symbol's {g.name_hint!.reason} reads as a{' '}
              {g.name_hint!.family} ({g.name_hint!.device_type.code}).
            </div>
          )}
          {s && g.review !== 'confirm' && (
            <div className="mt-1 text-xs text-amber-800">
              {g.label ? "App's guess" : "App's guess from the shape alone"}: {s.is_ignored ? 'not a device' : `${s.device_type?.code} · ${s.device_type?.name}`},{' '}
              {s.reason}
              {!s.is_ignored && s.device_type && pick !== s.device_type.id && (
                <button
                  onClick={() => onPick(s.device_type!.id)}
                  className="ml-2 rounded border border-amber-300 bg-amber-50 px-1.5 py-0.5 font-medium text-amber-900 hover:bg-amber-100"
                >
                  Use this guess
                </button>
              )}
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <DeviceTypeSelect
            types={types}
            value={pick}
            onChange={onPick}
            className="max-w-64"
            onTypeAdded={onTypeAdded}
            hint={{ code: g.label, category: s?.device_type?.category ?? g.device_type?.category, svg: g.svg }}
          />
          <Button disabled={pick === ''} onClick={() => pick !== '' && onAnswer({ signature: g.signature, device_type_id: pick })} title="Save it to the library as this device">
            Verify
          </Button>
          <button
            type="button"
            onClick={() => onAnswer({ signature: g.signature, ignore: true })}
            title="Save it to the library as not a device"
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
              architecture ? 'bg-slate-700 text-white hover:bg-slate-800' : 'border border-slate-300 bg-white text-slate-700 hover:bg-slate-50'
            }`}
          >
            Not a device
          </button>
          {g.review === 'skipped' ? (
            <Button variant="ghost" onClick={() => onAnswer({ signature: g.signature })} title="Put it back among the symbols to answer">
              Ask again
            </Button>
          ) : (
            <Button variant="ghost" onClick={() => onAnswer({ signature: g.signature, skip: true })} title="Leave it out of the quantities without saving it to the library">
              Skip
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
