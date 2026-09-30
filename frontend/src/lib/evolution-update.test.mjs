import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./evolution.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

/** Mirrors evolution.test.mjs: transpile the client and stub the transport. */
function loadEvolution(overrides = {}) {
  const calls = [];
  const exports = {};
  const http = {
    async get(url) {
      calls.push({ url, method: "GET" });
      if (overrides.get) return overrides.get(url, calls.length);
      return {};
    },
    async send(url, method, body) {
      calls.push({ url, method, body });
      if (overrides.send) return overrides.send(url, method, body, calls.length);
      return {};
    },
    asList(body, keys) {
      if (Array.isArray(body)) return body;
      for (const key of keys) if (Array.isArray(body?.[key])) return body[key];
      return [];
    },
    pick(body, keys, fallback) {
      for (const key of keys) if (body?.[key] !== undefined && body?.[key] !== null) return body[key];
      return fallback;
    },
  };
  new Function("exports", "require", compiled)(exports, (dependency) => {
    assert.equal(dependency, "./http");
    return http;
  });
  return { evo: exports, calls };
}

const FULL = {
  state: "UPDATE_AVAILABLE",
  checkedAt: "2026-09-27T00:00:00+00:00",
  installedVersion: "3.0.0",
  latestTag: "v3.1.0",
  error: null,
  availableVersion: "3.1.0",
  canApply: true,
  canSelfUpdate: true,
  deploymentMode: "source",
  reason: null,
  skippedVersions: [],
  mutationStarted: false,
  stateCorrupt: false,
};

// ------------------------------------------------------------------ routing

test("the update control talks to the real guarded update routes", async () => {
  const { evo, calls } = loadEvolution({ get: async () => FULL, send: async () => ({ ok: true }) });

  await evo.getEvolutionUpdateState();
  await evo.checkForEvolutionUpdate();
  await evo.requestEvolutionUpdate(true);
  await evo.skipEvolutionUpdate("3.1.0");
  await evo.recoverEvolutionUpdate();

  // The client hands `http` a base-relative path; `GATEWAY_BASE` prefixes /api.
  assert.deepEqual(calls.map(({ url, method }) => [url, method]), [
    ["/evolution/update-state", "GET"],
    ["/evolution/update-check", "POST"],
    ["/evolution/update-apply", "POST"],
    ["/evolution/update-skip", "POST"],
    ["/evolution/update-recover", "POST"],
  ]);
  // The client may never name a URL/ref; the server verifies its own candidate.
  assert.deepEqual(calls[2].body, { force: true });
  assert.deepEqual(calls[3].body, { version: "3.1.0" });
});

test("a blank skip is refused locally instead of being sent as an empty version", async () => {
  const { evo, calls } = loadEvolution({ send: async () => ({ ok: true }) });
  await assert.rejects(evo.skipEvolutionUpdate("   "), /no version was supplied/i);
  assert.equal(calls.length, 0);
});

// ------------------------------------------------------------------ gating

test("canApply is only true when the engine says so, never inferred from the state", async () => {
  const { evo } = loadEvolution();
  // The state says an update exists, but the engine withheld permission.
  assert.equal(evo.canApplyUpdate({ ...FULL, canApply: false }), false);
  // Permission without the state is still not an applicable update.
  assert.equal(evo.canApplyUpdate({ ...FULL, state: "UP_TO_DATE" }), false);
  assert.equal(evo.canApplyUpdate({ ...FULL, state: "CHECKING" }), false);
  // A missing/absent payload is a deny, never a maybe.
  assert.equal(evo.canApplyUpdate(null), false);
  assert.equal(evo.canApplyUpdate(undefined), false);
  // Only the full agreement permits it.
  assert.equal(evo.canApplyUpdate({ ...FULL }), true);
});

test("a truthy-but-not-true canApply from the server is still a deny", async () => {
  const { evo } = loadEvolution({ get: async () => ({ ...FULL, canApply: "true" }) });
  const state = await evo.getEvolutionUpdateState();
  assert.equal(state.canApply, false);
  assert.equal(evo.canApplyUpdate(state), false);
});

test("the server's reason survives the client unmangled", async () => {
  const { evo } = loadEvolution({
    get: async () => ({ ...FULL, canApply: false, reason: "worktree has uncommitted changes" }),
  });
  const state = await evo.getEvolutionUpdateState();
  assert.equal(state.reason, "worktree has uncommitted changes");
  assert.equal(state.canApply, false);
});

