import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import DrawingView from './DrawingView'
import ReadProgress from './ReadProgress'
import { Button, Card, ErrorBox, Spinner } from './ui'
import { api, type ReadJob } from './api'
import type { Capabilities, DrawingSummary } from './types'

/** "R3" -> 3; anything else -> -1. */
function revNumber(revision: string): number {
  const m = /^R(\d+)$/i.exec(revision.trim())
  return m ? Number(m[1]) : -1
}

const NEW_DRAWING = -1

/** A drawing and its revisions, newest first: [in force, ..., first issue]. */
function revisionChains(drawings: DrawingSummary[]): DrawingSummary[][] {
  const byId = new Map(drawings.map((d) => [d.id, d]))
  return drawings
    .filter((d) => d.current)
    .map((head) => {
      const chain = [head]
      let at = head
      while (at.supersedes_id !== null && byId.has(at.supersedes_id)) {
        at = byId.get(at.supersedes_id)!
        chain.push(at)
      }
      return chain
    })
}

/** What a revision changed against the one before it, device by device. */
function changes(now: DrawingSummary, before: DrawingSummary | undefined): { code: string; delta: number }[] | null {
  if (!before || now.review_required > 0 || before.review_required > 0) return null
  const codes = new Set([...Object.keys(now.devices), ...Object.keys(before.devices)])
  return [...codes]
    .map((code) => ({ code, delta: (now.devices[code] ?? 0) - (before.devices[code] ?? 0) }))
    .filter((c) => c.delta !== 0)
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
}

function stem(name: string) {
  return name.replace(/\.(dwg|dxf)$/i, '').replace(/[\s_-]*(\(.*\)|R(EV)?\.?\s*\d+)\s*$/i, '').trim().toUpperCase()
}

/** What a zip import did, in one line: a file that could not be read, or
 *  one left behind, is named rather than quietly missing from the list. */
const readFromZip = (r: NonNullable<ReadJob['result']>) => {
  const floors = r.read?.length ?? 0
  const parts = [`Read ${floors} ${floors === 1 ? 'drawing' : 'drawings'} from ${r.archive ?? 'the archive'}.`]
  if (r.failed?.length) parts.push(`Could not read ${r.failed.map((f) => f.filename).join(', ')}.`)
  if (r.skipped?.length) parts.push(`Left behind ${r.skipped.length} file${r.skipped.length === 1 ? '' : 's'} that ${r.skipped.length === 1 ? 'is' : 'are'} not a drawing: ${r.skipped.join(', ')}.`)
  return parts.join(' ')
}

/** The BOQ page's "As per IFC Drawings" tab: the drawing in force opens by
 *  itself, on its Verify Symbols step until every symbol on its floor plans
 *  is answered. A new file is imported as a revision -- of the drawing
 *  already linked, at a later revision than it -- and the revisions stay
 *  listed, each with what it changed. */
