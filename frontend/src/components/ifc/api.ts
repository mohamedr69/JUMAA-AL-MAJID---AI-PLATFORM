/** The BOQ as per IFC drawings' calls, on the platform's API (app/routers/ifc_boq.py).
 *
 * The same calls the original BOQ Extraction tool made, by the same names,
 * so its components are unchanged. What differs is where they go: a
 * drawing belongs to a project, so its calls carry the project; the device
 * types and the symbol library are the company's, shared by every project. */
import { API_BASE_URL, ApiError, api as platform, apiUrl } from '../../lib/api'
import type { Job } from '../../lib/useJob'
import type { Capabilities, Comparison, DeviceType, Drawing, DrawingSummary, ReviewAnswer } from './types'

/** A read of a drawing, as the job the server runs it in: `progress.stage`
 *  is where it is, `progress.eta_seconds` its estimate of the time left. */
export type ReadJob = Job & {
  progress: Job['progress'] & { stage?: string; eta_seconds?: number; file?: string }
  result: { drawing_id?: number; filename?: string; revision?: string; reference?: string
            engineer_review?: number; ai_note?: string | null
            archive?: string; drawings?: number
            read?: { drawing_id: number; filename: string; revision: string }[]
            failed?: { filename: string; reason: string }[]
            skipped?: string[]
            /** the same files as drawings already imported: nothing read again */
            unchanged?: { filename: string; drawing_id: number; revision: string }[]
            /** files that may revise a drawing in force without saying so: import them on their own */
            needs_confirmation?: { filename: string; drawing_id: number; existing: string; revision: string; reason: string }[] } | null
  /** On a start request: the same work was already queued or running (another click, another tab). */
  already_active?: boolean
}

/** A file that may be a revision of a drawing already imported: the engineer says which (HTTP 409). */
export interface RevisionQuestion {
  code: 'revision_confirmation_required'
  message: string
  uploaded: string
  candidates: { id: number; filename: string; revision: string; reference: string | null }[]
  suggested_revision: string
}

const drawings = (projectId: number) => `/projects/${projectId}/ifc-drawings`

/** POST a form with its upload progress (only XMLHttpRequest reports it). A
 *  refusal keeps the server's structured detail (`ApiError.code`, `.detail`),
 *  so the page can ask the question a 409 carries. */
