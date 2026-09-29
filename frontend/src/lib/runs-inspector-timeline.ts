/**
 * Timeline derivation — the pure half of one run's event timeline.
 *
 * `runs-inspector.ts` owns the read: it maps the Gateway's event stream into
 * `TimelineEvent[]` with a server-derived `severity` on every row. This module
 * owns only the *view* derivations the timeline surface needs — the three
 * filters, the counts beside them, and the summary line — so each of them is
 * testable without React and cannot drift from the words the panel prints.
 *
 * The rules it exists to hold:
 *
 *  - **A filter filters; it never re-sorts.** The store's order is the order the
 *    server returned it in (`seq` ascending on this endpoint), and the server is
 *    its only authority. Sorting the rows by a key this client chose would make
 *    the list look like the store's sequence when it is not — including for an
 *    event whose `seq` arrived out of order, which is a fact about the read, not
 *    something to tidy away.
 *  - **The label is the whole claim.** `Errors` keeps `severity === "error"`
 *    and nothing else. A button labelled `Errors` that also lists warnings is
 *    making a different claim than the one its name makes, and the number
 *    printed next to it would then be a count of something the label does not
 *    name. This is also exactly the `isRunIssueEvent` predicate in `runs.ts`,
 *    so the two cannot disagree about which rows are failure evidence.
 *  - **An empty filtered list is the FILTER's answer.** It says nothing about
 *    what the run recorded; `timelineNoMatchMessage` says so in words, because
 *    "the filter matched nothing" and "the run recorded nothing" are opposite
 *    claims about the same screen.
 *  - **A count is 0 only when the server reported 0.** No event, no count.
 *  - **No timestamp is ever invented.** The summary's `first` / `last` are the
 *    raw `created_at` strings the server wrote, taken at the store-order
 *    boundaries. There is no `Date.now()` fallback, no "now", and no reparse
 *    into a formatted value here — a row that reported no time contributes no
 *    boundary rather than the moment this page happened to load.
 */

import type { RunEventSeverity } from "./runs";
import type { TimelineEvent } from "./runs-inspector";

/** Which rows the timeline shows. `all` is the only one that shows every row. */
export type TimelineFilter = "all" | "errors" | "warnings";

export interface TimelineFilterOption {
  id: TimelineFilter;
  /** The button's own label. It names the filter and nothing more. */
  label: string;
  /**
   * The single severity this filter keeps, or `null` for "keeps every
   * severity". This is the whole behaviour — the label is not a summary of a
   * wider rule.
   */
  severity: RunEventSeverity | null;
}

/**
 * The three filters, in the order the panel renders them.
 *
 * The table is the single source of truth for both the buttons and the
 * severity each one keeps, so a label can never end up describing a different
 * set of rows than the code selects.
 */
export const TIMELINE_FILTERS: readonly TimelineFilterOption[] = [
  { id: "all", label: "All", severity: null },
  { id: "errors", label: "Errors", severity: "error" },
  { id: "warnings", label: "Warnings", severity: "warn" },
];

function optionFor(filter: TimelineFilter): TimelineFilterOption | undefined {
  return TIMELINE_FILTERS.find((option) => option.id === filter);
}

/** The label for a filter id, or a loud "Unknown filter" for anything else. */
export function timelineFilterLabel(filter: TimelineFilter): string {
  return optionFor(filter)?.label ?? "Unknown filter";
}

/**
 * The rows a filter selects, in the store's own order.
 *
 * Returns a new array; the caller's list is never mutated or reordered. An
 * unrecognised id selects **nothing**: quietly falling back to "everything"
 * would paint an unfiltered stream under a filter label and claim a narrowing
 * that never happened.
 */
export function filterTimelineEvents(events: TimelineEvent[], filter: TimelineFilter): TimelineEvent[] {
  const option = optionFor(filter);
  if (option === undefined) return [];
  if (option.severity === null) return [...events];
  return events.filter((event) => event.severity === option.severity);
}

export interface TimelineSummary {
  /** Every event the Gateway returned for this read. */
  total: number;
  errors: number;
  warnings: number;
  /**
   * Events the client classified as `info`. `runEventSeverity` only ever
   * returns the three severities, so the three buckets cover every event the
   * read can produce; a severity this client has never seen would be counted in
   * `total` and in **no** bucket, because calling it `info` would be a
   * classification the Gateway did not make. `total` is the count that never
   * drops a row, which is what the `All` filter and the summary header show.
   */
  info: number;
  /**
   * The `created_at` of the first event that reported one, verbatim, or `null`.
   * A store-order boundary, not a sort: the list is not reordered to find it,
   * and an event that reported no time contributes no boundary rather than
   * erasing the real times the events around it did report.
   */
  first: string | null;
  /** The `created_at` of the last event that reported one, verbatim, or `null`. */
  last: string | null;
}

/**
 * Count the read by severity and name its store-order time boundaries.
 *
 * An empty list yields all-zero counts and two `null` boundaries — the honest
 * "the Gateway reported no persisted events", which the panel words as the
 * server's answer rather than a failed read. An event that reported no
 * `created_at` still counts toward `total`; it simply contributes no boundary,
 * and a run whose every event is untimed yields two `null` boundaries rather
 * than two invented ones.
 */
export function timelineSummary(events: TimelineEvent[]): TimelineSummary {
  const summary: TimelineSummary = { total: 0, errors: 0, warnings: 0, info: 0, first: null, last: null };
  for (const event of events) {
    summary.total += 1;
    if (event.severity === "error") summary.errors += 1;
    else if (event.severity === "warn") summary.warnings += 1;
    else if (event.severity === "info") summary.info += 1;
    // Anything else is counted in `total` and in no severity bucket, so an
    // unrecognised severity is never relabelled as `info`.
    if (event.createdAt !== null) {
      if (summary.first === null) summary.first = event.createdAt;
      summary.last = event.createdAt;
    }
  }
  return summary;
}

/**
 * The count to print beside a filter button: exactly how many rows selecting it
 * will show. Pinned against `filterTimelineEvents(...).length` in the tests, so
 * a number on a button can never be a count of rows that button will not draw.
 */
export function filterCount(summary: TimelineSummary, filter: TimelineFilter): number {
  if (filter === "all") return summary.total;
  if (filter === "errors") return summary.errors;
  if (filter === "warnings") return summary.warnings;
  return 0;
}

/**
 * What a filter that matched nothing says.
 *
 * Deliberately names the filter and the run's real total, and never says the
 * run recorded no events: the two are opposite claims, and only the first one is
 * what an empty filtered list shows.
 */
export function timelineNoMatchMessage(filter: TimelineFilter, summary: TimelineSummary): string {
  const label = timelineFilterLabel(filter);
  const events = summary.total === 1 ? "event" : "events";
  return (
    `The ${label} filter matched none of the ${summary.total} ${events} the Gateway returned for this run. ` +
    `That is what this filter selected, not a claim that the run recorded nothing.`
  );
}
