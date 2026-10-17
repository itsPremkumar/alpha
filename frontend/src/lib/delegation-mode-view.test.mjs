// delegation-mode-view.test.mjs — how the composer's subagent picker is
// organised, and every place a fabricated budget could have appeared.
//
// Why this suite exists
// ---------------------
// The picker was one flat list of ~14 rows in a 176px native select. Three
// DIFFERENT kinds of choice sat in one run with nothing to separate them:
//
//   * how much to delegate at all  -> `off`, `auto`
//   * which INTENT to delegate with -> `category:*`
//   * WHICH NAMED WORKER to run      -> `type:*`
//
// and the descriptions were cut by `.slice(0, 72)`, producing rows that read
// "with clear dele…", "convention c…", "sanitization-guard r…". The eight
// `deep-*` workers also ran straight into the built-ins.
//
// The tempting "fix" — replacing the native select with a div-built popover —
// would have made a 14-item list unsearchable on a phone and cost the
// keyboard/screen-reader behaviour a `<select>` ships for free. These tests pin
// the `<optgroup>` answer instead, and pin the honesty rule that matters most
// here: a budget is shown only where the configuration actually states one.
//
// The `value` strings (`off`, `auto`, `category:<n>`, `type:<n>`) are the wire
// contract with `ChatView`, so they are asserted exactly — a "prettier" value
// here would silently stop delegating.
//
// Pure Node (node --test src/lib/*.test.mjs): no server, no DOM, no renderer.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const toDataUrl = (source) =>
  `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;

const source = readFileSync(new URL("./delegation-mode-view.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
  },
}).outputText;

const { firstSentence, buildDelegationGroups, optionText } = await import(toDataUrl(code));

const ids = (groups) => groups.map((g) => g.id);
const values = (groups) => groups.flatMap((g) => g.options.map((o) => o.value));
const groupOf = (groups, value) =>
  groups.find((g) => g.options.some((o) => o.value === value));

/** Catalog rows shaped like `SubagentDef` after the composer's filter. */
const catalog = [
  {
    name: "deep-architect",
    displayName: "deep-architect",
    description:
      "Autonomous multi-file refactoring specialist with AST impact analysis and an incremental apply plan.",
    source: "config",
  },
  {
    name: "deep-code-reviewer",
    displayName: null,
    description: "Autonomous patch gatekeeper with API, type, regression, and convention checks.",
    source: "config",
  },
  {
    name: "general-purpose",
    displayName: null,
    description: "A capable agent for bounded exploration and action when there is clear direction.",
    source: "builtin",
  },
  { name: "bash", displayName: "bash", description: "", source: "builtin" },
];

// ---------------------------------------------------------------------------
// firstSentence: the truncation that produced "with clear dele…"
// ---------------------------------------------------------------------------

test("a description is cut at a sentence, not mid-word", () => {
  const out = firstSentence(
    "A capable agent for bounded exploration and action. It also writes tests afterwards.",
  );
  assert.equal(out, "A capable agent for bounded exploration and action.");
  // The second sentence must not ride along.
  assert.ok(!out.includes("afterwards"));
});

test("a description with no sentence end is cut at a word boundary and marked", () => {
  const out = firstSentence(
    "A capable agent for bounded exploration and action when there is clear direction from the caller",
    40,
  );
  assert.ok(out.endsWith("…"), `expected a visible ellipsis, got ${JSON.stringify(out)}`);
  assert.ok(out.length <= 41, `expected <= 40 chars + ellipsis, got ${out.length}`);
  // No dangling half-word: the character before the ellipsis is not a letter
  // that was cut off from a word.
  assert.ok(!/\s…$/.test(out), `ellipsis should attach to a word, got ${JSON.stringify(out)}`);
});

test("whitespace is collapsed so a multi-line description reads as one row", () => {
  assert.equal(firstSentence("a   b\n\nc\t d."), "a b c d.");
});

test("absent or empty text yields empty, never an invented sentence", () => {
  assert.equal(firstSentence(null), "");
  assert.equal(firstSentence(undefined), "");
  assert.equal(firstSentence("   "), "");
});

// ---------------------------------------------------------------------------
// Grouping: three kinds of choice must not interleave
// ---------------------------------------------------------------------------

test("the picker groups delegation, intent presets and named workers separately", () => {
  const groups = buildDelegationGroups({ choices: catalog });
  assert.deepEqual(ids(groups).slice(0, 2), ["delegation", "intent"]);
  // Every worker group is namespaced, so a worker can never be mistaken for a
  // mode or a preset.
  for (const id of ids(groups).slice(2)) assert.ok(id.startsWith("worker:"), id);
  assert.ok(!values(groups).includes("deep-architect"), "a bare name is not a value");
});

test("the wire values are unchanged — off, auto, category:<n>, type:<n>", () => {
  const got = values(buildDelegationGroups({ choices: catalog }));
  assert.ok(got.includes("off"));
  assert.ok(got.includes("auto"));
  assert.ok(got.includes("category:quick"));
  assert.ok(got.includes("category:deep-research"));
  assert.ok(got.includes("type:deep-architect"));
  assert.ok(got.includes("type:general-purpose"));
});

test("every choice appears exactly once — no duplicate row for the same worker", () => {
  const got = values(buildDelegationGroups({ choices: catalog }));
  for (const name of ["deep-architect", "deep-code-reviewer", "general-purpose", "bash"]) {
    assert.equal(got.filter((v) => v === `type:${name}`).length, 1, name);
  }
});

test("workers are grouped by the source the server reported", () => {
  const groups = buildDelegationGroups({ choices: catalog });
  assert.deepEqual(ids(groups).sort(), ["delegation", "intent", "worker:builtin", "worker:config"]);
  // The eight deep-* specialists no longer run straight into the built-ins.
  assert.equal(groupOf(groups, "type:deep-architect").id, "worker:config");
  assert.equal(groupOf(groups, "type:general-purpose").id, "worker:builtin");
});

test("a worker with no reported source is disclosed, not folded into builtin", () => {
  const groups = buildDelegationGroups({
    choices: [{ name: "mystery", displayName: null, description: "x.", source: "" }],
  });
  // Folding it into "builtin" would be a claim the server never made.
  assert.ok(!ids(groups).includes("worker:builtin"));
  assert.ok(ids(groups).includes("worker:source not reported"));
});

test("an unknown source string from a newer Gateway is preserved verbatim", () => {
  const groups = buildDelegationGroups({
    choices: [{ name: "n", displayName: null, description: "x.", source: "experimental" }],
  });
  assert.ok(ids(groups).includes("worker:experimental"));
});

// ---------------------------------------------------------------------------
// Honesty: a budget appears only where configuration states one
// ---------------------------------------------------------------------------

test("quick's budget is quoted because config.example.yaml states it", () => {
  const quick = buildDelegationGroups({ choices: null }).find((g) => g.id === "intent")
    .options.find((o) => o.value === "category:quick");
  assert.match(quick.hint, /15 turns/);
  assert.match(quick.hint, /2 min/);
});

test("no preset without a reported budget is given an invented one", () => {
  const options = buildDelegationGroups({ choices: null }).find((g) => g.id === "intent").options;
  const budgeted = new Set(["category:quick", "category:research"]);
  for (const option of options) {
    if (budgeted.has(option.value)) continue;
    // `general` and `deep-research` quote no limit in configuration. A number
    // here would be the UI inventing a cost the operator then trusts.
    assert.ok(
      !/\d/.test(option.hint),
      `${option.value} must quote no number, got ${JSON.stringify(option.hint)}`,
    );
  }
});

test("presets with a reported budget sort before those without", () => {
  const options = buildDelegationGroups({ choices: null }).find((g) => g.id === "intent").options;
  const lastBudgeted = options.map((o) => o.value).lastIndexOf("category:research");
  const firstUnbudgeted = options.map((o) => o.value).indexOf("category:deep-research");
  assert.ok(lastBudgeted < firstUnbudgeted, "an unreported budget must not imply a smaller bill");
  // Cheapest first overall.
  assert.equal(options[0].value, "category:quick");
});

// ---------------------------------------------------------------------------
// Honesty: the three catalog answers stay distinguishable
// ---------------------------------------------------------------------------

test("an empty catalog says empty; it does not silently render no workers", () => {
  const groups = buildDelegationGroups({ choices: [] });
  assert.ok(ids(groups).includes("catalog-empty"));
  assert.ok(!ids(groups).includes("catalog-error"));
});

test("a failed catalog read says unavailable and carries the server's reason", () => {
  const groups = buildDelegationGroups({ choices: null, error: "503 backend unavailable" });
  const group = groups.find((g) => g.id === "catalog-error");
  assert.ok(group, "a failed read must be visible");
  assert.match(group.options[0].hint, /503 backend unavailable/);
  assert.ok(!ids(groups).includes("catalog-empty"), "a failure is not an empty catalog");
});

test("a failure row is disabled so the select's value can never be stranded on it", () => {
  const rows = buildDelegationGroups({ choices: null, error: "boom" })
    .flatMap((g) => g.options)
    .filter((o) => o.value === "");
  assert.equal(rows.length, 1);
});

test("no catalog at all still offers delegation and presets", () => {
  const groups = buildDelegationGroups({ choices: null, error: "boom" });
  assert.ok(values(groups).includes("off"));
  assert.ok(values(groups).includes("category:quick"));
});

// ---------------------------------------------------------------------------
// Rendering: the row text the operator actually reads
// ---------------------------------------------------------------------------

test("a worker row is a label and a complete sentence, never a fragment", () => {
  const option = buildDelegationGroups({ choices: catalog })
    .find((g) => g.id === "worker:config")
    .options.find((o) => o.value === "type:deep-code-reviewer");
  // `displayName: null` falls back to `name` — `null` is not "unnamed".
  assert.ok(option.label.length > 0);
  assert.equal(optionText(option), `${option.label} · ${option.hint}`);
  // The old `.slice(0, 72)` produced "…with convention c…". No mid-word cut here.
  assert.ok(!/\w…$/.test(option.hint), `mid-word cut survived: ${JSON.stringify(option.hint)}`);
});

test("a worker with no reported description is labelled unreported, not filled in", () => {
  const option = buildDelegationGroups({ choices: catalog })
    .find((g) => g.id === "worker:builtin")
    .options.find((o) => o.value === "type:bash");
  // The old code defaulted this to "specialist worker", which asserts a role
  // the server never reported.
  assert.match(option.hint, /not reported/);
});

test("every option carries a non-empty label and hint", () => {
  for (const option of buildDelegationGroups({ choices: catalog, error: "x" }).flatMap((g) => g.options)) {
    assert.ok(option.label.trim().length > 0, `empty label for ${option.value}`);
    assert.ok(option.hint.trim().length > 0, `empty hint for ${option.value}`);
  }
});