/**
 * The Bots tab's filter and sort rules, as pure functions.
 *
 * Why out of the component
 * -----------------------
 * A filter is a claim about what a list contains. Inlined in JSX it drifts in
 * three places at once — the predicate, the "N of M shown" line, and each
 * filter chip's count — and nothing fails, the numbers just stop meaning what
 * the label says. These functions are the single predicate the gallery calls,
 * so the rendered rows and the disclosures beside them are arithmetically
 * identical by construction.
 *
 * The honesty rules
 * -----------------
 * - **Options are derived from the roster, never hardcoded.** A status or model
 *   word the Gateway reports that this build has never seen is *offered* by the
 *   dropdown rather than silently unreachable. A bot whose `status` is `null`
 *   is matched by the explicit "not reported" option, not dropped.
 * - **Null sorts last, never first, and never as zero.** An unmeasured
 *   reputation is not a low reputation; a bot with no `last_active` is not a bot
 *   last active in 1970 (`new Date("").getTime()`). Sorting a null to the front
 *   ranks the least-known bots highest, which is how a fabricated answer
 *   usually enters a list.
 * - **The server's order is the default.** Sorting is opt-in, so the initial
 *   view is the roster exactly as the Gateway read it.
 */

import type { BotProfile } from "@/types/bots";
import { failedRuns, totalRuns } from "./bots";
// One vocabulary: the facet words live beside the status that produces them, so
// a facet can never exist that no derived status belongs to.
import {
  workingFacetOf,
  type WorkingStatus,
  type WorkingFacet,
} from "./bot-working-status";

export type { WorkingFacet };

export type ActivityFacet = "all" | "unread" | "read" | "not-reported";
export type RunsFacet = "all" | "measured" | "unmeasured" | "failures";
export type SortKey =
  "server" | "name" | "department" | "recent" | "reputation" | "tasks";

/** Facet value meaning "the server reported no status for these bots". */
export const STATUS_NOT_REPORTED = "__status_not_reported__";
/** Facet value meaning "the server reported no model for these bots". */
export const MODEL_NOT_REPORTED = "__model_not_reported__";

export interface BotFilterCriteria {
  /** Free text over the roster's own text fields. Empty matches everything. */
  search: string;
  department: string;
  status: string;
  model: string;
  working: WorkingFacet;
  activity: ActivityFacet;
  runs: RunsFacet;
  sort: SortKey;
}

export const DEFAULT_BOT_FILTERS: BotFilterCriteria = {
  search: "",
  department: "all",
  status: "all",
  model: "all",
  working: "all",
  activity: "all",
  runs: "all",
  sort: "server",
};

/** A function that answers the working question for one bot. */
export type WorkingStatusOf = (bot: BotProfile) => WorkingStatus | undefined;

/* -------------------------------------------------------------------------- */
/* Derived option lists                                                       */
/* -------------------------------------------------------------------------- */

/** Statuses the roster actually reports, verbatim and sorted. */
export function uniqueStatuses(bots: readonly BotProfile[]): string[] {
  const seen = new Set<string>();
  for (const b of bots)
    if (typeof b.status === "string" && b.status) seen.add(b.status);
  return Array.from(seen).sort((a, b) =>
    a.toLowerCase() < b.toLowerCase()
      ? -1
      : a.toLowerCase() > b.toLowerCase()
        ? 1
        : 0,
  );
}

/** Models the roster actually reports, verbatim and sorted. */
export function uniqueModels(bots: readonly BotProfile[]): string[] {
  const seen = new Set<string>();
  for (const b of bots)
    if (typeof b.model === "string" && b.model) seen.add(b.model);
  return Array.from(seen).sort((a, b) =>
    a.toLowerCase() < b.toLowerCase()
      ? -1
      : a.toLowerCase() > b.toLowerCase()
        ? 1
        : 0,
  );
}

/** Whether any bot has a missing status / model, which earns its own option. */
export function hasUnreportedStatus(bots: readonly BotProfile[]): boolean {
  return bots.some((b) => b.status == null);
}

