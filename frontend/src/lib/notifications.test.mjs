// Pure-derivation tests for the notification model.
//
// The rule this file exists to pin: **"the event happened" and "the operator
// was told" are separate facts.** A muted notification stays in history and
// stays counted; a preference that is off deletes nothing; and the sound path
// never reports success for a chime nobody could have heard.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = ts.transpileModule(readFileSync(new URL("./notifications-model.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

const model = {};
new Function("exports", "require", source)(model, (d) => {
  throw new Error(`notifications-model must have no imports, found ${d}`);
});

const {
  PRIORITY_ORDER,
  priorityRank,
  allows,
  shouldSound,
  inQuietHours,
  unreadBadge,
  sortNewestFirst,
  filterNotifications,
  enqueueToast,
  popToast,
  groupByDay,
} = model;

const notification = (over = {}) => ({
  notification_id: `n_${over.id ?? "1"}`,
  type: "message",
  title: "Agent posted",
  body: "deploy is green",
  priority: "normal",
  ...over,
});

const preferences = (over = {}) => ({
  enabled: true,
  sound_enabled: true,
  desktop_enabled: false,
  types: {},
  priorities: {},
  quiet_hours_start: null,
  quiet_hours_end: null,
  ...over,
});

// ── Priority ladder ────────────────────────────────────────────────────────

test("the ladder is declared loudest-last and the rank follows it", () => {
  assert.deepEqual([...PRIORITY_ORDER], ["low", "normal", "high", "urgent"]);
  assert.ok(priorityRank("low") < priorityRank("normal"));
  assert.ok(priorityRank("normal") < priorityRank("high"));
  assert.ok(priorityRank("high") < priorityRank("urgent"));
});

test("an unknown priority is normal, not the quietest thing on the ladder", () => {
  // Ranking an unrecognised value as `low` would let a future backend
  // escalation render silently — an unknown value is anticipated-by-noone,
  // not deliberately hushed.
  assert.equal(priorityRank("critical"), priorityRank("normal"));
  assert.equal(priorityRank(undefined), priorityRank("normal"));
  assert.equal(priorityRank(null), priorityRank("normal"));
  assert.equal(priorityRank(""), priorityRank("normal"));
});

// ── The allow gate ─────────────────────────────────────────────────────────

test("no preference read yet is the default posture, not silence", () => {
  assert.equal(allows(null, notification()), true);
  assert.equal(allows(undefined, notification()), true);
  assert.equal(allows(preferences(), notification()), true);
});

test("a disabled preference refuses everything", () => {
  assert.equal(allows(preferences({ enabled: false }), notification({ priority: "urgent" })), false);
});

test("an absent key inherits the default rather than being muted", () => {
  const pref = preferences({ priorities: { high: true } });
  // `low` was never declared; its default is off, and the rest stay on.
  assert.equal(allows(pref, notification({ priority: "low" })), false);
  assert.equal(allows(pref, notification({ priority: "normal" })), true);
  assert.equal(allows(pref, notification({ priority: "high" })), true);
  assert.equal(allows(pref, notification({ priority: "urgent" })), true);
});

test("a type the preference never mentions is still allowed", () => {
  assert.equal(allows(preferences({ types: { mention: true } }), notification({ type: "run" })), true);
  assert.equal(allows(preferences({ types: { mention: false } }), notification({ type: "mention" })), false);
});

test("the priority gate runs first, so a muted priority stays muted by type too", () => {
  const pref = preferences({ priorities: { low: false }, types: { message: true } });
  assert.equal(allows(pref, notification({ priority: "low", type: "message" })), false);
});

// ── Sound ──────────────────────────────────────────────────────────────────

test("nothing is audible before the operator's choice has been read", () => {
  assert.equal(shouldSound(null, notification()), false);
  assert.equal(shouldSound(undefined, notification(), new Date("2026-10-03T12:00:00")), false);
});

test("sound follows both switches independently", () => {
  const now = new Date("2026-10-03T12:00:00");
  assert.equal(shouldSound(preferences(), notification(), now), true);
  assert.equal(shouldSound(preferences({ enabled: false }), notification(), now), false);
  assert.equal(shouldSound(preferences({ sound_enabled: false }), notification(), now), false);
});

test("a record that opts out of sound stays silent even when sound is on", () => {
  assert.equal(shouldSound(preferences(), notification({ sound: false }), new Date("2026-10-03T12:00:00")), false);
});

