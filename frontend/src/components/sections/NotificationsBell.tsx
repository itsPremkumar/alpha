"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Bell, BellOff, Check, Monitor, ShieldCheck, Sparkles, Users, Volume2, X, Send } from "lucide-react";

import { Btn, ErrorBox } from "@/components/ui";
import { errMsg } from "@/lib/http";
import {
  allows,
  desktopPermission,
  enqueueToast,
  fetchNotificationPreferences,
  fetchNotifications,
  fetchUnreadNotificationCount,
  groupByDay,
  markAllNotificationsRead,
  markNotificationRead,
  playNotificationSound,
  popToast,
  primeNotificationAudio,
  notificationAudioReady,
  requestDesktopPermission,
  sendTestNotification,
  shouldSound,
  showDesktopNotification,
  sortNewestFirst,
  unreadBadge,
  updateNotificationPreferences,
  type NotificationPreferences,
  type NotificationRecord,
} from "@/lib/notifications";

const POLL_MS = 20_000;
const TOAST_MS = 6_000;
const MAX_VISIBLE = 3;

/**
 * The operator's ONE notification surface: a bell with a real unread count, a
 * day-grouped history, the preference switches, a toast + chime when a new
 * notification arrives, and the workspace/gateway status.
 *
 * **The header used to render two icons that both said "Notifications."** This
 * was the full one; beside it sat a hand-rolled `<Bell>` toggle whose panel
 * held a gateway status card and nothing else. Two bells is one too many, so
 * every feature of that toggle now lives here under `gatewayOk` and
 * `teamUnread`, and `WorkspaceTopBar` mounts a single `<NotificationsBell />`.
 *
 * The property everything below defends: **the first read establishes a
 * baseline and never announces anything.** Anything that sounded on page load
 * would be announcing notifications the operator has already seen, which is
 * the fastest way to train them to ignore the chime — so `seen` is seeded from
 * the first successful read and only ids that arrive *after* it are new.
 *
 * The three reads (preferences, list, count) are independent on purpose: a
 * Gateway that answers the list but not the preferences must not present the
 * absence as "sound is off", and a failed count must not render as zero.
 */
