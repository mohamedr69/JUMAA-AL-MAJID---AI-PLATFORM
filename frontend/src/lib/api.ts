export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";
/** An API address for the browser to open directly (a served PDF). */
export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}

export class ApiError extends Error {
  status: number;
  /** A machine-readable reason when the API gives one, e.g. "stale_write". */
  code: string | null;
  /** The structured detail, when the API sent an object rather than a sentence. */
  detail: Record<string, unknown> | null;
  constructor(status: number, message: string, code: string | null = null, detail: Record<string, unknown> | null = null) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }

  /** Someone else saved the same document after this page loaded it. */
  get isStaleWrite(): boolean {
    return this.status === 409 && this.code === "stale_write";
  }
}

async function errorFrom(res: Response): Promise<ApiError> {
  let message = res.statusText;
  let code: string | null = null;
  let detail: Record<string, unknown> | null = null;
  try {
    const body = await res.json();
    if (body.detail && typeof body.detail === "object" && !Array.isArray(body.detail)) {
      detail = body.detail as Record<string, unknown>;
      message = String(detail.message ?? message);
      code = detail.code ? String(detail.code) : null;
    } else if (Array.isArray(body.detail)) {
      // FastAPI validation errors: name the first field that failed.
      const first = body.detail[0];
      message = first ? `${(first.loc ?? []).slice(1).join(".")}: ${first.msg}` : message;
    } else {
      message = body.detail ?? message;
    }
  } catch {
    // no JSON body
  }
  return new ApiError(res.status, message, code, detail);
}

interface Versioned<T> {
  data: T;
  /** The document version the server reported (X-Resource-Version), if any. */
  version: number | null;
}

async function requestFull<T>(path: string, options: RequestInit = {}): Promise<Versioned<T>> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });

  if (!res.ok) throw await errorFrom(res);
  const header = res.headers.get("X-Resource-Version");
  const version = header !== null && header !== "" ? Number(header) : null;
  if (res.status === 204) return { data: undefined as T, version };
  return { data: (await res.json()) as T, version };
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  return (await requestFull<T>(path, options)).data;
}

/** The If-Match header for a save made against `version`; none when unknown. */
function ifMatch(version: number | null | undefined): Record<string, string> {
  return version === null || version === undefined ? {} : { "If-Match": `"${version}"` };
}

/** Fetch a file the API generates (an export) and hand it to the browser as a
 * download. Separate from `request`, which assumes a JSON reply. */
async function download(path: string, fallbackName: string, body?: unknown): Promise<Response> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    credentials: "include",
    // A file built from a request body -- the submittal package, whose
    // sections are chosen by the caller -- is a POST, not a GET.
    ...(body === undefined
      ? {}
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  if (!res.ok) throw await errorFrom(res);

  // Prefer the RFC 5987 form, which carries non-ASCII names intact.
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1];
  const plain = /filename="([^"]+)"/i.exec(disposition)?.[1];
  const filename = encoded ? decodeURIComponent(encoded) : (plain ?? fallbackName);

  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
  // Returned so a caller can read the headers the build reports on (page
  // count, how many documents were missing).
  return res;
}

/** A multipart upload. The browser sets the Content-Type itself, boundary
 * and all, so this cannot go through `request`. */
async function upload<T>(path: string, body: FormData): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, { method: "POST", credentials: "include", body });
  if (!res.ok) throw await errorFrom(res);
  return res.json() as Promise<T>;
}

export const api = {
  download,
  upload,
  get: <T>(path: string) => request<T>(path),
  /** A GET that also returns the document's version, for a later versioned save. */
  getVersioned: <T>(path: string) => requestFull<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown, version?: number | null) =>
    request<T>(path, {
      method: "PUT",
      body: body !== undefined ? JSON.stringify(body) : undefined,
      headers: ifMatch(version),
    }),
  /** A save made against `version`: refused with a stale-write ApiError if
   * someone else has saved since. Returns the new version. */
  putVersioned: <T>(path: string, body: unknown, version: number | null) =>
    requestFull<T>(path, { method: "PUT", body: JSON.stringify(body), headers: ifMatch(version) }),
  patch: <T>(path: string, body?: unknown, version?: number | null) =>
    request<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined, headers: ifMatch(version) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};