test("a muted type is history-only: no chime, but the record still exists", () => {
  const pref = preferences({ types: { activity: false } });
  const record = notification({ type: "activity" });
  assert.equal(shouldSound(pref, record, new Date("2026-10-03T12:00:00")), false);
  assert.equal(allows(pref, record), false, "the gate refuses it…");
  assert.ok(record.title, "…and the notification itself is untouched");
});

test("quiet hours silence the chime without disabling notifications", () => {
  const pref = preferences({ quiet_hours_start: "22:00", quiet_hours_end: "07:00" });
  assert.equal(shouldSound(pref, notification(), new Date("2026-10-03T23:30:00")), false);
  assert.equal(shouldSound(pref, notification(), new Date("2026-10-03T03:00:00")), false);
  assert.equal(shouldSound(pref, notification(), new Date("2026-10-03T12:00:00")), true);
  assert.equal(allows(pref, notification()), true, "quiet hours must never suppress the record");
});

// ── Quiet-hour parsing ─────────────────────────────────────────────────────

test("a window crossing midnight is read as two segments", () => {
  const pref = preferences({ quiet_hours_start: "22:00", quiet_hours_end: "07:00" });
  assert.equal(inQuietHours(pref, new Date("2026-10-03T22:00:00")), true, "the start is inclusive");
  assert.equal(inQuietHours(pref, new Date("2026-10-03T06:59:00")), true);
  assert.equal(inQuietHours(pref, new Date("2026-10-03T07:00:00")), false, "the end is exclusive");
  assert.equal(inQuietHours(pref, new Date("2026-10-03T21:59:00")), false);
});

test("a same-day window is the plain case", () => {
  const pref = preferences({ quiet_hours_start: "13:00", quiet_hours_end: "14:00" });
  assert.equal(inQuietHours(pref, new Date("2026-10-03T13:30:00")), true);
  assert.equal(inQuietHours(pref, new Date("2026-10-03T14:30:00")), false);
});

test("an absent, malformed or degenerate schedule means no quiet hours", () => {
  // Refusing to play over an unparsable schedule would invent a restriction
  // the operator never set.
  assert.equal(inQuietHours(preferences(), new Date("2026-10-03T03:00:00")), false);
  assert.equal(inQuietHours(preferences({ quiet_hours_start: "22:00" }), new Date("2026-10-03T03:00:00")), false);
  assert.equal(
    inQuietHours(preferences({ quiet_hours_start: "25:00", quiet_hours_end: "07:00" }), new Date("2026-10-03T03:00:00")),
    false,
  );
  assert.equal(
    inQuietHours(preferences({ quiet_hours_start: "07:00", quiet_hours_end: "07:00" }), new Date("2026-10-03T07:00:00")),
    false,
    "a zero-length window is not an always-on one",
  );
  assert.equal(
    inQuietHours(preferences({ quiet_hours_start: "noon", quiet_hours_end: "07:00" }), new Date("2026-10-03T03:00:00")),
    false,
  );
});

// ── Badge ──────────────────────────────────────────────────────────────────

test("zero renders no dot at all, and the readable range caps at 99+", () => {
  assert.equal(unreadBadge(0), "");
  assert.equal(unreadBadge(-3), "");
  assert.equal(unreadBadge(null), "");
  assert.equal(unreadBadge(undefined), "");
  assert.equal(unreadBadge(Number.NaN), "");
  assert.equal(unreadBadge(1.7), "1");
  assert.equal(unreadBadge(99), "99");
  assert.equal(unreadBadge(100), "99+");
  assert.equal(unreadBadge(10_000), "99+");
});

// ── Ordering ───────────────────────────────────────────────────────────────

test("newest first, with undated records keeping their incoming position", () => {
  const sorted = sortNewestFirst([
    notification({ id: "old", created_at: "2026-10-01T09:00:00Z" }),
    notification({ id: "undated" }),
    notification({ id: "new", created_at: "2026-10-03T09:00:00Z" }),
  ]);
  // An undated record is *unknown age*, not *oldest* — pushing it to the
  // bottom would assert an order the data does not carry.
  assert.deepEqual(sorted.map((item) => item.notification_id), ["n_new", "n_old", "n_undated"]);
});

test("an all-undated list keeps its original order and never throws", () => {
  const input = [notification({ id: "a" }), notification({ id: "b" })];
  assert.deepEqual(sortNewestFirst(input).map((item) => item.notification_id), ["n_a", "n_b"]);
  assert.deepEqual(sortNewestFirst(null), []);
  assert.deepEqual(sortNewestFirst(undefined), []);
});

