/**
 * Pure view derivations for the Messages surface (`MessagesSection`).
 *
 * The section is ~1,900 lines of JSX and owns the honest *read* states — a
 * failed room read, an unread roster, a history nobody has opened. Those
 * sentences are pinned where they are rendered (see
 * `collaboration-surfaces-honesty.test.mjs`), so they deliberately live in the
 * component and not here: a second copy in a helper is how a transcript and its
 * own helper end up contradicting each other.
 *
 * What DOES live here is the *shape* of the lists — how the conversation column
 * splits into sections, how many rows a filter chip claims to hold, and when
 * consecutive transcript rows are one author continuing rather than a new
 * speaker. Those are derivations, they decide what a reader sees at a glance,
 * and they are exactly the kind of rule that rots quietly when it is inlined in
 * JSX: nothing fails, the numbers just stop meaning what the label says.
 *
 * No imports with runtime values: `messages-view.test.mjs` transpiles this file
 * and evaluates it with a `require` that throws, so an accidental import is a
 * test failure rather than a second implementation drifting beside this one.
 */

/** Which column of the workspace a conversation belongs to. */
export type ConversationKind = "group" | "dm";

/**
 * One row of the conversation column.
 *
 * `lastAt` is `null` when the server carried no timestamp — never `""`, which
 * would sort as older than every real time and read as "never".
 */
export interface ConversationRow {
  id: string;
  kind: ConversationKind;
  title: string;
  subtitle: string;
  lastText: string;
  lastAt: string | null;
  unread: number;
  members: string[];
}

/** One labelled group of rows in the conversation column. */
export interface ConversationSection {
  key: "groups" | "direct";
  label: string;
  rows: ConversationRow[];
}

/**
 * Split rows into "Groups" then "Direct".
 *
 * An empty section is omitted entirely rather than rendered with a count of
 * zero: a header over nothing invites the reader to look for rows that are not
 * coming, and "Direct (0)" says the server measured a zero when in fact there
 * were simply no DM threads to place under it.
 *
 * Row order is preserved — the caller already sorted by recency, and re-sorting
 * here would silently disagree with that decision.
 */
export function sectionConversations(
  rows: readonly ConversationRow[],
): ConversationSection[] {
  const groups = rows.filter((r) => r.kind === "group");
  const direct = rows.filter((r) => r.kind === "dm");
  const out: ConversationSection[] = [];
  if (groups.length > 0)
    out.push({ key: "groups", label: "Groups", rows: groups });
  if (direct.length > 0)
    out.push({ key: "direct", label: "Direct", rows: direct });
  return out;
}

/**
 * The disclosure for a list a filter or search shortened: "Showing 3 of 12".
 *
 * `null` when nothing was hidden, so the happy path renders no sentence at all
 * rather than a redundant "Showing 12 of 12". A short list that never says how
 * many it is short by reads as the whole inbox.
 */
export function hiddenSummary(shown: number, total: number): string | null {
  if (shown >= total) return null;
  const hidden = total - shown;
  return `Showing ${shown} of ${total} — ${hidden} hidden by this filter or search`;
}

/** `99+` above the range a pill can hold; the real count is still in the row. */
export function unreadLabel(unread: number): string {
  return unread > 99 ? "99+" : String(unread);
}

/** One author continuing: the header names them once, not on every row. */
export interface MessageRun<T> {
  sender: string;
  day: string;
  items: T[];
}

/**
 * Group consecutive transcript rows that are the same author on the same day.
 *
 * A run breaks when the author changes, when the day changes, and when a
 * deleted row sits between two live ones. The third rule is the one that reads
 * as pedantry until it fires: a tombstone renders as its own line of italic
 * text, and a tombstone sharing a header with the message above it implies the
 * two were written together.
 *
 * `dayOf` is injected rather than imported so this module stays import-free;
 * the component passes its own `dayLabel`, which is the same clock vocabulary
 * the transcript already uses for its dividers. A row with no recorded time
 * yields `""` from that helper and therefore groups with other unstamped rows —
 * grouping by a day nobody measured would be inventing one.
 */
export function groupMessageRuns<
  T extends { sender: string; deleted?: boolean },
>(messages: readonly T[], dayOf: (message: T) => string): MessageRun<T>[] {
  const runs: MessageRun<T>[] = [];
  for (const message of messages) {
    const day = dayOf(message);
    const previous = runs.length > 0 ? runs[runs.length - 1] : null;
    const continues =
      previous !== null &&
      previous.sender === message.sender &&
      previous.day === day &&
      Boolean(previous.items[previous.items.length - 1].deleted) ===
        Boolean(message.deleted);
    if (continues && previous) {
      previous.items.push(message);
    } else {
      runs.push({ sender: message.sender, day, items: [message] });
    }
  }
  return runs;
}