export default function IfcBoqTab({ projectId, canEdit }: { projectId: number; canEdit: boolean }) {
  const [drawings, setDrawings] = useState<DrawingSummary[] | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const [error, setError] = useState('')
  // The drawing being sent and read: the upload's bytes, then the server's job.
  const [reading, setReading] = useState<{
    filename: string
    revision: string
    sent: { loaded: number; total: number; started: number } | null
    job: ReadJob | null
    polledAt: number
  } | null>(null)
  // A file chosen, waiting for its revision to be said.
  const [pending, setPending] = useState<{ file: File; supersedes: number; revision: string } | null>(null)
  const [notice, setNotice] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [caps, setCaps] = useState<Capabilities | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const zipInput = useRef<HTMLInputElement>(null)
  // The drawing in force opens by itself once; after "Back" the list stays.
  const autoOpened = useRef(false)

  const load = useCallback(
    () =>
      api
        .listDrawings(projectId)
        .then((list) => {
          setDrawings(list)
          return list
        })
        .catch((e) => {
          setError(`The drawings could not be loaded: ${e.message}`)
          return null
        }),
    [projectId],
  )

  useEffect(() => {
    setOpen(null)
    autoOpened.current = false
    let live = true
    load().then((list) => {
      if (!live || !list || autoOpened.current) return
      autoOpened.current = true
      // The latest drawing in force: what the tab is for.
      const latest = list.find((d) => d.current)
      if (latest) setOpen(latest.id)
    })
    api.capabilities().then(setCaps).catch(() => setCaps(null))
    // A read still going (started before a refresh, or by a colleague) is followed here.
    api
      .runningRead(projectId)
      .then((job) => {
        if (!live || !job) return
        autoOpened.current = true
        setOpen(null)
        setReading({ filename: job.progress.file ?? 'the drawing', revision: '', sent: null, job, polledAt: Date.now() })
      })
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [load, projectId])

  // Follow the read until it ends: the drawing it made opens by itself.
  const jobId = reading?.job?.id
  useEffect(() => {
    if (jobId === undefined) return
    let live = true
    const timer = setInterval(async () => {
      try {
        const job = await api.getJob(jobId)
        if (!live) return
        if (job.status === 'queued' || job.status === 'running') {
          setReading((r) => (r ? { ...r, job, polledAt: Date.now() } : r))
          return
        }
        clearInterval(timer)
        setReading(null)
        load()
        if (job.status === 'succeeded' && job.result?.read) {
          setNotice(readFromZip(job.result))
          // Start floor wise: the first floor opens on Verify Symbols,
          // the way a single import does.
          const first = job.result.read[0]
          if (first) setOpen(first.drawing_id)
        }
        else if (job.status === 'succeeded' && job.result?.drawing_id) setOpen(job.result.drawing_id)
        else if (job.status === 'cancelled') setNotice(
          job.kind === 'ifc_read_zip'
            ? `Stopped at ${job.progress.file ?? 'a floor'}: the floors read before it were kept.`
            : `Stopped reading ${job.progress.file ?? 'the drawing'}: nothing was kept.`)
        else setError((job.error ?? 'The drawing could not be read').replace(/^\w+(Error|Exception): /, ''))
      } catch (e) {
        if (live) setError(`Lost track of the read: ${(e as Error).message}`)
      }
    }, 1000)
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [jobId, load])

  const chains = useMemo(() => revisionChains(drawings ?? []), [drawings])
  const inForce = chains.map((c) => c[0])

  /** The revisions a file may be issued as: after the one it revises, or any for a new drawing. */
  const revisionOptions = (supersedes: number) => {
    const previous = inForce.find((d) => d.id === supersedes)
    const from = previous ? revNumber(previous.revision) + 1 : 0
    return Array.from({ length: previous ? 10 : 21 }, (_, i) => `R${from + i}`)
  }

  /** A zip is the building, not a revision of one drawing: every file in
   *  it is read as its own drawing, so there is nothing to choose first. */
  const uploadZip = async (file: File) => {
    setError('')
    setNotice('')
    setOpen(null)
    setReading({ filename: file.name, revision: '', sent: { loaded: 0, total: file.size, started: Date.now() }, job: null, polledAt: 0 })
    try {
      const job = await api.startZipRead(projectId, file, (loaded, total) =>
        setReading((r) => (r && r.sent ? { ...r, sent: { ...r.sent, loaded, total } } : r)),
      )
      setReading((r) => (r ? { ...r, sent: null, job, polledAt: Date.now() } : r))
    } catch (e) {
      setReading(null)
      setError((e as Error).message)
    }
  }

  const choose = (file: File) => {
    setError('')
    const lower = file.name.toLowerCase()
    if (lower.endsWith('.zip')) {
      void uploadZip(file)
      return
    }
    if (!lower.endsWith('.dxf') && !lower.endsWith('.dwg')) {
      setError('Upload a DWG or DXF drawing, or a zip of them.')
      return
    }
    if (lower.endsWith('.dwg') && caps && !caps.dwg) {
      setError('This PC has no DWG converter. Install AutoCAD or the free ODA File Converter, or upload a DXF.')
      return
    }
    setNotice('')
    // A revision of the drawing already linked: the one of the same name, or the only one.
    const same = inForce.find((d) => stem(d.filename) === stem(file.name))
    const target = same ?? (inForce.length === 1 ? inForce[0] : undefined)
    setPending({
      file,
      supersedes: target ? target.id : NEW_DRAWING,
      // The next revision is the likely one; a first import says its own.
      revision: target ? `R${revNumber(target.revision) + 1}` : '',
    })
  }

  const upload = async () => {
    if (!pending || !pending.revision) return
    const { file, supersedes, revision } = pending
    setPending(null)
    setOpen(null)
    setReading({ filename: file.name, revision, sent: { loaded: 0, total: file.size, started: Date.now() }, job: null, polledAt: 0 })
    try {
      const job = await api.startRead(projectId, file, { revision, supersedes_id: supersedes === NEW_DRAWING ? null : supersedes }, (loaded, total) =>
        setReading((r) => (r && r.sent ? { ...r, sent: { ...r.sent, loaded, total } } : r)),
      )
      setReading((r) => (r ? { ...r, sent: null, job, polledAt: Date.now() } : r))
    } catch (e) {
      setReading(null)
      setError((e as Error).message)
    }
  }

  const stop = async () => {
    if (!reading?.job) return
    try {
      const job = await api.cancelJob(reading.job.id)
      setReading((r) => (r ? { ...r, job, polledAt: Date.now() } : r))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const remove = async (d: DrawingSummary) => {
    const note = d.current && d.supersedes_id !== null ? ' The revision before it becomes the drawing in force again.' : ''
    if (!confirm(`Delete ${d.revision} of "${d.filename}"?${note} The verified symbols stay in the library, and the copy filed in the project folder stays there.`)) return
    await api.deleteDrawing(projectId, d.id).catch((e) => setError(e.message))
    load()
  }

  if (open !== null) {
    return (
      <DrawingView
        key={open}
        projectId={projectId}
        drawingId={open}
        canEdit={canEdit}
        onOpen={setOpen}
        onBack={() => {
          setOpen(null)
          load()
        }}
      />
    )
  }

  const pendingTarget = pending ? inForce.find((d) => d.id === pending.supersedes) : undefined

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold tracking-tight">Fire alarm IFC drawings</h2>
        <p className="mt-1 text-sm text-slate-600">
          Upload a fire alarm IFC drawing as DWG or DXF. Symbols are recognised by their drawing, not their block name. Before the
          quantities, the platform asks about every symbol on the floor plans that the symbol library does not know, and saves each answer
          to the library, so the next drawing, on any project, recognises it. A revised drawing is imported as the next revision of the
          one linked; the earlier revisions stay listed with what each changed. Emergency lighting comes next.
        </p>
      </div>

      {error && <ErrorBox message={error} onClose={() => setError('')} />}

      {notice && <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm text-slate-700">{notice}</div>}

      {reading ? (
        <ReadProgress
          filename={reading.revision ? `${reading.filename} (${reading.revision})` : reading.filename}
          sent={reading.sent}
          job={reading.job}
          polledAt={reading.polledAt}
          onStop={canEdit ? stop : undefined}
        />
      ) : pending ? (
        <Card className="space-y-4 p-5">
          <div>
            <div className="text-sm font-semibold">Import {pending.file.name}</div>
            <div className="text-xs text-slate-500">{(pending.file.size / 1e6).toFixed(1)} MB</div>
          </div>
          {inForce.length > 0 && (
            <label className="block text-sm">
              <span className="font-medium text-slate-700">This file is</span>
              <select
                value={pending.supersedes}
                onChange={(e) => {
                  const supersedes = Number(e.target.value)
                  const target = inForce.find((d) => d.id === supersedes)
                  setPending({ ...pending, supersedes, revision: target ? `R${revNumber(target.revision) + 1}` : '' })
                }}
                className="mt-1 block w-full max-w-xl rounded-md border border-slate-300 px-3 py-2 text-sm"
              >
                {inForce.map((d) => (
                  <option key={d.id} value={d.id}>
                    A revision of {d.filename} (now {d.revision})
                  </option>
                ))}
                <option value={NEW_DRAWING}>A new drawing, not a revision of one linked</option>
              </select>
            </label>
          )}
          <label className="block text-sm">
            <span className="font-medium text-slate-700">IFC revision</span>
            <select
              value={pending.revision}
              onChange={(e) => setPending({ ...pending, revision: e.target.value })}
              className="mt-1 block w-40 rounded-md border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="" disabled>
                Choose…
              </option>
              {revisionOptions(pending.supersedes).map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <span className="mt-1 block text-xs text-slate-500">
              {pendingTarget
                ? `${pendingTarget.revision} stays listed as superseded; its sheet floor counts and skipped symbols carry over.`
                : 'The revision the drawing was issued at, as on its title block.'}
            </span>
          </label>
          <div className="flex gap-2">
            <Button onClick={upload} disabled={!pending.revision}>
              {pending.revision ? `Import as ${pending.revision}` : 'Choose the revision'}
            </Button>
            <Button variant="ghost" onClick={() => setPending(null)}>
              Cancel
            </Button>
          </div>
        </Card>
      ) : (
        canEdit && (
          <div
            onDragOver={(e) => {
              e.preventDefault()
              setDragOver(true)
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragOver(false)
              const f = e.dataTransfer.files[0]
              if (f) choose(f)
            }}
            className={`flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed px-6 py-8 text-center transition ${
              dragOver ? 'border-brand-500 bg-brand-50' : 'border-slate-300 bg-white'
            }`}
          >
            {inForce.length > 0 && (
              <div className="w-full max-w-2xl rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-left text-sm text-amber-900">
                <span className="font-semibold">A drawing is already linked: </span>
                {inForce.map((d, i) => (
                  <span key={d.id}>
                    {i > 0 && ', '}
                    {d.filename} <span className="font-semibold">{d.revision}</span>
                  </span>
                ))}
                . A file you add now is imported as its revised revision
                {inForce.length === 1 && <> ({`R${revNumber(inForce[0].revision) + 1}`})</>}; you can say otherwise before it is read.
              </div>
            )}
            <div className="text-sm text-slate-600">{inForce.length > 0 ? 'Drag the revised DWG or DXF here, or' : 'Drag a DWG or DXF file here, or'}</div>
            <div className="flex flex-wrap items-center justify-center gap-2">
              <Button onClick={() => input.current?.click()}>{inForce.length > 0 ? 'Import revised revision' : 'Choose drawing'}</Button>
              <Button variant="ghost" onClick={() => zipInput.current?.click()}>
                Import a zip of all floors
              </Button>
            </div>
            <div className="text-xs text-slate-500">
              {caps === null
                ? ''
                : caps.dwg
                  ? `DWG files are converted to DXF by ${caps.dwg_converter}.`
                  : 'DWG needs AutoCAD or the ODA File Converter on the server PC. DXF always works.'}
              {' '}Symbols drawn without a block (exploded, or drawn in lines) are found by what they look
              like. A zip is read a file at a time, each one its own drawing; floors still come from the
              sheets inside them.
            </div>
            <input
              ref={zipInput}
              type="file"
              accept=".zip"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) choose(f)
                e.target.value = ''
              }}
            />
            <input
              ref={input}
              type="file"
              accept=".dwg,.dxf,.zip"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) choose(f)
                e.target.value = ''
              }}
            />
          </div>
        )
      )}

      {drawings === null ? (
        <Card className="p-6">
          <Spinner label="Loading…" />
        </Card>
      ) : chains.length === 0 ? (
        <Card className="p-6 text-sm text-slate-500">No IFC drawing has been uploaded for this project yet.</Card>
      ) : (
        chains.map((chain) => (
          <Card key={chain[0].id}>
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
              <div>
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Revision monitoring</div>
                <div className="font-medium">{chain[0].filename}</div>
              </div>
              <Button onClick={() => setOpen(chain[0].id)}>Open {chain[0].revision}</Button>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-4 py-2.5">Rev.</th>
                    <th className="px-4 py-2.5">Status</th>
                    <th className="px-4 py-2.5">Drawing</th>
                    <th className="px-4 py-2.5">Imported</th>
                    <th className="px-4 py-2.5 text-right">Fire alarm</th>
                    <th className="px-4 py-2.5">Change from the revision before</th>
                    <th className="px-4 py-2.5" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {chain.map((d, i) => {
                    const locked = d.review_required > 0 // no quantities until the symbols are answered
                    const before = chain[i + 1]
                    const delta = changes(d, before)
                    return (
                      <tr key={d.id} className={d.current ? 'hover:bg-slate-50' : 'text-slate-500 hover:bg-slate-50'}>
                        <td className="px-4 py-3">
                          <span className={`rounded-md px-2 py-0.5 text-xs font-semibold ${d.current ? 'bg-slate-800 text-white' : 'bg-slate-100 text-slate-600'}`}>
                            {d.revision}
                          </span>
                        </td>
                        <td className="whitespace-nowrap px-4 py-3">
                          {d.current ? (
                            <span className="text-xs font-medium text-emerald-700">In force</span>
                          ) : (
                            <span className="text-xs font-medium text-slate-500">Superseded</span>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <button type="button" onClick={() => setOpen(d.id)} className="text-left font-medium text-brand-700 hover:underline">
                            {d.filename}
                          </button>
                          {d.archive_path && <div className="text-xs text-slate-500">{d.archive_path}</div>}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3">{new Date(d.uploaded_at + 'Z').toLocaleString()}</td>
                        <td className="px-4 py-3 text-right tabular-nums">
                          {locked ? (
                            <button
                              type="button"
                              onClick={() => setOpen(d.id)}
                              className="whitespace-nowrap rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-600/25 hover:bg-amber-100"
                            >
                              {d.review_required} to answer
                            </button>
                          ) : (
                            d.totals.fire_alarm
                          )}
                        </td>
                        <td className="px-4 py-3 text-xs">
                          {!before ? (
                            <span className="text-slate-400">First issue</span>
                          ) : delta === null ? (
                            <span className="text-slate-400">Once both revisions are verified</span>
                          ) : delta.length === 0 ? (
                            <span className="text-slate-500">No change in quantities</span>
                          ) : (
                            <span className="flex flex-wrap gap-1">
                              {delta.map((c) => (
                                <span
                                  key={c.code}
                                  className={`rounded px-1.5 py-0.5 font-medium tabular-nums ${c.delta > 0 ? 'bg-sky-50 text-sky-800' : 'bg-rose-50 text-rose-800'}`}
                                >
                                  {c.code} {c.delta > 0 ? '+' : '−'}
                                  {Math.abs(c.delta)}
                                </span>
                              ))}
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-right">
                          {canEdit && (
                            <Button variant="ghost" onClick={() => remove(d)}>
                              Delete
                            </Button>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        ))
      )}
    </div>
  )
}