test("equal timestamps fall back to incoming order", () => {
  const at = "2026-10-03T09:00:00Z";
  const sorted = sortNewestFirst([notification({ id: "a", created_at: at }), notification({ id: "b", created_at: at })]);
  assert.deepEqual(sorted.map((item) => item.notification_id), ["n_a", "n_b"]);
});

// ── Filtering ──────────────────────────────────────────────────────────────

test("filtering hides without deleting, and leaves the source untouched", () => {
  const sourceList = [
    notification({ id: "read", read: true, type: "message" }),
    notification({ id: "unread", read: false, type: "mention" }),
    notification({ id: "quiet", read: false, priority: "low" }),
  ];
  const before = sourceList.length;

  assert.deepEqual(filterNotifications(sourceList, { unreadOnly: true }).map((n) => n.notification_id), [
    "n_unread",
    "n_quiet",
  ]);
  assert.deepEqual(filterNotifications(sourceList, { types: ["mention"] }).map((n) => n.notification_id), ["n_unread"]);
  assert.deepEqual(filterNotifications(sourceList, { minPriority: "high" }).map((n) => n.notification_id), []);
  assert.equal(sourceList.length, before, "a filtered-out notification is hidden, not removed");
});

test("an empty type filter and no filter show everything", () => {
  const sourceList = [notification({ id: "a" }), notification({ id: "b" })];
  assert.equal(filterNotifications(sourceList, { types: [] }).length, 2);
  assert.equal(filterNotifications(sourceList).length, 2);
  assert.deepEqual(filterNotifications(null), []);
});

// ── Toast stack ────────────────────────────────────────────────────────────

test("the toast stack is bounded and the overflow is counted, not lost", () => {
  let state = { visible: [], queued: 0 };
  state = enqueueToast(state, notification({ id: "1" }), 2);
  state = enqueueToast(state, notification({ id: "2" }), 2);
  assert.equal(state.visible.length, 2);
  assert.equal(state.queued, 0);

  state = enqueueToast(state, notification({ id: "3" }), 2);
  assert.equal(state.visible.length, 2, "the screen never grows past the bound");
  assert.equal(state.queued, 1, "an operator who asked for two should still learn a third arrived");

  state = enqueueToast(state, notification({ id: "4" }), 2);
  assert.equal(state.visible.length, 2);
  assert.equal(state.queued, 2);
});

test("re-delivering the same notification updates it instead of duplicating", () => {
  let state = { visible: [notification({ id: "1" })], queued: 0 };
  state = enqueueToast(state, notification({ id: "1", title: "updated" }), 3);
  assert.equal(state.visible.length, 1);
  assert.equal(state.visible[0].title, "updated");
});

test("popping dismisses the oldest on-screen toast and promotes exactly one queued", () => {
  let state = { visible: [], queued: 0 };
  for (const id of ["1", "2", "3", "4"]) state = enqueueToast(state, notification({ id }), 2);
  assert.deepEqual(state.visible.map((n) => n.notification_id), ["n_4", "n_3"], "newest first");
  assert.equal(state.queued, 2, "n_1 and n_2 never got a slot");

  state = popToast(state);
  assert.deepEqual(state.visible.map((n) => n.notification_id), ["n_4"], "the oldest on screen leaves first");
  assert.equal(state.queued, 1, "one slot freed, so one queued toast moves up");

  state = popToast(state);
  assert.deepEqual(state.visible, []);
  assert.equal(state.queued, 0);

  state = popToast(state);
  assert.deepEqual(state.visible, []);
  assert.equal(state.queued, 0, "popping an empty stack must not manufacture a pending count");
});

// ── Day grouping ───────────────────────────────────────────────────────────

test("today and yesterday are labelled; older days are their own date", () => {
  const now = new Date("2026-10-03T15:00:00");
  const groups = groupByDay(
    [
      notification({ id: "t", created_at: new Date("2026-10-03T09:00:00").toISOString() }),
      notification({ id: "y", created_at: new Date("2026-10-02T09:00:00").toISOString() }),
      notification({ id: "o", created_at: new Date("2026-09-28T09:00:00").toISOString() }),
    ],
    now,
  );
  assert.deepEqual(
    groups.map((group) => group.label),
    ["Today", "Yesterday", "2026-09-28"],
  );
  assert.deepEqual(
    groups.map((group) => group.items.map((item) => item.notification_id)),
    [["n_t"], ["n_y"], ["n_o"]],
  );
});

