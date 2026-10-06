// Contract tests for the `@` tag grammar.
//
// The composer's picker and the Gateway's `alpha.channels.mentions` are two
// readers of one grammar, so every test below is written as a pairing: the
// frontend's answer is only correct if the backend would resolve the exact token
// it inserts. The cases that matter most are the negative ones, because the
// backend's stated reason for existing is that a near match is how a message
// reaches a bot nobody named.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = ts.transpileModule(readFileSync(new URL("./agent-mentions.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

const m = {};
new Function("exports", "require", source)(m, (d) => {
  throw new Error(`agent-mentions must have no imports, found ${d}`);
});

function agent(handle, over = {}) {
  return {
    handle,
    displayName: over.displayName ?? handle[0].toUpperCase() + handle.slice(1),
    role: over.role ?? "Specialist",
    department: over.department ?? "engineering",
    status: over.status ?? "active",
    avatar: over.avatar ?? "",
    model: over.model ?? null,
    capabilities: over.capabilities ?? [],
  };
}

const ROSTER = [
  agent("alice"),
  agent("rev-1", { department: "review" }),
  agent("rev_1", { department: "review" }),
  agent("bob", { department: "design", status: "paused" }),
];

// ── trigger detection ────────────────────────────────────────────────────────

test("a bare @ at the start of an empty composer opens the picker", () => {
  const t = m.findMentionAtCaret("@", 1);
  assert.equal(t.kind, null);
  assert.equal(t.query, "");
  assert.equal(t.start, 0);
  assert.equal(t.end, 1);
});

test("@ opens after whitespace and after an opening delimiter, but not mid-word", () => {
  assert.ok(m.findMentionAtCaret("hi @ali", 7), "after a space");
  assert.ok(m.findMentionAtCaret("( @ali", 6), "after an opening bracket");
  assert.equal(m.findMentionAtCaret("email@example.com", 17), null, "an email is not a mention");
  assert.equal(m.findMentionAtCaret("src/lib/a@b", 10), null, "a path segment is not a mention");
  assert.equal(m.findMentionAtCaret("version@2", 9), null, "a version tag is not a mention");
});

test("a mention can never swallow a sentence: typing a space closes the token", () => {
  // Caret 6 sits immediately after "e" and is still inside the token; the
  // space the operator just typed is character 6, so a caret at 7 or beyond is
  // past it and the token is closed. That is exactly why a mention cannot
  // swallow a sentence.
  assert.ok(m.findMentionAtCaret("@alice", 6), "the caret right at the token end is still in it");
  assert.equal(m.findMentionAtCaret("@alice ", 7), null, "the space closed the token");
  assert.equal(m.findMentionAtCaret("@alice and then", 15), null);
});

test("an explicit namespace is a commitment, not a filter hint", () => {
  assert.deepEqual(
    { kind: m.findMentionAtCaret("@bot:ali", 8).kind, query: m.findMentionAtCaret("@bot:ali", 8).query },
    { kind: "bot", query: "ali" },
  );
  assert.equal(m.findMentionAtCaret("@role:", 6).kind, "role");
  assert.equal(m.findMentionAtCaret("@role:", 6).query, "");
  assert.equal(m.findMentionAtCaret("@everyone", 9).kind, "all");
});

test("a bare all/everyone is the fan-out selector, never a handle", () => {
  assert.equal(m.findMentionAtCaret("@all", 4).kind, "all");
  assert.equal(m.findMentionAtCaret("@every", 6).kind, null, "partial text is still a bare handle search");
});

test("a caret moved back into an earlier token finds that token", () => {
  const text = "@alice talk to @bob";
  const trigger = m.findMentionAtCaret(text, 6);
  assert.equal(trigger.query, "alice", "the caret just after @alice is still inside it");
  assert.equal(trigger.start, 0);
  assert.equal(trigger.end, 6);
  // Moving on past the space closes it, and the later @bob becomes the token.
  assert.equal(m.findMentionAtCaret(text, 8), null, "between the two tokens there is no @ token");
  assert.equal(m.findMentionAtCaret(text, text.length).query, "bob");
});

test("the trigger offsets point at the token the picker will replace", () => {
  const text = "hey @bo";
  const trigger = m.findMentionAtCaret(text, text.length);
  const applied = m.applyMention(text, trigger, { token: "@bot:bob" });
  assert.equal(applied.text, "hey @bot:bob ");
  assert.equal(applied.caret, applied.text.length);
});

