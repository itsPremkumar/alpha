/**
 * "Is this agent working right now?" — the client boundary for
 * `GET /api/bots/health/overview` plus `GET /api/bots/kill-switch`.
 *
 * Why this module exists
 * ---------------------
 * The Bots tab could answer *nothing* about whether a bot is working. Its only
 * presence signal was `isRecent(bot.last_active, PRESENCE_WINDOW_SECONDS)` — a
 * display reading over a registry timestamp. Meanwhile the Gateway runs a real
 * liveness engine that owns the answer (`alpha.bots.health`): healthy / stale /
 * stalled / dead / sleeping / suspended / archived, whether the worker answers,
 * whether it holds a task, and whether that task's lease has lapsed. That
 * verdict was produced on every `/health/overview` call and no surface rendered
 * it, so the tab showed a green dot and called it "working".
 *
 * The gap is not cosmetic. A bot whose lease expired mid-task is *stalled* — it
 * holds work that will not finish — and the card that said "Working" a second
 * ago is the only thing an operator can see. Same for a bot an operator paused.
 *
 * The honesty rules this module enforces
 * ------------------------------------
 * - **The liveness string travels verbatim.** An unknown word from a newer
 *   Gateway is shown as itself, never snapped to one of the eight this build
 *   knows. A bot that is `is_responsive: null` renders "responsiveness not
 *   reported", not "responsive".
 * - **A missing counter is `null`, never `0`.** A summary block the server
 *   omitted renders as `—` beside the word "not reported", because "we could
 *   not look" and "the answer was zero" lead to opposite decisions.
 * - **A failed read refuses.** It does not resolve into an empty overview with
 *   `total: 0`, which would paint a fleet-wide no-heartbeat claim from a
 *   network error. Callers get the server's own reason.
 * - **No liveness verdict means no liveness claim.** When the report is
 *   unavailable the card falls back to the *presence* reading and says
 *   explicitly that the monitor did not report for it.
 */

import { apiFetch } from "./api-client";
import type { BotProfile } from "@/types/bots";
import { isRecent, PRESENCE_WINDOW_SECONDS, relTime } from "./time";

/* -------------------------------------------------------------------------- */
/* Types                                                                      */
/* -------------------------------------------------------------------------- */

/**
 * One row of `GET /api/bots/health/overview`'s `bots` list, as
 * `alpha.bots.health.BotHealthMonitor.evaluate_liveness` writes it.
 *
 * Every field is nullable: an absent field is "the server did not report it",
 * never a default. Note `seconds_since_heartbeat` is `null` exactly when the
 * heartbeat timestamp could not be parsed — the engine refuses the
 * `999999.0`-style sentinel it used to send, so a `null` here means
 * "unmeasurable", not "old".
 */
export interface BotHealthRow {
  bot_name: string | null;
  status: string | null;
  /** `healthy` | `stale` | `stalled` | `dead` | `sleeping` | `suspended` | `archived`, or a newer word verbatim. */
  liveness: string | null;
  is_responsive: boolean | null;
  active_task_id: string | null;
  last_heartbeat: string | null;
  seconds_since_heartbeat: number | null;
  heartbeat_parse_error: boolean | null;
  lease_expired: boolean | null;
}

/**
 * The fleet counters. Each is `null` when the server sent no such key: a
 * summary block that omits `stalled` must not render as `0 stalled`, which
 * would claim the engine found none.
 */
export interface FleetHealthSummary {
  total: number | null;
  healthy: number | null;
  stale: number | null;
  stalled: number | null;
  dead: number | null;
  sleeping: number | null;
  suspended: number | null;
  archived: number | null;
}

export interface FleetHealthOverview {
  timestamp: string | null;
  summary: FleetHealthSummary;
  fleet_health_score: number | null;
  bots: BotHealthRow[];
  /** The rows the engine flagged as stalled — the recovery queue. */
  stalled_workers: BotHealthRow[];
}

export type ReadResult<T> =
  { ok: true; value: T } | { ok: false; error: string };

const asString = (v: unknown): string | null =>
  typeof v === "string" && v.length > 0 ? v : null;
const asBool = (v: unknown): boolean | null =>
  typeof v === "boolean" ? v : null;
const asCount = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

/* -------------------------------------------------------------------------- */
/* Reader                                                                     */
/* -------------------------------------------------------------------------- */

/** Map one health row, refusing every default the wire might be missing. */
export function normalizeHealthRow(raw: unknown): BotHealthRow | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  return {
    bot_name: asString(r.bot_name),
    status: asString(r.status),
    liveness: asString(r.liveness),
    is_responsive: asBool(r.is_responsive),
    active_task_id: asString(r.active_task_id),
    last_heartbeat: asString(r.last_heartbeat),
    seconds_since_heartbeat: asCount(r.seconds_since_heartbeat),
    heartbeat_parse_error: asBool(r.heartbeat_parse_error),
    lease_expired: asBool(r.lease_expired),
  };
}