export function hasUnreportedModel(bots: readonly BotProfile[]): boolean {
  return bots.some((b) => typeof b.model !== "string" || !b.model);
}

/* -------------------------------------------------------------------------- */
/* The predicate                                                              */
/* -------------------------------------------------------------------------- */

/** Everything the free-text search matches, in one place. */
export function searchHaystack(bot: BotProfile): string {
  return [
    bot.name,
    bot.display_name,
    bot.role,
    bot.department,
    bot.model ?? "",
    bot.reports_to ?? "",
    bot.succession_fallback ?? "",
    (bot.capabilities ?? []).join(" "),
    (bot.skills ?? []).join(" "),
    (bot.responsibilities ?? []).join(" "),
  ]
    .join(" ")
    .toLowerCase();
}

function matchesWorking(
  bot: BotProfile,
  criteria: BotFilterCriteria,
  statusOf?: WorkingStatusOf,
): boolean {
  if (criteria.working === "all") return true;
  const status = statusOf?.(bot);
  // No status function means the working question was never asked; only the
  // "unknown" facet can honestly claim that population.
  if (!status) return criteria.working === "unknown";
  return workingFacetOf(status) === criteria.working;
}

/** One bot against the criteria. Exported so a chip can count what it would show. */
export function botMatches(
  bot: BotProfile,
  criteria: BotFilterCriteria,
  statusOf?: WorkingStatusOf,
): boolean {
  if (criteria.department !== "all" && bot.department !== criteria.department)
    return false;
  if (criteria.status === STATUS_NOT_REPORTED) {
    if (bot.status != null) return false;
  } else if (criteria.status !== "all" && bot.status !== criteria.status) {
    return false;
  }
  if (criteria.model === MODEL_NOT_REPORTED) {
    if (typeof bot.model === "string" && bot.model) return false;
  } else if (criteria.model !== "all" && bot.model !== criteria.model) {
    return false;
  }
  if (criteria.activity !== "all") {
    const unread = bot.unread_count;
    if (criteria.activity === "not-reported") {
      if (unread != null) return false;
    } else if (unread == null) {
      return false;
    } else if (criteria.activity === "unread" && unread <= 0) {
      return false;
    } else if (criteria.activity === "read" && unread !== 0) {
      return false;
    }
  }
  if (criteria.runs !== "all") {
    if (criteria.runs === "failures") {
      const failed = failedRuns(bot);
      if (failed === null || failed <= 0) return false;
    } else {
      const total = totalRuns(bot);
      if (criteria.runs === "measured" && total === null) return false;
      if (criteria.runs === "unmeasured" && total !== null) return false;
    }
  }
  if (!matchesWorking(bot, criteria, statusOf)) return false;
  const q = criteria.search.trim().toLowerCase();
  if (q && !searchHaystack(bot).includes(q)) return false;
  return true;
}

export function filterBots(
  bots: readonly BotProfile[],
  criteria: BotFilterCriteria,
  statusOf?: WorkingStatusOf,
): BotProfile[] {
  return bots.filter((b) => botMatches(b, criteria, statusOf));
}

/* -------------------------------------------------------------------------- */
/* Sort                                                                       */
/* -------------------------------------------------------------------------- */

function compareText(a: string, b: string): number {
  const x = a.toLowerCase();
  const y = b.toLowerCase();
  if (x < y) return -1;
  if (x > y) return 1;
  return 0;
}

/**
 * Measured-or-null comparisons, nulls last.
 *
 * The null case is the load-bearing half: `parseTime(null)` is `null` (never
 * the epoch), and `null` sorts after every number rather than converting to a
 * fabricated `-Infinity`/`0`.
 */
function compareMeasuredDesc(a: number | null, b: number | null): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return b - a;
}

