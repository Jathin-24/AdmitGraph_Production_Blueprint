/**
 * W5 endpoint surface — every endpoint the W5-owned pages need that
 * `api.ts` (W4-owned) does not expose, plus the small shared helpers those
 * pages reuse (URL sanitising, upload validation, 404 classification).
 *
 * All JSON reads go through the shared `apiFetch`/`API_BASE` exported from
 * `./api` (PLAN.md cross-WS contract), so auth headers, session-expiry
 * handling and error envelopes behave identically to the rest of the app.
 * Binary/multipart calls (file upload/download) build their own requests on
 * top of `API_BASE` + the stored bearer token for the same reason.
 */

import {
  API_BASE,
  ApiError,
  apiFetch,
  getEvidenceHealth,
  getToken,
  type EvidenceHealth,
  type StrategyDetail,
} from "./api";

/* ------------------------------------------------------------------ admin */

/** GET /admin/search-usage — aggregated search usage (never provider keys). */
export interface SearchUsage {
  total_searches: number;
  successful_searches: number;
  total_results: number;
  cache_hits: number;
  /** Mean duration in ms over successful searches; null when none succeeded. */
  avg_duration_ms: number | null;
  by_engine: Record<string, number>;
  by_status: Record<string, number>;
}

/** GET /admin/research-runs → one row of `items` (see backend admin.py). */
export interface ResearchRunItem {
  id: string;
  status: string;
  created_at: string;
  completed_at: string | null;
  error_message: string | null;
  steps_total: number;
  steps_failed: number;
  steps_partial: number;
}

export interface ResearchRunsPage {
  items: ResearchRunItem[];
  page: number;
  page_size: number;
  total: number;
  next_cursor: string | null;
}

export function getSearchUsage(): Promise<SearchUsage> {
  return apiFetch<SearchUsage>("/admin/search-usage");
}

export function getResearchRuns(page = 1, pageSize = 20): Promise<ResearchRunsPage> {
  return apiFetch<ResearchRunsPage>(
    `/admin/research-runs?page=${page}&page_size=${pageSize}`
  );
}

/* ------------------------------------------------------- password reset */

/** POST /auth/forgot {email} → always the same accepted shape (202), never
 *  revealing whether the address has an account (no user enumeration). */
