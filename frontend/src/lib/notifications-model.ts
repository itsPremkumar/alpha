/**
 * Notification derivations — **pure, no imports.**
 *
 * The network layer and the sound player live in `./notifications`; this file
 * holds every rule that decides *what the operator is shown*, so
 * `notifications.test.mjs` can check them without a DOM or a fetch.
 *
 * The property this whole module protects: **"the event happened" and "the
 * operator was told" are separate facts.** A muted notification still exists in
 * history and still counts as unread; a preference that is off must not delete
 * anything, and the badge must not go quiet just because the sound did.
 */

/** Urgency ladder, loudest last. Mirrors `alpha.notifications.NOTIFICATION_PRIORITIES`. */
export const PRIORITY_ORDER = ["low", "normal", "high", "urgent"] as const;
export type NotificationPriority = (typeof PRIORITY_ORDER)[number];

export const NOTIFICATION_TYPES = [
  "message",
  "mention",
  "activity",
  "claim",
  "run",
  "system",
] as const;
export type NotificationType = (typeof NOTIFICATION_TYPES)[number];

export interface NotificationRecord {
  notification_id: string;
  type: string;
  title: string;
  body?: string;
  room_id?: string | null;
  message_id?: string | null;
  sender?: string | null;
  priority?: string;
  created_at?: string;
  read?: boolean;
  read_at?: string | null;
  action_url?: string | null;
  icon?: string | null;
  sound?: boolean;
}

export interface NotificationPreferences {
  enabled: boolean;
  sound_enabled: boolean;
  desktop_enabled?: boolean;
  types?: Record<string, boolean>;
  priorities?: Record<string, boolean>;
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
  digest_mode?: boolean;
  digest_interval_minutes?: number;
}

/**
 * Rank on the urgency ladder.
 *
 * An unrecognised priority ranks as `normal` rather than as `low`: an unknown
 * value is a vocabulary this build did not anticipate, and treating it as the
 * quietest thing would let a future backend escalation render silently.
 */
export function priorityRank(priority: string | null | undefined): number {
  const index = PRIORITY_ORDER.indexOf((priority ?? "normal") as NotificationPriority);
  return index === -1 ? PRIORITY_ORDER.indexOf("normal") : index;
}

/**
 * Does this preference admit this notification?
 *
 * Mirrors `NotificationPreference.allows` exactly. The three-step fallback —
 * priority gate, then type gate, then the global switch — is what keeps an
 * *absent* key from reading as a muted one.
 */
export function allows(preferences: NotificationPreferences | null | undefined, notification: NotificationRecord): boolean {
  if (!preferences) return true; // no preference read yet: default posture, not silence
  if (preferences.enabled === false) return false;

  const priority = notification.priority ?? "normal";
  const priorityVerdict = preferences.priorities?.[priority] ?? PRIORITY_DEFAULTS[priority] ?? true;
  if (!priorityVerdict) return false;

  const typeVerdict = preferences.types?.[notification.type] ?? true;
  if (!typeVerdict) return false;

  return true;
}

const PRIORITY_DEFAULTS: Record<string, boolean> = { low: false, normal: true, high: true, urgent: true };

/**
 * Should the chime play?
 *
 * Three independent gates, all of which must pass: the preference admits the
 * notification, sound is on, and we are not inside quiet hours. The record's
 * own `sound` flag is honoured last because a system-level "no sound on this
 * one" beats a global "sound on".
 */
export function shouldSound(
  preferences: NotificationPreferences | null | undefined,
  notification: NotificationRecord,
  now: Date = new Date(),
): boolean {
  if (notification.sound === false) return false;
  if (!preferences) return false; // never audible before the operator's choice is read
  if (preferences.enabled === false) return false;
  if (preferences.sound_enabled === false) return false;
  if (inQuietHours(preferences, now)) return false;
  return allows(preferences, notification);
}

/**
 * Are we between the configured quiet hours?
 *
 * A window that crosses midnight (`22:00`–`07:00`) is the normal case and has
 * to be read as two segments; a malformed or absent pair means "no quiet
 * hours", because refusing to play over an unparsable schedule would invent a
 * restriction the operator never set.
 */
export function inQuietHours(preferences: NotificationPreferences | null | undefined, now: Date = new Date()): boolean {
  const start = parseClock(preferences?.quiet_hours_start);
  const end = parseClock(preferences?.quiet_hours_end);
  if (start === null || end === null || start === end) return false;
  const minutes = now.getHours() * 60 + now.getMinutes();
  return start < end ? minutes >= start && minutes < end : minutes >= start || minutes < end;
}

function parseClock(value: string | null | undefined): number | null {
  if (!value) return null;
  const match = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (!Number.isFinite(hours) || !Number.isFinite(minutes)) return null;
  if (hours > 23 || minutes > 59) return null;
  return hours * 60 + minutes;
}

/**
 * Bell label: `""` for zero — so no dot renders at all — and `99+` past the
 * readable range, because a three-digit badge stops being countable anyway.
 */