// ── insertion produces server-resolvable tokens ──────────────────────────────

test("every inserted token is the canonical spelling the server parses", () => {
  const rows = m.buildMentionRows({ agents: ROSTER, allowSwitch: true });
  for (const row of rows) {
    assert.ok(
      /^@bot:[A-Za-z0-9_.\-]+$/.test(row.token) ||
        /^@role:[A-Za-z0-9_.\-]+$/.test(row.token) ||
        row.token === "@everyone",
      `row ${row.id} inserts a token outside the server grammar: ${row.token}`,
    );
  }
});

test("a tag written by the picker resolves back to exactly one agent", () => {
  const rows = m.buildMentionRows({ agents: ROSTER, allowSwitch: true });
  const alice = rows.find((r) => r.id === "bot:alice");
  const applied = m.applyMention("", { start: 0, end: 0, kind: null, query: "" }, alice);
  const resolution = m.parseMentions(applied.text, m.rosterHandles(ROSTER));
  assert.deepEqual(resolution.resolvedHandles, ["alice"]);
  assert.equal(resolution.ok, true);
});

test("applying a tag keeps the surrounding sentence intact", () => {
  const text = "please ask @bo about the outage";
  const caret = text.indexOf("@bo") + 3;
  const trigger = m.findMentionAtCaret(text, caret);
  const row = m.buildMentionRows({ agents: ROSTER }).find((r) => r.id === "bot:bob");
  const applied = m.applyMention(text, trigger, row);
  assert.equal(applied.text, "please ask @bot:bob  about the outage");
});

// ── exact resolution, never a near match ─────────────────────────────────────

test("a prefix names nobody: the server refuses @rev, so the preview must too", () => {
  // `rev-1` and `rev_1` both exist and neither is a prefix of the other. A
  // fuzzy picker could still offer one for `@rev`; the resolution must not.
  const resolution = m.parseMentions("@rev look at this", m.rosterHandles(ROSTER));
  assert.deepEqual(resolution.resolvedHandles, []);
  assert.equal(resolution.ok, false);
  assert.match(resolution.unresolved[0].reason, /unknown handle/);
});

test("punctuation is significant: rev-1 and rev_1 are different handles", () => {
  const handles = m.rosterHandles(ROSTER);
  assert.deepEqual(m.parseMentions("@bot:rev-1", handles).resolvedHandles, ["rev-1"]);
  assert.deepEqual(m.parseMentions("@bot:rev_1", handles).resolvedHandles, ["rev_1"]);
  assert.equal(m.parseMentions("@bot:rev-1_1", handles).ok, false);
});

test("an unknown handle is reported with a reason, never silently dropped", () => {
  const resolution = m.parseMentions("@nobody are you there", m.rosterHandles(ROSTER));
  assert.equal(resolution.ok, false);
  assert.equal(resolution.unresolved.length, 1);
  assert.equal(resolution.unresolved[0].raw, "@nobody");
});

test("an empty roster resolves nothing at all, including @everyone", () => {
  const resolution = m.parseMentions("@bot:alice @everyone", []);
  assert.deepEqual(resolution.resolvedHandles, []);
  assert.equal(resolution.unresolved.length, 2);
  assert.match(resolution.unresolved[1].reason, /roster is empty/);
});

test("one bad token does not swallow the good ones", () => {
  const resolution = m.parseMentions("@bot:alice and @ghost", m.rosterHandles(ROSTER));
  assert.deepEqual(resolution.resolvedHandles, ["alice"]);
  assert.equal(resolution.unresolved.length, 1);
  assert.equal(resolution.unresolved[0].raw, "@ghost");
});

test("a repeated tag resolves once", () => {
  const resolution = m.parseMentions("@bot:alice @bot:alice @Alice", m.rosterHandles(ROSTER));
  assert.deepEqual(resolution.resolvedHandles, ["alice"]);
  assert.equal(resolution.ok, true);
});

// ── roles ────────────────────────────────────────────────────────────────────

test("the role index is derived from the roster's department column", () => {
  const index = m.departmentRoleIndex(ROSTER);
  assert.deepEqual(index.engineering, ["alice"]);
  assert.deepEqual(index.review.sort(), ["rev-1", "rev_1"]);
  assert.deepEqual(index.design, ["bob"]);
});