export function sortBots(
  bots: readonly BotProfile[],
  key: SortKey,
): BotProfile[] {
  const out = bots.slice();
  switch (key) {
    case "name":
      return out.sort(
        (a, b) =>
          compareText(a.display_name || a.name, b.display_name || b.name) ||
          compareText(a.name, b.name),
      );
    case "department":
      return out.sort(
        (a, b) =>
          compareText(a.department || "", b.department || "") ||
          compareText(a.name, b.name),
      );
    case "recent":
      // Most recent activity first; a bot with nothing recorded goes last.
      return out.sort(
        (a, b) =>
          compareMeasuredDesc(activityMs(a), activityMs(b)) ||
          compareText(a.name, b.name),
      );
    case "reputation":
      return out.sort(
        (a, b) =>
          compareMeasuredDesc(a.reputation_score, b.reputation_score) ||
          compareText(a.name, b.name),
      );
    case "tasks":
      return out.sort(
        (a, b) =>
          compareMeasuredDesc(totalRuns(a), totalRuns(b)) ||
          compareText(a.name, b.name),
      );
    case "server":
    default:
      return out;
  }
}

/**
 * `last_active` in milliseconds, or `null` when unreadable.
 *
 * Deliberately not `Number.parseInt(...) || 0`: a bot with no timestamp is
 * unknown, not "active in 1970", and converting it to zero would put it last
 * *for a reason the server never gave*.
 */
function activityMs(bot: BotProfile): number | null {
  const raw = bot.last_active;
  if (typeof raw !== "string" || !raw.trim()) return null;
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

/* -------------------------------------------------------------------------- */
/* Disclosure helpers                                                         */
/* -------------------------------------------------------------------------- */

/** How many facets are narrowing the view. `0` means "the whole roster". */
export function activeFilterCount(criteria: BotFilterCriteria): number {
  let n = 0;
  if (criteria.search.trim()) n += 1;
  if (criteria.department !== "all") n += 1;
  if (criteria.status !== "all") n += 1;
  if (criteria.model !== "all") n += 1;
  if (criteria.working !== "all") n += 1;
  if (criteria.activity !== "all") n += 1;
  if (criteria.runs !== "all") n += 1;
  if (criteria.sort !== "server") n += 1;
  return n;
}

export function isDefaultFilters(criteria: BotFilterCriteria): boolean {
  return activeFilterCount(criteria) === 0;
}

/**
 * Human-readable descriptions of the active facets, for the "N of M shown"
 * line. An empty array means nothing is hidden.
 */
export function activeFilterLabels(criteria: BotFilterCriteria): string[] {
  const labels: string[] = [];
  const search = criteria.search.trim();
  if (search) labels.push(`search "${search}"`);
  if (criteria.department !== "all")
    labels.push(`department ${criteria.department}`);
  if (criteria.status === STATUS_NOT_REPORTED)
    labels.push("status not reported");
  else if (criteria.status !== "all") labels.push(`status ${criteria.status}`);
  if (criteria.model === MODEL_NOT_REPORTED) labels.push("model not reported");
  else if (criteria.model !== "all") labels.push(`model ${criteria.model}`);
  if (criteria.working !== "all") {
    labels.push(
      `working: ${WORKING_FACET_WORDS[criteria.working] ?? criteria.working}`,
    );
  }
  if (criteria.activity !== "all") labels.push(`activity ${criteria.activity}`);
  if (criteria.runs !== "all") labels.push(`runs ${criteria.runs}`);
  if (criteria.sort !== "server") labels.push(`sorted ${criteria.sort}`);
  return labels;
}

/**
 * Short words for the disclosure line beside "N of M shown".
 *
 * Deliberately not the dropdown labels: those are full sentences for a menu
 * item ("Stalled / needs recovery"), and a summary line quoting them reads as
 * the facet's marketing copy rather than as a count's explanation.
 */
const WORKING_FACET_WORDS: Record<WorkingFacet, string> = {
  all: "any",
  working: "working now",
  stalled: "stalled",
  "not-working": "not working",
  unknown: "not reported",
};
