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
