import { get, send } from "./http";

/**
 * Live unified execution mode as reported by ``GET /api/plan-mode/mode``.
 *
 * Fields are passed through exactly as the gateway sent them: an empty
 * ``updated_at``/``actor`` on a default record stays empty and a server note
 * (for example "default: no persisted mode found") is preserved verbatim.
 * Nothing here is ever backfilled with a plausible-looking value.
 */
export interface ExecutionModeStatus {
  /** Raw mode string from the gateway (e.g. work.normal, work.plan, code.normal, code.plan). */
  mode: string;
  /** Where the mode came from: "context" | "persisted" | "default" (server-defined). */
  source: string;
  /** Honest note from disk (or the default/corruption note). */
  note: string;
  /** True only when the gateway re-read the file and found this mode. */
  persisted: boolean;
  updated_at: string;
  actor: string;
  path: string;
}

/**
 * Parse the `/api/plan-mode/mode` payload.
 *
 * Throws when the body is not an object or carries no usable `mode` string,
 * so callers render an honest "unavailable" state instead of inventing a mode.
 */
export function parseExecutionModeStatus(body: unknown): ExecutionModeStatus {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    throw new Error("The server returned an unreadable execution-mode payload.");
  }
  const rec = body as Record<string, unknown>;
  const mode = typeof rec.mode === "string" ? rec.mode.trim() : "";
  if (!mode) {
    throw new Error("The server response did not include an execution mode.");
  }
  const str = (key: string): string => (typeof rec[key] === "string" ? (rec[key] as string) : "");
  return {
    mode,
    source: str("source"),
    note: str("note"),
    persisted: rec.persisted === true,
    updated_at: str("updated_at"),
    actor: str("actor"),
    path: str("path"),
  };
}

/** Read the live execution mode. Throws on any failure — never returns a guessed mode. */
export async function fetchExecutionMode(): Promise<ExecutionModeStatus> {
  return parseExecutionModeStatus(await get<unknown>("/plan-mode/mode"));
}

/** 8-dimension strategic review of a goal. Returns raw server data. */
export async function evaluatePlan(prompt: string): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>("/plan-mode/evaluate", "POST", { prompt });
}

/** Review AND immediately run the plan across subsystems (needs admin). */
export async function dispatchPlan(prompt: string): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>("/plan-mode/dispatch", "POST", { prompt });
}

/** Point the live browser tab at a URL (needs browser control enabled + a chat). */
export async function navigateBrowser(threadId: string, url: string): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(`/threads/${encodeURIComponent(threadId)}/browser/navigate`, "POST", { url });
}
