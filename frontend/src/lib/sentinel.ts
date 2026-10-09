// lib/sentinel.ts — the Sentinel plane: signals, repairs, history, and the
// human handoffs the engine could not close.
//
// Routes (backend/app/gateway/routers/autonomy.py, prefix /api/autonomy):
//   GET  /sentinel/signals                → observe-only collection (nothing fixed)
//   GET  /sentinel/reports?limit=N        → capped read of the durable journal
//   POST /sentinel/run                    → one real pass; auto_heal always sent
//   GET  /sentinel/analytics?limit=N      → the journal folded into one reading
//   GET  /sentinel/kinds                  → declared fault kinds + repair posture
//   GET  /sentinel/escalations?limit=N    → human handoffs, oldest first
//   POST /sentinel/escalations/{id}/acknowledge  → admin; marks it seen
//   POST /sentinel/escalations/{id}/resolve       → admin; records the decision
//
// The first three live in lib/supervisor.ts because that file already owns the
// autonomy plane; they are re-exported here so one panel has one client.
//
// Honesty contract, in the same canon as the effect journal and APEX panels:
//
//  * **Absent is `null`, never `0`.** `scanned_total` is null when no pass
//    reported one — rendering 0 would claim the engine saw no signals when the
//    truth is that nobody measured. The same rule covers `duration_*`,
//    `auto_heal_passes`, `first_seen` and every count in the kind roll-up.
//  * **A verdict is derived, never asserted.** `analyticsHealthView` names the
//    *reason* beside the label, so a green word is always attached to the
//    counters that earned it.
//  * **An admin refusal is kept verbatim.** `failureText` is not `errMsg`: the
//    shared helper paraphrases a 403 into "needs admin rights", which is right
//    for an incidental read and wrong for the answer to a deliberate write.
//  * **Bounds mirror the server.** The server answers 422 outside 1..200, so
//    `clampLimit` refuses locally with the bound named rather than earning a
//    422 that says the same thing.
import {
  get,
  send,
  pick,
  errMsg,
} from "./http";
import {
  getSentinelReports,
  runSentinelPass,
  getSentinelSignals,
} from "./supervisor";
import type {
  SentinelReportEntry,
  SentinelRunReport,
  SentinelSignals,
} from "./supervisor";

export {
  getSentinelReports,
  runSentinelPass,
  getSentinelSignals,
};
export type {
  SentinelReportEntry,
  SentinelRunReport,
  SentinelSignals,
  SentinelReports,
  SentinelSignal,
} from "./supervisor";

/** The gateway's window for every capped Sentinel read. */
export const MAX_ANALYTICS_LIMIT = 200;
export const DEFAULT_ANALYTICS_LIMIT = 50;

/** Verdicts the fold can reach. An unrecognised one is preserved verbatim. */
export type SentinelKindVerdict = "repaired" | "reverted" | "unrepaired" | "deferred" | "unmeasured" | (string & {});

export interface SentinelKindReading {
  kind: string;
  occurrences: number;
  /** Passes this kind appeared in — not the same number as occurrences. */
  passes: number;
  fixed: number;
  reverted: number;
  escalated: number;
  skipped: number;
  other: number;
  status_missing: number;
  distinct_fingerprints: number;
  first_seen: string | null;
  last_seen: string | null;
  verdict: SentinelKindVerdict;
  unknown_statuses: string[];
}

export interface SentinelRepeat {
  fingerprint: string;
  kind: string;
  passes: number;
  occurrences: number;
  first_seen: string | null;
  last_seen: string | null;
  verdict: SentinelKindVerdict;
}

export interface SentinelAnalytics {
  passes: number;
  malformed: number;
  first_recorded_at: string | null;
  last_recorded_at: string | null;
  passes_with_outcomes: number;
  passes_without_outcomes: number;
  outcome_count: number;
  distinct_fingerprints: number;
  scanned_total: number | null;
  scanned_reporting_passes: number;
  fixed_total: number;
  reverted_total: number;
  escalated_total: number;
  error_total: number;
  auto_heal_passes: number | null;
  observe_passes: number | null;
  trigger_counts: Record<string, number>;
  status_counts: Record<string, number>;
  duration_measured_passes: number;
  duration_min_s: number | null;
  duration_mean_s: number | null;
  duration_max_s: number | null;
  kinds: SentinelKindReading[];
  repeats: SentinelRepeat[];
  errors: string[];
  cap: number | null;
  dropped_by_cap: number;
  disclosures: string[];
}

