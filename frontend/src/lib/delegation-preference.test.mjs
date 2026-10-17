import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./delegation-preference.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const pref = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(js)}`);

/** A Storage stand-in that records writes and can be made to throw. */
function storage(initial = {}, { throwOn = null } = {}) {
  const map = new Map(Object.entries(initial));
  return {
    map,
    getItem: (k) => {
      if (throwOn === "get") throw new Error("storage disabled");
      return map.has(k) ? map.get(k) : null;
    },
    setItem: (k, v) => {
      if (throwOn === "set") throw new Error("quota exceeded");
      map.set(k, v);
    },
    removeItem: (k) => {
      if (throwOn === "remove") throw new Error("storage disabled");
      map.delete(k);
    },
  };
}

// --- shape validation -------------------------------------------------------

test("off and auto are always structurally valid", () => {
  assert.equal(pref.isDelegationModeShape("off"), true);
  assert.equal(pref.isDelegationModeShape("auto"), true);
});

test("a catalog-prefixed mode needs a name after the prefix", () => {
  assert.equal(pref.isDelegationModeShape("type:deep-security"), true);
  assert.equal(pref.isDelegationModeShape("category:research"), true);
  // A bare prefix is a broken row, not a mode.
  assert.equal(pref.isDelegationModeShape("type:"), false);
  assert.equal(pref.isDelegationModeShape("category:"), false);
});

test("nonsense is not a mode", () => {
  for (const bad of [null, undefined, 42, "", "type", "category", "engine:arena", "TYPE:deep-security", {}]) {
    assert.equal(pref.isDelegationModeShape(bad), false, `${JSON.stringify(bad)} must not be a mode`);
  }
});

// --- reconciliation against the live catalog --------------------------------

test("a stored type survives when the catalog still carries it", () => {
  const r = pref.reconcileDelegationMode("type:deep-security", ["off", "auto", "type:deep-security"]);
  assert.deepEqual(r, { mode: "type:deep-security", restored: true });
});

test("a stored type is DISCARDED when the catalog dropped it", () => {
  // The whole reason this module is not a bare localStorage read: a renamed or
  // removed custom agent would otherwise leave the selector on a mode that
  // resolves to nothing.
  const r = pref.reconcileDelegationMode("type:removed-agent", ["off", "auto", "type:deep-security"]);
  assert.deepEqual(r, { mode: "off", restored: false });
});

test("a stored category is discarded when the catalog dropped it", () => {
  const r = pref.reconcileDelegationMode("category:deep-research", ["off", "auto", "category:research"]);
  assert.equal(r.mode, "off");
  assert.equal(r.restored, false);
});

test("an unloaded catalog cannot verify, so the preference is kept", () => {
  // Load-ordering trap: treating "not loaded yet" as "nothing is valid" would
  // make the restored setting depend on whether the catalog read won the race.
  const r = pref.reconcileDelegationMode("type:deep-security", []);
  assert.deepEqual(r, { mode: "type:deep-security", restored: true });
});

test("auto is restored only when the catalog actually renders it", () => {
  assert.equal(pref.reconcileDelegationMode("auto", ["off", "auto"]).mode, "auto");
  // A catalog with neither is a degraded read; do not sit on a dead value.
  assert.deepEqual(pref.reconcileDelegationMode("auto", ["type:bash"]), { mode: "off", restored: false });
});

test("an unknown value falls back to the default and reports not restored", () => {
  assert.deepEqual(pref.reconcileDelegationMode("telepathy", ["off", "auto"]), { mode: "off", restored: false });
  assert.deepEqual(pref.reconcileDelegationMode(undefined, ["off"]), { mode: "off", restored: false });
});

// --- read / write -----------------------------------------------------------

test("a round trip preserves the chosen mode", () => {
  const s = storage();
  assert.equal(pref.storeDelegationMode(s, "type:deep-security"), true);
  assert.equal(pref.readStoredDelegationMode(s), "type:deep-security");
});

test("choosing the default clears the key rather than storing it", () => {
  // Keeps "the operator has an opinion" distinguishable from "nothing chosen".
  const s = storage();
  pref.storeDelegationMode(s, "auto");
  assert.equal(s.map.has(pref.DELEGATION_MODE_STORAGE_KEY), true);
  pref.storeDelegationMode(s, "off");
  assert.equal(s.map.has(pref.DELEGATION_MODE_STORAGE_KEY), false);
  assert.equal(pref.readStoredDelegationMode(s), null);
});

test("an invalid mode is never written", () => {
  const s = storage();
  assert.equal(pref.storeDelegationMode(s, "engine:arena"), false);
  assert.equal(s.map.size, 0);
});

// --- storage that throws ----------------------------------------------------

test("a storage that throws on read degrades to no preference", () => {
  assert.equal(pref.readStoredDelegationMode(storage({}, { throwOn: "get" })), null);
});

test("a storage that throws on write reports false instead of raising", () => {
  // Quota and private-mode failures must not take the composer down mid-send.
  assert.equal(pref.storeDelegationMode(storage({}, { throwOn: "set" }), "auto"), false);
  assert.equal(pref.storeDelegationMode(storage({}, { throwOn: "remove" }), "off"), false);
});

test("an absent storage is handled without touching window", () => {
  // Guards the server-render path: this module must not require a browser.
  assert.equal(pref.readStoredDelegationMode(null), null);
  assert.equal(pref.storeDelegationMode(undefined, "auto"), false);
});

// --- the wiring claims ------------------------------------------------------

test("ChatView seeds the mode from the shared default, not a duplicated literal", () => {
  const chatView = readFileSync(new URL("../components/ChatView.tsx", import.meta.url), "utf8");
  assert.equal(/useState\("off"\)/.test(chatView), false, "delegationMode must not hardcode the default");
  assert.match(chatView, /useState\(DEFAULT_DELEGATION_MODE\)/);
  assert.match(chatView, /from "@\/lib\/delegation-preference"/);
});

test("Composer reads, validates and writes the mode", () => {
  // Composer owns this because it is the only holder of the *catalog*, and a
  // stored mode is only restorable if the catalog still offers it.
  const composer = readFileSync(new URL("../components/Composer.tsx", import.meta.url), "utf8");
  assert.match(composer, /readStoredDelegationMode\(/);
  assert.match(composer, /reconcileDelegationMode\(/);
  assert.match(composer, /storeDelegationMode\(/);
});

test("the write is gated on the restore having run", () => {
  // Without this gate the write effect fires on first paint and stores the
  // default "off" over the stored value before the restore reads it - the
  // preference would be destroyed by the act of restoring it.
  const composer = readFileSync(new URL("../components/Composer.tsx", import.meta.url), "utf8");
  assert.match(composer, /delegationModeReady/);
  const writes = composer.match(/if \(!delegationModeReady\.current\) return;/g) ?? [];
  assert.ok(writes.length >= 2, `both the validate and write effects must be gated, found ${writes.length}`);
});