/**
 * Map the fleet overview envelope.
 *
 * `summary` is read key by key rather than wholesale, so a payload from a
 * Gateway that has not yet gained a state (or one that lost the block to a
 * proxy) renders the states it *did* report and `—` for the rest, instead of
 * collapsing the whole summary to zeros.
 */
export function normalizeHealthOverview(
  raw: Record<string, unknown>,
): FleetHealthOverview {
  const summaryRaw = (raw.summary ?? {}) as Record<string, unknown>;
  const rows = Array.isArray(raw.bots)
    ? raw.bots
        .map(normalizeHealthRow)
        .filter((r): r is BotHealthRow => r !== null)
    : [];
  return {
    timestamp: asString(raw.timestamp),
    summary: {
      total: asCount(summaryRaw.total),
      healthy: asCount(summaryRaw.healthy),
      stale: asCount(summaryRaw.stale),
      stalled: asCount(summaryRaw.stalled),
      dead: asCount(summaryRaw.dead),
      sleeping: asCount(summaryRaw.sleeping),
      suspended: asCount(summaryRaw.suspended),
      archived: asCount(summaryRaw.archived),
    },
    fleet_health_score: asCount(raw.fleet_health_score),
    bots: rows,
    stalled_workers: Array.isArray(raw.stalled_workers)
      ? raw.stalled_workers
          .map(normalizeHealthRow)
          .filter((r): r is BotHealthRow => r !== null)
      : [],
  };
}

/**
 * Read the fleet liveness overview.
 *
 * Returns a `ReadResult` instead of `[]`-on-failure: an empty roster means "the
 * server said nobody is here", and a failed read means "we do not know". Only
 * the caller can tell the difference, so both shapes survive to it.
 */
export async function fetchBotHealthOverview(): Promise<
  ReadResult<FleetHealthOverview>
