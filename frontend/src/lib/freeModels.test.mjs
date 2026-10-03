// freeModels.test.mjs — eligibility honesty: server data only, never inferred
// from health, unknown surfaced distinctly (unknown ≠ eligible).
// Pure Node test (node --test src/lib/freeModels.test.mjs): transpiles
// freeModels.ts and rewrites its api-client import to a data: URL stub so no
// network/browser runs — same pattern as voice.test.mjs.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const apiClientStub = `
let nextPayload = {};
let nextOk = true;
let nextStatus = 200;
export function setCatalog(payload, response) {
  nextPayload = payload;
  nextOk = response ? response.ok !== false : true;
  nextStatus = response && typeof response.status === "number" ? response.status : 200;
}
export async function apiFetch() {
  const payload = nextPayload;
  const ok = nextOk;
  const status = nextStatus;
  return { ok, status, json: async () => payload };
}
`;
const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;
const stubUrl = toDataUrl(apiClientStub);

const source = readFileSync(new URL("./freeModels.ts", import.meta.url), "utf8");
let code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
code = code.replace(/from\s+"\.\/api-client"/, `from "${stubUrl}"`);

const { fetchFreeCatalog } = await import(toDataUrl(code));
const { setCatalog } = await import(stubUrl);

test("absent eligible is never inferred from health — unknown providers are not eligible", async () => {
  setCatalog({
    providers: [
      { name: "healthy-unknown", healthy: null },
      { name: "proven-healthy", healthy: true },
      { name: "proven-unhealthy", healthy: false },
    ],
  });
  const { providers } = await fetchFreeCatalog();
  assert.deepEqual(
    providers.map((p) => ({ name: p.name, eligible: p.eligible, eligibilityKnown: p.eligibilityKnown })),
    [
      { name: "healthy-unknown", eligible: false, eligibilityKnown: false },
      { name: "proven-healthy", eligible: false, eligibilityKnown: false },
      { name: "proven-unhealthy", eligible: false, eligibilityKnown: false },
    ],
  );
});

test("derives eligibility from the catalog's eligible_candidates (server disclosure)", async () => {
  setCatalog({
    providers: [
      { name: "a", healthy: null },
      { name: "b", healthy: true },
      { name: "c", healthy: false },
      { name: "ab", healthy: null },
    ],
    eligible_candidates: ["a:model-x", "ab:model-y"],
  });
  const { providers } = await fetchFreeCatalog();
  assert.deepEqual(
    providers.map((p) => ({ name: p.name, eligible: p.eligible, eligibilityKnown: p.eligibilityKnown })),
    [
      { name: "a", eligible: true, eligibilityKnown: true },
      { name: "b", eligible: false, eligibilityKnown: true },
      { name: "c", eligible: false, eligibilityKnown: true },
      // prefix collision: "a" must not match candidate "ab:model-y".
      { name: "ab", eligible: true, eligibilityKnown: true },
    ],
  );
});

test("an explicit per-provider eligible flag is respected over candidates", async () => {
  setCatalog({
    providers: [
      { name: "a", healthy: true, eligible: false },
      { name: "b", healthy: false, eligible: true },
    ],
    eligible_candidates: ["a:model-x"],
  });
  const { providers } = await fetchFreeCatalog();
  assert.deepEqual(
    providers.map((p) => ({ name: p.name, eligible: p.eligible, eligibilityKnown: p.eligibilityKnown })),
    [
      { name: "a", eligible: false, eligibilityKnown: true },
      { name: "b", eligible: true, eligibilityKnown: true },
    ],
  );
});

test("an invalid eligible value (non-boolean) is treated as absent", async () => {
  setCatalog({ providers: [{ name: "a", healthy: null, eligible: "yes" }] });
  const { providers } = await fetchFreeCatalog();
  assert.equal(providers[0].eligible, false);
  assert.equal(providers[0].eligibilityKnown, false);
});

test("an empty eligible_candidates list is known-zero, not unknown", async () => {
  setCatalog({ providers: [{ name: "a", healthy: null }], eligible_candidates: [] });
  const { providers } = await fetchFreeCatalog();
  assert.equal(providers[0].eligible, false);
  assert.equal(providers[0].eligibilityKnown, true);
});

test("non-array provider payloads map to an empty list (no fabricated providers)", async () => {
  setCatalog({ providers: "not-an-array" });
  const { providers } = await fetchFreeCatalog();
  assert.deepEqual(providers, []);
});

test("a failed catalog request rejects instead of returning an empty healthy view", async () => {
  setCatalog({}, { ok: false, status: 503 });
  await assert.rejects(() => fetchFreeCatalog(), /HTTP 503/);
});

/* ══ Per-provider model disclosure (the dropdown's list) ═══════════════════ */

/**
 * The real `catalog_dict()` shape from
 * `alpha/models/free_router/catalog.py`: `models` is a list of descriptor
 * objects each carrying an `id`, `model_count` is the provider's true total,
 * and `models_truncated` flags the server's 25-per-provider bound.
 */
const REAL_SHAPE = {
  providers: [
    {
      name: "openrouter",
      healthy: true,
      discovery_ok: true,
      models: [{ id: "a/free-one" }, { id: "b/free-two" }],
      model_count: 2,
      models_truncated: false,
      latency_ms: 812.5,
      consecutive_failures: 0,
      source_labels: ["catalog", "discovery"],
    },
    {
      name: "siliconflow",
      healthy: false,
      last_error: "chat call failed: 502",
      models: Array.from({ length: 25 }, (_, i) => ({ id: `sf/model-${i}` })),
      model_count: 61,
      models_truncated: true,
      latency_ms: null,
      consecutive_failures: 4,
      cooldown_until: 1780000000,
    },
  ],
  eligible_candidates: ["openrouter:a/free-one"],
  refreshed_at: "2026-10-03T09:00:00+00:00",
  selection_method: "provider order ranked by tri-state health",
  disclaimer: "Reachability on anonymous gateways is never guaranteed.",
};

test("model IDs are read from the server's descriptor objects", async () => {
  setCatalog(REAL_SHAPE);
  const { providers } = await fetchFreeCatalog();
  assert.deepEqual(providers[0].modelIds, ["a/free-one", "b/free-two"]);
  assert.equal(providers[0].modelCount, 2);
  assert.equal(providers[0].modelsTruncated, false);
  assert.deepEqual(providers[0].sourceLabels, ["catalog", "discovery"]);
});

test("a truncated provider keeps the server's true total, not the length of the cut list", async () => {
  // The list the server sent has 25 entries and `model_count` is 61. Reading the
  // count off the array would make a bounded prefix look like a whole catalog.
  setCatalog(REAL_SHAPE);
  const { providers } = await fetchFreeCatalog();
  assert.equal(providers[1].modelIds.length, 25);
  assert.equal(providers[1].modelCount, 61);
  assert.equal(providers[1].modelsTruncated, true);
});

test("an unmeasured latency stays null and is never coerced to zero", async () => {
  // `null` means nothing answered; `0` is the fastest possible round-trip and
  // would read as an instant gateway.
  setCatalog(REAL_SHAPE);
  const { providers } = await fetchFreeCatalog();
  assert.equal(providers[1].latencyMs, null);
  assert.equal(providers[0].latencyMs, 812.5);
});

test("an epoch cooldown_until is disclosed as an ISO instant, absent stays null", async () => {
  setCatalog(REAL_SHAPE);
  const { providers } = await fetchFreeCatalog();
  assert.equal(providers[1].cooldownUntil, new Date(1780000000 * 1000).toISOString());
  assert.equal(providers[0].cooldownUntil, null);
});

test("a malformed models entry is skipped, never rendered as [object Object]", async () => {
  setCatalog({
    providers: [
      { name: "a", healthy: null, models: [{ id: "good" }, null, 42, {}, { id: "" }] },
    ],
  });
  const { providers } = await fetchFreeCatalog();
  assert.deepEqual(providers[0].modelIds, ["good"]);
  // The server sent no total, so this is unreported rather than "1 model".
  assert.equal(providers[0].modelCount, null);
  assert.equal(providers[0].modelsTruncated, false);
});

test("a provider with no models array reports none, not an empty success", async () => {
  setCatalog({ providers: [{ name: "a", healthy: null }] });
  const { providers } = await fetchFreeCatalog();
  assert.deepEqual(providers[0].modelIds, []);
  assert.equal(providers[0].modelCount, null);
});

test("refreshed_at is read, so the header can show when the catalog was last seen", async () => {
  // The endpoint publishes `refreshed_at`; `updated_at` is only the cache-file
  // key. Reading the latter alone is why the header never showed a time.
  setCatalog(REAL_SHAPE);
  const view = await fetchFreeCatalog();
  assert.equal(view.refreshedAt, "2026-10-03T09:00:00+00:00");
  assert.equal(view.updatedAt, "2026-10-03T09:00:00+00:00");
  assert.equal(view.selectionMethod, "provider order ranked by tri-state health");
  assert.equal(view.disclaimer, "Reachability on anonymous gateways is never guaranteed.");
});

test("an explicit updated_at still wins, and an absent one is null rather than a fabricated time", async () => {
  setCatalog({ providers: [], updated_at: "2026-01-01T00:00:00+00:00" });
  const first = await fetchFreeCatalog();
  assert.equal(first.updatedAt, "2026-01-01T00:00:00+00:00");
  setCatalog({ providers: [] });
  const second = await fetchFreeCatalog();
  assert.equal(second.updatedAt, null);
  assert.equal(second.refreshedAt, null);
});

test("health is per provider and never copied onto the model IDs", async () => {
  // A model listed under a healthy gateway has not itself been probed. If this
  // ever becomes true, the dropdown's per-model rows would be claiming a
  // measurement that only ever happened at the provider level.
  setCatalog(REAL_SHAPE);
  const { providers } = await fetchFreeCatalog();
  for (const id of providers[0].modelIds) {
    assert.equal(Object.hasOwn(id, "healthy"), false);
  }
  assert.equal(Object.hasOwn(providers[0], "healthy"), true);
  assert.equal(providers[0].healthy, true);
  assert.equal(providers[1].healthy, false);
});