function send(url: string, body: FormData, onSent: (loaded: number, total: number) => void, what: string): Promise<ReadJob> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE_URL}${url}`)
    xhr.withCredentials = true
    xhr.upload.onprogress = (e) => e.lengthComputable && onSent(e.loaded, e.total)
    xhr.onload = () => {
      let parsed: { detail?: unknown } & Partial<ReadJob> = {}
      try {
        parsed = JSON.parse(xhr.responseText)
      } catch {
        /* no JSON body */
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(parsed as ReadJob)
        return
      }
      const detail = parsed.detail
      if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
        const d = detail as Record<string, unknown>
        reject(new ApiError(xhr.status, String(d.message ?? xhr.statusText), typeof d.code === 'string' ? d.code : null, d))
      } else if (Array.isArray(detail)) {
        // a validation refusal: its first reason
        const first = detail[0] as { msg?: string } | undefined
        reject(new ApiError(xhr.status, first?.msg?.replace(/^Value error, /, '') ?? `${what} was refused`))
      } else {
        reject(new ApiError(xhr.status, typeof detail === 'string' ? detail : xhr.statusText || `${what} could not be sent`))
      }
    }
    xhr.onerror = () => reject(new ApiError(0, `${what} could not be sent: the platform did not answer`))
    xhr.send(body)
  })
}

export const api = {
  capabilities: () => platform.get<Capabilities>('/ifc/capabilities'),
  listDrawings: (projectId: number) => platform.get<DrawingSummary[]>(drawings(projectId)),
  getDrawing: (projectId: number, id: number) => platform.get<Drawing>(`${drawings(projectId)}/${id}`),
  deleteDrawing: (projectId: number, id: number) => platform.delete<void>(`${drawings(projectId)}/${id}`),
  uploadDrawing: async (projectId: number, file: File): Promise<Drawing> => {
    // A form upload, which the JSON helper does not send.
    const body = new FormData()
    body.append('file', file)
    const res = await fetch(`${API_BASE_URL}${drawings(projectId)}`, { method: 'POST', credentials: 'include', body })
    if (!res.ok) {
      let detail: unknown = res.statusText
      try {
        detail = (await res.json()).detail ?? detail
      } catch {
        /* no JSON body */
      }
      throw new ApiError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail))
    }
    return (await res.json()) as Drawing
  },
  /** Send the drawing and queue it for the IFC worker (HTTP 202); `onSent(loaded, total)` hears the upload's bytes.
   *  `confirm_new`: the engineer said a file that looks like a revision is a new drawing. */
  startRead: (
    projectId: number,
    file: File,
    issue: { revision: string; supersedes_id: number | null; confirm_new?: boolean },
    onSent: (loaded: number, total: number) => void,
  ): Promise<ReadJob> => {
    const body = new FormData()
    body.append('file', file)
    if (issue.revision) body.append('revision', issue.revision)
    if (issue.supersedes_id !== null) body.append('supersedes_id', String(issue.supersedes_id))
    if (issue.confirm_new) body.append('confirm_new', 'true')
    return send(`${drawings(projectId)}/jobs`, body, onSent, 'The drawing')
  },
  /** A zip of the building: every drawing in it is read, a floor at a
   *  time, as one job the tab follows like a single read. */
  startZipRead: (projectId: number, file: File, onSent: (loaded: number, total: number) => void): Promise<ReadJob> => {
    const body = new FormData()
    body.append('file', file)
    return send(`${drawings(projectId)}/zip/jobs`, body, onSent, 'The archive')
  },
  comparison: (projectId: number) => platform.get<Comparison>(`/projects/${projectId}/ifc-comparison`),
  getJob: (id: number) => platform.get<ReadJob>(`/jobs/${id}`),
  cancelJob: (id: number) => platform.post<ReadJob>(`/jobs/${id}/cancel`),
  runningRead: async (projectId: number): Promise<ReadJob | null> =>
    (await platform.get<ReadJob[]>(`/projects/${projectId}/jobs`)).find(
      (j) => (j.kind === 'ifc_read' || j.kind === 'ifc_read_zip') && (j.status === 'queued' || j.status === 'running'),
    ) ?? null,
  exportUrl: (projectId: number, id: number) => apiUrl(`${drawings(projectId)}/${id}/export`),
  setFloors: (projectId: number, id: number, sheet: string, multiplier: number | null) =>
    platform.put<Drawing>(`${drawings(projectId)}/${id}/floors`, { sheet, multiplier }),

  listDeviceTypes: () => platform.get<DeviceType[]>('/ifc/device-types'),
  createDeviceType: (body: { code: string; name: string; category: string; unit?: string; sort_order?: number }) =>
    platform.post<DeviceType>('/ifc/device-types', body),
  updateDeviceType: (id: number, body: Partial<DeviceType>) => platform.patch<DeviceType>(`/ifc/device-types/${id}`, body),

  verify: (
    projectId: number,
    body: { drawing_id: number; signatures: string[]; device_type_id?: number | null; ignore?: boolean; notes?: string },
  ) => {
    const { drawing_id, ...rest } = body
    return platform.post<Drawing>(`${drawings(projectId)}/${drawing_id}/verify`, rest)
  },
  saveReview: (projectId: number, drawing_id: number, answers: ReviewAnswer[]) =>
    platform.post<Drawing>(`${drawings(projectId)}/${drawing_id}/review`, { answers }),
  unverify: (signatures: string[]) => platform.post<{ deleted: number }>('/ifc/symbols/unverify', { signatures }),
}