test("a check failure keeps the real error and does not claim an update", async () => {
  const { evo } = loadEvolution({
    get: async () => ({ state: "CHECK_FAILED", installedVersion: "3.0.0", error: "GitHub API rate limit exceeded" }),
  });
  const state = await evo.getEvolutionUpdateState();
  assert.equal(state.state, "CHECK_FAILED");
  assert.equal(state.error, "GitHub API rate limit exceeded");
  assert.equal(state.latestTag, null);
  assert.equal(state.canApply, false);
});

test("a corrupt persisted state is disclosed instead of being read as up to date", async () => {
  const { evo } = loadEvolution({ get: async () => ({ stateCorrupt: true, canApply: true }) });
  const state = await evo.getEvolutionUpdateState();
  assert.equal(state.stateCorrupt, true);
  // A corrupt file cannot grant permission, whatever else it claims.
  assert.equal(evo.canApplyUpdate(state), false);
});

test("skipped versions are mapped to a list, absent becomes empty", async () => {
  const withSkips = loadEvolution({ get: async () => ({ ...FULL, skippedVersions: ["3.0.5", "3.0.6"] }) });
  assert.deepEqual((await withSkips.evo.getEvolutionUpdateState()).skippedVersions, ["3.0.5", "3.0.6"]);
  const without = loadEvolution({ get: async () => FULL });
  assert.deepEqual((await without.evo.getEvolutionUpdateState()).skippedVersions, []);
});

test("a recovery-required state keeps its backup ref so the operator can see it", async () => {
  const { evo } = loadEvolution({
    get: async () => ({
      state: "RECOVERY_REQUIRED",
      installedVersion: "3.0.0",
      mutationStarted: true,
      backupRef: "refs/alpha-backup/abc123",
      transactionId: "tx-9",
      canApply: false,
      reason: "an update staged but never verified",
    }),
  });
  const state = await evo.getEvolutionUpdateState();
  assert.equal(state.state, "RECOVERY_REQUIRED");
  assert.equal(state.mutationStarted, true);
  assert.equal(state.backupRef, "refs/alpha-backup/abc123");
  assert.equal(evo.canApplyUpdate(state), false);
});

test("a failed update call rejects with the server's reason rather than resolving empty", async () => {
  const { evo } = loadEvolution({
    send: async () => {
      throw new Error("A real interactive administrator session is required for source update operations.");
    },
  });
  await assert.rejects(evo.requestEvolutionUpdate(true), /administrator session is required/);
});

// ------------------------------------------------------------- component pins

test("the update control is mounted on the main screen, not only in settings", () => {
  const view = readFileSync(new URL("../components/ChatView.tsx", import.meta.url), "utf8");
  // `WorkspaceTopBar` is the one header every view shares, so mounting the
  // control there puts it in front of the user on the chat screen too. It used
  // to live only in the per-section header, which `view === "chat"` never
  // renders — the strongest screen of all was the one without it.
  assert.match(view, /<WorkspaceTopBar\b/);
  assert.doesNotMatch(
    view,
    /<UpdateControl \/>/,
    "the control must not be mounted a second time beside WorkspaceVitals",
  );
  const topBar = readFileSync(new URL("../components/chat-shell/WorkspaceTopBar.tsx", import.meta.url), "utf8");
  assert.match(topBar, /import \{ UpdateControl \} from "@\/components\/UpdateControl"/);
  assert.match(topBar, /<UpdateControl \/>/);
});

test("the control reads persisted state on mount and only calls GitHub on a click", () => {
  const control = readFileSync(new URL("../components/UpdateControl.tsx", import.meta.url), "utf8");
  // Mount reads the persisted route only; the network check is the click handler.
  const mount = control.slice(0, control.indexOf("const check = async"));
  assert.match(mount, /getEvolutionUpdateState\(\)/);
  assert.doesNotMatch(mount, /checkForEvolutionUpdate\(\)/);
  assert.match(control, /onClick=\{\(\) => \(open \? setOpen\(false\) : void check\(\)\)\}/);
});

test("the control never claims an update from a version comparison of its own", () => {
  const control = readFileSync(new URL("../components/UpdateControl.tsx", import.meta.url), "utf8");
  // The only source of "an update exists" is the server's own state string.
  assert.match(control, /current === "UPDATE_AVAILABLE"/);
  assert.doesNotMatch(control, /localeCompare\(/);
  assert.doesNotMatch(control, /semver/i);
  // Apply is gated on the engine, and the refusal is shown.
  assert.match(control, /canApplyUpdate\(state\)/);
  assert.match(control, /disabled=\{busy \|\| !applicable\}/);
  assert.match(control, /state\?\.reason/);
});
