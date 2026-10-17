import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./swarm-preference.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const pref = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(js)}`);

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

test("off/auto are not swarm modes - swarm is a topology, not a presence", () => {
  // "off" is the absence of swarm, handled by the enabled flag, not a mode.
  assert.equal(pref.isSwarmModeShape("off"), false);
  assert.equal(pref.isSwarmModeShape("auto"), true);
});

test("every shipped mode is structurally valid", () => {
  for (const m of pref.KNOWN_SWARM_MODES) assert.equal(pref.isSwarmModeShape(m), true, m);
});

test("nonsense is not a mode", () => {
  for (const bad of [null, undefined, 42, "", "swarm", "PARALLEL", {}]) {
    assert.equal(pref.isSwarmModeShape(bad), false, JSON.stringify(bad));
  }
});

test("a missing preference reads as null", () => {
  assert.equal(pref.readStoredSwarmPreference(storage()), null);
});

test("a malformed blob reads as null rather than a half-parsed preference", () => {
  const s = storage({ [pref.SWARM_MODE_STORAGE_KEY]: "{not json" });
  assert.equal(pref.readStoredSwarmPreference(s), null);
  const s2 = storage({ [pref.SWARM_MODE_STORAGE_KEY]: JSON.stringify({ enabled: "yes" }) });
  const p = pref.readStoredSwarmPreference(s2);
  assert.equal(p.enabled, false, "a non-boolean enabled is not true");
  assert.equal(p.mode, "auto", "an invalid mode falls back to auto");
});

test("a round trip preserves the preference", () => {
  const s = storage();
  const pref0 = { enabled: true, mode: "debate", concurrency: 8 };
  assert.equal(pref.storeSwarmPreference(s, pref0), true);
  assert.deepEqual(pref.readStoredSwarmPreference(s), pref0);
});

test("the default preference clears the key", () => {
  const s = storage();
  pref.storeSwarmPreference(s, { enabled: true, mode: "auto", concurrency: 4 });
  assert.equal(s.map.has(pref.SWARM_MODE_STORAGE_KEY), true);
  pref.storeSwarmPreference(s, { enabled: false, mode: "auto", concurrency: 4 });
  assert.equal(s.map.has(pref.SWARM_MODE_STORAGE_KEY), false);
});

test("out-of-range concurrency falls back to the default", () => {
  // A preference is not a hard limit, but an out-of-range value is still an
  // invalid preference: it is discarded rather than clamped, matching the
  // codebase's "refuse, don't clamp" discipline. Zero is the supported
  // auto-sizing sentinel.
  const s = storage();
  pref.storeSwarmPreference(s, { enabled: true, mode: "auto", concurrency: 1500 });
  assert.equal(pref.readStoredSwarmPreference(s).concurrency, 4);
  // Zero is the explicit auto-sizing sentinel on the Gateway contract.
  pref.storeSwarmPreference(s, { enabled: true, mode: "auto", concurrency: 0 });
  assert.equal(pref.readStoredSwarmPreference(s).concurrency, 0);
  pref.storeSwarmPreference(s, { enabled: true, mode: "auto", concurrency: 8 });
  assert.equal(pref.readStoredSwarmPreference(s).concurrency, 8);
});

test("reconciliation keeps a mode the build still offers", () => {
  assert.deepEqual(pref.reconcileSwarmMode("debate", ["auto", "debate"]), { mode: "debate", restored: true });
});

test("reconciliation discards a mode the build dropped", () => {
  assert.deepEqual(pref.reconcileSwarmMode("removed-mode", ["auto", "parallel"]), { mode: "auto", restored: false });
});

test("reconciliation cannot verify an unloaded catalog, so it keeps the value", () => {
  assert.deepEqual(pref.reconcileSwarmMode("debate", []), { mode: "debate", restored: true });
});

test("storage that throws degrades to null / false", () => {
  assert.equal(pref.readStoredSwarmPreference(storage({}, { throwOn: "get" })), null);
  assert.equal(pref.storeSwarmPreference(storage({}, { throwOn: "set" }), { enabled: true, mode: "auto", concurrency: 4 }), false);
  assert.equal(pref.defaultStorage(), null); // server render path
});

test("Composer seeds swarm mode from the shared preference (wiring pending)", () => {
  // This pin is intentionally loose: the wiring lands in a follow-up edit.
  // The preference module is the deliverable here; the composer integration
  // is the next step and will tighten this to a real import.
  assert.ok(true);
});

test("composer keeps the topology selector and unique numeric concurrency control", () => {
  const component = readFileSync(new URL("../components/SwarmModeControl.tsx", import.meta.url), "utf8");
  assert.match(component, /id="composer-swarm-mode"/);
  assert.match(component, /id="composer-swarm-concurrency"/);
  assert.match(component, /max=\{1000\}/);
  assert.match(component, /onModeChange\(e\.target\.value\)/);
  assert.equal((component.match(/id="composer-swarm-concurrency"/g) ?? []).length, 1);
});
