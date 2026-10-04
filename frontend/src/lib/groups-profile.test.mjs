// Pure-derivation tests for the group profile / links / goals / receipts model.
//
// Every rule pinned here exists because its absence produces a *quietly wrong*
// UI rather than a broken one:
//
//   * a profile scored against its own filled fields would report an empty
//     profile as complete (0/0), so the denominator is always the offered set;
//   * `javascript:` in a group link becomes a live anchor the moment the label
//     is clicked, so only http(s) survives;
//   * a read-receipt percentage without the roster beside it is uncheckable;
//   * an empty typing indicator is *absent*, never "nobody is typing";
//   * a deleted message must not keep an unread badge lit over withheld text.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = ts.transpileModule(readFileSync(new URL("./groups-profile-model.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

const model = {};
new Function("exports", "require", source)(model, (d) => {
  throw new Error(`groups-profile-model must have no imports, found ${d}`);
});

const {
  PROFILE_FIELDS,
  profileCompletion,
  profileHeadline,
  safeHref,
  linksByType,
  goalSummary,
  readersSummary,
  typingHeadline,
  unreadCount,
  appendBounded,
} = model;

// ── Profile completion ─────────────────────────────────────────────────────

test("an empty profile scores 0, never a vacuous 100%", () => {
  const done = profileCompletion({});
  assert.equal(done.filled, 0);
  assert.equal(done.total, PROFILE_FIELDS.length);
  assert.ok(done.total > 0, "the denominator must be the offered set, not the data");
  assert.equal(done.percent, 0);
  assert.deepEqual(done.missing, [...PROFILE_FIELDS]);
});

test("every offered field set is 100%", () => {
  const full = Object.fromEntries(PROFILE_FIELDS.map((field) => [field, `${field} value`]));
  const done = profileCompletion(full);
  assert.equal(done.filled, done.total);
  assert.equal(done.percent, 100);
  assert.deepEqual(done.missing, []);
});

test("whitespace-only and empty-array fields count as missing", () => {
  const done = profileCompletion({
    description: "   ",
    tags: [],
    purpose: "real purpose",
    category: null,
    avatar_url: undefined,
    banner_url: "",
    avatar_color: "#fff",
  });
  assert.deepEqual(done.missing, ["description", "category", "tags", "avatar_url", "banner_url"]);
  assert.equal(done.filled, 2);
});

test("a profile that was never read is reported as unread, not as empty", () => {
  assert.equal(profileHeadline(null), "Profile not read yet");
  assert.equal(profileHeadline(undefined), "Profile not read yet");
  assert.equal(profileHeadline({}), "No profile yet");
  assert.equal(profileHeadline({ description: "hi" }), `Profile 1/${PROFILE_FIELDS.length} fields (${Math.round((1 / PROFILE_FIELDS.length) * 100)}%)`);
});

test("completion never throws on a partial or hostile record", () => {
  // The emptiness rule targets blank strings, empty arrays and nulls; a
  // value of another type is *present* (not silently coerced to empty),
  // and neither shape is allowed to throw.
  assert.equal(profileCompletion({ tags: 42 }).total, PROFILE_FIELDS.length);
  assert.equal(profileCompletion({ tags: 42 }).filled, 1, "42 is a value, however nonsensical — not a blank list");
  assert.equal(profileCompletion({ banner_url: 0 }).filled, 1);
  assert.equal(profileCompletion({ description: false }).filled, 1);
});

// ── Link safety ────────────────────────────────────────────────────────────

test("only http(s) links are renderable", () => {
  assert.equal(safeHref("https://example.test/board"), "https://example.test/board");
  assert.equal(safeHref("http://example.test"), "http://example.test/", "URL normalises an empty path to /");
  assert.equal(safeHref("  https://example.test  "), "https://example.test/");
});

test("script-bearing and unparseable URLs are refused, not passed through", () => {
  assert.equal(safeHref("javascript:alert(1)"), null);
  assert.equal(safeHref("  JAVASCRIPT:alert(1)"), null);
  assert.equal(safeHref("data:text/html,<script>x</script>"), null);
  assert.equal(safeHref("file:///etc/passwd"), null);
  assert.equal(safeHref("not a url"), null);
  assert.equal(safeHref(""), null);
  assert.equal(safeHref(null), null);
  assert.equal(safeHref(undefined), null);
});

// ── Links grouped ──────────────────────────────────────────────────────────

test("links group by declared type in declaration order", () => {
  const grouped = linksByType(
    [
      { link_id: "a", label: "Repo", url: "https://r.test", link_type: "repository", position: 1 },
      { link_id: "b", label: "Docs", url: "https://d.test", link_type: "docs", position: 0 },
      { link_id: "c", label: "Board", url: "https://b.test", link_type: "repository", position: 0 },
    ],
    ["docs", "repository", "project", "custom"],
  );
  assert.deepEqual(Object.keys(grouped), ["docs", "repository", "project", "custom"]);
  assert.deepEqual(grouped.repository.map((link) => link.link_id), ["c", "a"], "position orders inside a type");
  assert.deepEqual(grouped.project, []);
});

