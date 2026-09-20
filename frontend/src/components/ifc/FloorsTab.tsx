import { useMemo, useState } from 'react'
import { formatFloors } from './floors'
import type { Category, Drawing, SheetInfo } from './types'
import { Card } from './ui'

/** Floor by floor: each floor-plan sheet's device count, how many floors the
 *  plan stands for (read from its title block, editable), and the building
 *  total. Riser, schematic and detail sheets, and devices outside every
 *  sheet, are listed underneath and not counted. */
export default function FloorsTab({
  drawing,
  onSetFloors,
}: {
  drawing: Drawing
  onSetFloors: (sheet: string, multiplier: number | null) => void
}) {
  const [category, setCategory] = useState<Category>('fire_alarm')

  const { codes, names, cell } = useMemo(() => {
    const names = new Map<string, string>()
    const cell = new Map<string, number>()
    for (const g of drawing.groups) {
      if (g.status !== 'verified' || !g.device_type || g.device_type.category !== category) continue
      names.set(g.device_type.code, g.device_type.name)
      for (const [sheet, n] of Object.entries(g.by_sheet ?? {})) {
        const k = `${sheet}\u0000${g.device_type.code}`
        cell.set(k, (cell.get(k) ?? 0) + n)
      }
    }
    return { codes: [...names.keys()].sort(), names, cell }
  }, [drawing.groups, category])

  const get = (sheet: string, code: string) => cell.get(`${sheet}\u0000${code}`) ?? 0
  const plans = drawing.sheets.filter((s) => s.kind === 'plan')
  const others = drawing.sheets.filter((s) => s.kind !== 'plan' && codes.some((c) => get(s.name, c) > 0))
  const building = codes.map((c) => plans.reduce((sum, s) => sum + get(s.name, c) * s.multiplier, 0))

  return (
    <div className="space-y-3">
      <Card className="flex flex-wrap items-center justify-between gap-3 p-3">
        <div className="text-sm text-slate-600">
          {drawing.floor_info.mode === 'single'
            ? `Single floor: ${drawing.floor_info.floor_name}, read from the title block.`
            : `${plans.length} floor plan${plans.length > 1 ? 's' : ''} read from the title blocks. Each floor's count is multiplied by the floors the plan stands for.`}
        </div>
        <div className="flex gap-1 rounded-lg bg-slate-100 p-1">
          {/* fire alarm only for now: emergency lighting is the next step */ (['fire_alarm'] as Category[]).map((c) => (
            <button
              key={c}
              onClick={() => setCategory(c)}
              className={`rounded-md px-3 py-1 text-sm font-medium ${category === c ? 'bg-white text-brand-700 shadow-sm' : 'text-slate-600'}`}
            >
              {c === 'fire_alarm' ? 'Fire Alarm' : 'Emergency Lighting'}
            </button>
          ))}
        </div>
      </Card>

      {codes.length === 0 ? (
        <Card className="p-6 text-sm text-slate-500">Nothing verified in this category yet.</Card>
      ) : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="sticky left-0 bg-slate-50 px-3 py-2 text-left">Sheet / floor</th>
                  <th className="px-3 py-2 text-center" title="How many floors this plan stands for">
                    Floors
                  </th>
                  {codes.map((c) => (
                    <th key={c} className="px-2 py-2 text-right font-mono normal-case" title={names.get(c)}>
                      {c}
                    </th>
                  ))}
                  <th className="px-3 py-2 text-right">Per floor</th>
                  <th className="px-3 py-2 text-right">× Floors</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {plans.map((s) => {
                  const vals = codes.map((c) => get(s.name, c))
                  const perFloor = vals.reduce((a, b) => a + b, 0)
                  return (
                    <tr key={s.name} className="hover:bg-slate-50">
                      <td className="sticky left-0 bg-white px-3 py-2">
                        <div className="font-medium">{s.floor_name}</div>
                        <div className="text-xs text-slate-500">
                          {s.name} · {s.title}
                          {s.floors.length > 1 && ` · floors ${formatFloors(s.floors)}`}
                        </div>
                        {s.note && <div className="text-xs text-amber-700">{s.note}</div>}
                      </td>
                      <td className="px-3 py-2 text-center">
                        <FloorsInput key={`${s.name}-${s.multiplier}`} sheet={s} onSet={onSetFloors} />
                      </td>
                      {vals.map((v, i) => (
                        <td key={codes[i]} className={`px-2 py-2 text-right tabular-nums ${v ? '' : 'text-slate-300'}`}>
                          {v}
                        </td>
                      ))}
                      <td className="px-3 py-2 text-right tabular-nums text-slate-600">{perFloor}</td>
                      <td className="px-3 py-2 text-right font-semibold tabular-nums">{perFloor * s.multiplier}</td>
                    </tr>
                  )
                })}
                <tr className="bg-brand-50 font-semibold">
                  <td className="sticky left-0 bg-brand-50 px-3 py-2">Building total</td>
                  <td className="px-3 py-2 text-center tabular-nums">{plans.reduce((a, s) => a + s.multiplier, 0)}</td>
                  {building.map((v, i) => (
                    <td key={codes[i]} className="px-2 py-2 text-right tabular-nums">
                      {v}
                    </td>
                  ))}
                  <td />
                  <td className="px-3 py-2 text-right tabular-nums">{building.reduce((a, b) => a + b, 0)}</td>
                </tr>
                {others.map((s) => {
                  const vals = codes.map((c) => get(s.name, c))
                  return (
                    <tr key={s.name} className="bg-slate-50 text-slate-400">
                      <td className="sticky left-0 bg-slate-50 px-3 py-2">
                        <div>{s.title}</div>
                        <div className="text-xs">{s.kind === 'outside' ? 'not counted' : `${s.name} · schematic, not counted`}</div>
                      </td>
                      <td className="px-3 py-2 text-center">–</td>
                      {vals.map((v, i) => (
                        <td key={codes[i]} className="px-2 py-2 text-right tabular-nums line-through decoration-slate-300">
                          {v || ''}
                        </td>
                      ))}
                      <td className="px-3 py-2 text-right tabular-nums">{vals.reduce((a, b) => a + b, 0)}</td>
                      <td className="px-3 py-2 text-right">0</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}

function FloorsInput({ sheet, onSet }: { sheet: SheetInfo; onSet: (sheet: string, n: number | null) => void }) {
  const [v, setV] = useState(String(sheet.multiplier))
  const commit = () => {
    const n = Number(v)
    if (!Number.isInteger(n) || n < 0) {
      setV(String(sheet.multiplier))
      return
    }
    if (n !== sheet.multiplier) onSet(sheet.name, n === sheet.parsed_multiplier ? null : n)
  }
  return (
    <div className="flex items-center justify-center gap-1">
      <input
        value={v}
        onChange={(e) => setV(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
        inputMode="numeric"
        aria-label={`Floors for ${sheet.title}`}
        className={`w-14 rounded border px-1.5 py-0.5 text-center tabular-nums ${sheet.overridden ? 'border-amber-400 bg-amber-50' : 'border-slate-300'}`}
      />
      {sheet.overridden && (
        <button
          title={`Back to the title's reading (${sheet.parsed_multiplier})`}
          onClick={() => onSet(sheet.name, null)}
          className="text-xs text-amber-700 hover:underline"
        >
          reset
        </button>
      )}
    </div>
  )
}
