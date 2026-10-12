/**
 * Single fetch wrapper for the Flask `/api/v1` backend (Phase 2, task 2).
 *
 * Contract (02-api-contract.md): every response is an envelope —
 *   success: { success: true, data, request_id }
 *   error:   { success: false, error: { code, message, details?, request_id } }
 * Auth: Supabase access token attached as Bearer. On 401 we refresh the
 * session once and retry; a second 401 signs the user out. Tokens are never
 * logged or persisted outside the Supabase client (cookies via @supabase/ssr).
 */
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:5000";
const TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string | null;
  readonly details?: unknown;

  constructor(code: string, message: string, status: number, requestId: string | null, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
    this.details = details;
  }
}

export interface Paginated<T> {
  items: T[];
  pagination: { page: number; page_size: number; total: number; total_pages: number };
}

async function accessToken(): Promise<string | null> {
  const supabase = getSupabaseBrowserClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  return session?.access_token ?? null;
}

let refreshPromise: Promise<string | null> | null = null;

/** Refresh once across concurrent 401s (single-flight). */
function refreshTokenOnce(): Promise<string | null> {
  const existing: Promise<string | null> | null = refreshPromise;
  if (existing) return existing;
  const created: Promise<string | null> = getSupabaseBrowserClient()
    .auth.refreshSession()
    .then((result: { data: { session: { access_token: string } | null } }) =>
      result.data.session?.access_token ?? null,
    )
    .finally(() => {
      refreshPromise = null;
    });
  refreshPromise = created;
  return created;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  /** Caller-managed signal is merged with the 15 s timeout. */
  signal?: AbortSignal;
  /** internal: marks the post-refresh retry */
  _retried?: boolean;
}

interface Envelope {
  success: boolean;
  data?: unknown;
  pagination?: Paginated<unknown>["pagination"];
  error?: { code?: string; message?: string; details?: unknown };
}

/** Raw request: returns the parsed envelope (or throws ApiError). */
async function request(path: string, options: RequestOptions = {}): Promise<{ body: Envelope; requestId: string | null }> {
  const { method = "GET", body, signal, _retried } = options;
  const token = await accessToken();

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", () => controller.abort(), { once: true });
  }

  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      cache: "no-store",
    });
  } catch {
    clearTimeout(timeout);
    if (controller.signal.aborted && !signal?.aborted) {
      throw new ApiError("TIMEOUT", "The server took too long to respond.", 0, null);
    }
    throw new ApiError("NETWORK_ERROR", "Could not reach the server.", 0, null);
  } finally {
    clearTimeout(timeout);
  }

  const requestId = response.headers.get("X-Request-ID");
  const payload = (await response.json().catch(() => null)) as Envelope | null;

  if (response.ok && payload?.success) {
    return { body: payload, requestId };
  }

  const code = payload?.error?.code ?? "HTTP_" + response.status;
  const message = payload?.error?.message ?? "The request failed.";

  if (response.status === 401 && !_retried) {
    const fresh = await refreshTokenOnce();
    if (fresh) return request(path, { ...options, _retried: true });
    // Refresh failed: session is dead.
    await getSupabaseBrowserClient().auth.signOut();
    throw new ApiError("UNAUTHORIZED", "Your session has expired. Please sign in again.", 401, requestId);
  }

  throw new ApiError(code, message, response.status, requestId, payload?.error?.details);
}

export async function api<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body } = await request(path, options);
  return body.data as T;
}

/**
 * Multipart variant for file uploads (Phase 3: bulk import). The browser
 * sets the multipart boundary itself, so no Content-Type header is set.
 * `body` must be a FormData instance. `extraHeaders` carries optional
 * request semantics such as the import `Idempotency-Key`.
 *
 * Bulk import gets a caller-specific longer timeout (120 s): a 300-row
 * file legitimately outlasts the normal 15 s API budget. The global budget
 * stays 15 s for every other call — only callers that pass `timeoutMs`
 * opt out. No automatic retries: a timed-out commit must be verified and
 * replayed with the SAME content-derived Idempotency-Key.
 */
export const IMPORT_TIMEOUT_MS = 120_000;

export async function apiFormData<T = unknown>(
  path: string,
  body: FormData,
  extraHeaders?: Record<string, string>,
  timeoutMs: number = TIMEOUT_MS,
): Promise<T> {
  const token = await accessToken();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  const headers: Record<string, string> = { Accept: "application/json", ...extraHeaders };
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers,
      body,
      signal: controller.signal,
      cache: "no-store",
    });
  } catch {
    clearTimeout(timeout);
    if (controller.signal.aborted) {
      throw new ApiError("TIMEOUT", "The server took too long to respond.", 0, null);
    }
    throw new ApiError("NETWORK_ERROR", "Could not reach the server.", 0, null);
  } finally {
    clearTimeout(timeout);
  }

  const requestId = response.headers.get("X-Request-ID");
  const payload = (await response.json().catch(() => null)) as Envelope | null;

  if (response.ok && payload?.success) {
    return payload.data as T;
  }

  const code = payload?.error?.code ?? "HTTP_" + response.status;
  const message = payload?.error?.message ?? "The import failed.";
  throw new ApiError(code, message, response.status, requestId, payload?.error?.details);
}

/**
 * List endpoints use the paginated envelope: `data` is the array and
 * `pagination` is a sibling key (see app/core/__init__.py `paginated()`).
 */
export async function apiList<T>(path: string, options: RequestOptions = {}): Promise<Paginated<T>> {
  const { body } = await request(path, options);
  return {
    items: (body.data ?? []) as T[],
    pagination: body.pagination ?? { page: 1, page_size: 50, total: 0, total_pages: 1 },
  };
}

interface BlobRequestOptions {
  /** Caller-managed signal is merged with the 15 s timeout. */
  signal?: AbortSignal;
  /** internal: marks the post-refresh retry */
  _retried?: boolean;
}

/**
 * Binary variant (Phase 6): QR images and printable label-sheet PDFs.
 * Same auth / timeout / 401-refresh semantics as `request`, but the body
 * is returned as a Blob instead of being parsed as the JSON envelope.
 */
export async function apiBlob(path: string, options: BlobRequestOptions = {}): Promise<Blob> {
  const { signal, _retried } = options;
  const token = await accessToken();

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", () => controller.abort(), { once: true });
  }

  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method: "GET",
      headers,
      signal: controller.signal,
      cache: "no-store",
    });
  } catch {
    clearTimeout(timeout);
    if (controller.signal.aborted && !signal?.aborted) {
      throw new ApiError("TIMEOUT", "The server took too long to respond.", 0, null);
    }
    throw new ApiError("NETWORK_ERROR", "Could not reach the server.", 0, null);
  } finally {
    clearTimeout(timeout);
  }

  const requestId = response.headers.get("X-Request-ID");
  if (response.ok) return response.blob();

  // Binary endpoints still return the JSON error envelope on failure.
  const payload = (await response.json().catch(() => null)) as Envelope | null;
  const code = payload?.error?.code ?? "HTTP_" + response.status;
  const message = payload?.error?.message ?? "The download failed.";

  if (response.status === 401 && !_retried) {
    const fresh = await refreshTokenOnce();
    if (fresh) return apiBlob(path, { ...options, _retried: true });
    await getSupabaseBrowserClient().auth.signOut();
    throw new ApiError("UNAUTHORIZED", "Your session has expired. Please sign in again.", 401, requestId);
  }

  throw new ApiError(code, message, response.status, requestId, payload?.error?.details);
}