test("a link with no declared type still lands somewhere", () => {
  const grouped = linksByType([{ link_id: "x", label: "X", url: "https://x.test" }], ["docs"]);
  assert.equal(grouped.custom.length, 1);
  assert.equal(grouped.docs.length, 0);
});

test("an absent link list reads as no groups, never as a thrown read", () => {
  assert.deepEqual(linksByType(null, ["docs"]), { docs: [] });
  assert.deepEqual(linksByType(undefined, []), {});
});

// ── Goals ──────────────────────────────────────────────────────────────────

test("an empty goal list is 0%, not an unearned 100%", () => {
  assert.deepEqual(goalSummary([]), { total: 0, completed: 0, percent: 0, next: null });
  assert.deepEqual(goalSummary(null), { total: 0, completed: 0, percent: 0, next: null });
});

test("progress is the mean of each goal's own progress", () => {
  const summary = goalSummary([
    { goal_id: "g1", title: "Done", status: "completed", progress: 100 },
    { goal_id: "g2", title: "Half", status: "pending", progress: 50 },
    { goal_id: "g3", title: "Started", status: "pending", progress: 0 },
  ]);
  assert.equal(summary.total, 3);
  assert.equal(summary.completed, 1);
  assert.equal(summary.percent, 50, "closing one of three thirds is half-finished work, not 0% or 33%");
  assert.equal(summary.next, "Half", "the next goal is the first unfinished one, in order");
});

test("a goal at 100% counts complete even if its status lagged behind", () => {
  const summary = goalSummary([{ goal_id: "g1", title: "Auto", progress: 100 }]);
  assert.equal(summary.completed, 1);
  assert.equal(summary.next, null);
});

test("progress outside 0-100 is clamped rather than extrapolated", () => {
  assert.equal(goalSummary([{ goal_id: "g", title: "x", progress: 400 }]).percent, 100);
  assert.equal(goalSummary([{ goal_id: "g", title: "x", progress: -20 }]).percent, 0);
});

// ── Read receipts ──────────────────────────────────────────────────────────

test("receipts report read and total together", () => {
  const summary = readersSummary(["architect", "coder"], ["architect", "coder", "designer"]);
  assert.equal(summary.read, 2);
  assert.equal(summary.total, 3);
  assert.deepEqual(summary.unread, ["designer"]);
  assert.equal(summary.percent, 67);
});

test("without a roster the read list is the only honest denominator", () => {
  const summary = readersSummary(["architect"], null);
  assert.equal(summary.total, 1, "inventing a larger denominator would report readers who never will read");
  assert.equal(summary.percent, 100);
});

test("no readers at all is 0/0 → 0%, never 100%", () => {
  const summary = readersSummary([], ["architect", "coder"]);
  assert.equal(summary.read, 0);
  assert.equal(summary.total, 2);
  assert.equal(summary.percent, 0);
  assert.deepEqual(summary.unread, ["architect", "coder"]);
});

test("read-list whitespace is not a second member", () => {
  const summary = readersSummary([" architect ", ""], ["architect", "coder"]);
  assert.equal(summary.read, 1);
  assert.deepEqual(summary.unread, ["coder"]);
});

// ── Typing ─────────────────────────────────────────────────────────────────

test("no typists renders as no line at all", () => {
  assert.equal(typingHeadline([]), "");
  assert.equal(typingHeadline(null), "");
  assert.equal(typingHeadline(undefined), "");
  assert.equal(typingHeadline(["", ""]), "", "blank names are not typists");
});

test("one, two and many typists read as English, not as a count", () => {
  assert.equal(typingHeadline(["coder"]), "coder is typing…");
  assert.equal(typingHeadline(["coder", "designer"]), "coder and designer are typing…");
  assert.equal(typingHeadline(["a", "b", "c"]), "a, b and 1 more are typing…");
  assert.equal(typingHeadline(["a", "b", "c", "d"]), "a, b and 2 more are typing…");
});

// ── Unread ─────────────────────────────────────────────────────────────────

test("deleted messages never keep a badge lit", () => {
  const messages = [
    { id: "1", deleted: false },
    { id: "2", deleted: true },
    { id: "3", deleted: false },
  ];
  const read = new Set(["1"]);
  const count = unreadCount(messages, (message) => read.has(message.id));
  assert.equal(count, 1, "the withheld row must not be counted");
});

test("an absent message list is zero, and the receipt callback is honoured", () => {
  assert.equal(unreadCount(null, () => false), 0);
  assert.equal(unreadCount([], () => false), 0);
  assert.equal(unreadCount([{ id: "a" }, { id: "b" }], (message) => message.id === "a"), 1);
});

// ── Bounded buffers ────────────────────────────────────────────────────────

test("the recent-event buffer keeps the newest entries and never grows", () => {
  let list = [];
  for (let i = 0; i < 25; i += 1) list = appendBounded(list, i, 5);
  assert.deepEqual(list, [24, 23, 22, 21, 20]);
  assert.equal(list.length, 5);
});

test("a nonsensical bound is floored to one rather than unbounded", () => {
  assert.deepEqual(appendBounded([1, 2, 3], 4, 0), [4]);
  assert.deepEqual(appendBounded([1, 2, 3], 4, -5), [4]);
  assert.deepEqual(appendBounded([1], 2, 1.7), [2]);
});
