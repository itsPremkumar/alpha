import { apiUrl, csrfToken } from "./api-client";

/**
 * Typed client for the 5 multimodal HTTP endpoints.
 *
 * Deliberately uses raw `fetch` (not `apiFetch`): `apiFetch` throws a generic
 * `ApiClientError` on any !ok response, which would discard the 503 body that
 * carries the honest `{capability, attempts, message}` chain. Every failure is
 * re-raised as `MultimodalError` preserving status, capability, and attempts —
 * the UI must always be able to show *why* a capability failed.
 */

export type ProbeStatus = "available" | "not_installed" | "not_configured" | "skipped_no_provider" | "probe_failed";

export interface CapabilityRow {
  capability: string;
  tier: string;
  engine: string;
  status: ProbeStatus;
  detail: string;
}

export interface VoiceWakeConfig {
  engine: string | null;
  threshold: number | null;
  armed_default: boolean | null;
}

export interface VoiceBlock {
  enabled: boolean;
  wake_word?: VoiceWakeConfig;
  tts?: { autoplay: boolean | null; voice: string | null };
  stt?: { model_size: string | null; language: string | null };
  detail?: string;
}

export interface CapabilitiesReport {
  rows: CapabilityRow[];
  voice: VoiceBlock;
  note: string;
}

/** One hop of the T1→T2→T3 chain, exactly as the backend recorded it. */
export interface AttemptRow {
  tier: string;
  engine: string;
  error: string;
  detail: string;
  retryable: boolean | null;
}

export interface SttResult {
  ok: boolean;
  text: string;
  language?: string | null;
  engine: string;
  tier: string;
  attempts: AttemptRow[];
  note?: string;
}

export interface OcrResult {
  ok: boolean;
  text: string;
  engine: string;
  tier: string;
  attempts: AttemptRow[];
  note?: string;
}

export interface ImageGenResult {
  ok: boolean;
  url?: string;
  b64?: string;
  engine: string;
  tier: string;
  attempts: AttemptRow[];
  note?: string;
}

/** Honest failure: HTTP status + capability + the real attempt chain (may be empty). */
export class MultimodalError extends Error {
  readonly status: number;
  readonly capability: string;
  readonly attempts: AttemptRow[];
  readonly detail?: string;

  constructor(status: number, capability: string, message: string, attempts: AttemptRow[] = [], detail?: string) {
    super(message);
    this.name = "MultimodalError";
    this.status = status;
    this.capability = capability;
    this.attempts = attempts;
    this.detail = detail;
  }

  /** Render the attempt chain: "T1/(configured): ImportError(...) → T2/edge-tts: ...". */
  formatAttempts(): string {
    return this.attempts.map((attempt) => `${attempt.tier}/${attempt.engine}: ${attempt.error}`).join(" → ");
  }
}

/** FastAPI nests `HTTPException(detail=...)` under a top-level "detail" key. */
async function errorFromResponse(response: Response, capability: string): Promise<MultimodalError> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return new MultimodalError(response.status, capability, `HTTP ${response.status} ${response.statusText}`.trim());
  }
  const outer = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : null;
  const detail = outer ? outer.detail : undefined;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const d = detail as Record<string, unknown>;
    const attempts = Array.isArray(d.attempts) ? (d.attempts as AttemptRow[]) : [];
    const message = typeof d.message === "string" && d.message ? d.message : `HTTP ${response.status}`;
    const cap = typeof d.capability === "string" ? d.capability : capability;
    const errorKind = typeof d.error === "string" ? d.error : undefined;
    return new MultimodalError(response.status, cap, message, attempts, errorKind);
  }
  if (typeof detail === "string" && detail) {
    return new MultimodalError(response.status, capability, detail);
  }
  if (Array.isArray(detail)) {
    // FastAPI request-validation shape: [{loc, msg, type}, ...].
    const message = detail
      .map((entry) => (entry && typeof entry === "object" && "msg" in entry ? String((entry as { msg: unknown }).msg) : JSON.stringify(entry)))
      .join("; ");
    if (message) return new MultimodalError(response.status, capability, message);
  }
  return new MultimodalError(response.status, capability, `HTTP ${response.status} ${response.statusText}`.trim());
}

/** Mirrors apiFetch (CSRF cookie, credentials, no redirects) but keeps error bodies. */
async function request(path: string, capability: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method || "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method) && !headers.has("X-CSRF-Token")) {
    const cookie = typeof document === "undefined" ? "" : document.cookie;
    const token = csrfToken(cookie);
    if (token) headers.set("X-CSRF-Token", token);
  }
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      ...init,
      method,
      headers,
      credentials: "include",
      redirect: "error",
    });
  } catch {
    throw new MultimodalError(0, capability, "The request could not be completed. Check your connection.");
  }
  if (!response.ok) throw await errorFromResponse(response, capability);
  return response;
}

/** GET /api/multimodal/capabilities — honest availability matrix + observed voice config. */
export async function getCapabilities(): Promise<CapabilitiesReport> {
  const response = await request("/api/multimodal/capabilities", "capabilities");
  return (await response.json()) as CapabilitiesReport;
}

export interface TtsAudio {
  blob: Blob;
  engine: string | null;
  tier: string | null;
  mediaType: string;
}

/**
 * POST /api/multimodal/tts → binary audio + X-Alpha-Engine / X-Alpha-Tier headers.
 * Never returns an empty blob: zero-byte responses are raised as exhaustion.
 */
export async function synthesizeSpeech(text: string, options: { voice?: string; engine?: string } = {}): Promise<TtsAudio> {
  const response = await request(
    "/api/multimodal/tts",
    "tts",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        ...(options.voice ? { voice: options.voice } : {}),
        ...(options.engine ? { engine: options.engine } : {}),
      }),
    },
  );
  const blob = await response.blob();
  if (blob.size === 0) {
    throw new MultimodalError(response.status || 503, "tts", "serving engine returned 0 audio bytes");
  }
  return {
    blob,
    engine: response.headers.get("X-Alpha-Engine"),
    tier: response.headers.get("X-Alpha-Tier"),
    mediaType: blob.type || response.headers.get("Content-Type") || "application/octet-stream",
  };
}

/** POST /api/multimodal/stt — multipart `audio` (suffix decides codec; see backend limits). */
export async function transcribeUpload(file: Blob, filename = "audio.webm"): Promise<SttResult> {
  const form = new FormData();
  form.append("audio", file, filename);
  const response = await request("/api/multimodal/stt", "stt", { method: "POST", body: form });
  return (await response.json()) as SttResult;
}

/** POST /api/multimodal/ocr — multipart `image` → extracted text (rapidocr → tesseract → vision). */
export async function recognizeText(file: Blob, filename = "image.png"): Promise<OcrResult> {
  const form = new FormData();
  form.append("image", file, filename);
  const response = await request("/api/multimodal/ocr", "ocr", { method: "POST", body: form });
  return (await response.json()) as OcrResult;
}

/** POST /api/multimodal/image-gen — {url|b64} or a 503 carrying the attempt chain. */
export async function generateImage(prompt: string, options: { size?: string } = {}): Promise<ImageGenResult> {
  const response = await request(
    "/api/multimodal/image-gen",
    "image_gen",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options.size ? { prompt, size: options.size } : { prompt }),
    },
  );
  return (await response.json()) as ImageGenResult;
}
