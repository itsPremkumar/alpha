// subagent-catalog-view.test.mjs — every field of a subagent definition, and
// every place a `null` could be mistaken for a measured value.
//
// Why this suite exists
// ---------------------
// `GET /api/subagents` sends fifteen fields per definition. The client mapped
// six, and the catalog block rendered four of them as one card per subagent —
// so an operator choosing a helper could not see which tools it may call, what
// it is forbidden, what skills it holds, what its turn and time budgets are, or
// what it is actually instructed to do.
//
// The panel's sentences live in `lib/subagent-catalog-view.ts` rather than in
// JSX so this suite drives the exact function that renders each one. A test
// that greps prose out of a component proves the words are present; it cannot
// prove the *branch* that chose them. Each test below names the server payload
// that would make a plausible wrong word appear.
//
// Pure Node (node --test src/lib/*.test.mjs): no server, no DOM, no renderer.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const read = (relative) =>
  readFileSync(new URL(relative, import.meta.url), "utf8");
const toDataUrl = (source) =>
  `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;

const source = read("./subagent-catalog-view.ts");
const code = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
  },
}).outputText;
// `subagents` is imported for the `SubagentDef` type only, so the transpile
// elides it. The stub is a guard, not decoration: a real value import added here
// would resolve against a nonexistent path and fail loudly.
const stubUrl = toDataUrl(`export const unusedStub = null;`);
const {
  sourceMeta,
  isKnownSource,
  titleOf,
  enabledView,
  listView,
  limitView,
  promptDisclosure,
  overridesView,
  conflictNote,
  groupBySource,
  catalogCounts,
  countsSentence,
  rowCapabilityLine,
} = await import(
  toDataUrl(code.replace(/from\s+"\.\/subagents"/, `from "${stubUrl}"`))
);

/** A definition with every field the server sends, so a test overrides one. */
const full = (over = {}) => ({
  name: "deep-architect",
  displayName: null,
  description: "Architecture specialist.",
  systemPrompt: "You are an architecture specialist.",
  tools: ["read_file", "glob", "write_file"],
  disallowedTools: ["task", "bash"],
  skills: ["design-review"],
  model: "inherit",
  maxTurns: 50,
  timeoutSeconds: 900,
  enabled: true,
  source: "builtin",
  editable: false,
  conflict: false,
  configOverrides: {},
  ...over,
});

// ---------------------------------------------------------------------------
// The three states of a list field, which must never collapse
// ---------------------------------------------------------------------------

test("a null allowlist means unrestricted, and says so in words", () => {
  const v = listView(null, "allowlist");
  assert.equal(v.state, "absent");
  assert.match(v.summary, /not restricted/i);
  // The dangerous phrasing: "no tools" reads as a restriction the server never
  // reported, and would read as a subagent that cannot do anything.
  assert.doesNotMatch(v.summary, /no tools/i);
});

test("an empty allowlist is an explicit restriction and reads differently", () => {
  const v = listView([], "allowlist");
  assert.equal(v.state, "empty");
  assert.match(v.summary, /empty/i);
  assert.match(v.summary, /no tool is callable/i);
});

test("null and [] are different facts about the same field", () => {
  // This is the whole reason `SubagentDef.tools` is `string[] | null`: the
  // server sends `None` for an unconstrained definition and `[]` only when a
  // caller explicitly asked for nothing. Collapsing them would make an
  // unrestricted subagent look like a disabled one.
  assert.notEqual(
    listView(null, "allowlist").state,
    listView([], "allowlist").state,
  );
});

test("a populated list reports its count in words, not as a bare number", () => {
  const v = listView(["read_file", "glob", "write_file"], "allowlist");
  assert.equal(v.state, "listed");
  assert.equal(v.chips.length, 3);
  assert.match(v.summary, /3 allowlist entries/);
});

test("a single-entry list is not pluralised", () => {
  assert.match(
    listView(["read_file"], "allowlist").summary,
    /1 allowlist entry\./,
  );
});

test("an empty skills list says empty rather than unreported", () => {
  assert.equal(listView([], "skills").state, "empty");
  assert.equal(listView(null, "skills").state, "absent");
});

test("the caller supplies the word, so the three fields stay distinct", () => {
  // `deny-list` and `skills` have no allowlist reading. If this module hardcoded
  // "allowlist", the deny-list row would claim the subagent may call nothing
  // when it is in fact allowed everything except those names.
  assert.match(listView(null, "deny-list").summary, /No deny-list reported/);
  assert.match(listView(["task"], "deny-list").summary, /1 deny-list entry/);
});

// ---------------------------------------------------------------------------
// Limits: never a locally-invented default
// ---------------------------------------------------------------------------

test("an unreported turn ceiling stays unreported, never 50", () => {
  // `ManagedSubagentCreateRequest.max_turns` defaults to 50, so 50 is what a
  // reader would guess. Asserting it without reading it is precisely the claim
  // this suite exists to prevent.
  assert.equal(limitView(null, "turns"), "turns not reported");
  assert.doesNotMatch(limitView(null, "turns"), /\b50\b/);
});

test("a reported ceiling is rendered with its unit", () => {
  assert.equal(limitView(12, "turns"), "12 turns");
  assert.equal(limitView(900, "s"), "900 s");
});

test("a zero ceiling renders as zero, not as unreported", () => {
  // 0 is not `null`. The server's validators reject 0 (`ge=1`), so this state
  // cannot arrive from this route today — but collapsing it to "not reported"
  // would hide a future real value, which is the mirror of the bug above.
  assert.equal(limitView(0, "turns"), "0 turns");
});

// ---------------------------------------------------------------------------
// The enabled tri-state, and the regression that made it binary
// ---------------------------------------------------------------------------

test("an unreported enabled flag is unknown, never on", () => {
  const v = enabledView(null);
  assert.equal(v.label, "unknown");
  assert.equal(v.tone, "muted");
  assert.match(v.reason, /was not measured/i);
  // The exact regression: `Boolean(pick(s, ["enabled"], true))` made absent
  // TRUE, and `SubagentsSection` painted that green.
  assert.notEqual(v.tone, "green");
});

test("enabled and disabled carry the server's own verdict", () => {
  assert.equal(enabledView(true).tone, "green");
  assert.equal(enabledView(true).label, "on");
  assert.equal(enabledView(false).tone, "gray");
  assert.equal(enabledView(false).label, "off");
});

test("every enabled state carries a reason the badge has no room for", () => {
  for (const v of [enabledView(true), enabledView(false), enabledView(null)]) {
    assert.ok(
      v.reason.length > 20,
      "a badge alone cannot say why it is that colour",
    );
  }
});

// ---------------------------------------------------------------------------
// The system prompt: a permission outcome, not an empty document
// ---------------------------------------------------------------------------

test("a withheld prompt names the admin gate rather than claiming no prompt", () => {
  // `subagents.py:183` — `include_system_prompt = await is_admin_user(request)`.
  // A non-admin receives `system_prompt=None` for every row, so `null` means
  // "you may not see it". Rendering it as an empty prompt would tell an
  // operator this subagent has no instructions, which is false for all eight
  // builtins.
  const v = promptDisclosure(null);
  assert.equal(v.present, false);
  assert.match(v.reason, /admin/i);
  assert.doesNotMatch(v.reason, /no prompt|no instructions/i);
});

test("a blank prompt is disclosed as blank, distinct from withheld", () => {
  const v = promptDisclosure("   ");
  assert.equal(v.present, false);
  assert.match(v.reason, /empty prompt/i);
});

test("a real prompt is present and carries no caveat", () => {
  const v = promptDisclosure("You are an architecture specialist.");
  assert.equal(v.present, true);
  assert.equal(v.reason, "");
});

// ---------------------------------------------------------------------------
// config.yaml overrides: the empty case is the normal case
// ---------------------------------------------------------------------------

test("an absent overrides block says it was not reported", () => {
  const v = overridesView(null);
  assert.equal(v.rows.length, 0);
  assert.match(v.note, /no config_overrides block/i);
});

test("an empty overrides object says the defaults are in force", () => {
  // `_explicit_overrides` only reports keys the operator actually wrote, so `{}`
  // is what every definition on a stock install sends. Saying "not configured"
  // would be false — the definition's own defaults are very much applied.
  const v = overridesView({});
  assert.equal(v.rows.length, 0);
  assert.match(v.note, /defaults are in force/i);
  assert.doesNotMatch(v.note, /not configured/i);
});

test("reported overrides render sorted, with the value formatted as written", () => {
  const v = overridesView({ timeout_seconds: 120, skills: ["a", "b"] });
  assert.deepEqual(
    v.rows.map((r) => r.key),
    ["skills", "timeout_seconds"],
  );
  assert.equal(v.rows[0].value, '["a","b"]');
  assert.equal(v.rows[1].value, "120");
});

test("the override note always explains the omission rule", () => {
  assert.match(overridesView({ model: "x" }).note, /definition's own default/);
});

// ---------------------------------------------------------------------------
// Naming and conflict
// ---------------------------------------------------------------------------

test("display_name wins when the server sent one, and the row says which it used", () => {
  const v = titleOf(
    full({ name: "code-reviewer", displayName: "Code Reviewer" }),
  );
  assert.equal(v.title, "Code Reviewer");
  assert.equal(v.fromDisplayName, true);
});

test("a null display_name falls back to the name, not to a blank title", () => {
  const v = titleOf(full({ displayName: null }));
  assert.equal(v.title, "deep-architect");
  assert.equal(v.fromDisplayName, false);
});

test("an unnamed definition is disclosed rather than rendered blank", () => {
  assert.match(titleOf(full({ name: "" })).title, /unnamed/i);
});

test("a name conflict is stated, with the precedence that causes it", () => {
  const note = conflictNote(true);
  assert.ok(note);
  assert.match(note, /config\.yaml/);
  assert.equal(conflictNote(false), null);
});

test("an unknown source renders verbatim and claims to be unrecognised", () => {
  // Snapping an unrecognised source to "built in" would tell the operator a
  // runtime-created definition ships with Alpha.
  const m = sourceMeta("tenant-supplied");
  assert.equal(m.label, "tenant-supplied");
  assert.equal(m.tone, undefined);
  assert.match(m.blurb, /does not recognise/i);
  assert.equal(isKnownSource("tenant-supplied"), false);
});

test("the three server sources are named and toned", () => {
  for (const s of ["builtin", "config", "managed"]) {
    assert.equal(
      isKnownSource(s),
      true,
      `${s} is declared in SubagentResponse`,
    );
    assert.ok(sourceMeta(s).label.length > 0);
  }
  assert.notEqual(sourceMeta("builtin").tone, sourceMeta("managed").tone);
});

// ---------------------------------------------------------------------------
// Grouping and counts
// ---------------------------------------------------------------------------

test("groups follow the server's tier order, not alphabetical accident", () => {
  const groups = groupBySource([
    full({ name: "z", source: "managed" }),
    full({ name: "a", source: "config" }),
    full({ name: "b", source: "builtin" }),
  ]);
  assert.deepEqual(
    groups.map((g) => g.source),
    ["builtin", "config", "managed"],
  );
});

test("an unrecognised source is grouped last so it stays visible", () => {
  const groups = groupBySource([
    full({ name: "a", source: "future" }),
    full({ name: "b", source: "builtin" }),
  ]);
  assert.deepEqual(
    groups.map((g) => g.source),
    ["builtin", "future"],
  );
});

test("items within a group are name-sorted", () => {
  const groups = groupBySource([
    full({ name: "zebra" }),
    full({ name: "apple" }),
  ]);
  assert.deepEqual(
    groups[0].items.map((i) => i.name),
    ["apple", "zebra"],
  );
});

test("counts separate enabled, disabled, and unreported", () => {
  const c = catalogCounts([
    full({ enabled: true }),
    full({ enabled: false }),
    full({ enabled: null }),
  ]);
  assert.deepEqual(c, { total: 3, enabled: 1, disabled: 1, unknown: 1 });
});

test("an unreported flag is disclosed in the headline, not absorbed", () => {
  // "2 of 3 enabled" leaves the reader assuming the third is off. It might be
  // that the server never said.
  const s = countsSentence({ total: 3, enabled: 2, disabled: 0, unknown: 1 });
  assert.match(s, /1 not reporting an enabled flag/);
});

test("a clean catalog headline names only what was measured", () => {
  const s = countsSentence({ total: 8, enabled: 8, disabled: 0, unknown: 0 });
  assert.match(s, /8 of 8 enabled/);
  assert.doesNotMatch(s, /not reporting/);
});

test("an empty catalog says the server reported none, rather than implying zero", () => {
  assert.match(
    countsSentence({ total: 0, enabled: 0, disabled: 0, unknown: 0 }),
    /reported no subagent definitions/,
  );
});

// ---------------------------------------------------------------------------
// The list row: a claim the server made, not a measurement the panel took
// ---------------------------------------------------------------------------

test("an unconstrained definition's row says all tools, not zero", () => {
  assert.match(rowCapabilityLine(full({ tools: null })), /all tools/);
});

test("an explicitly empty allowlist's row says no tools", () => {
  assert.match(rowCapabilityLine(full({ tools: [] })), /no tools/);
  assert.doesNotMatch(rowCapabilityLine(full({ tools: [] })), /all tools/);
});

test("an unreported allowlist's row says unreported, not all tools", () => {
  // Both map to `null` at the client boundary, so the row is where a guess
  // would surface. `all tools` is a capability claim; unreported is not.
  const line = rowCapabilityLine(
    full({ tools: null, skills: null, maxTurns: null }),
  );
  assert.match(line, /turns unreported/);
});

test("skills appear in a row only as a measured fact", () => {
  // `skills: null` and `skills: []` are opposite facts here too, so the row says
  // nothing for the first and "no skills" for the second. Writing "no skills"
  // for an unreported list would claim the server measured an absence.
  assert.match(rowCapabilityLine(full({ skills: ["a"] })), /1 skills/);
  assert.doesNotMatch(rowCapabilityLine(full({ skills: null })), /skills/);
  assert.match(rowCapabilityLine(full({ skills: [] })), /no skills/);
});

// ---------------------------------------------------------------------------
// Structural pins: the panel must not regress into a one-line card
// ---------------------------------------------------------------------------

test("the catalog panel renders every field, not a name and a badge", () => {
  // The card this replaced printed name / badge / description / model+source.
  // Each field below was mapped by the client and then never shown.
  const section = read("../components/sections/SubagentsSection.tsx");
  for (const field of [
    "def.systemPrompt",
    "def.tools",
    "def.disallowedTools",
    "def.skills",
    "def.maxTurns",
    "def.timeoutSeconds",
    "def.conflict",
    "def.editable",
  ]) {
    assert.ok(section.includes(field), `the panel must render ${field}`);
  }
  // `display_name` is reached through `titleOf`, which is what decides between
  // the display name and the name — so the pin is on the derivation, not on a
  // literal the markup has no reason to spell.
  assert.ok(
    section.includes("titleOf(def)"),
    "display_name must reach the header",
  );
});

test("titleOf really does read display_name, so the pin above cannot go stale", () => {
  assert.equal(titleOf(full({ name: "n", displayName: "D" })).title, "D");
});

test("no field is rendered twice — the sentences live in the view module", () => {
  // A second copy of a sentence is how a list row and its own detail pane end
  // up contradicting each other for the same field.
  const section = read("../components/sections/SubagentsSection.tsx");
  for (const helper of [
    "enabledView",
    "listView",
    "promptDisclosure",
    "overridesView",
    "limitView",
  ]) {
    assert.ok(section.includes(helper), `${helper} must be the single source`);
  }
  // Scoped to the catalog panel, which runs from `CatalogPanel` to the
  // `BatchesBlock` that follows it. The live-fleet block above has its own
  // "Objective not reported" for a `LiveSubagent` — a different type with a
  // different absence — and asserting on the whole file would be asserting
  // about a surface this change does not own.
  const panel = section.slice(
    section.indexOf("function CatalogPanel"),
    section.indexOf("function BatchesBlock"),
  );
  const handRolled = panel.match(/[A-Za-z]* ?not reported(?!["'`])/g) ?? [];
  assert.deepEqual(
    handRolled,
    [],
    "the catalog panel must not hand-roll its own absence phrasing",
  );
});