> {
  try {
    const res = await apiFetch("/bots/health/overview");
    return { ok: true, value: normalizeHealthOverview(await res.json()) };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

/**
 * Index the rows by bot name.
 *
 * Keyed on the lowercased name because `evaluate_liveness` writes
 * `bot_name = profile.name.lower().strip()`, so a mixed-case roster name
 * (`QA_Lead`) must still find its row. The FIRST row for a name wins so a
 * duplicate cannot silently flip a verdict.
 */
export function healthRowIndex(
  rows: readonly BotHealthRow[],
): Map<string, BotHealthRow> {
  const index = new Map<string, BotHealthRow>();
  for (const row of rows) {
    const key = (row.bot_name ?? "").trim().toLowerCase();
    if (key && !index.has(key)) index.set(key, row);
  }
  return index;
}

/**
 * Look a row up by the roster's own name.
 *
 * The lookup folds case here rather than trusting every caller to remember:
 * a caller that passed `"Coder"` into a map keyed `"coder"` would silently get
 * `undefined`, and a bot missing its row is exactly how a real "stalled"
 * verdict degrades into a presence fallback that says nothing.
 */
export function healthRowFor(
  index: ReadonlyMap<string, BotHealthRow>,
  botName: string,
): BotHealthRow | null {
  return index.get(botName.trim().toLowerCase()) ?? null;
}

/* -------------------------------------------------------------------------- */
/* Operator pause / kill switch                                               */
/* -------------------------------------------------------------------------- */

/**
 * `GET /api/bots/kill-switch` projected onto the bot-level question.
 *
 * Separate from `teamops.ts`'s `killSwitchState()` on purpose: that reader
 * exists for the Team ops sub-tab's headline badge and is deliberately
 * tri-state, while this one carries the per-bot pause rows the gallery needs
 * to say which agent is stopped and why. Both read the same envelope; only one
 * of them has to answer a per-bot question.
 */
export interface PauseState {
  /** `null` when the server sent no flag — an unreported switch is not an off switch. */
  active: boolean | null;
  reason: string | null;
  /** Lowercased bot name -> the server's own pause reason, or `null` when unreported. */
  paused: Record<string, string> | null;
}

export async function fetchPauseState(): Promise<ReadResult<PauseState>> {
  try {
    const res = await apiFetch("/bots/kill-switch");
    const raw = (await res.json()) as Record<string, unknown>;
    const rawPaused = raw.paused_bots;
    let paused: Record<string, string> | null = null;
    if (
      rawPaused &&
      typeof rawPaused === "object" &&
      !Array.isArray(rawPaused)
    ) {
      paused = {};
      for (const [name, value] of Object.entries(
        rawPaused as Record<string, unknown>,
      )) {
        const reason =
          value && typeof value === "object"
            ? asString((value as Record<string, unknown>).reason)
            : null;
        paused[name.trim().toLowerCase()] = reason ?? "paused by an operator";
      }
    }
    return {
      ok: true,
      value: {
        active: asBool(raw.global_kill_switch_active),
        reason: asString(raw.reason),
        paused,
      },
    };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

/* -------------------------------------------------------------------------- */
/* Working status                                                             */
/* -------------------------------------------------------------------------- */

export type WorkingKey =
  | "working"
  | "idle"
  | "sleeping"
  | "suspended"
  | "archived"
  | "stalled"
  | "dead"
  | "paused"
  /** A liveness word this build does not classify, shown verbatim. */
  | "unclassified"
  /** No verdict is obtainable at all. */
  | "unknown";

export type WorkingTone = "good" | "warn" | "bad" | "muted";

export interface WorkingStatus {
  key: WorkingKey;
  /** Short badge word. For `unclassified` this is the server's own word. */
  label: string;
  /** The sentence: what was measured, and the number behind it. */
  detail: string;
  tone: WorkingTone;
  /**
   * `true` = working, `false` = not working, `null` = the server did not say.
   * A caller must never render `null` as "not working".
   */
  working: boolean | null;
  /** Which server reading the verdict came from. */
  evidence: "health" | "presence" | "pause" | "none";
}

export interface WorkingStatusOptions {
  /** A live pause reason for this bot, when the operator stopped it. */
  pausedReason?: string | null;
}

/**
 * "Seen working within the last PRESENCE_WINDOW_SECONDS seconds", used as the
 * fallback reading only.
 */
export function presentByActivity(
  bot: Pick<BotProfile, "last_active">,
  now: number = Date.now(),
): boolean {
  return isRecent(bot.last_active, PRESENCE_WINDOW_SECONDS, now);
}

/** The heartbeat sentence: a number, or the reason there is no number. */
export function heartbeatWords(row: BotHealthRow): string {
  if (row.seconds_since_heartbeat != null) {
    return `heartbeat ${Math.round(row.seconds_since_heartbeat)}s ago`;
  }
  if (row.heartbeat_parse_error) return "heartbeat timestamp unreadable";
  return "no heartbeat recorded";
}

/** Responsiveness, with `null` staying a word rather than an assumed boolean. */
function responsiveWords(row: BotHealthRow): string {
  if (row.is_responsive === null) return "responsiveness not reported";
  return row.is_responsive ? "responsive" : "not responding";
}

/**
 * Derive the working status for one bot.
 *
 * Precedence, and why:
 *
 * 1. **An operator pause wins.** A stopped bot holding a fresh heartbeat would
 *    otherwise read as working; the kill switch is the strongest statement
 *    anyone has made about this bot.
 * 2. **The monitor's liveness verdict.** It is the engine that owns the
 *    question, so it wins over roster timestamps.
 * 3. **Presence** (`last_active` inside the display window) only when no health
 *    row arrived — either the read failed or the server omitted the bot. The
 *    fallback labels itself as presence so it cannot be mistaken for the
 *    monitor's answer.
 * 4. **Unknown.** Nothing measured: an unreadable timestamp and no report.
 */
export function workingStatusFor(
  bot: Pick<BotProfile, "name" | "last_active">,
  row: BotHealthRow | null | undefined,
  options: WorkingStatusOptions = {},
): WorkingStatus {
  const pauseReason = options.pausedReason ?? null;
  if (pauseReason) {
    return {
      key: "paused",
      label: "Paused by operator",
      detail: `${pauseReason} — no work is accepted while paused.`,
      tone: "warn",
      working: false,
      evidence: "pause",
    };
  }

  if (row) {
    const hb = heartbeatWords(row);
    const responsive = responsiveWords(row);
    const task = row.active_task_id;

    switch (row.liveness) {
      case "healthy":
        return task
          ? {
              key: "working",
              label: "Working",
              detail: `Working on task ${task} · ${hb} · ${responsive}.`,
              tone: "good",
              working: true,
              evidence: "health",
            }
          : {
              key: "idle",
              label: "Idle",
              detail: `${responsive} (${hb}) · no task reported.`,
              tone: "muted",
              working: false,
              evidence: "health",
            };
      case "stale":
        // 60–300s since the last heartbeat but the monitor still counts the
        // worker responsive. With a task in hand that is work in progress, not
        // an idle bot — but the age is stated so the claim is qualified.
        return task
          ? {
              key: "working",
              label: "Working",
              detail: `Task ${task} in hand · ${hb} · the monitor still reports it ${responsive}.`,
              tone: "warn",
              working: true,
              evidence: "health",
            }
          : {
              key: "idle",
              label: "Idle",
              detail: `${responsive} · ${hb} · no task reported.`,
              tone: "muted",
              working: false,
              evidence: "health",
            };
      case "stalled":
        // The lease lapsed while the bot still holds the task. This is the
        // state that must never read as working: the work will not finish on
        // its own.
        return {
          key: "stalled",
          label: "Stalled",
          detail: task
            ? `Stalled on task ${task} · its lease has expired · ${hb}.`
            : `Stalled · lease expired · ${hb}.`,
          tone: "bad",
          working: false,
          evidence: "health",
        };
      case "dead":
        return {
          key: "dead",
          label: "No heartbeat",
          detail: task
            ? `Dead · no heartbeat for over five minutes · task ${task} may be stuck · ${hb}.`
            : `Dead · no heartbeat for over five minutes · ${hb}.`,
          tone: "bad",
          working: false,
          evidence: "health",
        };
      case "sleeping":
        return {
          key: "sleeping",
          label: "Sleeping",
          detail:
            "Sleeping — the monitor reports it wakes on demand, so it is not taking work right now.",
          tone: "muted",
          working: false,
          evidence: "health",
        };
      case "suspended":
        return {
          key: "suspended",
          label: "Suspended",
          detail: "Suspended — out of service, so it is not eligible for work.",
          tone: "muted",
          working: false,
          evidence: "health",
        };
      case "archived":
        return {
          key: "archived",
          label: "Archived",
          detail: "Archived — retained for history and not eligible for work.",
          tone: "muted",
          working: false,
          evidence: "health",
        };
      case null:
        return {
          key: "unknown",
          label: "Liveness not reported",
          detail:
            "The health report included this bot but named no liveness state, so nothing is known.",
          tone: "muted",
          working: null,
          evidence: "health",
        };
      default:
        // A word from a newer Gateway: preserve it verbatim. Snapping it to
        // `healthy` would hand this build's green badge to a verdict it cannot
        // read.
        return {
          key: "unclassified",
          label: row.liveness,
          detail: `The Gateway reported liveness "${row.liveness}", which this build does not classify — shown verbatim · ${hb}.`,
          tone: "muted",
          working: null,
          evidence: "health",
        };
    }
  }

  // No health row: presence fallback. The evidence is named in the sentence so
  // nobody reads it as the monitor's answer.
  const seen = relTime(bot.last_active);
  if (presentByActivity(bot)) {
    return {
      key: "working",
      label: "Working",
      detail: `Activity inside the last ${PRESENCE_WINDOW_SECONDS}s (presence only — the health report did not cover this bot).`,
      tone: "good",
      working: true,
      evidence: "presence",
    };
  }
  if (seen) {
    return {
      key: "idle",
      label: "Idle",
      detail: `Last activity ${seen} (presence only — the health report did not cover this bot).`,
      tone: "muted",
      working: false,
      evidence: "presence",
    };
  }
  return {
    key: "unknown",
    label: "Unknown",
    detail:
      "No activity recorded and no health report for this bot — nothing was measured.",
    tone: "muted",
    working: null,
    evidence: "none",
  };
}

/* -------------------------------------------------------------------------- */
/* Filters over the derived status                                            */
/* -------------------------------------------------------------------------- */

export type WorkingFacet =
  "all" | "working" | "stalled" | "not-working" | "unknown";

export const WORKING_FACETS: ReadonlyArray<{
  value: WorkingFacet;
  label: string;
}> = [
  { value: "all", label: "Any working state" },
  { value: "working", label: "Working now" },
  { value: "stalled", label: "Stalled / needs recovery" },
  { value: "not-working", label: "Not working" },
  { value: "unknown", label: "Working state not reported" },
];

/**
 * Which facet a status belongs to. The buckets are disjoint by construction —
 * a stalled bot is not also counted as "not working" — so each option answers
 * for exactly one population and two options can never claim the same bot.
 */
export function workingFacetOf(
  status: WorkingStatus,
): Exclude<WorkingFacet, "all"> {
  if (status.working === null) return "unknown";
  if (status.working) return "working";
  if (status.key === "stalled") return "stalled";
  return "not-working";
}

/**
 * A bot a human has to do something about.
 *
 * Deliberately NOT every non-working state: `dead` with no task in hand is what
 * an idle fleet looks like, so folding it in would raise the notice on every
 * install and train operators to ignore it. The engine's own recovery queue
 * (`check_stalled_tasks`) is narrower still — a lapsed lease on a live task —
 * so `dead` is *counted* by the strip and, when it names a task, said so in
 * the card's own detail rather than escalated here.
 *
 * Stalled is always actionable (work is stuck), and a pause is an operator's
 * statement that must not be mistaken for the monitor's verdict.
 */
export function needsAttention(status: WorkingStatus): boolean {
  return status.key === "stalled" || status.key === "paused";
}