export interface SentinelAnalyticsEnvelope {
  analytics: SentinelAnalytics;
  limit: number;
  total_on_disk: number;
  note: string | null;
}

export interface SentinelKindEntry {
  kind: string;
  recognised: boolean;
  repair_registered: boolean;
  repair_note: string | null;
}

export interface SentinelKinds {
  known_kinds: string[];
  repair_kinds: string[];
  kinds: SentinelKindEntry[];
  unrecognised_repair_kinds: string[];
  verification_commands: Record<string, string[]>;
  source_root: string | null;
  disclosures: string[];
}

export interface SentinelEscalation {
  escalation_id: string;
  domain: string;
  task_id: string | null;
  from_ref: string | null;
  to_ref: string | null;
  reason: string | null;
  reason_class: string | null;
  attempt: number | null;
  max_attempts: number | null;
  detail: string | null;
  status: string | null;
  created_at_iso: string | null;
  acknowledged_by: string | null;
  resolved_by: string | null;
  resolution: string | null;
}

export interface SentinelEscalations {
  escalations: SentinelEscalation[];
  total: number;
  returned: number;
  truncated: boolean;
  status_filter: string | null;
  source: string | null;
  disclosures: string[];
}

export interface SentinelEscalationDecision {
  escalation: SentinelEscalation;
  applied: boolean;
  note: string | null;
}

/* ── Mapping ─────────────────────────────────────────────────────────────── */

function numOrNull(v: unknown): number | null {
  if (typeof v === "boolean") return null;
  if (typeof v === "number" && Number.isFinite(v) && v >= 0) return v;
  return null;
}

function intOrZero(v: unknown): number {
  const n = numOrNull(v);
  return n === null ? 0 : Math.floor(n);
}

function strOrNull(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function countMap(v: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (v && typeof v === "object") {
    for (const [key, value] of Object.entries(v as Record<string, unknown>)) {
      out[key] = intOrZero(value);
    }
  }
  return out;
}

function strList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x !== "") : [];
}

function strListMap(v: unknown): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  if (v && typeof v === "object") {
    for (const [key, value] of Object.entries(v as Record<string, unknown>)) {
      out[key] = strList(value);
    }
  }
  return out;
}

function toKindReading(raw: Record<string, unknown>): SentinelKindReading {
  return {
    kind: String(pick(raw, ["kind"], "")),
    occurrences: intOrZero(raw.occurrences),
    passes: intOrZero(raw.passes),
    fixed: intOrZero(raw.fixed),
    reverted: intOrZero(raw.reverted),
    escalated: intOrZero(raw.escalated),
    skipped: intOrZero(raw.skipped),
    other: intOrZero(raw.other),
    status_missing: intOrZero(raw.status_missing),
    distinct_fingerprints: intOrZero(raw.distinct_fingerprints),
    first_seen: strOrNull(raw.first_seen),
    last_seen: strOrNull(raw.last_seen),
    // Preserved verbatim: a verdict from a newer Gateway must not be snapped to
    // one of the five this build knows.
    verdict: strOrNull(raw.verdict) ?? "unmeasured",
    unknown_statuses: strList(raw.unknown_statuses),
  };
}

function toRepeat(raw: Record<string, unknown>): SentinelRepeat {
  return {
    fingerprint: String(pick(raw, ["fingerprint"], "")),
    kind: String(pick(raw, ["kind"], "")),
    passes: intOrZero(raw.passes),
    occurrences: intOrZero(raw.occurrences),
    first_seen: strOrNull(raw.first_seen),
    last_seen: strOrNull(raw.last_seen),
    verdict: strOrNull(raw.verdict) ?? "unmeasured",
  };
}

