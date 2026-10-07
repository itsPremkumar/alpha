/**
 * Notification API client and delivery surface — the network layer plus the
 * two side effects the operator actually perceives: a chime and a toast.
 *
 * The rules that decide *whether* to notify live in `./notifications-model`,
 * which imports nothing; this file only performs them.
 *
 * ## Why the sound is synthesised
 *
 * The chime is generated with the Web Audio API rather than loaded from an
 * asset. A bundled `.mp3` is a file that can go missing, that costs a decode
 * before the first notification can be heard, and that the browser will refuse
 * to play until a user gesture has unlocked the context — a silent failure on
 * exactly the first notification, which is the one the operator is checking.
 * Two oscillators need no asset, start instantly, and cannot 404.
 */

import { get, send } from "@/lib/http";
import type { NotificationPreferences, NotificationRecord } from "./notifications-model";

export * from "./notifications-model";

export interface NotificationListResponse {
  notifications: NotificationRecord[];
  count: number;
  valid_types: string[];
  valid_priorities: string[];
}

export async function fetchNotifications(
  opts: { unreadOnly?: boolean; types?: string[]; limit?: number } = {},
): Promise<NotificationListResponse> {
  const params = new URLSearchParams();
  if (opts.unreadOnly) params.set("unreadOnly", "true");
  if (opts.types && opts.types.length > 0) params.set("types", opts.types.join(","));
  if (opts.limit) params.set("limit", String(opts.limit));
  const query = params.toString();
  const d = await get<Record<string, unknown>>(`/notifications${query ? `?${query}` : ""}`);
  return {
    notifications: (d.notifications as NotificationRecord[]) ?? [],
    count: typeof d.count === "number" ? d.count : 0,
    valid_types: (d.valid_types as string[]) ?? [],
    valid_priorities: (d.valid_priorities as string[]) ?? [],
  };
}

/**
 * The unread notification count, or `null` when the server did not report it.
 *
 * Returning `0` for a missing field would claim "you have no unread
 * notifications" for a read that never happened — the same absent-as-zero
 * defect this repository has been correcting all session.
 */
export async function fetchUnreadNotificationCount(): Promise<number | null> {
  const d = await get<Record<string, unknown>>("/notifications/unread-count");
  return typeof d.unread_count === "number" && Number.isFinite(d.unread_count)
    ? d.unread_count
    : null;
}

export async function markNotificationRead(id: string): Promise<void> {
  await send(`/notifications/${encodeURIComponent(id)}/read`, "POST");
}

export async function markAllNotificationsRead(): Promise<number> {
  const d = (await send("/notifications/read-all", "POST")) as Record<string, unknown>;
  return typeof d.marked_read === "number" ? d.marked_read : 0;
}

export async function fetchNotificationPreferences(): Promise<NotificationPreferences & { valid_types?: string[] }> {
  const d = await get<Record<string, unknown>>("/notifications/preferences");
  return (d as unknown) as NotificationPreferences & { valid_types?: string[] };
}

export async function updateNotificationPreferences(
  patch: Partial<NotificationPreferences>,
): Promise<NotificationPreferences> {
  return (await send("/notifications/preferences", "PATCH", patch)) as NotificationPreferences;
}

/** Create one notification so the whole delivery path can be verified live. */
export async function sendTestNotification(): Promise<NotificationRecord> {
  const d = await get<Record<string, unknown>>("/notifications/test");
  return d.notification as NotificationRecord;
}

// ── Sound ──────────────────────────────────────────────────────────────────

let context: AudioContext | null = null;
let unlocked = false;

/**
 * Unlock audio output from the first real user gesture.
 *
 * Browsers start an `AudioContext` suspended and only resume it from inside a
 * gesture handler. Registering once, at module scope, and remembering the
 * outcome means an early notification degrades to "no sound yet" instead of
 * throwing, and every later one plays.
 */
export function primeNotificationAudio(): void {
  if (typeof window === "undefined" || typeof AudioContext === "undefined") return;
  if (!context) context = new AudioContext();
  if (context.state === "suspended") {
    void context.resume().then(() => {
      unlocked = context?.state === "running";
    });
  } else {
    unlocked = true;
  }
}

/** Whether the audio path is currently able to make a noise. */
export function notificationAudioReady(): boolean {
  return unlocked && context !== null && context.state === "running";
}

/**
 * Play the notification chime.
 *
 * Returns `true` when a sound was actually scheduled, so a caller can tell
 * "played" from "silently skipped because audio was never unlocked" rather
 * than assuming the operator heard something.
 *
 * Priority sets the pitch and the gain: `urgent` is higher and louder so it
 * cuts through, `low` is a single quiet tone. The frequencies are fixed and
 * short — this is an attention cue, not a ringtone.
 */
export function playNotificationSound(priority = "normal"): boolean {
  if (typeof window === "undefined" || typeof AudioContext === "undefined") return false;
  if (!context) return false;
  if (context.state !== "running") return false;

  const profile =
    priority === "urgent"
      ? { base: 880, gain: 0.18, notes: [880, 1174.7] }
      : priority === "high"
        ? { base: 784, gain: 0.14, notes: [784, 1046.5] }
        : priority === "low"
          ? { base: 523.3, gain: 0.07, notes: [523.3] }
          : { base: 659.3, gain: 0.12, notes: [659.3, 880] };

  const now = context.currentTime;
  profile.notes.forEach((frequency, index) => {
    const oscillator = context!.createOscillator();
    const gain = context!.createGain();
    oscillator.type = "sine";
    oscillator.frequency.value = frequency;
    const start = now + index * 0.12;
    const end = start + 0.22;
    gain.gain.setValueAtTime(0.0001, start);
    // Exponential ramp from near-zero: a linear ramp to a fixed level leaves
    // an audible click at note onset, which is the part that reads as a glitch
    // rather than as a notification.
    gain.gain.exponentialRampToValueAtTime(profile.gain, start + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, end);
    oscillator.connect(gain);
    gain.connect(context!.destination);
    oscillator.start(start);
    oscillator.stop(end + 0.01);
  });

  return true;
}

// ── Desktop popups ─────────────────────────────────────────────────────────

/** `default` until proven otherwise — never claim permission we do not have. */
export function desktopPermission(): NotificationPermission | "unsupported" {
  if (typeof window === "undefined" || typeof Notification === "undefined") return "unsupported";
  return Notification.permission;
}

/**
 * Ask for desktop-popup permission. Resolves `false` when the browser lacks
 * the API or the operator declines — never throws, because a declined prompt
 * is an answer, not an error.
 */
export async function requestDesktopPermission(): Promise<boolean> {
  if (desktopPermission() === "unsupported") return false;
  try {
    const result = await Notification.requestPermission();
    return result === "granted";
  } catch {
    return false;
  }
}

/**
 * Raise a desktop popup for a notification.
 *
 * Returns `false` for anything it did not show — unsupported, denied, or a
 * failure to construct — so the caller can fall back to the in-app toast
 * instead of believing the operator has been told.
 */
export function showDesktopNotification(notification: NotificationRecord): boolean {
  if (desktopPermission() !== "granted") return false;
  try {
    const popup = new Notification(notification.title, {
      body: notification.body ?? "",
      tag: notification.notification_id,
      silent: true, // the in-app chime owns audio; two sounds for one event is noise
    });
    popup.onclick = () => {
      window.focus();
      if (notification.action_url) window.location.href = notification.action_url;
      popup.close();
    };
    return true;
  } catch {
    return false;
  }
}
