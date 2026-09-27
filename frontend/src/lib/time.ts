/**
 * One clock vocabulary for every surface that shows a time.
 *
 * Alpha carries three different time shapes on the wire:
 *
 *   - ISO-8601 strings — thread messages (`ChatMessage.createdAt`), group
 *     rows (`ChatMsg.at`), bot list rows (`BotProfile.last_active`);
 *   - epoch **seconds** — bot DMs (`DMInboxMessage.created_at` comes from
 *     `alpha/bots/inbox.py::DMMessage.created_at`, a `time.time()` float);
 *   - epoch milliseconds — anything already parsed by `Date`.
 *
 * They are read here so no component has to guess which shape it was handed.
 *
 * The honesty rule this module exists to enforce: **a value that cannot be
 * read returns `null`, never "now".** A fabricated timestamp is
 * indistinguishable from a real one once it is painted, and the previous
 * behaviour of falling back to `new Date().toISOString()` in
 * `lib/api.ts::messageFromRow` meant a history row with no recorded time
 * rendered as the moment the page happened to load.
 */

export type TimeInput = string | number | null | undefined;

const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Below this, a numeric timestamp is not a plausible record time. */
const MIN_PLAUSIBLE_MS = 978_307_200_000; // 2001-01-01T00:00:00Z
/** Above this the value is microseconds or finer, which nothing writes. */
const MAX_PLAUSIBLE_MS = 4_102_444_800_000; // 2100-01-01T00:00:00Z
/** Epoch seconds in 2001 are ~0.98e9; anything smaller is not an epoch. */
const MIN_PLAUSIBLE_SECONDS = MIN_PLAUSIBLE_MS / 1000;

function fromEpochNumber(value: number): number | null {
  if (!Number.isFinite(value) || value <= 0) return null;
  if (value >= MAX_PLAUSIBLE_MS) return null;
  if (value >= MIN_PLAUSIBLE_MS) return Math.round(value);
  if (value >= MIN_PLAUSIBLE_SECONDS) return Math.round(value * SECOND);
  return null;
}

/**
 * Read any of the three wire shapes as epoch milliseconds, or `null`.
 *
 * Numeric strings are classified by magnitude rather than by `Date.parse`,
 * which happily reads a bare `"1758980000"` as a year on some engines.
 */
export function parseTime(value: TimeInput): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return fromEpochNumber(value);
  const raw = value.trim();
  if (!raw) return null;
  if (/^-?\d+(\.\d+)?$/.test(raw)) return fromEpochNumber(Number(raw));
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

/** `true` when the value reads as a real, plausibly-dated timestamp. */
export function hasTime(value: TimeInput): boolean {
  return parseTime(value) !== null;
}

/**
 * Relative age — `"just now"`, `"5m ago"`, `"3h ago"`, `"2d ago"`, then the
 * date once it stops being useful as a distance.
 *
 * `now` is injectable so the buckets are testable without sleeping.
 *
 * A timestamp *after* `now` is clamped to `just now` rather than printed as a
 * countdown: client/server clock skew is routine, and `"in 3 hours"` would be
 * a claim about the future that nothing here can support.
 */
export function relTime(value: TimeInput, now: number = Date.now()): string | null {
  const ms = parseTime(value);
  if (ms === null) return null;
  const delta = Math.max(0, now - ms);
  if (delta < MINUTE) return "just now";
  if (delta < HOUR) return `${Math.floor(delta / MINUTE)}m ago`;
  if (delta < DAY) return `${Math.floor(delta / HOUR)}h ago`;
  if (delta < 7 * DAY) return `${Math.floor(delta / DAY)}d ago`;
  return new Date(ms).toLocaleDateString();
}

/** Clock time (`"14:32"` in a 12-hour locale), or `null` when unreadable. */
export function clockTime(value: TimeInput): string | null {
  const ms = parseTime(value);
  if (ms === null) return null;
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function dayKey(ms: number): string {
  const d = new Date(ms);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

/** `"Today"` / `"Yesterday"` / a locale date, or `null` when unreadable. */
export function dayLabel(value: TimeInput, now: number = Date.now()): string | null {
  const ms = parseTime(value);
  if (ms === null) return null;
  const today = dayKey(now);
  const key = dayKey(ms);
  if (key === today) return "Today";
  if (key === dayKey(now - DAY)) return "Yesterday";
  return new Date(ms).toLocaleDateString();
}

/** Absolute stamp for a `title` tooltip, or `null` when unreadable. */
export function absoluteStamp(value: TimeInput): string | null {
  const ms = parseTime(value);
  if (ms === null) return null;
  return new Date(ms).toLocaleString();
}

/**
 * Within `windowSeconds` of `now` — the presence predicate behind the roster's
 * active-now reading. An unreadable timestamp is *not* recent, so a bot with
 * no recorded activity never shows as working.
 */
export function isRecent(value: TimeInput, windowSeconds: number, now: number = Date.now()): boolean {
  const ms = parseTime(value);
  if (ms === null) return false;
  return Math.max(0, now - ms) <= windowSeconds * SECOND;
}