export function unreadBadge(count: number | null | undefined): string {
  const n = typeof count === "number" && Number.isFinite(count) && count > 0 ? Math.floor(count) : 0;
  if (n === 0) return "";
  if (n > 99) return "99+";
  return String(n);
}

/**
 * Sort newest-first by creation time.
 *
 * Records with no parsable timestamp are parked *after* everything dated and
 * keep their relative incoming order. That is a display convention, not a
 * claim about age: "newest first" is only ever asserted about records that
 * actually carry a time, and an undated one is unknown age rather than
 * oldest — so it is neither promoted to the top nor interleaved as if its
 * position in the source were evidence.
 *
 * The comparator is a total order (dated-vs-dated, dated-vs-undated,
 * undated-vs-undated), because a comparator that answers the same pair
 * differently depending on argument order makes `Array#sort` results depend
 * on the engine's insertion order.
 */
export function sortNewestFirst(list: NotificationRecord[] | null | undefined): NotificationRecord[] {
  const input = list ?? [];
  return input
    .map((notification, index) => ({ notification, index, at: Date.parse(notification.created_at ?? "") }))
    .sort((a, b) => {
      const aDated = !Number.isNaN(a.at);
      const bDated = !Number.isNaN(b.at);
      if (aDated !== bDated) return aDated ? -1 : 1;
      if (!aDated) return a.index - b.index;
      return b.at - a.at || a.index - b.index;
    })
    .map((entry) => entry.notification);
}

export interface NotificationFilter {
  unreadOnly?: boolean;
  types?: string[];
  /** Minimum priority to show; anything below is hidden but not deleted. */
  minPriority?: string;
}

/**
 * Apply a client-side filter.
 *
 * Filtering never mutates the source list — a hidden notification is still in
 * history and still counted, so the operator can always tell that something
 * was filtered rather than that it never arrived.
 */
export function filterNotifications(
  list: NotificationRecord[] | null | undefined,
  filter: NotificationFilter = {},
): NotificationRecord[] {
  const floor = filter.minPriority ? priorityRank(filter.minPriority) : -1;
  return (list ?? []).filter((notification) => {
    if (filter.unreadOnly && notification.read) return false;
    if (filter.types && filter.types.length > 0 && !filter.types.includes(notification.type)) return false;
    if (floor >= 0 && priorityRank(notification.priority) < floor) return false;
    return true;
  });
}

export interface ToastState {
  visible: NotificationRecord[];
  /** Notifications waiting because `max` are already on screen. */
  queued: number;
}

/**
 * Push a notification onto a bounded toast stack.
 *
 * The queue count is reported rather than silently dropped: an operator who
 * asked for five toasts and got five should still learn that six arrived.
 */
export function enqueueToast(state: ToastState, notification: NotificationRecord, max = 3): ToastState {
  const bound = Math.max(1, Math.floor(max));
  const visible = [notification, ...state.visible.filter((n) => n.notification_id !== notification.notification_id)];
  if (visible.length <= bound) return { visible, queued: state.queued };
  return {
    visible: visible.slice(0, bound),
    queued: state.queued + (visible.length - bound),
  };
}

/**
 * Dismiss the oldest on-screen toast and promote one from the queue.
 *
 * `visible` is newest-first, so the oldest sits at the end — which is the one
 * whose own timer expires first. Exactly one queued record moves up per freed
 * slot, so the pending count can never drift away from the space it is
 * waiting for.
 */
export function popToast(state: ToastState): ToastState {
  if (state.visible.length === 0) return { visible: [], queued: Math.max(0, state.queued) };
  return {
    visible: state.visible.slice(0, -1),
    queued: Math.max(0, state.queued - 1),
  };
}

/**
 * Group notifications by calendar day, newest day first.
 *
 * Uses the record's own local calendar day rather than a raw ISO date string:
 * `2026-10-03T23:30:00+00:00` read as UTC is tomorrow for an operator east of
 * Greenwich, and a "Tomorrow" heading would be a wrong claim about when the
 * message arrived.
 */
export function groupByDay(
  list: NotificationRecord[] | null | undefined,
  now: Date = new Date(),
): Array<{ key: string; label: string; items: NotificationRecord[] }> {
  const today = dayKey(now);
  const yesterday = dayKey(new Date(now.getTime() - 86_400_000));
  const buckets = new Map<string, NotificationRecord[]>();
  for (const notification of sortNewestFirst(list)) {
    const at = new Date(notification.created_at ?? now.getTime());
    const key = dayKey(Number.isNaN(at.getTime()) ? now : at);
    const bucket = buckets.get(key);
    if (bucket) bucket.push(notification);
    else buckets.set(key, [notification]);
  }
  return [...buckets.entries()].map(([key, items]) => ({
    key,
    label: key === today ? "Today" : key === yesterday ? "Yesterday" : key,
    items,
  }));
}

function dayKey(at: Date): string {
  const year = at.getFullYear();
  const month = String(at.getMonth() + 1).padStart(2, "0");
  const day = String(at.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
