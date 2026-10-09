// integration.test.mjs — `lib/integration.ts` against the real Gateway contract,
// plus the wiring-badge honesty inversion that used to paint an all-clear.
//
// THE DEFECT
// ----------
// `IntegrationSection` rendered `no unwired entries` in GREEN whenever
// `health.unwired.length === 0`, without ever consulting `manifest_found`.
// The Gateway reports `unwired: []` precisely when it could NOT read the
// manifest (backend/Dockerfile:80; backend/tests/
// test_feature_manifest_deployment.py:9 both pin `coverage: {}` and
// `unwired: []` for a deployment with no `contracts/`). So a deployment where
// nothing was ever diffed showed the same badge as one where everything was
// checked and found wired — a fabricated all-clear, which is the exact thing
// `frontend/AGENTS.md` non-negotiable #1 forbids.
//
// These tests are REAL, not structural:
//
//  * `UnwiredBadge` is server-rendered through `react-dom/server` and the
//    assertions run against emitted markup (visible wording + the actual
//    Tailwind class `Badge` maps each tone to) — not a source-code regex.
//  * The client assertions load the real `integration.ts` behind a `./http`
//    shim, so they pin the route, the verb, and the envelope normalisation.
//
// Node test (node --test src/lib/*.test.mjs): transpiles the TS/TSX with the
// local `typescript` and rewires specifiers to data:/file: URLs. The render
// graph is five modules with `api-client.ts` as the leaf, so every dependency
// is enumerated explicitly rather than resolved lazily.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const here = (relative) => fileURLToPath(new URL(relative, import.meta.url));
const resolveUrl = (specifier) => pathToFileURL(require.resolve(specifier)).href;
const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const toDataUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
const transpile = (source, extra = {}) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, ...extra },
  }).outputText;
/** Import a module that has ALREADY been wrapped in a data: URL by the caller. */
const loadUrl = (url) => import(url);

/**
 * Point `react` / `react/jsx-runtime` at the real packages on disk, and the
 * `@/lib/a11y` alias at no-op hooks. Server-rendered section tests do not
 * exercise focus/scroll effects, and Node cannot resolve a path alias from
 * inside a `data:` specifier — without this the whole file dies with
 * ERR_UNSUPPORTED_RESOLVE_REQUEST before a single assertion runs.
 */
const A11Y_URL = toDataUrl(
  "export function useFocusTrap() {} export function useScrollLock() {}",
);
const reactRewrites = (code) =>
  code
    .replace(/from "react\/jsx-runtime"/g, `from "${resolveUrl("react/jsx-runtime")}"`)
    .replace(/from "react"/g, `from "${resolveUrl("react")}"`)
    .replace(/from "@\/lib\/a11y"/g, `from "${A11Y_URL}"`);

/* ── The render graph, leaf first ─────────────────────────────────────────── */

const apiUrl = toDataUrl(transpile(read("./api-client.ts")));
const httpUrl = toDataUrl(transpile(read("./http.ts")).replace(/from "\.\/api-client"/g, `from "${apiUrl}"`));
const integrationUrl = toDataUrl(
  transpile(read("./integration.ts")).replace(/from "\.\/http"/g, `from "${httpUrl}"`),
);
const uiUrl = toDataUrl(reactRewrites(transpile(read("../components/ui.tsx"), { jsx: ts.JsxEmit.ReactJSX })));
const sectionUrl = toDataUrl(
  reactRewrites(transpile(read("../components/sections/IntegrationSection.tsx"), { jsx: ts.JsxEmit.ReactJSX }))
    .replace(/from "@\/components\/ui"/g, `from "${uiUrl}"`)
    .replace(/from "@\/lib\/integration"/g, `from "${integrationUrl}"`)
    .replace(/from "@\/lib\/http"/g, `from "${httpUrl}"`)
    .replace(/from "lucide-react"/g,
      `from "${pathToFileURL(here("../../node_modules/lucide-react/dist/esm/lucide-react.js")).href}"`),
);

const { UnwiredBadge } = await loadUrl(sectionUrl);
const { createElement } = await import(resolveUrl("react"));
const { renderToStaticMarkup } = await import(resolveUrl("react-dom/server"));

const render = (props) => renderToStaticMarkup(createElement(UnwiredBadge, props));

// `assert.match`/`doesNotMatch` require a RegExp, so these are compiled rather
// than passed as substrings. Each pattern is the exact literal class fragment
// `Badge` emits for its tone, so the matching meaning is unchanged.
/** The class `Badge` maps tone="green" to (ui.tsx:123). */
const GREEN = /text-emerald-600/;
/** tone="amber" (ui.tsx:125). */
const AMBER = /text-amber-600/;
/** tone falls through to the neutral default (ui.tsx:136). */
const NEUTRAL = /bg-muted text-muted-foreground/;

