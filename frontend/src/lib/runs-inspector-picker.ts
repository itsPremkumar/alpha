/**
 * Run-picker helpers for the run inspector: a client-side filter over the runs
 * already in memory, and the URL-hash permalink for one selected run.
 *
 * **This module makes no request.** The picker is a view over one bounded read
 * (`fetchRecentRuns`), so filtering it can never turn a list the Gateway has
 * already answered into a second, silent query — and it can never turn a
 * *failed* read into an empty list, because it never touches the transport. A
 * failed list read is still the caller's error to surface.
 *
 * The one thing the filter has to earn is the difference between two opposite
 * claims about the same empty array:
 *
 *  - the Gateway reported this conversation has **no runs**; and
 *  - the Gateway reported runs and the operator's query matched **none** of them.
 *
 * Those are not the same sentence, and the second one is not evidence about the
 * conversation at all. `applyRunFilter` keeps them apart with an explicit
 * `filtered` flag, so a view can say "the filter matched no loaded runs" over a
 * list the server filled without ever claiming the conversation is empty.
 *
 * Matching is a case-insensitive substring test against the four fields a run
 * record actually carries and the picker actually displays: `run_id`, `status`,
 * `model` and `error`. Nothing else is consulted — not token counts, not
 * timestamps, not the prompt text — and a field the server did not report stays
 * `null` and contributes nothing. It is never stringified into `"null"`, which
 * would let a query for the word "null" match every unreported run.
 */

import type { RunRecord } from "./runs-inspector";

/** The run fields a filter query is matched against, in the order shown. */
const MATCHED_FIELDS = ["run_id", "status", "model", "error"] as const;

/**
 * The query as the filter sees it: trimmed and lowercased.
 *
 * A caller with no query at all is the same as an empty box — both mean "show
 * the list the Gateway sent, in full" — so a missing local query string never
 * has to be invented into a filter that would hide rows.
 */
function normalizeQuery(query: string | null | undefined): string {
  return typeof query === "string" ? query.trim().toLowerCase() : "";
}

/**
 * True when the run's own fields contain the query.
 *
 * An empty query matches every run, because "no filter" is not a filter. A
 * `null` field matches nothing: the server reported no value there, and the
 * absence of a value is not a string to search in.
 */
export function runMatchesQuery(run: RunRecord, query: string): boolean {
  const needle = normalizeQuery(query);
  if (needle === "") return true;
  for (const field of MATCHED_FIELDS) {
    const value = run[field];
    if (typeof value !== "string") continue;
    if (value.toLowerCase().includes(needle)) return true;
  }
  return false;
}

/**
 * The runs that match the query, in the order the Gateway returned them.
 *
 * An empty or whitespace-only query returns **the same array**, untouched: the
 * unfiltered list is still the server's whole answer for this page, and handing
 * back a copy of it would suggest the client had done something to it.
 *
 * A query that matches nothing returns `[]`, which says only that *the filter*
 * matched nothing. Callers that need to tell that apart from "the server
 * reported no runs" must use `applyRunFilter` instead.
 */
export function filterRuns(runs: RunRecord[], query: string): RunRecord[] {
  const needle = normalizeQuery(query);
  if (needle === "") return runs;
  return runs.filter((run) => runMatchesQuery(run, needle));
}

export interface RunFilter {
  /** The rows to render. */
  runs: RunRecord[];
  /** The query as the filter normalized it: `""` when no filter is applied. */
  query: string;
  /**
   * True when a non-empty query is in force — i.e. when the list above is the
   * filter's answer rather than the Gateway's.
   *
   * `filtered: true` with `runs: []` means **the filter matched no loaded run**.
   * `filtered: false` with `runs: []` means the caller is looking at the
   * server's own empty list, which is a different sentence entirely.
   */
  filtered: boolean;
}

/**
 * `filterRuns` plus the flag a view needs to keep the two empty states apart.
 */
export function applyRunFilter(runs: RunRecord[], query: string): RunFilter {
  const needle = normalizeQuery(query);
  if (needle === "") return { runs, query: "", filtered: false };
  return { runs: runs.filter((run) => runMatchesQuery(run, needle)), query: needle, filtered: true };
}

/* ── the permalink ─────────────────────────────────────────────────────────── */

export const RUN_INSPECTOR_HASH_PREFIX = "run-inspector";

/**
 * The hash a permalink carries: `#run-inspector/<threadId>/<runId>`.
 *
 * The hash — not the query string — because the selection is a *view* of the
 * current page, not a request for a different resource, and because a hash
 * survives a reload without the app having to route anything.
 */
export function runInspectorHash(threadId: string, runId: string): string {
  return `#${RUN_INSPECTOR_HASH_PREFIX}/${encodeURIComponent(threadId)}/${encodeURIComponent(runId)}`;
}

/** A segment that is not a well-formed escape names no run this client can open. */
function decodeSegment(segment: string): string | null {
  try {
    return decodeURIComponent(segment);
  } catch {
    return null;
  }
}

/**
 * Read a run selection back out of a location hash, or `null` when the hash
 * names no run.
 *
 * `null` is deliberately one answer for "not one of ours", "no run id in it",
 * and "malformed escapes" — none of those is a run, and guessing which run a
 * broken link meant would be inventing a selection nobody asked for.
 */
export function parseRunInspectorHash(hash: string | null | undefined): { threadId: string; runId: string } | null {
  if (typeof hash !== "string") return null;
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  const parts = raw.split("/");
  if (parts[0] !== RUN_INSPECTOR_HASH_PREFIX) return null;
  const threadId = decodeSegment(parts[1] ?? "");
  const runId = decodeSegment(parts[2] ?? "");
  if (threadId === null || runId === null || threadId === "" || runId === "") return null;
  return { threadId, runId };
}

/**
 * The full permalink for one run, built from a page URL.
 *
 * Any hash already on `base` is replaced, so copying a link twice never stacks
 * two `#run-inspector/...` fragments into something unparseable.
 */
export function runPermalink(base: string, threadId: string, runId: string): string {
  const withoutHash = base.split("#")[0];
  return `${withoutHash === undefined ? base : withoutHash}${runInspectorHash(threadId, runId)}`;
}
