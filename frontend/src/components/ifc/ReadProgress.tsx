import { useEffect, useState } from 'react'
import type { ReadJob } from './api'
import { Button, Card } from './ui'

/** What the read of a drawing is doing, in order: the server's stages
 *  (app/ifc/progress.py STAGES), after the upload itself. */
const STEPS: { key: string; label: string; dwgOnly?: boolean }[] = [
  { key: 'upload', label: 'Upload' },
  { key: 'convert', label: 'Convert DWG to DXF', dwgOnly: true },
  { key: 'read', label: 'Open the drawing' },
  { key: 'walk', label: 'Read the symbols' },
  { key: 'finish', label: 'Sheets and floors' },
  { key: 'file', label: 'File in the project folder' },
]

/** "About 25 s left", "About 2 min left". */
function timeLeft(seconds: number): string {
  if (seconds <= 1) return 'Almost done'
  if (seconds < 60) return `About ${Math.max(1, Math.round(seconds))} s left`
  const min = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `About ${min} min${s >= 5 ? ` ${s} s` : ''} left`
}

function mb(bytes: number): string {
  return `${(bytes / 1e6).toFixed(1)} MB`
}

/** The upload and the read of one drawing: a percentage, the step it is on,
 *  and the time left. The time left comes from the server's estimate at each
 *  poll and counts down in between, so it moves every second. */
export default function ReadProgress({
  filename,
  sent,
  job,
  polledAt,
  onStop,
}: {
  filename: string
  /** The upload: bytes sent and in all; null once the server has it all. */
  sent: { loaded: number; total: number; started: number } | null
  job: ReadJob | null
  /** When `job` was last heard from (ms), so its time left counts down between polls. */
  polledAt: number
  onStop?: () => void
}) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  const dwg = filename.toLowerCase().endsWith('.dwg')
  const steps = STEPS.filter((s) => dwg || !s.dwgOnly)
  const uploading = job === null
  const stage = uploading ? 'upload' : job.progress.stage === 'save' ? 'read' : (job.progress.stage ?? 'read')
  const current = steps.findIndex((s) => s.key === stage)

  let percent: number
  let left: number | null
  let detail: string
  if (uploading) {
    const fraction = sent && sent.total ? sent.loaded / sent.total : 0
    percent = Math.round(100 * fraction)
    const elapsed = sent ? (now - sent.started) / 1000 : 0
    left = fraction > 0.05 ? (elapsed * (1 - fraction)) / fraction : null
    detail = sent ? `Sending ${mb(sent.loaded)} of ${mb(sent.total)}` : 'Sending the drawing'
  } else {
    percent = job.progress.done ?? 0
    const eta = job.progress.eta_seconds
    left = eta === undefined ? null : Math.max(0, eta - (now - polledAt) / 1000)
    detail = job.progress.message ?? 'Reading the drawing'
  }

  return (
    <Card className="p-5">
      <div role="status" aria-live="polite" className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-slate-800">
              {uploading ? 'Uploading' : 'Reading'} {filename}
            </div>
            <div className="text-xs text-slate-500">{detail}</div>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs font-medium text-slate-600">{left === null ? 'Estimating the time…' : timeLeft(left)}</span>
            <span className="text-2xl font-semibold tabular-nums text-brand-700">{percent}%</span>
          </div>
        </div>
        <div
          className="h-2.5 overflow-hidden rounded-full bg-slate-100"
          role="progressbar"
          aria-label={`${uploading ? 'Uploading' : 'Reading'} ${filename}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <div className="h-full rounded-full bg-brand-600 transition-[width] duration-500 ease-out" style={{ width: `${percent}%` }} />
        </div>
        <ol className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {steps.map((s, i) => {
            const state = i < current ? 'done' : i === current ? 'now' : 'todo'
            return (
              <li
                key={s.key}
                className={`flex items-center gap-1.5 ${state === 'done' ? 'text-emerald-700' : state === 'now' ? 'font-semibold text-brand-700' : 'text-slate-400'}`}
              >
                <span aria-hidden="true">{state === 'done' ? '✓' : state === 'now' ? '●' : '○'}</span>
                {s.label}
              </li>
            )
          })}
        </ol>
        {onStop && !uploading && (
          <div className="flex items-center justify-between gap-3 border-t border-slate-100 pt-3">
            <span className="text-xs text-slate-500">
              {job?.cancel_requested
                ? 'Stopping at the end of this step…'
                : 'You can leave this tab: the read goes on, and is picked up here when you come back.'}
            </span>
            {!job?.cancel_requested && (
              <Button variant="secondary" onClick={onStop}>
                Stop
              </Button>
            )}
          </div>
        )}
      </div>
    </Card>
  )
}