export function requestPasswordReset(email: string): Promise<{ status?: string }> {
  return apiFetch<{ status?: string }>("/auth/forgot", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

/** POST /auth/reset {token, password} → 200 on success; 4xx with an
 *  expired/invalid-token error otherwise. */
export function resetPassword(token: string, password: string): Promise<{ status?: string }> {
  return apiFetch<{ status?: string }>("/auth/reset", {
    method: "POST",
    body: JSON.stringify({ token, password }),
  });
}

/* --------------------------------------------------- email verification */

/** POST /auth/verify {token} → 200 on success; 400 INVALID_TOKEN for an
 *  unknown, expired or already-used token (auth.py). */
export function verifyEmail(token: string): Promise<{ status?: string }> {
  return apiFetch<{ status?: string }>("/auth/verify", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

/** POST /auth/verify-request {email} → always the same accepted shape (202),
 *  never revealing whether the address has an account (no user enumeration). */
export function requestEmailVerification(email: string): Promise<{ status?: string }> {
  return apiFetch<{ status?: string }>("/auth/verify-request", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

/* ------------------------------------------------------- evidence health */

/**
 * The full evidence-health payload (MASTER_SPEC §17). `api.ts` types only the
 * base fields; the backend also returns `by_authority`, `unknowns` and
 * `conflicting_count` — widened here so api.ts (W4-owned) stays untouched.
 */
export interface EvidenceHealthFull extends EvidenceHealth {
  /** SourceAuthority enum keys → claim counts (OFFICIAL_UNIVERSITY, …). */
  by_authority: Record<string, number>;
  /** Claims whose source could not be verified (UNAVAILABLE) — never guessed. */
  unknowns: number;
  /** Claims whose sources disagree (by_status.CONFLICTING). */
  conflicting_count: number;
}

/** Same endpoint as `getEvidenceHealth` (api.ts stays the source of truth for
 *  the route) — only the result type is widened here. */
export async function getEvidenceHealthFull(strategyId: string): Promise<EvidenceHealthFull> {
  return (await getEvidenceHealth(strategyId)) as EvidenceHealthFull;
}

/* ------------------------------------------------------- roadmap evidence */

/** The api.ts StrategyDetail type drops `evidence_ids` even though the
 *  contract ships it (API_CONTRACT §Strategy roadmap) — re-widen locally. */
type RoadmapTask = StrategyDetail["roadmap_tasks"][number];
export type RoadmapTaskFull = RoadmapTask & {
  /** Evidence UUIDs backing this task (MASTER_SPEC §10 traceability). */
  evidence_ids?: string[];
};

/* ---------------------------------------------------------- document files */

/** Client-side mirror of the server's upload rules (≤10 MB, pdf/png/jpg/jpeg/docx). */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export const UPLOAD_EXTENSIONS = [".pdf", ".png", ".jpg", ".jpeg", ".docx"];

/** Returns a student-readable reason the file is rejected, or null if OK. */
export function uploadRejection(file: File): string | null {
  if (file.size > MAX_UPLOAD_BYTES) {
    return "That file is too large — the limit is 10 MB.";
  }
  const name = file.name.toLowerCase();
  if (!UPLOAD_EXTENSIONS.some((ext) => name.endsWith(ext))) {
    return "Unsupported format — use PDF, PNG, JPG or DOCX.";
  }
  return null;
}

/** True when a failed request means "this route doesn't exist on this server
 *  yet" (FastAPI's default 404 carries no error envelope / 405) rather than
 *  "the resource is missing". Lets the UI degrade gracefully while a backend
 *  endpoint is still being deployed. */
export function isRouteUnavailable(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  return error.status === 405 || (error.status === 404 && error.code === null);
}

/** True for the backend's own 404 envelope: route exists, resource doesn't. */
export function isMissingResource(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404 && error.code === "NOT_FOUND";
}

async function errorFromResponse(res: Response): Promise<ApiError> {
  let code: string | null = null;
  let message = `Request failed (${res.status})`;
  try {
    const body = await res.json();
    message = body?.error?.message ?? body?.detail ?? message;
    code = typeof body?.error?.code === "string" ? body.error.code : null;
  } catch {
    /* ignore */
  }
  return new ApiError(message, res.status, code);
}

function authInit(): { headers: Record<string, string> } {
  const token = getToken();
  return { headers: token ? { Authorization: `Bearer ${token}` } : {} };
}

/** POST multipart to `path`; resolves when the server accepts the file.
 *  Uses XHR so the caller can show real upload progress. */
function postMultipart(
  path: string,
  form: FormData,
  onProgress?: (percent: number) => void
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}${path}`);
    const token = getToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (event) => {
      if (onProgress && event.lengthComputable && event.total > 0) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        if (onProgress) onProgress(100);
        resolve();
        return;
      }
      let code: string | null = null;
      let message = `Request failed (${xhr.status})`;
      try {
        const body = JSON.parse(xhr.responseText);
        message = body?.error?.message ?? body?.detail ?? message;
        code = typeof body?.error?.code === "string" ? body.error.code : null;
      } catch {
        /* ignore */
      }
      reject(new ApiError(message, xhr.status, code));
    };
    xhr.onerror = () =>
      reject(new ApiError("Cannot reach the server — check that the backend is running, then try again.", 0, null));
    xhr.send(form);
  });
}

/**
 * POST the file for a document. Tries `POST /documents/{id}/file` first and
 * falls back to `POST /documents/{id}/upload` (PLAN.md's cross-WS contract
 * spelling) when the primary route doesn't exist yet, so the UI works against
 * either backend deployment order.
 */
export async function uploadDocumentFile(
  documentId: string,
  file: File,
  onProgress?: (percent: number) => void
): Promise<void> {
  const form = new FormData();
  form.append("file", file, file.name);
  try {
    await postMultipart(`/documents/${documentId}/file`, form, onProgress);
  } catch (error) {
    if (!isRouteUnavailable(error)) throw error;
    await postMultipart(`/documents/${documentId}/upload`, form, onProgress);
  }
}

/** GET the stored file. Throws ApiError 404 NOT_FOUND when no file was ever
 *  uploaded, or a route-unavailable error when the endpoint isn't deployed. */
export async function downloadDocumentFile(documentId: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}/documents/${documentId}/file`, {
    headers: authInit().headers,
  });
  if (!res.ok) throw await errorFromResponse(res);
  return res.blob();
}

/** DELETE the stored file (the document checklist row itself stays). */
export async function removeDocumentFile(documentId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/documents/${documentId}/file`, {
    method: "DELETE",
    headers: { ...authInit().headers },
  });
  if (!res.ok) throw await errorFromResponse(res);
}

/* -------------------------------------------------------------- url safety */

/**
 * Backend-provided absolute URLs (official_url, source_url): only http/https
 * survives `new URL()` — `javascript:`, `data:`, malformed or relative values
 * return null so callers render the raw text instead of an href.
 */
export function sanitizeHttpUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  try {
    const url = new URL(raw.trim());
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.toString();
  } catch {
    return null;
  }
}

/**
 * Notification-style links: same-origin paths (`/monitor`, never `//host`)
 * plus absolute http/https URLs. Anything else renders as plain text.
 */
export function sanitizeHref(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const value = raw.trim();
  if (value === "") return null;
  if (value.startsWith("//")) return null; // protocol-relative — off-site
  if (value.startsWith("/")) return value; // same-origin path
  return sanitizeHttpUrl(value);
}
