import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { Category, DeviceType } from './types'
import { CATEGORY_LABEL } from './types'
import { Button, SymbolThumb } from './ui'

const NEW = 'new'
const CATEGORIES: Category[] = ['fire_alarm', 'emergency_light', 'other']

/** What a new device type would be for: the symbol's letters (a likely
 *  code), its category, and its drawing to show while naming it. */
export interface NewTypeHint {
  code?: string
  category?: Category
  svg?: string
}

/** Device type picker grouped by category. Given onTypeAdded it also offers
 *  "add a device not in this list": the new type is saved for good (in the
 *  database and the library file), handed to the page for its list, and
 *  selected here. */
export function DeviceTypeSelect({
  types,
  value,
  onChange,
  className = '',
  placeholder = 'Choose device type…',
  onTypeAdded,
  hint,
}: {
  types: DeviceType[]
  value: number | ''
  onChange: (id: number | '') => void
  className?: string
  placeholder?: string
  onTypeAdded?: (t: DeviceType) => void
  hint?: NewTypeHint
}) {
  const [adding, setAdding] = useState(false)
  return (
    <>
      <select
        value={value}
        onChange={(e) => {
          if (e.target.value === NEW) setAdding(true)
          else onChange(e.target.value ? Number(e.target.value) : '')
        }}
        className={`rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm ${className}`}
      >
        <option value="">{placeholder}</option>
        {onTypeAdded && <option value={NEW}>+ Add a device not in this list…</option>}
        {CATEGORIES.map((c) => {
          const list = types.filter((t) => t.category === c && t.is_active)
          if (!list.length) return null
          return (
            <optgroup key={c} label={CATEGORY_LABEL[c]}>
              {list.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.code} · {t.name}
                </option>
              ))}
            </optgroup>
          )
        })}
      </select>
      {adding && onTypeAdded && (
        <NewDeviceTypeDialog
          types={types}
          hint={hint}
          onClose={() => setAdding(false)}
          onSaved={(t) => {
            setAdding(false)
            onTypeAdded(t)
            onChange(t.id)
          }}
        />
      )}
    </>
  )
}

function NewDeviceTypeDialog({
  types,
  hint,
  onClose,
  onSaved,
}: {
  types: DeviceType[]
  hint?: NewTypeHint
  onClose: () => void
  onSaved: (t: DeviceType) => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const codeRef = useRef<HTMLInputElement>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  const [code, setCode] = useState(hint?.code ?? '')
  const [name, setName] = useState('')
  const [category, setCategory] = useState<Category>(hint?.category ?? 'fire_alarm')
  const [unit, setUnit] = useState('Nos')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    const d = ref.current
    if (d && !d.open) d.showModal()
    // the symbol's letters already fill the code: start at the name
    ;(hint?.code ? nameRef : codeRef).current?.focus()
  }, [hint?.code])

  const clean = code.trim().toUpperCase()
  const existing = clean ? types.find((t) => t.code.toUpperCase() === clean) : undefined
  const ready = clean !== '' && name.trim() !== '' && !existing && !saving

  const save = async (fn: () => Promise<DeviceType>) => {
    setSaving(true)
    setError('')
    try {
      onSaved(await fn())
    } catch (e) {
      setError((e as Error).message)
      setSaving(false)
    }
  }
  const close = () => ref.current?.close()

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-labelledby="new-device-type"
      className="m-auto w-[min(92vw,32rem)] rounded-xl bg-white p-0 text-slate-800 shadow-xl backdrop:bg-slate-900/40"
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (ready) save(() => api.createDeviceType({ code: clean, name: name.trim(), category, unit: unit.trim() || 'Nos' }))
        }}
        className="space-y-4 p-5"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="new-device-type" className="text-base font-semibold">
              Add a device type
            </h2>
            <p className="mt-0.5 text-xs text-slate-500">Saved for good: it stays in the device list for every drawing from now on.</p>
          </div>
          <button type="button" onClick={close} className="text-slate-400 hover:text-slate-700" aria-label="Close">
            ✕
          </button>
        </div>

        {hint?.svg && (
          <div className="flex items-center gap-3 rounded-lg bg-slate-50 p-2 text-sm text-slate-600">
            <SymbolThumb svg={hint.svg} size={52} />
            <span>
              For this symbol
              {hint.code ? (
                <>
                  {' '}
                  with the letters <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-xs text-white">{hint.code}</span>
                </>
              ) : (
                ' (no letters)'
              )}
            </span>
          </div>
        )}

        <div className="grid grid-cols-[8rem_1fr] gap-3">
          <label className="text-xs font-medium text-slate-600">
            Code
            <input
              ref={codeRef}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              maxLength={20}
              placeholder="e.g. LHD"
              className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1.5 font-mono text-sm uppercase"
            />
          </label>
          <label className="text-xs font-medium text-slate-600">
            Name (as in the BOQ)
            <input
              ref={nameRef}
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={120}
              placeholder="e.g. Linear Heat Detector"
              className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            />
          </label>
        </div>

        {existing && (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            <span>
              <span className="font-mono font-semibold">{existing.code}</span> is already in the list: {existing.name}
              {existing.is_active ? '' : ' (hidden)'}.
            </span>
            <Button
              variant="secondary"
              disabled={saving}
              onClick={() => save(async () => (existing.is_active ? existing : api.updateDeviceType(existing.id, { is_active: true })))}
            >
              Use {existing.code}
            </Button>
          </div>
        )}

        <div className="grid grid-cols-[1fr_8rem] gap-3">
          <label className="text-xs font-medium text-slate-600">
            Category (BOQ tab)
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value as Category)}
              className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_LABEL[c]}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs font-medium text-slate-600">
            Unit
            <input
              value={unit}
              onChange={(e) => setUnit(e.target.value)}
              maxLength={10}
              className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            />
          </label>
        </div>

        {error && (
          <p role="alert" className="text-sm text-rose-700">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" disabled={!ready}>
            {saving ? 'Saving…' : 'Add device type'}
          </Button>
        </div>
      </form>
    </dialog>
  )
}
