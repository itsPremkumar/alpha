/**
 * Group profile / links / goals / receipts derivations — **pure, no imports.**
 *
 * The network layer lives in `./groups-profile`; this file exists so the rules
 * above it can be tested without a bundler (`groups-profile.test.mjs` transpiles
 * this module and evaluates it with a `require` that throws on any import).
 *
 * The recurring rule across every function here: **an absent value and a zero
 * value are different facts.** A group with no description, a goal list nobody
 * has filled in, and a link list the read failed to return must not collapse
 * into the same rendering — otherwise the UI's calmest state is also its least
 * honest one.
 */

/** Fields the profile editor offers. Order is the render order. */
export const PROFILE_FIELDS = [
  "description",
  "purpose",
  "category",
  "tags",
  "avatar_url",
  "banner_url",
  "avatar_color",
] as const;

export type ProfileField = (typeof PROFILE_FIELDS)[number];

export interface GroupProfile {
  name?: string;
  topic?: string | null;
  description?: string | null;
  purpose?: string | null;
  goals?: unknown[];
  tags?: string[];
  category?: string | null;
  avatar_url?: string | null;
  banner_url?: string | null;
  avatar_color?: string | null;
  created_by?: string | null;
  valid_categories?: string[];
}

export interface GroupLink {
  link_id: string;
  room_id?: string;
  label: string;
  url: string;
  link_type?: string;
  icon?: string | null;
  position?: number;
  created_at?: string;
}

export interface GroupGoal {
  goal_id: string;
  title: string;
  description?: string;
  status?: string;
  progress?: number;
  created_at?: string;
  completed_at?: string | null;
}

export interface ProfileCompletion {
  /** How many of the offered fields carry a value. */
  filled: number;
  /** How many fields the editor offers — never derived from what is filled. */
  total: number;
  /** 0–100, rounded. `100` means every offered field is set. */
  percent: number;
  /** The fields still empty, in render order. */
  missing: ProfileField[];
}

/**
 * How complete a group's identity is.
 *
 * `total` is always `PROFILE_FIELDS.length`, not `count(present fields)`:
 * measuring the denominator from the data would make an empty profile score
 * 0/0 = "complete", which is exactly the state this is meant to surface.
 */
export function profileCompletion(profile: GroupProfile | null | undefined): ProfileCompletion {
  const missing: ProfileField[] = [];
  for (const field of PROFILE_FIELDS) {
    const value = profile ? (profile as Record<string, unknown>)[field] : undefined;
    const empty =
      value === undefined || value === null || (typeof value === "string" && value.trim() === "") ||
      (Array.isArray(value) && value.length === 0);
    if (empty) missing.push(field);
  }
  const total = PROFILE_FIELDS.length;
  const filled = total - missing.length;
  return { filled, total, percent: Math.round((filled / total) * 100), missing };
}

/**
 * A one-line profile headline for a collapsed pane.
 *
 * Never says "100%" for a profile the caller did not read: an unknown profile
 * (`null`) is reported as unknown, not as empty and not as full.
 */
export function profileHeadline(profile: GroupProfile | null | undefined): string {
  if (!profile) return "Profile not read yet";
  const { percent, filled, total } = profileCompletion(profile);
  if (filled === total) return "Profile complete";
  if (filled === 0) return "No profile yet";
  return `Profile ${filled}/${total} fields (${percent}%)`;
}

/**
 * Only `http(s)` links survive.
 *
 * A `javascript:` or `data:` URL in a group link is an injection vector the
 * moment the label is clicked, so the caller gets `null` and must render the
 * link as inert text rather than as an anchor.
 */
export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null;
  const trimmed = url.trim();
  if (trimmed === "") return null;
  // `URL` is a global in every supported runtime; parse rather than regex so a
  // mixed-case or entity-obfuscated scheme is caught by the parser, not by a
  // pattern someone has to keep correct by hand.
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") return parsed.toString();
    return null;
  } catch {
    return null;
  }
}

/**
 * Group links by declared type, preserving the type declaration order so the
 * rendered grouping is stable across reads.
 */
