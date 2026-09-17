import { apiFetch, ApiClientError } from "./api-client";

export { GATEWAY_BASE } from "./api-client";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function parseBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** Default ceiling for API calls so a hung backend cannot freeze the UI forever. */
export const DEFAULT_TIMEOUT_MS = 60000;

/** Low-level request. Throws ApiError with the server's detail message on failure. */
export async function req<T = unknown>(path: string, init?: RequestInit, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  // Honor a caller-provided signal too: aborting either side aborts the request.
  const onCallerAbort = () => ctrl.abort();
  init?.signal?.addEventListener("abort", onCallerAbort);
  if (init?.signal?.aborted) ctrl.abort();
  let res: Response;
  try {
    res = await apiFetch(path, { ...init, signal: ctrl.signal });
  } catch (err) {
    if (ctrl.signal.aborted && !init?.signal?.aborted) {
      throw new ApiError(0, `Request timed out after ${Math.round(timeoutMs / 1000)}s — the server may be busy.`);
    }
    throw new ApiError(err instanceof ApiClientError ? err.status : 0, err instanceof ApiClientError ? err.message : "Network error");
  } finally {
    clearTimeout(timer);
    init?.signal?.removeEventListener("abort", onCallerAbort);
  }
  const body = await parseBody(res);
  return body as T;
}

export function get<T = unknown>(path: string, timeoutMs?: number): Promise<T> {
  return req<T>(path, undefined, timeoutMs);
}

export function send<T = unknown>(path: string, method: string, payload?: unknown, timeoutMs?: number): Promise<T> {
  return req<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  }, timeoutMs);
}

/** Human-friendly message for catch blocks. */
export function errMsg(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 0) return err.message;
    if (err.status === 401) return "Not allowed — this action needs different permissions.";
    if (err.status === 403) return "Not allowed — this action needs admin rights or a feature flag.";
    if (err.status === 404) return "Not found — it may have been deleted.";
    return err.message;
  }
  return err instanceof Error ? err.message : "Something went wrong.";
}

/** Pick the first present key from a record (tolerates backend shape drift). */
export function pick<T>(obj: unknown, keys: string[], fallback: T): T {
  if (obj && typeof obj === "object") {
    const rec = obj as Record<string, unknown>;
    for (const k of keys) {
      if (rec[k] !== undefined && rec[k] !== null) return rec[k] as T;
    }
  }
  return fallback;
}

/** Coerce an unknown value to a record array from common envelope shapes. */
export function asList(body: unknown, keys: string[]): Array<Record<string, unknown>> {
  if (Array.isArray(body)) return body as Array<Record<string, unknown>>;
  for (const k of keys) {
    if (body && typeof body === "object" && Array.isArray((body as Record<string, unknown>)[k])) {
      return (body as Record<string, unknown>)[k] as Array<Record<string, unknown>>;
    }
  }
  return [];
}