test("a role row discloses how many agents it reaches", () => {
  const rows = m.buildMentionRows({ agents: ROSTER });
  const review = rows.find((r) => r.id === "role:review");
  assert.equal(review.token, "@role:review");
  assert.equal(review.resolves.length, 2);
  assert.match(review.subtitle, /2 agents/);
  assert.equal(review.refusal, null);
});

test("a server-supplied role index is merged over the derived one", () => {
  const rows = m.buildMentionRows({ agents: ROSTER, roles: { oncall: ["alice", "bob"] } });
  const oncall = rows.find((r) => r.id === "role:oncall");
  assert.deepEqual(oncall.resolves.sort(), ["alice", "bob"]);
  assert.equal(m.parseMentions("@role:oncall", m.rosterHandles(ROSTER), { oncall: ["alice", "bob"] }).resolvedHandles.length, 2);
});

test("a role the roster cannot staff resolves to nobody and says so", () => {
  const resolution = m.parseMentions("@role:interns go", m.rosterHandles(ROSTER));
  assert.equal(resolution.ok, false);
  assert.match(resolution.unresolved[0].reason, /unknown role selector/);
});

test("a role naming an agent outside the roster is refused whole, not partly applied", () => {
  const resolution = m.parseMentions("@role:mixed go", m.rosterHandles(ROSTER), { mixed: ["alice", "ghost"] });
  assert.deepEqual(resolution.resolvedHandles, [], "the server emits no target when any member is absent");
  assert.match(resolution.unresolved[0].reason, /not in the roster/);
});

// ── the fan-out ceiling ──────────────────────────────────────────────────────

test("a fan-out above the ceiling is refused with the numbers, not clamped", () => {
  const many = Array.from({ length: 40 }, (_, i) => agent(`bot-${i}`));
  const rows = m.buildMentionRows({ agents: many });
  const everyone = rows.find((r) => r.id === "everyone");
  assert.equal(everyone.refusal !== null, true);
  assert.match(everyone.refusal, /40 agents/);
  assert.match(everyone.refusal, /32 fan-out ceiling/);

  const resolution = m.parseMentions("@everyone", m.rosterHandles(many));
  assert.equal(resolution.ok, false);
  assert.match(resolution.unresolved[0].reason, /fan-out ceiling/);
  assert.deepEqual(resolution.resolvedHandles, []);
});

test("the ceiling itself is allowed", () => {
  const exact = Array.from({ length: m.MAX_TARGETS_PER_MESSAGE }, (_, i) => agent(`bot-${i}`));
  assert.equal(m.buildMentionRows({ agents: exact }).find((r) => r.id === "everyone").refusal, null);
  assert.equal(m.parseMentions("@everyone", m.rosterHandles(exact)).ok, true);
});

// ── bot mode (switch) rows ──────────────────────────────────────────────────

test("the active agent is not offered a switch back to itself", () => {
  const rows = m.buildMentionRows({ agents: ROSTER, activeHandle: "alice", allowSwitch: true });
  const switches = rows.filter((r) => r.mode === "switch").map((r) => r.switchHandle);
  assert.equal(switches.includes("alice"), false, "a control that could only be a no-op is not a control");
  assert.equal(switches.includes("bob"), true);
});

test("no switch rows exist when the surface cannot switch", () => {
  const rows = m.buildMentionRows({ agents: ROSTER, allowSwitch: false });
  assert.equal(rows.some((r) => r.mode === "switch"), false);
});

test("a switch row writes the same resolvable token as its mention row", () => {
  const rows = m.buildMentionRows({ agents: ROSTER, allowSwitch: true });
  const mention = rows.find((r) => r.id === "bot:bob");
  const sw = rows.find((r) => r.id === "switch:bob");
  assert.equal(mention.token, sw.token);
  assert.equal(sw.switchHandle, "bob");
  assert.equal(m.parseMentions(sw.token, m.rosterHandles(ROSTER)).resolvedHandles[0], "bob");
});

// ── filtering, ranking and the disclosed bound ───────────────────────────────

