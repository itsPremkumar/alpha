/**
 * Contract pins for the operator identity.
 *
 * These exist because the previous implementation hardcoded `userName = "MK"`
 * in five places and **no test failed**. Each assertion below is an inversion
 * of that defect: a name must come from the operator, never from a default, and
 * an absent name must render as absent rather than as a plausible stand-in.
 *
 * To prove these bite, reintroduce `userName = "MK"` as a default in
 * `ChatShellLanding.tsx` — the suite that reads the source (below) fails.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { moduleUrl } from "./test-modules.mjs";

const {
  ANONYMOUS_INITIALS,
  ANONYMOUS_LABEL,
  ANONYMOUS_ROLE,
  OPERATOR_NAME_STORAGE_KEY,
  currentOperatorIdentity,
  landingGreeting,
  normalizeOperatorName,
  operatorIdentity,
  operatorInitials,
  readOperatorName,
  writeOperatorName,
} = await import(moduleUrl("operator"));

const srcDir = new URL("../", import.meta.url);
const read = (rel) => readFileSync(new URL(rel, srcDir), "utf8");

test("an unconfigured operator has no name, and says so", () => {
  const id = operatorIdentity(null);
  assert.equal(id.name, null, "no name may be invented when none is configured");
  assert.equal(id.configured, false);
  assert.equal(id.initials, ANONYMOUS_INITIALS);
  assert.equal(id.role, ANONYMOUS_ROLE);
});

test("operatorInitials derives from the name and never returns a hardcoded pair", () => {
  assert.equal(operatorInitials("Ada Lovelace"), "AL");
  assert.equal(operatorInitials("Prince"), "PR");
  assert.equal(operatorInitials("  grace   brewster  hopper "), "GH");
  // A name the operator typed with no space at all still yields a real glyph.
  assert.equal(operatorInitials("x"), "X");
});

test("an empty or whitespace-only name is null, not a blank avatar", () => {
  for (const empty of ["", "   ", "\t\n"]) {
    assert.equal(normalizeOperatorName(empty), null, `${JSON.stringify(empty)} must normalise to null`);
    assert.equal(operatorInitials(empty), ANONYMOUS_INITIALS);
  }
  // And it must not read as "configured".
  assert.equal(operatorIdentity("   ").configured, false);
});

test("a non-string name is rejected rather than coerced", () => {
  for (const bad of [undefined, null, 42, {}, [], true]) {
    assert.equal(normalizeOperatorName(bad), null);
  }
});

test("names collapse internal whitespace and are length-capped", () => {
  assert.equal(normalizeOperatorName("  Ada   Lovelace  "), "Ada Lovelace");
  const long = "x".repeat(500);
  assert.equal(normalizeOperatorName(long).length, 64);
});

test("the greeting says 'Welcome back' only when a previous session is real", () => {
  const anon = operatorIdentity(null);
  const named = operatorIdentity("Ada");

  // First run: no "back". This is the copy the old build got wrong.
  assert.equal(landingGreeting(anon, false), "Welcome to Alpha!");
  assert.equal(landingGreeting(named, false), "Welcome, Ada!");

  // Returning is caller-supplied; when true it is honest in both name states.
  assert.equal(landingGreeting(anon, true), "Welcome back!");
  assert.equal(landingGreeting(named, true), "Welcome back, Ada!");
});

test("the greeting never renders an empty or double space when a name is absent", () => {
  const anon = operatorIdentity(null);
  for (const returning of [true, false]) {
    const greeting = landingGreeting(anon, returning);
    assert.ok(!greeting.includes("  "), `greeting has a double space: ${greeting}`);
    assert.ok(!greeting.includes("undefined"), `greeting leaked undefined: ${greeting}`);
    assert.ok(!greeting.includes("null"), `greeting leaked null: ${greeting}`);
  }
});

test("storage reads are safe without a window and writes report their outcome", () => {
  // node has no `window`; these must not throw, and must not invent a name.
  assert.equal(readOperatorName(), null);
  assert.equal(currentOperatorIdentity().name, null);
  assert.equal(writeOperatorName("Ada"), false, "a no-window write reports failure, not silent success");
});

test("the anonymous label is a role word, never a person", () => {
  assert.ok(!ANONYMOUS_LABEL.includes("MK"));
  assert.ok(!ANONYMOUS_ROLE.includes("MK"));
  assert.ok(!ANONYMOUS_INITIALS.includes("M"));
});

test("the storage key is the documented one", () => {
  assert.equal(OPERATOR_NAME_STORAGE_KEY, "alpha_operator_name");
});

// --- Source pins: the defect was a literal in a default parameter ------------

test("no component hardcodes the developer's initials as an operator identity", () => {
  const offenders = [];
  for (const rel of [
    "components/chat-shell/ChatShellLanding.tsx",
    "components/chat-shell/WorkspaceTopBar.tsx",
    "components/ChatView.tsx",
  ]) {
    const source = read(rel);
    source.split("\n").forEach((line, i) => {
      if (/\buserName\s*=\s*["']/i.test(line) || /\buserInitials\s*=\s*["']/.test(line)) {
        offenders.push(`${rel}:${i + 1} ${line.trim()}`);
      }
      if (/\buserName=["'][^"']+["']/.test(line) && !/userName={"`/.test(line)) {
        offenders.push(`${rel}:${i + 1} ${line.trim()}`);
      }
    });
  }
  assert.deepEqual(offenders, [], `hardcoded operator identity: ${offenders.join(" | ")}`);
});

/**
 * Strip comments so a pin can target *code* rather than the prose that
 * documents the fix.
 *
 * This deliberately ignores comments, because the fix records the history of
 * the defect ("this defaulted to `"MK"`") and a pin that flagged its own
 * explanation would make that documentation undeletable.
 *
 * Handles all three comment forms this codebase uses: `/* ... *\/` blocks, the
 * JSX form `{/* ... *\/}`, and trailing `// ...`. Blanking preserves line
 * numbering so a failure still points at the right line.
 */