/* ══ 1. A missing manifest must never render an all-clear ═══════════════════ */

test("an unreadable manifest does not claim there are no unwired entries", () => {
  const markup = render({ manifestFound: false, count: 0 });
  // The exact regression: green "no unwired entries" from `unwired: []`.
  assert.doesNotMatch(markup, />no unwired entries</, "fabricated an all-clear from a manifest that was never read");
  assert.doesNotMatch(markup, /no unwired entries/, "the claim is present at all when nothing was diffed");
  assert.doesNotMatch(markup, GREEN, "an unmeasured state is painted as success");
  assert.match(markup, />unwired unknown</, "expected the honest unknown label");
  assert.match(markup, NEUTRAL, "unknown should read as neutral, not as success");
  assert.doesNotMatch(markup, AMBER, "unknown is not a warning either — nothing went wrong, it was never measured");
});

test("an unreadable manifest never reports a count it does not have", () => {
  const markup = render({ manifestFound: false, count: 0 });
  assert.doesNotMatch(markup, /\b0 unwired\b/, "a count of zero is a measurement; none was taken");
  assert.doesNotMatch(markup, />0</, "no numeric reading should appear when the manifest is absent");
});

/* ══ 2. The green badge still means what it says when it IS measured ════════ */

test("a read manifest that is clean still earns the green badge", () => {
  const markup = render({ manifestFound: true, count: 0 });
  assert.match(markup, />no unwired entries</);
  assert.match(markup, GREEN);
  assert.doesNotMatch(markup, NEUTRAL);
});

test("a read manifest with unwired entries reports the real count in amber", () => {
  const markup = render({ manifestFound: true, count: 7 });
  assert.match(markup, />7 unwired</);
  assert.match(markup, AMBER);
  assert.doesNotMatch(markup, GREEN, "a non-zero unwired count must not look like success");
  assert.doesNotMatch(markup, />unwired unknown</, "a measured count must not be downgraded to unknown");
});

/* ══ 3. The client contract: route, verb, envelope ══════════════════════════ */

/** Transpile the real client to CJS and hand it a recording `./http`. */
function loadIntegration(getImpl) {
  const calls = [];
  const exports = {};
  const compiled = ts.transpileModule(read("./integration.ts"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  new Function("exports", "require", compiled)(exports, (dependency) => {
    assert.equal(dependency, "./http", `integration.ts pulled an unexpected dependency: ${dependency}`);
    return {
      get: (path) => {
        calls.push({ path, method: "GET" });
        return getImpl(path);
      },
    };
  });
  return { integration: exports, calls };
}

test("fetchIntegrationHealth reads GET /ops/integration-health", async () => {
  const { integration, calls } = loadIntegration(async () => ({ manifest_found: true }));
  await integration.fetchIntegrationHealth();
  assert.deepEqual(calls, [{ path: "/ops/integration-health", method: "GET" }]);
});

test("the envelope is normalised, not passed through: capabilities sort and unwired survives", async () => {
  const body = {
    manifest_found: true,
    manifest_version: "7",
    coverage: { tools: { total: 100, wired: 90, intentionally_unwired: 8, excluded: 2 } },
    capabilities: {
      zulu: { enabled: true, kind: "engine", module: "m.z", target: "t", description: "d" },
      alpha: { enabled: false, kind: "engine", module: "m.a", target: "t", description: "d" },
    },
    unwired: ["war_room_tool"],
    generated_at: "2026-09-27T00:00:00Z",
  };
  const { integration } = loadIntegration(async () => body);
  const health = await integration.fetchIntegrationHealth();

  assert.equal(health.manifest_found, true);
  assert.deepEqual(health.coverage.tools, {
    total: 100, wired: 90, intentionally_unwired: 8, excluded: 2,
  });
  assert.deepEqual(health.unwired, ["war_room_tool"], "the unwired list must reach the UI verbatim");
  assert.deepEqual(health.capabilities.map((c) => c.id), ["alpha", "zulu"], "capabilities are sorted by id");
  // absent optional `error` stays absent, never coerced to a string
  assert.equal(health.capabilities[0].error, undefined);
});

test("degraded reads keep manifest_found=false rather than defaulting to a clean state", async () => {
  // No manifest at all: the whole payload is absent/false. The mapping must
  // preserve that so the badge layer can tell the difference.
  const { integration } = loadIntegration(async () => ({}));
  const health = await integration.fetchIntegrationHealth();
  assert.equal(health.manifest_found, false, "a missing manifest_found must not default to true");
  assert.deepEqual(health.unwired, [], "an absent unwired list is [] — it is the badge layer's job to refuse the green");
  assert.deepEqual(health.coverage, {}, "coverage stays empty; it must not be invented");
});