test("an exact handle ranks above a prefix, which ranks above a substring", () => {
  const agents = [agent("bo"), agent("bobby"), agent("oscar"), agent("amy")];
  const rows = m.buildMentionRows({ agents });
  const order = m.filterMentionRows(rows, { start: 0, end: 0, kind: null, query: "bo" }).matched.map((r) => r.badge);
  assert.deepEqual(order.slice(0, 3), ["@bo", "@bobby", "@oscar"]);
});

test("a subsequence match is offered but never writes what the operator typed", () => {
  const rows = m.buildMentionRows({ agents: [agent("reviewer")] });
  const matched = m.filterMentionRows(rows, { start: 0, end: 0, kind: null, query: "rvwr" }).matched;
  assert.equal(matched.length, 1);
  assert.equal(matched[0].token, "@bot:reviewer", "the canonical handle is inserted, never the query");
});

test("an explicit namespace narrows the list instead of ranking within it", () => {
  const rows = m.buildMentionRows({ agents: ROSTER, allowSwitch: true });
  assert.deepEqual(
    [...new Set(m.filterMentionRows(rows, { start: 0, end: 0, kind: "role", query: "" }).matched.map((r) => r.mode))],
    ["role"],
  );
  assert.deepEqual(
    [...new Set(m.filterMentionRows(rows, { start: 0, end: 0, kind: "all", query: "" }).matched.map((r) => r.mode))],
    ["everyone"],
  );
  assert.deepEqual(
    [...new Set(m.filterMentionRows(rows, { start: 0, end: 0, kind: "bot", query: "" }).matched.map((r) => r.mode))].sort(),
    ["mention", "switch"],
  );
});

test("a hidden row is counted, so the bound is disclosed rather than silent", () => {
  const many = Array.from({ length: 14 }, (_, i) => agent(`agent-${i}`));
  const rows = m.buildMentionRows({ agents: many });
  const filtered = m.filterMentionRows(rows, { start: 0, end: 0, kind: null, query: "" }, 10);
  assert.equal(filtered.visible.length, 10);
  assert.equal(filtered.hidden, filtered.matched.length - 10);
  assert.equal(filtered.hidden > 0, true);
});

test("no matches is an empty list, not a fabricated row", () => {
  const rows = m.buildMentionRows({ agents: ROSTER });
  const filtered = m.filterMentionRows(rows, { start: 0, end: 0, kind: null, query: "zzzz" });
  assert.deepEqual(filtered.matched, []);
  assert.deepEqual(filtered.visible, []);
  assert.equal(filtered.hidden, 0);
});

// ── the status strip's trigger condition ─────────────────────────────────────

test("the status strip is warranted by a token count, so no-tag and dead-tag differ", () => {
  assert.equal(m.countMentionTokens(""), 0);
  assert.equal(m.countMentionTokens("plain text"), 0);
  assert.equal(m.countMentionTokens("hi @bot:alice"), 1);
  assert.equal(m.countMentionTokens("@bot:alice @role:review @everyone"), 3);
  assert.equal(m.countMentionTokens("@ghost"), 1, "a token that resolves to nobody still warrants the strip");
});

// ── the mirror must not drift from the server grammar ────────────────────────

test("the client's token charset matches the server's", () => {
  // `alpha.channels.mentions._MENTION_RE` allows [A-Za-z0-9_.-] only.
  const server = /@(bot|role|everyone):([A-Za-z0-9_.\-]+)|@([A-Za-z0-9_.\-]+)/g;
  const probes = ["alice", "rev-1", "rev_1", "a.b", "a b", "a/b", "a:b", "a@b", "a+b", "héllo", "a#b"];
  for (const probe of probes) {
    const text = `@${probe}`;
    const serverHit = server.test(text);
    server.lastIndex = 0;
    const clientHit = m.countMentionTokens(text) > 0;
    assert.equal(clientHit, serverHit, `token '${text}' disagreed with the server grammar`);
  }
});

test("the client mirror agrees with the server on the fan-out aliases and ceiling", () => {
  assert.deepEqual([...m.ALL_SELECTOR_ALIASES].sort(), ["all", "everyone"]);
  assert.equal(m.MAX_TARGETS_PER_MESSAGE, 32);
});

test("an unfamiliar roster status is carried verbatim, never snapped", () => {
  const row = m.buildMentionRows({ agents: [agent("x", { status: "hibernating_v2" })] }).find((r) => r.id === "bot:x");
  assert.equal(row.status, "hibernating_v2");
});