export function NotificationsBell(props: {
  onError?: (message: string) => void;
  /**
   * Gateway reachability as the shared header probes it, or `null` while it is
   * still connecting. Carried over from the removed second bell; it is the one
   * status this header actually measures, so it is the only one rendered.
   */
  gatewayOk?: boolean | null;
  /**
   * Unread messages across the bot roster, or `null` when the roster read did
   * not ask for the activity projection. `null` is not zero: it renders
   * "not reported", exactly as `unreadCount` documents it in the top bar.
   */
  teamUnread?: number | null;
}) {
  const { gatewayOk = null, teamUnread = null } = props;
  const [items, setItems] = useState<NotificationRecord[] | null>(null);
  const [itemsError, setItemsError] = useState<string | null>(null);
  const [unread, setUnread] = useState<number | null>(null);
  const [unreadError, setUnreadError] = useState<string | null>(null);
  const [prefs, setPrefs] = useState<NotificationPreferences | null>(null);
  const [prefsError, setPrefsError] = useState<string | null>(null);

  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  /**
   * Dismiss on an outside click or Escape.
   *
   * This component never had one while it shared the bar with the second bell,
   * because that bell had its own handler. Now that it IS the only bell, a
   * panel that only closes by clicking its own trigger again would be the
   * merged control's most visible flaw — the same `absolute`-inside-`relative`
   * shape as the profile menu, so one ref covers it (no portal involved).
   */
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const [busy, setBusy] = useState(false);
  const [toasts, setToasts] = useState<{ visible: NotificationRecord[]; queued: number }>({
    visible: [],
    queued: 0,
  });
  const [audioNotice, setAudioNotice] = useState<string | null>(null);

  /** Ids already announced. Seeded on the first successful list read. */
  const seen = useRef<Set<string> | null>(null);
  const baselineDone = useRef(false);
  /**
   * Preferences read through a ref, not the state value.
   *
   * The poll interval is started once with an empty dependency list, so a
   * closure over `prefs` would freeze the *first* value it saw — `null` — and
   * `shouldSound(null, …)` is false forever, i.e. the chime would never play
   * no matter what the operator set. The ref is the live answer.
   */
  const prefsRef = useRef<NotificationPreferences | null>(null);
  useEffect(() => {
    prefsRef.current = prefs;
  }, [prefs]);

  const loadList = useCallback(async () => {
    const read = await fetchNotifications({ limit: 50 });
    const list = sortNewestFirst(read.notifications);
    setItems(list);
    setItemsError(null);

    if (!baselineDone.current) {
      // First read: remember what is already here, announce nothing.
      baselineDone.current = true;
      seen.current = new Set(list.map((n) => n.notification_id));
      return;
    }

    const fresh = list.filter((n) => !seen.current!.has(n.notification_id));
    if (fresh.length === 0) return;
    for (const notification of fresh) seen.current!.add(notification.notification_id);

    const current = prefsRef.current;
    let sounded = false;
    if (shouldSound(current, fresh[0])) sounded = playNotificationSound(fresh[0].priority ?? "normal");
    if (!sounded && shouldSound(current, { ...fresh[0], sound: true })) {
      setAudioNotice("A notification arrived but the browser had not unlocked audio yet — click anywhere once.");
    }
    if (current && allows(current, fresh[0])) showDesktopNotification(fresh[0]);
    setToasts((state) => fresh.reduce((next, n) => enqueueToast(next, n, MAX_VISIBLE), state));
  }, []);

  const loadUnread = useCallback(async () => {
    try {
      setUnread(await fetchUnreadNotificationCount());
      setUnreadError(null);
    } catch (e) {
      setUnreadError(errMsg(e));
    }
  }, []);

  const loadPrefs = useCallback(async () => {
    try {
      setPrefs(await fetchNotificationPreferences());
      setPrefsError(null);
    } catch (e) {
      setPrefsError(errMsg(e));
    }
  }, []);

  useEffect(() => {
    primeNotificationAudio();
    void loadPrefs();
    void loadList()
      .then(() => void loadUnread())
      .catch((e) => setItemsError(errMsg(e)));

    const handle = window.setInterval(() => {
      void loadList()
        .then(() => void loadUnread())
        .catch((e) => setItemsError(errMsg(e)));
    }, POLL_MS);
    return () => window.clearInterval(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Toast expiry: the stack is newest-first, so one tick retires the oldest —
  // the same order each toast's own timer would have used.
  useEffect(() => {
    if (toasts.visible.length === 0) return;
    const handle = window.setInterval(() => setToasts((state) => popToast(state)), TOAST_MS);
    return () => window.clearInterval(handle);
  }, [toasts.visible.length]);

  const setPreference = async (patch: Partial<NotificationPreferences>) => {
    if (busy) return;
    setBusy(true);
    try {
      const updated = await updateNotificationPreferences(patch);
      // The reconciled server row is what renders — never the checkbox draft.
      setPrefs(updated);
    } catch (e) {
      props.onError?.(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const onMarkAll = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await markAllNotificationsRead();
      await loadList();
      await loadUnread();
    } catch (e) {
      props.onError?.(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const onMarkOne = async (id: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await markNotificationRead(id);
      await loadList();
      await loadUnread();
    } catch (e) {
      props.onError?.(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const onTest = async () => {
    if (busy) return;
    setBusy(true);
    try {
      primeNotificationAudio();
      await sendTestNotification();
      await loadList();
      await loadUnread();
    } catch (e) {
      props.onError?.(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const badge = unreadBadge(unread);
  /**
   * The roster's unread is a second, INDEPENDENT signal: the numeric badge
   * counts notification records, so it must never absorb the message count —
   * summing two differently-sourced totals would be one number nobody measured.
   * When there is no record badge but messages are unread, the bell shows the
   * un-numbered dot the old second icon used: activity, without a count.
   */
  const teamDot = teamUnread !== null && teamUnread > 0;
  /**
   * Two labelled facts, never one merged number. The `unread !== null` guards
   * are redundant with `badge` being non-empty at runtime, but they keep this
   * honest for TypeScript too, where `unread` is still `number | null`.
   */
  const summary = [
    badge && unread !== null
      ? `${unread} unread notification${unread === 1 ? "" : "s"}`
      : "",
    teamDot && teamUnread !== null
      ? `${teamUnread} unread message${teamUnread === 1 ? "" : "s"}`
      : "",
  ].filter(Boolean);
  const groups = groupByDay(items);
  const desktop = desktopPermission();

  return (
    <>
      {/* ── Toasts ── */}
      {toasts.visible.length > 0 && (
        <div className="fixed top-16 right-4 z-50 w-72 space-y-2" aria-live="polite" aria-label="Notifications">
          {toasts.visible.map((toast) => (
            <div
              key={toast.notification_id}
              className="rounded-xl border border-border bg-card shadow-lg p-3 flex items-start gap-2"
            >
              <Bell className="size-4 text-primary mt-0.5 shrink-0" aria-hidden="true" />
              <div className="flex-1 min-w-0">
                <p className="text-[11px] font-bold truncate">{toast.title}</p>
                {toast.body && <p className="text-[10px] text-muted-foreground line-clamp-2">{toast.body}</p>}
                {toast.room_id && <p className="text-[9px] text-muted-foreground">in {toast.room_id}</p>}
              </div>
              <button
                type="button"
                onClick={() => setToasts((state) => popToast(state))}
                className="p-0.5 rounded hover:bg-muted text-muted-foreground shrink-0"
                aria-label="Dismiss notification"
              >
                <X className="size-3.5" aria-hidden="true" />
              </button>
            </div>
          ))}
          {toasts.queued > 0 && (
            <p className="text-[10px] text-muted-foreground text-right">+{toasts.queued} more waiting</p>
          )}
        </div>
      )}

      <div className="relative" ref={rootRef}>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="relative p-2 rounded-lg hover:bg-muted text-muted-foreground"
          title={summary.length ? `Notifications — ${summary.join(", ")}` : "Notifications"}
          aria-label={summary.length ? `Notifications, ${summary.join(", ")}` : "Notifications"}
          aria-expanded={open}
        >
          <Bell className="size-4" aria-hidden="true" />
          {/* `null` (count not read) renders no badge at all — a zero badge here
              would claim the server measured zero unread. The dot fallback below
              is the second bell's unread indicator, kept so un-read roster
              messages are still visible when there is no record count to show. */}
          {badge ? (
            <span className="absolute -top-0.5 -right-0.5 min-w-4 h-4 px-1 rounded-full bg-destructive text-destructive-foreground text-[9px] font-bold flex items-center justify-center">
              {badge}
            </span>
          ) : teamDot ? (
            <span className="absolute top-1.5 right-1.5 size-2 rounded-full bg-primary ring-2 ring-card" aria-hidden="true" />
          ) : null}
        </button>

        {open && (
          <div className="absolute right-0 mt-2 w-80 max-h-[70vh] overflow-y-auto rounded-xl border border-border bg-card shadow-xl z-40 p-3 space-y-3">
            <div className="flex items-center gap-2">
              <p className="text-xs font-bold flex-1">Notifications</p>
              <Btn variant="ghost" onClick={() => void onTest()} disabled={busy} title="Create one so the whole path can be checked">
                <Send className="size-3" aria-hidden="true" /> Test
              </Btn>
              <Btn variant="ghost" onClick={() => void onMarkAll()} disabled={busy || (unread ?? 0) === 0}>
                <Check className="size-3" aria-hidden="true" /> All read
              </Btn>
            </div>

            {unreadError && (
              <p className="text-[10px] text-destructive">
                Unread count unavailable — the badge is hidden, not zero. ({unreadError})
              </p>
            )}

            {/* ── Preferences ── */}
            <div className="rounded-lg border border-border/60 p-2 space-y-1.5">
              <p className="text-[10px] font-bold text-muted-foreground">DELIVERY</p>
              {prefsError ? (
                <ErrorBox
                  message={`Preferences unavailable — sound state is unknown, not off. (${prefsError})`}
                  onRetry={() => void loadPrefs()}
                />
              ) : prefs === null ? (
                <p className="text-[10px] text-muted-foreground">Reading your delivery settings…</p>
              ) : (
                <>
                  <label className="flex items-center gap-2 text-[11px]">
                    <input
                      type="checkbox"
                      checked={prefs.enabled !== false}
                      disabled={busy}
                      onChange={(e) => void setPreference({ enabled: e.target.checked })}
                    />
                    Notifications on
                  </label>
                  <label className="flex items-center gap-2 text-[11px]">
                    <input
                      type="checkbox"
                      checked={prefs.sound_enabled !== false}
                      disabled={busy || prefs.enabled === false}
                      onChange={(e) => void setPreference({ sound_enabled: e.target.checked })}
                    />
                    <Volume2 className="size-3" aria-hidden="true" /> Notification sound
                  </label>
                  <div className="flex items-center gap-2 text-[11px]">
                    <input
                      type="checkbox"
                      checked={desktop === "granted"}
                      disabled={busy || desktop === "unsupported" || desktop === "denied"}
                      onChange={(e) => {
                        if (e.target.checked) void requestDesktopPermission();
                      }}
                    />
                    <Monitor className="size-3" aria-hidden="true" />
                    Desktop popups
                    {desktop === "unsupported" && <span className="text-[9px] text-muted-foreground">not supported</span>}
                    {desktop === "denied" && <span className="text-[9px] text-destructive">blocked by the browser</span>}
                  </div>
                  {!notificationAudioReady() && (
                    <p className="text-[9px] text-amber-600">
                      Audio is not unlocked yet — browsers require one click before a sound can play.
                    </p>
                  )}
                  {audioNotice && <p className="text-[9px] text-amber-600">{audioNotice}</p>}
                </>
              )}
            </div>

            {/* ── Workspace status ──
                Everything the header's SECOND bell showed, merged in. It is
                labelled workspace rather than folded into the notification
                history, because a gateway being offline and a notification
                being unread are not the same fact. */}
            <div className="rounded-lg border border-border/60 p-2 space-y-1.5">
              <p className="text-[10px] font-bold text-muted-foreground">WORKSPACE</p>
              <div className="flex items-start gap-1.5 text-[11px]">
                <Sparkles className="size-3 text-primary mt-0.5 shrink-0" aria-hidden="true" />
                <div className="min-w-0">
                  <p className="font-medium text-foreground">
                    {gatewayOk === false
                      ? "Gateway offline"
                      : gatewayOk
                        ? "Gateway connected"
                        : "Connecting to Gateway…"}
                  </p>
                  <p className="text-[10px] text-muted-foreground">
                    {gatewayOk === false
                      ? "Views are showing local or cached state."
                      : gatewayOk
                        ? "Live counts and status come from the Gateway."
                        : "Reaching the Gateway…"}
                  </p>
                </div>
              </div>
              <div className="flex items-start gap-1.5 text-[11px]">
                <Users className="size-3 text-muted-foreground mt-0.5 shrink-0" aria-hidden="true" />
                <div className="min-w-0">
                  <p className="font-medium text-foreground">
                    {teamUnread === null
                      ? "Team messages not reported"
                      : `${teamUnread} unread message${teamUnread === 1 ? "" : "s"}`}
                  </p>
                  <p className="text-[10px] text-muted-foreground">
                    {teamUnread === null
                      ? "The roster read did not ask for the activity projection, so no count exists — this is not zero."
                      : "Across the bot roster. The Messages view carries the per-room detail."}
                  </p>
                </div>
              </div>
              <div className="flex items-start gap-1.5 text-[11px]">
                <ShieldCheck className="size-3 text-muted-foreground mt-0.5 shrink-0" aria-hidden="true" />
                <div className="min-w-0">
                  <p className="font-medium text-foreground">Service status</p>
                  <p className="text-[10px] text-muted-foreground">
                    Not reported here. The Supervisor, Integration and System views carry the measured
                    per-loop status and the reason any control is off.
                  </p>
                </div>
              </div>
            </div>

            {/* ── History ── */}
            {itemsError ? (
              <ErrorBox
                message={`Notifications unavailable — failed to load, not empty. (${itemsError})`}
                onRetry={() => void loadList().catch((e) => setItemsError(errMsg(e)))}
              />
            ) : items === null ? (
              <p className="text-[11px] text-muted-foreground">Reading notifications…</p>
            ) : items.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">No notifications recorded.</p>
            ) : (
              groups.map((group) => (
                <div key={group.key} className="space-y-1">
                  <p className="text-[9px] font-bold text-muted-foreground uppercase">{group.label}</p>
                  {group.items.map((notification) => {
                    const visible = prefs === null || allows(prefs, notification);
                    return (
                      <div
                        key={notification.notification_id}
                        className={`rounded-lg px-2 py-1.5 flex items-start gap-2 ${
                          notification.read ? "opacity-60" : "bg-muted/40"
                        }`}
                      >
                        <div className="flex-1 min-w-0">
                          <p className="text-[11px] font-semibold truncate">{notification.title}</p>
                          {notification.body && (
                            <p className="text-[10px] text-muted-foreground line-clamp-2">{notification.body}</p>
                          )}
                          <p className="text-[9px] text-muted-foreground">
                            {notification.priority ?? "priority not reported"}
                            {notification.room_id ? ` · ${notification.room_id}` : ""}
                            {!visible ? " · muted by your settings" : ""}
                          </p>
                        </div>
                        {!notification.read && (
                          <button
                            type="button"
                            onClick={() => void onMarkOne(notification.notification_id)}
                            disabled={busy}
                            className="text-[10px] text-primary hover:underline shrink-0 disabled:opacity-40"
                            title="Mark as read"
                          >
                            Read
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              ))
            )}

            <p className="text-[9px] text-muted-foreground">
              <BellOff className="size-3 inline align-[-2px]" aria-hidden="true" /> Muting delivery keeps every record
              here — a muted notification is not a deleted one.
            </p>
          </div>
        )}
      </div>
    </>
  );
}