test("day boundaries use the operator's calendar, not UTC", () => {
  const now = new Date("2026-10-03T15:00:00");
  // 23:30 local on the 3rd is the 3rd locally, whatever its UTC offset says.
  const local = new Date(2026, 9, 3, 23, 30, 0);
  const groups = groupByDay([notification({ id: "late", created_at: local.toISOString() })], now);
  assert.equal(groups[0].label, "Today");
});

test("an undated notification is bucketed to now, not dropped", () => {
  const now = new Date("2026-10-03T15:00:00");
  const groups = groupByDay([notification({ id: "undated" })], now);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].label, "Today");
  assert.deepEqual(groupByDay(null, now), []);
});

// ------------------------------------------------------------- component pins

test("the notification surface is mounted in the header every view renders", () => {
  // Mounting `NotificationsBell` is not only what shows the panel — it is what
  // runs `primeNotificationAudio()` and the 20s poll. An agent's message raised
  // its record correctly and was announced *nowhere* while the operator sat on
  // the chat view, which is the screen `UpdateControl` had already been caught
  // failing to appear on for exactly this reason.
  const topBar = readFileSync(
    new URL("../components/chat-shell/WorkspaceTopBar.tsx", import.meta.url),
    "utf8",
  );
  assert.match(
    topBar,
    /import \{ NotificationsBell \} from "@\/components\/sections\/NotificationsBell"/,
    "the bell must be imported by the shared header",
  );
  assert.match(topBar, /<NotificationsBell\b/, "the bell must be mounted in the shared header");

  const messages = readFileSync(
    new URL("../components/sections/MessagesSection.tsx", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(
    messages,
    /<NotificationsBell\b/,
    "mounting it a second time in the Messages section leaves two bells in one view",
  );
});

test("the header renders exactly one notification bell", () => {
  // The bar used to render `NotificationsBell` AND a second, hand-rolled
  // `<Bell>` toggle right beside it — two icons in a row both labelled
  // "Notifications", one holding the real panel and the other holding a
  // gateway status card. Everything that toggle showed is passed into the one
  // bell now, so a bare `<Bell` returning here means the duplication is back.
  const topBar = readFileSync(
    new URL("../components/chat-shell/WorkspaceTopBar.tsx", import.meta.url),
    "utf8",
  );
  assert.equal(
    (topBar.match(/<NotificationsBell\b/g) || []).length,
    1,
    "the shared header must mount exactly one notification bell",
  );
  // Comments are stripped first. The comment above the mount explains this
  // change and names the old toggle `<Bell>` literally, so matching the raw
  // source would assert against my own prose rather than the rendered tree.
  const code = topBar
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
  // `<Bell ` cannot match `<NotificationsBell`, so this is the second icon.
  assert.doesNotMatch(
    code,
    /<Bell[\s/>]/,
    "a second bell is rendered in the top bar; merge it into NotificationsBell",
  );
  assert.doesNotMatch(
    code,
    /import \{[^}]*\bBell\b[^}]*\} from "lucide-react"/,
    "the second bell's lucide import must not survive the merge",
  );

  // …and the two features it owned must arrive with it, or the merge dropped
  // information rather than consolidating it.
  const bell = readFileSync(
    new URL("../components/sections/NotificationsBell.tsx", import.meta.url),
    "utf8",
  );
  assert.match(bell, /gatewayOk/, "the gateway status card must move into the single bell");
  assert.match(bell, /teamUnread/, "the roster unread signal must move into the single bell");
  assert.match(
    topBar,
    /<NotificationsBell gatewayOk=\{gatewayOk\} teamUnread=\{unreadCount\} \/>/,
    "the top bar must pass both through rather than rendering its own copy",
  );
  // Neither count may be folded into the other: they are different facts.
  assert.doesNotMatch(
    bell,
    /teamUnread\s*\?\?\s*unread|unread\s*\?\?\s*teamUnread/,
    "the notification count and the roster count must never be summed",
  );
});

test("the Messages section keeps its own group profile editor", () => {
  // The profile panel is a drill-down on one group, so the Messages view is its
  // right home — unlike the delivery surface, which has to reach every view.
  const messages = readFileSync(
    new URL("../components/sections/MessagesSection.tsx", import.meta.url),
    "utf8",
  );
  assert.match(messages, /import \{ GroupProfilePanel \}/);
  assert.match(messages, /<GroupProfilePanel\b/);
});