function toAnalytics(d: Record<string, unknown>): SentinelAnalytics {
  const raw = (d.analytics && typeof d.analytics === "object" ? d.analytics : d) as Record<string, unknown>;
  return {
    passes: intOrZero(raw.passes),
    malformed: intOrZero(raw.malformed),
    first_recorded_at: strOrNull(raw.first_recorded_at),
    last_recorded_at: strOrNull(raw.last_recorded_at),
    passes_with_outcomes: intOrZero(raw.passes_with_outcomes),
    passes_without_outcomes: intOrZero(raw.passes_without_outcomes),
    outcome_count: intOrZero(raw.outcome_count),
    distinct_fingerprints: intOrZero(raw.distinct_fingerprints),
    scanned_total: numOrNull(raw.scanned_total),
    scanned_reporting_passes: intOrZero(raw.scanned_reporting_passes),
    fixed_total: intOrZero(raw.fixed_total),
    reverted_total: intOrZero(raw.reverted_total),
    escalated_total: intOrZero(raw.escalated_total),
    error_total: intOrZero(raw.error_total),
    auto_heal_passes: numOrNull(raw.auto_heal_passes),
    observe_passes: numOrNull(raw.observe_passes),
    trigger_counts: countMap(raw.trigger_counts),
    status_counts: countMap(raw.status_counts),
    duration_measured_passes: intOrZero(raw.duration_measured_passes),
    duration_min_s: numOrNull(raw.duration_min_s),
    duration_mean_s: numOrNull(raw.duration_mean_s),
    duration_max_s: numOrNull(raw.duration_max_s),
    kinds: Array.isArray(raw.kinds)
      ? (raw.kinds as Array<Record<string, unknown>>).filter((k) => k && typeof k === "object").map(toKindReading)
      : [],
    repeats: Array.isArray(raw.repeats)
      ? (raw.repeats as Array<Record<string, unknown>>).filter((r) => r && typeof r === "object").map(toRepeat)
      : [],
    errors: strList(raw.errors),
    cap: numOrNull(raw.cap),
    dropped_by_cap: intOrZero(raw.dropped_by_cap),
    disclosures: strList(raw.disclosures),
  };
}

function toEscalation(raw: Record<string, unknown>): SentinelEscalation {
  return {
    escalation_id: String(pick(raw, ["escalation_id"], "")),
    domain: String(pick(raw, ["domain"], "")),
    task_id: strOrNull(raw.task_id),
    from_ref: strOrNull(raw.from_ref),
    to_ref: strOrNull(raw.to_ref),
    reason: strOrNull(raw.reason),
    reason_class: strOrNull(raw.reason_class),
    attempt: numOrNull(raw.attempt),
    max_attempts: numOrNull(raw.max_attempts),
    detail: strOrNull(raw.detail),
    status: strOrNull(raw.status),
    created_at_iso: strOrNull(raw.created_at_iso),
    acknowledged_by: strOrNull(raw.acknowledged_by),
    resolved_by: strOrNull(raw.resolved_by),
    resolution: strOrNull(raw.resolution),
  };
}

/** Refuse a limit the server would 422, naming the bound instead of sending it. */
export function clampLimit(limit: number, max = MAX_ANALYTICS_LIMIT): number {
  if (!Number.isFinite(limit)) return DEFAULT_ANALYTICS_LIMIT;
  const floored = Math.floor(limit);
  if (floored < 1) throw new Error(`limit must be >= 1 (got ${limit})`);
  if (floored > max) throw new Error(`limit must be <= ${max} (got ${limit})`);
  return floored;
}

/* ── Reads ───────────────────────────────────────────────────────────────── */

/** GET /sentinel/analytics?limit=N — fold the journal; rejects on an unreadable one. */
export async function getSentinelAnalytics(limit = DEFAULT_ANALYTICS_LIMIT): Promise<SentinelAnalyticsEnvelope> {
  const bounded = clampLimit(limit);
  const d = await get<Record<string, unknown>>(`/autonomy/sentinel/analytics?limit=${bounded}`);
  return {
    analytics: toAnalytics((d && typeof d === "object" ? d : {}) as Record<string, unknown>),
    limit: numOrNull(d?.limit) ?? bounded,
    total_on_disk: intOrZero(d?.total_on_disk),
    note: strOrNull(d?.note),
  };
}

