// A failed custom-agent read must not render as "0 agents".
//
// Root cause this pins: `GET /api/console/stats` counts runs/threads/tokens with
// SQL COUNT()/SUM() — a failure there fails the whole route — but `total_agents`
// comes from a filesystem scan (`list_custom_agents`) that degrades instead of
// raising. That degraded path answered `total_agents: 0`, and `workspace.ts`
// then did `count(...) ?? 0` on a field typed `number`, so a read that never
// completed became a measured "0 custom agent profiles" in the vitals strip and
// the dashboard card. The vitals tooltip already promised the opposite: "a
// failure to read it would render as 'not reported' instead."
//
// Three links each had to be wrong for that to reach the screen:
//   backend  total_agents: int -> int | None, plus a total_agents_reason
//   client   ConsoleStats.agents: number -> number | null (no ?? 0)
//   view     both callers render "—" with the reason instead of a number
//
// The negative control at the bottom loads the pre-fix code and shows it
// produces the dishonest value.
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import test from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";

const ROOT = "C:\\Users\\PREM KUMAR\\Videos\\alpha";
const read = (rel) => readFileSync(`${ROOT}\\frontend\\${rel}`, "utf8");

// Pinned to the parent of 4edede6, the commit that fixed this defect, rather
// than to HEAD. Reading HEAD made this control self-destruct the moment the fix
// landed: HEAD then *is* the fixed code, it correctly returns null, and the
// assertion that the old code returned 0 failed — with the file's own message
// saying "re-derived from git". Pinning the pre-fix snapshot keeps the claim
// checkable forever: that exact revision still coerces an unreported count to
// a measured zero, which is what proves the assertion above can fail at all.
const PRE_FIX = "4edede6^:frontend/";
const preFix = (rel) =>
  execFileSync("git", ["show", `${PRE_FIX}${rel}`], {
    cwd: ROOT,
    encoding: "utf8",
  });

function loadWorkspace(source) {
  const js = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const exports = {};
  new Function("exports", "module", "require", js)(
    exports,
    { exports },
    (spec) => {
      if (spec === "./http" || spec === "@/lib/http") {
        return {
          // `workspace.ts` also imports `pick` from the same module, and
          // `count()` is built on it — a stub without it throws before the
          // mapping under test ever runs.
          pick: (obj, keys, fallback) => {
            if (!obj) return fallback;
            for (const k of keys) {
              if (Object.hasOwn(obj, k) && obj[k] !== undefined) return obj[k];
            }
            return fallback;
          },
          get: async (path) => globalThis.__statsHandler(path),
        };
      }
      return {};
    },
  );
  return exports;
}

const statsPayload = (extra = {}) => ({
  total_runs: 12,
  total_threads: 7,
  total_tokens: 4242,
  total_cost: null,
  currency: null,
  ...extra,
});

test("an unreadable agent count stays null — it is not a measured zero", async () => {
  const mod = loadWorkspace(read("src/lib/workspace.ts"));
  globalThis.__statsHandler = () => statsPayload({ total_agents: null, total_agents_reason: "custom agent profiles could not be read: PermissionError" });
  const stats = await mod.fetchConsoleStats();
  assert.equal(stats.agents, null, "a failed read must not become 0");
  assert.equal(stats.agentsReason, "custom agent profiles could not be read: PermissionError");

  // The other five counters are SQL aggregates that cannot degrade silently, so
  // they stay plain numbers — and still resolve here.
  assert.equal(stats.runs, 12);
  assert.equal(stats.threads, 7);
  assert.equal(stats.tokens, 4242);
});

test("a measured zero is still zero: the two facts stay distinguishable", async () => {
  const mod = loadWorkspace(read("src/lib/workspace.ts"));
  globalThis.__statsHandler = () => statsPayload({ total_agents: 0, total_agents_reason: null });
  const stats = await mod.fetchConsoleStats();
  assert.equal(stats.agents, 0, "a real measurement of zero must survive");
  assert.equal(stats.agentsReason, null);
});

test("the client type admits the null the backend now sends", () => {
  const source = read("src/lib/workspace.ts");
  assert.match(source, /agents:\s*number\s*\|\s*null;/);
  assert.doesNotMatch(
    source,
    /agents:\s*count\(d,\s*\["agents",\s*"total_agents"\]\)\s*\?\?\s*0/,
    "the ?? 0 coercion is the bug: it turns an unreported count into a measured zero",
  );
});

test("both call sites render the absence instead of a number", () => {
  const vitals = read("src/components/WorkspaceVitals.tsx");
  const dashboard = read("src/components/sections/DashboardSection.tsx");

  for (const [name, source] of [
    ["WorkspaceVitals", vitals],
    ["DashboardSection", dashboard],
  ]) {
    assert.ok(
      /agents\s*===\s*null/.test(source),
      `${name} must branch on the null, not format a number unconditionally`,
    );
    // The formatter may only see a value that was already proven non-null.
    // `s.agents === null ? "—" : compactNumber(s.agents)` qualifies; a bare
    // `compactNumber(s.agents)` with no guard is the defect.
    const guarded = /agents\s*===\s*null[\s\S]{0,200}?compactNumber\(\s*s\.agents\s*\)/;
    const unguarded = /compactNumber\(\s*s\.agents\s*\)/;
    if (unguarded.test(source)) {
      assert.ok(
        guarded.test(source),
        `${name} formats s.agents without first proving it is not null`,
      );
    }
  }

  // The tooltip's promise is now the behaviour rather than an aspiration.
  assert.match(vitals, /Custom agent profiles not reported/);
  assert.match(vitals, /not a measured zero|failure to read it renders as "not reported"/);
  assert.match(dashboard, /the Gateway did not report a count/);
});

// ---------------------------------------------------------------------------
// Negative control: the pre-fix client, read straight from the revision that
// last contained it. A control that passes against the fixed code proves
// nothing, so this asserts the OLD code returns 0.
// ---------------------------------------------------------------------------
test("NEGATIVE CONTROL  the pre-fix client turned total_agents: null into 0", async () => {
  const mod = loadWorkspace(preFix("src/lib/workspace.ts"));
  globalThis.__statsHandler = () => statsPayload({ total_agents: null, total_agents_reason: "boom" });
  const stats = await mod.fetchConsoleStats();
  assert.equal(
    stats.agents,
    0,
    "the pre-fix snapshot coerced the absent count to 0 — if this ever fails, that revision's source changed and the pinned SHA must be re-derived from git",
  );
});