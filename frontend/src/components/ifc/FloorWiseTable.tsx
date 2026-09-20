import { Fragment } from 'react'
import { formatFloors } from './floors'
import type { Category, CategoryBoq } from './types'
import { Card } from './ui'

const NOUN: Record<Category, string> = {
  fire_alarm: 'fire alarm devices',
  emergency_light: 'emergency lighting',
  other: 'other devices',
}

/** The quantities floor by floor, in the drawing's sheet order: each floor
 *  plan's devices with the count on the plan, the floors the plan stands
 *  for and the product, a floor total, and the building total per device.
 *  `only` limits the list to one floor plan (its sheet name). */
export default function FloorWiseTable({
  category,
  boq,
  buildingFloors,
  only,
}: {
  category: Category
  boq: CategoryBoq
  buildingFloors: number
  only: string
}) {
  // S.No runs over the whole building, so a filtered floor keeps its numbers (same as the Excel)
  const serial = new Map<string, number>()
  let n = 0
  for (const f of boq.floors) for (const r of f.rows) serial.set(`${f.sheet} ${r.device_type.id}`, ++n)

  const withDevices = boq.floors.filter((f) => f.rows.length > 0)
  const shown = only ? withDevices.filter((f) => f.sheet === only) : withDevices
  const empty = boq.floors.filter((f) => f.rows.length === 0)

  if (!shown.length) {
    const f = boq.floors.find((x) => x.sheet === only)
    return (
      <Card className="p-6 text-sm text-slate-500">
        No {NOUN[category]} {f ? `on ${f.floor_name} (${f.sheet})` : 'on any floor plan'}.
      </Card>
    )
  }

  return (
    <div className="space-y-2">
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="w-12 px-4 py-2.5">#</th>
                <th className="px-4 py-2.5">Floor</th>
                <th className="px-4 py-2.5">Device</th>
                <th className="px-4 py-2.5">Code</th>
                <th className="px-4 py-2.5 text-right" title="Count on the floor plan">
                  Qty per floor
                </th>
                <th className="px-4 py-2.5 text-right" title="Floors the plan stands for, from its title block">
                  Floors
                </th>
                <th className="px-4 py-2.5 text-right">Total qty</th>
                <th className="px-4 py-2.5">Unit</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((f) => (
                <Fragment key={f.sheet}>
                  {f.rows.map((r, j) => (
                    <tr key={r.device_type.id} className={j === 0 ? 'border-t-2 border-slate-200' : 'border-t border-slate-100'}>
                      <td className="px-4 py-2 tabular-nums text-slate-500">{serial.get(`${f.sheet} ${r.device_type.id}`)}</td>
                      {j === 0 && (
                        <td rowSpan={f.rows.length + 1} className="min-w-44 border-r border-slate-100 px-4 py-2 align-top">
                          <div className="font-semibold text-slate-800">{f.floor_name}</div>
                          <div className="text-xs text-slate-500">{f.sheet}</div>
                          {f.multiplier !== 1 && (
                            <div className="mt-1 inline-flex rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-600/20">
                              {f.multiplier} floors
                            </div>
                          )}
                          {f.floors.length > 1 && <div className="mt-0.5 text-xs text-slate-500">floors {formatFloors(f.floors)}</div>}
                        </td>
                      )}
                      <td className="px-4 py-2 font-medium">{r.device_type.name}</td>
                      <td className="px-4 py-2 font-mono text-xs text-slate-600">{r.device_type.code}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{r.per_floor}</td>
                      <td className="px-4 py-2 text-right tabular-nums text-slate-400">× {f.multiplier}</td>
                      <td className="px-4 py-2 text-right text-base font-semibold tabular-nums">{r.qty}</td>
                      <td className="px-4 py-2 text-slate-500">{r.device_type.unit}</td>
                    </tr>
                  ))}
                  <tr className="border-t border-slate-100 bg-slate-50">
                    <td />
                    <td colSpan={2} className="px-4 py-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                      Floor total
                    </td>
                    <td className="px-4 py-1.5 text-right font-semibold tabular-nums">{f.per_floor}</td>
                    <td className="px-4 py-1.5 text-right tabular-nums text-slate-400">× {f.multiplier}</td>
                    <td className="px-4 py-1.5 text-right font-bold tabular-nums">{f.qty}</td>
                    <td />
                  </tr>
                </Fragment>
              ))}

              {!only &&
                boq.building.map((b, j) => (
                  <tr key={`building-${b.device_type.id}`} className={j === 0 ? 'border-t-4 border-slate-200' : 'border-t border-slate-100'}>
                    <td />
                    {j === 0 && (
                      <td rowSpan={boq.building.length + 1} className="border-r border-slate-100 bg-brand-50/60 px-4 py-2 align-top">
                        <div className="font-semibold text-brand-800">Building total</div>
                        <div className="text-xs text-slate-500">
                          {withDevices.length} floor plan{withDevices.length > 1 ? 's' : ''}, {buildingFloors} floors
                        </div>
                      </td>
                    )}
                    <td className="px-4 py-2 font-medium">{b.device_type.name}</td>
                    <td className="px-4 py-2 font-mono text-xs text-slate-600">{b.device_type.code}</td>
                    <td colSpan={2} />
                    <td className="px-4 py-2 text-right text-base font-semibold tabular-nums">{b.qty}</td>
                    <td className="px-4 py-2 text-slate-500">{b.device_type.unit}</td>
                  </tr>
                ))}
              {!only && (
                <tr className="border-t border-slate-200 bg-brand-50 font-semibold">
                  <td />
                  <td colSpan={2} className="px-4 py-2.5">
                    Total
                  </td>
                  <td colSpan={2} />
                  <td className="px-4 py-2.5 text-right text-base tabular-nums">{boq.qty}</td>
                  <td />
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
      {!only && empty.length > 0 && (
        <p className="px-1 text-xs text-slate-500">
          No {NOUN[category]} on {empty.map((f) => `${f.floor_name} (${f.sheet})`).join(', ')}.
        </p>
      )}
    </div>
  )
}