/** GET /sentinel/kinds — the declared registry, never a scan. */
export async function getSentinelKinds(): Promise<SentinelKinds> {
  const d = (await get<Record<string, unknown>>("/autonomy/sentinel/kinds")) ?? {};
  return {
    known_kinds: strList(d.known_kinds),
    repair_kinds: strList(d.repair_kinds),
    kinds: Array.isArray(d.kinds)
      ? (d.kinds as Array<Record<string, unknown>>)
          .filter((k) => k && typeof k === "object")
          .map((k) => ({
            kind: String(pick(k, ["kind"], "")),
            recognised: k.recognised === true,
            repair_registered: k.repair_registered === true,
            repair_note: strOrNull(k.repair_note),
          }))
      : [],
    unrecognised_repair_kinds: strList(d.unrecognised_repair_kinds),
    verification_commands: strListMap(d.verification_commands),
    source_root: strOrNull(d.source_root),
    disclosures: strList(d.disclosures),
  };
}

/** GET /sentinel/escalations?limit=N — human handoffs, oldest first. */
export async function getSentinelEscalations(limit = DEFAULT_ANALYTICS_LIMIT, status: string | null = null): Promise<SentinelEscalations> {
  const bounded = clampLimit(limit);
  const query = status ? `&status=${encodeURIComponent(status)}` : "";
  const d = (await get<Record<string, unknown>>(`/autonomy/sentinel/escalations?limit=${bounded}${query}`)) ?? {};
  const rows = Array.isArray(d.escalations) ? (d.escalations as Array<Record<string, unknown>>) : [];
  const returned = numOrNull(d.returned);
  const total = numOrNull(d.total);
  return {
    escalations: rows.filter((r) => r && typeof r === "object").map(toEscalation),
    total: total === null ? rows.length : total,
    // `truncated` is derived as well as read: a Gateway that bounds the list
    // without declaring it would otherwise look internally consistent.
    truncated: d.truncated === true || (returned !== null && total !== null && returned < total),
    returned: returned === null ? rows.length : returned,
    status_filter: strOrNull(d.status_filter),
    source: strOrNull(d.source),
    disclosures: strList(d.disclosures),
  };
}

/* ── Writes ───────────────────────────────────────────────────────────────── */

/**
 * POST /sentinel/escalations/{id}/acknowledge — mark one handoff as seen.
 * Admin-only; a 403 is the answer, not a failure to retry.
 */
export async function acknowledgeSentinelEscalation(escalationId: string, by: string): Promise<SentinelEscalationDecision> {
  return decideEscalation(escalationId, "acknowledge", { by });
}

/**
 * POST /sentinel/escalations/{id}/resolve — record the human decision.
 * Admin-only; the note is capped where the server caps it.
 */
export async function resolveSentinelEscalation(escalationId: string, by: string, note = ""): Promise<SentinelEscalationDecision> {
  if (note.length > 2000) throw new Error("resolution note must be <= 2000 characters");
  return decideEscalation(escalationId, "resolve", { by, note });
}

async function decideEscalation(
  escalationId: string,
  verb: "acknowledge" | "resolve",
  payload: Record<string, unknown>,
): Promise<SentinelEscalationDecision> {
  const d =
    (await send<Record<string, unknown>>(`/autonomy/sentinel/escalations/${encodeURIComponent(escalationId)}/${verb}`, "POST", payload)) ??
    {};
  const raw = (d.escalation && typeof d.escalation === "object" ? d.escalation : {}) as Record<string, unknown>;
  return {
    escalation: toEscalation(raw),
    applied: d.applied === true,
    note: strOrNull(d.note),
  };
}

/**
 * The server's own words for a refusal.
 *
 * `errMsg` paraphrases 401/403/404 on purpose — right for an incidental read,
 * wrong for the answer to a deliberate write. This keeps an `Error` carrying a
 * numeric `status` verbatim and falls back to `errMsg` for anything else.
 */
export function failureText(err: unknown): string {
  if (err instanceof Error) {
    const status = (err as Error & { status?: number }).status;
    if (typeof status === "number" && status >= 400) return err.message;
  }
  return errMsg(err);
}

/* ── Pure view helpers (the panel holds no sentence of its own) ──────────── */

export type Tone = "green" | "amber" | "gray" | "blue" | "red" | "purple" | "cyan" | "indigo";

/** Tone per verdict. An unrecognised verdict is neutral gray, never green. */
export function verdictTone(verdict: SentinelKindVerdict): Tone {
  switch (verdict) {
    case "repaired":
      return "green";
    case "reverted":
      return "amber";
    case "unrepaired":
      return "red";
    case "deferred":
      return "gray";
    case "unmeasured":
      return "gray";
    default:
      return "gray";
  }
}