function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "))
    .split("\n")
    .map((line) => {
      // A `//` inside a string literal would confuse this, but the files under
      // test are JSX/TSX with no `http://` outside comments or a quoted URL
      // constant, and the check is a heuristic pin rather than a parser.
      const cut = line.indexOf("//");
      return cut === -1 ? line : line.slice(0, cut);
    })
    .join("\n");
}

/**
 * A prop may legitimately be passed a *literal* name string, so that alone is
 * not the defect. What must never appear is the specific developer's initials
 * this codebase shipped.
 */
test("the developer's initials appear in no line of executable code", () => {
  const rel = [
    "components/chat-shell/ChatShellLanding.tsx",
    "components/chat-shell/WorkspaceTopBar.tsx",
    "components/ChatView.tsx",
    "lib/operator.ts",
  ];
  const offenders = [];
  for (const file of rel) {
    stripComments(read(file))
      .split("\n")
      .forEach((line, i) => {
        if (/"MK"|'MK'|>MK<|\bMK\b/.test(line)) {
          offenders.push(`${file}:${i + 1} ${line.trim()}`);
        }
      });
  }
  assert.deepEqual(offenders, [], `the hardcoded initials are still in code: ${offenders.join(" | ")}`);
});

test("the comment stripper actually strips, so the pin above is not vacuous", () => {
  // Guards the guard: if `stripComments` ever stopped removing comments, the
  // initials pin would either pass for the wrong reason or start failing on
  // its own documentation. This proves the two behaviours separately.
  const sample = [
    'const a = 1; // trailing "MK"',
    '{/* jsx "MK" */}',
    "/* block",
    '   "MK" */',
    'const b = "MK";',
  ].join("\n");
  const stripped = stripComments(sample);
  assert.ok(!stripped.includes("MK //"), "trailing comment not stripped");
  assert.ok(!stripped.includes("jsx"), "JSX block comment not stripped");
  assert.ok(!stripped.includes('block'), "multi-line block comment not stripped");
  // The real code line survives, which is what makes the pin meaningful.
  assert.ok(stripped.includes('const b = "MK";'), "code was stripped along with the comments");
});

test("the operator name is never defaulted to a literal in a prop signature", () => {
  // `userName = "anything"` in a destructuring default is the exact shape of
  // the original defect: a visitor is named without ever choosing to be.
  const offenders = [];
  for (const file of [
    "components/chat-shell/ChatShellLanding.tsx",
    "components/chat-shell/WorkspaceTopBar.tsx",
  ]) {
    read(file)
      .split("\n")
      .forEach((line, i) => {
        if (/userName\s*=\s*"|userInitials\s*=\s*"/.test(line)) {
          offenders.push(`${file}:${i + 1} ${line.trim()}`);
        }
      });
  }
  assert.deepEqual(offenders, [], `a name is still defaulted to a literal: ${offenders.join(" | ")}`);
});

test("the landing page derives its greeting through the shared helper", () => {
  const source = read("components/chat-shell/ChatShellLanding.tsx");
  assert.ok(
    source.includes("landingGreeting"),
    "ChatShellLanding must render the greeting from landingGreeting(), not a literal",
  );
  assert.ok(
    !/Welcome back,\s*\{/.test(source),
    'the literal "Welcome back, {name}!" template must be gone',
  );
});

test("the top bar takes the identity from its caller and defaults to anonymous", () => {
  const source = read("components/chat-shell/WorkspaceTopBar.tsx");
  assert.ok(source.includes("ANONYMOUS_INITIALS"), "the avatar must fall back to the anonymous glyph");
  assert.ok(
    !/userName\s*=\s*["'][A-Za-z]/.test(source),
    "WorkspaceTopBar must not default userName to a literal name",
  );
});