export function linksByType(links: GroupLink[] | null | undefined, types: string[]): Record<string, GroupLink[]> {
  const grouped: Record<string, GroupLink[]> = {};
  for (const type of types) grouped[type] = [];
  for (const link of links ?? []) {
    const type = link.link_type || "custom";
    if (!grouped[type]) grouped[type] = [];
    grouped[type].push(link);
  }
  for (const type of Object.keys(grouped)) grouped[type].sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
  return grouped;
}

export interface GoalSummary {
  total: number;
  completed: number;
  /** 0–100 across every goal, by its own `progress`. */
  percent: number;
  /** The first goal not yet completed, or `null` when there is none. */
  next: string | null;
}

/**
 * Roll the goal list up to one honest headline.
 *
 * `percent` is the mean of the goals' own progress rather than
 * `completed / total`, because a list of half-finished goals is genuinely
 * half-finished and "2 of 4" would report it as 0% or 50% depending on which
 * goal happened to be closed first. An empty list is 0%, never 100%.
 */
export function goalSummary(goals: GroupGoal[] | null | undefined): GoalSummary {
  const list = goals ?? [];
  if (list.length === 0) return { total: 0, completed: 0, percent: 0, next: null };
  let completed = 0;
  let sum = 0;
  let next: string | null = null;
  for (const goal of list) {
    const progress = typeof goal.progress === "number" ? Math.max(0, Math.min(100, goal.progress)) : 0;
    sum += progress;
    if (goal.status === "completed" || progress >= 100) completed += 1;
    else if (next === null) next = goal.title;
  }
  return { total: list.length, completed, percent: Math.round(sum / list.length), next };
}

/**
 * "N of M" for a read-receipt list.
 *
 * The two numbers travel together on purpose: a bar that shows 4/6 read next
 * to a list of six rows is checkable, while a percentage alone is not.
 */
export function readersSummary(readers: string[] | null | undefined, members: string[] | null | undefined): {
  read: number;
  total: number;
  unread: string[];
  percent: number;
} {
  const roster = (members ?? []).filter(Boolean);
  const seen = new Set((readers ?? []).map((name) => name.trim()).filter(Boolean));
  // The denominator is the roster when one was supplied; otherwise the read
  // list is all the evidence there is, and inventing a larger denominator
  // would report readers who were never going to read it.
  const total = roster.length > 0 ? roster.length : seen.size;
  const unread = roster.filter((name) => !seen.has(name));
  return {
    read: seen.size,
    total,
    unread,
    percent: total === 0 ? 0 : Math.round((seen.size / total) * 100),
  };
}

/**
 * A typing line, or `""` when nobody is typing.
 *
 * `""` — not "nobody is typing" — so an empty indicator is genuinely absent
 * from the layout rather than being a permanent line of text.
 */
export function typingHeadline(names: string[] | null | undefined): string {
  const list = (names ?? []).filter(Boolean);
  if (list.length === 0) return "";
  if (list.length === 1) return `${list[0]} is typing…`;
  if (list.length === 2) return `${list[0]} and ${list[1]} are typing…`;
  return `${list[0]}, ${list[1]} and ${list.length - 2} more are typing…`;
}

/**
 * Count messages a reader has not seen.
 *
 * Deleted rows are dropped: they were withheld from the transcript, and
 * counting them as unread would keep a badge lit over content nobody can see.
 */
export function unreadCount(
  messages: Array<{ deleted?: boolean }> | null | undefined,
  hasReceipt: (message: { deleted?: boolean }) => boolean,
): number {
  let count = 0;
  for (const message of messages ?? []) {
    if (message.deleted) continue;
    if (!hasReceipt(message)) count += 1;
  }
  return count;
}

/**
 * Merge a server event into a bounded client-side recent-event list.
 *
 * The newest event wins a slot and the list never grows past `limit`, so a
 * stream that stays open all day cannot leak memory one event at a time.
 */
export function appendBounded<T>(list: T[], item: T, limit: number): T[] {
  const bound = Math.max(1, Math.floor(limit));
  return [item, ...list].slice(0, bound);
}