/** The word the panel prints for a verdict, with its reason beside it. */
export function verdictWords(verdict: SentinelKindVerdict): string {
  switch (verdict) {
    case "repaired":
      return "repaired";
    case "reverted":
      return "fix reverted";
    case "unrepaired":
      return "escalated, never repaired";
    case "deferred":
      return "deferred";
    case "unmeasured":
      return "no outcome recorded";
    default:
      return verdict;
  }
}

/**
 * The evidence line under a kind row.
 *
 * Every number in it is a count that actually exists in the fold. A kind with
 * one repair and nine escalations reads "seen 12x, repaired 1x, escalated 9x"
 * — the verdict word and the counters stay separate, so neither can carry the
 * other's claim. ASCII only, so the separator cannot be corrupted in transit.
 */
export function kindEvidenceLine(kind: SentinelKindReading): string {
  const parts: string[] = [];
  parts.push(kind.occurrences === 1 ? "seen once" : `seen ${kind.occurrences}x`);
  if (kind.fixed) parts.push(`repaired ${kind.fixed}x`);
  if (kind.reverted) parts.push(`reverted ${kind.reverted}x`);
  if (kind.escalated) parts.push(`escalated ${kind.escalated}x`);
  if (kind.skipped) parts.push(`deferred ${kind.skipped}x`);
  if (kind.status_missing) parts.push(`no status recorded ${kind.status_missing}x`);
  if (kind.other > kind.status_missing) parts.push(`other ${kind.other - kind.status_missing}x`);
  return parts.join(", ");
}

export interface SentinelHealthView {
  tone: Tone;
  label: string;
  reason: string;
}

/**
 * The panel's headline verdict.
 *
 * Ordered so a word is only ever attached to the counters that earned it, and
 * so the two un-measured states are distinguishable from each other:
 *
 *  * no passes folded → gray, "no passes recorded" (nothing was measured);
 *  * passes but no per-signal detail → gray, "no per-signal detail recorded"
 *    (a pass ran; it recorded nothing per signal — a different fact);
 *  * repairs happened → green, naming how many;
 *  * only reverts → amber;
 *  * escalations and no repair → red.
 */
export function analyticsHealthView(a: SentinelAnalytics): SentinelHealthView {
  if (a.passes === 0) {
    return {
      tone: "gray",
      label: "no passes recorded",
      reason: "the folded window contains no pass, so nothing here was measured",
    };
  }
  if (a.outcome_count === 0 && a.passes_without_outcomes > 0) {
    return {
      tone: "gray",
      label: "no per-signal detail recorded",
      reason: `${a.passes_without_outcomes} of ${a.passes} pass(es) recorded no per-outcome rows, so no per-kind reading is reachable`,
    };
  }
  if (a.fixed_total > 0) {
    return {
      tone: "green",
      label: "repairing",
      reason: `${a.fixed_total} verified repair(s) recorded across the folded window`,
    };
  }
  if (a.reverted_total > 0) {
    return {
      tone: "amber",
      label: "repairs reverted",
      reason: `${a.reverted_total} repair(s) failed verification and were reverted; none was committed`,
    };
  }
  if (a.escalated_total > 0) {
    return {
      tone: "red",
      label: "escalating, not repairing",
      reason: `${a.escalated_total} escalation(s) and no repair recorded across the folded window`,
    };
  }
  return {
    tone: "blue",
    label: "observing",
    reason: "passes were recorded but recorded neither a repair nor an escalation",
  };
}

/** A measured duration, or the words that say it was not measured. */
export function durationText(seconds: number | null): string {
  if (seconds === null) return "not measured";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  return `${seconds.toFixed(2)} s`;
}

/**
 * The signal total, or the words that say nobody reported it.
 *
 * `0` is a real measurement (the engine looked and found nothing); `null` is
 * its absence. Rendering the second as the first is the specific failure this
 * helper exists to prevent.
 */
export function scannedText(a: SentinelAnalytics): string {
  if (a.scanned_total === null) {
    return a.scanned_reporting_passes === 0
      ? "not reported by any pass"
      : `summed over ${a.scanned_reporting_passes} of ${a.passes} pass(es)`;
  }
  return String(a.scanned_total);
}
