/**
 * Context-window occupancy: the typed client over `GET /api/ops/context-windows`.
 *
 * The server owns every number here. This module maps the envelope and derives
 * *only* the words that go beside a value — never the value itself. That split
 * is what stops a client from rendering a window it was not given: a model that
 * declares no `context_window` arrives with `declared_input_window: null`, and
 * `modelWindowView` reports that as "not declared" rather than snapping it to a
 * size, because "we do not know the size of the room" and "the room is empty"
 * lead to opposite decisions.
 */

import { get } from "./http";

/** Occupancy bands the Gateway derives, weakest first. */
export type ContextPressureBand = "unknown" | "nominal" | "elevated" | "critical" | "over";

export interface ContextWindowRow {
  name: string;
  /** Input window in tokens the operator declared, or `null` when undeclared. */
  declaredInputWindow: number | null;
  /** `declared - output_reserve - next_turn_reserve`, or `null`. */
  usableInputWindow: number | null;
  reservedTokens: number;
  /** True when the reserves were clamped to the configured floor. */
  clamped: boolean;
  reason: string;
  escalationCandidate: string | null;
}

export interface ContextWindows {
  /** False when the Gateway has no policy to report at all. */
  reported: boolean;
  reason: string;
  models: ContextWindowRow[] | null;
  /** `null` when no policy section exists — a third state, not "disabled". */
  escalationEnabled: boolean | null;
  notes: string[];
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function bool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function str(value: unknown, fallback: string): string {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function row(raw: Record<string, unknown>): ContextWindowRow {
  return {
    name: str(raw.name, ""),
    declaredInputWindow: num(raw.declared_input_window),
    usableInputWindow: num(raw.usable_input_window),
    reservedTokens: num(raw.reserved_tokens) ?? 0,
    clamped: raw.clamped === true,
    reason: str(raw.reason, "not_reported"),
    escalationCandidate: typeof raw.escalation_candidate === "string" ? raw.escalation_candidate : null,
  };
}

export function toContextWindows(payload: unknown): ContextWindows {
  const p = (payload ?? {}) as Record<string, unknown>;
  const rawModels = p.models;
  return {
    reported: p.reported === true,
    reason: str(p.reason, "not_reported"),
    // `null` is "the Gateway sent no list", which is distinct from `[]`.
    models: Array.isArray(rawModels) ? rawModels.map((m) => row((m ?? {}) as Record<string, unknown>)) : null,
    escalationEnabled: bool(p.escalation_enabled),
    notes: Array.isArray(p.notes) ? p.notes.filter((n): n is string => typeof n === "string") : [],
  };
}

export async function fetchContextWindows(): Promise<ContextWindows> {
  const payload = await get<unknown>("/ops/context-windows");
  return toContextWindows(payload);
}

/* ── pure view helpers ─────────────────────────────────────────────────────── */

/** Words for a band. An unrecognised band renders verbatim, never green. */
export function bandLabel(band: string | null | undefined): string {
  switch (band) {
    case "unknown":
      return "not measured";
    case "nominal":
      return "nominal";
    case "elevated":
      return "elevated";
    case "critical":
      return "critical";
    case "over":
      return "over";
    default:
      return "not reported";
  }
}

/**
 * Tone for a band. `unknown` and an unrecognised word are both neutral-grey:
 * a window nobody measured is not a healthy window, and a word from a newer
 * Gateway must not be snapped into a colour this build invented.
 */
export function bandTone(band: string | null | undefined): "green" | "amber" | "red" | "grey" {
  switch (band) {
    case "nominal":
      return "green";
    case "elevated":
      return "amber";
    case "critical":
    case "over":
      return "red";
    default:
      return "grey";
  }
}

export interface ModelWindowView {
  name: string;
  declaredText: string;
  usableText: string;
  reservedText: string;
  escalationText: string;
  tone: "green" | "amber" | "red" | "grey";
  /** True when the reserves were clamped, which the view must disclose. */
  clamped: boolean;
  /** The server's own machine-readable reason, surfaced beside the words. */
  reason: string;
}

/**
 * Build every sentence one row renders. Keeping them here means the list row
 * and its detail line cannot disagree about the same field, and the suite can
 * drive the function that produces each phrase instead of scraping JSX.
 */
export function modelWindowView(r: ContextWindowRow): ModelWindowView {
  const declared = r.declaredInputWindow;
  if (declared === null) {
    return {
      name: r.name,
      declaredText: "not declared",
      usableText: "not derivable",
      reservedText: "not reported",
      escalationText: "not comparable",
      tone: "grey",
      clamped: false,
      reason: r.reason,
    };
  }
  return {
    name: r.name,
    declaredText: declared.toLocaleString(),
    usableText: r.usableInputWindow === null ? "not reported" : r.usableInputWindow.toLocaleString(),
    reservedText: r.reservedTokens.toLocaleString(),
    escalationText: r.escalationCandidate ?? "none larger declared",
    tone: r.clamped ? "amber" : "green",
    clamped: r.clamped,
    reason: r.reason,
  };
}

/** Headline for the panel, naming how many models declared nothing. */
export function windowSummaryText(windows: ContextWindows): string {
  if (!windows.reported) return "the Gateway reported no context-window policy";
  if (windows.models === null) return "the Gateway sent no model list";
  const total = windows.models.length;
  const undeclared = windows.models.filter((m) => m.declaredInputWindow === null).length;
  if (total === 0) return "this deployment declares no models";
  if (undeclared === 0) return `${total} model${total === 1 ? "" : "s"}, all declaring a context window`;
  return `${total - undeclared} of ${total} models declare a context window`;
}
