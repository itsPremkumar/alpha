import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

/**
 * The capability scoping client: `GET`/`PUT /api/bots/{name}/capabilities`.
 *
 * The load-bearing pins here are the refusal path — a structured 422 must
 * arrive as `CapabilityRefusalError` with every violation, because the shared
 * client keeps only string details and the panel exists to show the reasons —
 * and the honesty contract: absent fields map to `null`, never fabricated
 * `[]`, `0` or `false`.
 */

const compile = (name) =>
  ts.transpileModule(readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;

function instantiate(source, { exports = {}, requireMap = {}, processEnv = {}, consoleSink = [], fetchImpl } = {}) {
  new Function("exports", "require", "process", "console", "fetch", source)(
    exports,
    (dependency) => {
      assert.ok(Object.hasOwn(requireMap, dependency), `Unexpected dependency: ${dependency}`);
      return requireMap[dependency];
    },
    { env: processEnv },
    { error: (...args) => consoleSink.push(args) },
    fetchImpl ||
      (() => {
        throw new Error("Raw fetch must not be used");
      }),
  );
  return exports;
}

const apiClient = instantiate(compile("api-client"));

/** Pure helpers and the class identity for this suite. */
const module = instantiate(compile("bot-capabilities"), {
  requireMap: { "./api-client": apiClient },
});

function fixture(respond, { baseUrl = "/api", cookie = "other=value; csrf_token=test_token-123" } = {}) {
  const calls = [];
  // One client only: the module builds the real `createApiClient` around this
  // stub, so CSRF/credentials come from production code and the error-body
  // capture sits where it does at runtime.
  const fetchImpl = async (url, init) => {
    calls.push({ url, ...init });
    return respond(url, init);
  };
  const instance = instantiate(compile("bot-capabilities"), {
    requireMap: { "./api-client": { ...apiClient, GATEWAY_BASE: baseUrl } },
  });
  return {
    calls,
    client: instance.createCapabilityClient({ baseUrl, getCookie: () => cookie, fetch: fetchImpl }),
    module: instance,
  };
}

function assertRequest(call, method, url) {
  assert.equal(call.url, url);
  assert.equal(call.method, method);
  assert.equal(call.credentials, "include");
  assert.equal(call.redirect, "error");
  assert.equal(call.headers.get("X-CSRF-Token"), method === "GET" ? null : "test_token-123");
}

const scopeEnvelope = (extra = {}) => ({
  bot: "quill",
  requested_capabilities: ["observe", "reason"],
  effective_capabilities: ["observe", "reason"],
  denied_capabilities: [],
  demoted_capabilities: [],
  last_rescope: { by: "server", reason: "Operator re-scope via the capability panel", previous: ["reason"] },
  ceiling: {
    schema_version: 1,
    max_capability_rank: 50,
    allowed_capabilities: ["observe", "reason", "dispatch", "workspace_write", "process_exec"],
    max_live_profiles: 8,
    max_total_profiles: 32,
    source: "server default",
  },
  known_capabilities: [
    { name: "observe", rank: 10 },
    { name: "reason", rank: 20 },
    { name: "dispatch", rank: 30 },
    { name: "workspace_write", rank: 40 },
    { name: "process_exec", rank: 50 },
  ],
  declared: {
    toolsets: ["all"],
    skills: ["copywriting"],
    mcp_servers: [],
    memory_scope: "bot:quill",
    model: "gpt-5-mini",
  },
  epoch: "ep-abc123",
  version: 4,
  enforcement: {
    capabilities:
      "dispatch admission re-validates the live ceiling (authorized_bot); the grant gates work assignment",
    toolsets: "declared only - routing/selection read it; no run-time tool filter is wired to it yet",
  },
  provenance: { capabilities: "roster profile, ceiling-narrowed" },
  ...extra,
});

// ── Routes, verbs, CSRF ────────────────────────────────────────────────────

test("every route, verb and CSRF header matches the Gateway contract", async () => {
  const f = fixture(() => Response.json(scopeEnvelope()));

  await f.client.get("quill");
  assertRequest(f.calls[0], "GET", "/api/bots/quill/capabilities");

  await f.client.put("quill", ["observe", "reason"], "Tightening during a review");
  assertRequest(f.calls[1], "PUT", "/api/bots/quill/capabilities");
  assert.deepEqual(JSON.parse(f.calls[1].body), {
    capabilities: ["observe", "reason"],
    reason: "Tightening during a review",
  });
  assert.equal(f.calls[1].headers.get("Content-Type"), "application/json");

  // A blank reason is omitted, never sent as "" - the server's own default
  // names the panel when the operator states none.
  await f.client.put("quill", ["observe"], "   ");
  assert.deepEqual(JSON.parse(f.calls[2].body), { capabilities: ["observe"] });

  // Names are encoded, never interpolated raw.
  await f.client.get("a/b");
  assertRequest(f.calls[3], "GET", "/api/bots/a%2Fb/capabilities");
});

// ── Envelope mapping ───────────────────────────────────────────────────────

test("the mapper preserves the server's grant, ceiling, catalog and declarations", async () => {
  const f = fixture(() => Response.json(scopeEnvelope()));
  const scope = await f.client.get("quill");

  assert.equal(scope.bot, "quill");
  assert.deepEqual(scope.requested, ["observe", "reason"]);
  assert.deepEqual(scope.effective, ["observe", "reason"]);
  assert.deepEqual(scope.denied, []);
  assert.deepEqual(scope.demoted, []);
  assert.equal(scope.ceiling.max_capability_rank, 50);
  assert.equal(scope.ceiling.source, "server default");
  assert.deepEqual(scope.known, [
    { name: "observe", rank: 10 },
    { name: "reason", rank: 20 },
    { name: "dispatch", rank: 30 },
    { name: "workspace_write", rank: 40 },
    { name: "process_exec", rank: 50 },
  ]);
  assert.deepEqual(scope.declared, {
    toolsets: ["all"],
    skills: ["copywriting"],
    mcp_servers: [],
    memory_scope: "bot:quill",
    model: "gpt-5-mini",
  });
  assert.equal(scope.epoch, "ep-abc123");
  assert.equal(scope.version, 4);
  assert.equal(scope.last_rescope.by, "server");
  assert.deepEqual(scope.last_rescope.previous, ["reason"]);
  assert.match(scope.enforcement.capabilities, /dispatch admission/);
  assert.equal(scope.change, null, "a plain read carries no change record");
});

test("a denied difference is preserved, not folded into the effective grant", async () => {
  const f = fixture(() =>
    Response.json(
      scopeEnvelope({
        requested_capabilities: ["process_exec", "reason"],
        effective_capabilities: ["reason"],
        denied_capabilities: ["process_exec"],
      }),
    ),
  );
  const scope = await f.client.get("quill");
  assert.deepEqual(scope.requested, ["process_exec", "reason"]);
  assert.deepEqual(scope.effective, ["reason"]);
  assert.deepEqual(scope.denied, ["process_exec"]);
});

test("a saved answer carries the change record verbatim", async () => {
  const f = fixture(() =>
    Response.json(
      scopeEnvelope({
        changed: {
          previous_capabilities: ["reason"],
          effective_capabilities: ["observe", "reason"],
          epoch_before: "ep-abc123",
          epoch_moved: false,
          version_before: 3,
          note: "capabilities is descriptive, not epoch-bearing: only toolsets/skills/model change the fingerprint",
        },
      }),
    ),
  );
  const scope = await f.client.put("quill", ["observe", "reason"]);
  assert.equal(scope.change.epoch_moved, false);
  assert.deepEqual(scope.change.previous_capabilities, ["reason"]);
  assert.equal(scope.change.epoch_before, "ep-abc123");
  assert.equal(scope.change.version_before, 3);
  assert.match(scope.change.note, /not epoch-bearing/);
});

test("absent fields map to null, never fabrications", async () => {
  const f = fixture(() => Response.json({ bot: "quill" }));
  const scope = await f.client.get("quill");
  assert.equal(scope.requested, null, "a grant the server did not report is not an empty grant");
  assert.equal(scope.effective, null);
  assert.equal(scope.denied, null);
  assert.equal(scope.ceiling, null);
  assert.equal(scope.declared, null);
  assert.equal(scope.epoch, null);
  assert.equal(scope.version, null);
  assert.equal(scope.last_rescope, null);
  assert.deepEqual(scope.known, [], "no catalog at all is [], which the editor renders as 'no catalog sent'");
  assert.deepEqual(scope.enforcement, {});
  assert.equal(scope.change, null);
});

test("an unreadable scope fails the read instead of rendering an empty grant", async () => {
  const f = fixture(() => Response.json({ grants: [] }));
  await assert.rejects(() => f.client.get("quill"), /unreadable capability scope/);
  assert.throws(() => module.normalizeCapabilityScope(null), /unreadable capability scope/);
});

// ── The refusal path ───────────────────────────────────────────────────────

const refusalBody = {
  detail: {
    error: "authority_ceiling_violation",
    message: "The re-scope of 'quill' was refused.",
    violations: [
      "capability 'process_exec' ranks 50 above max_capability_rank 20",
      "capability 'process_exec' is not within the ceiling's allowed set",
    ],
  },
};

test("a structured refusal arrives with every violation, and the class is the module's", async () => {
  const f = fixture(() => Response.json(refusalBody, { status: 422 }));
  await assert.rejects(
    () => f.client.put("quill", ["observe", "process_exec"]),
    (err) => {
      assert.ok(err instanceof f.module.CapabilityRefusalError, `expected CapabilityRefusalError, got ${err.name}`);
      assert.deepEqual(err.violations, refusalBody.detail.violations);
      assert.equal(err.message, "The re-scope of 'quill' was refused.");
      assert.equal(err.status, 422);
      return true;
    },
  );
});

test("a string-detail error stays a plain client error", async () => {
  const f = fixture(() =>
    Response.json({ detail: "Admin privileges are required to manage bots." }, { status: 403 }),
  );
  await assert.rejects(
    () => f.client.put("quill", ["observe"]),
    (err) => {
      assert.equal(err.name, "ApiClientError");
      assert.equal(err.status, 403);
      assert.match(err.message, /Admin privileges/);
      return true;
    },
  );
});

test("violationsFromBody and messageFromBody read only the shapes they promise", () => {
  assert.deepEqual(module.violationsFromBody(refusalBody), refusalBody.detail.violations);
  assert.equal(module.messageFromBody(refusalBody), "The re-scope of 'quill' was refused.");
  assert.deepEqual(module.violationsFromBody({ detail: "a string detail" }), []);
  assert.equal(module.messageFromBody({ detail: "a string detail" }), "a string detail");
  assert.deepEqual(module.violationsFromBody(null), []);
  assert.equal(module.messageFromBody(null), null);
  assert.deepEqual(module.messageFromBody({}), null);
});

// ── View helpers ───────────────────────────────────────────────────────────

test("capabilityStanding marks picks the ceiling would refuse", () => {
  const ceiling = { max_capability_rank: 20 };
  assert.equal(module.capabilityStanding({ name: "reason", rank: 20 }, ceiling), "within");
  assert.equal(module.capabilityStanding({ name: "observe", rank: 10 }, ceiling), "within");
  assert.equal(module.capabilityStanding({ name: "process_exec", rank: 50 }, ceiling), "above");
  assert.equal(
    module.capabilityStanding({ name: "reason", rank: 20 }, null),
    "unknown",
    "no ceiling on the wire must not read as 'within'",
  );
  assert.equal(
    module.capabilityStanding({ name: "reason", rank: null }, ceiling),
    "unknown",
    "no rank must not read as rank 0",
  );
});

test("capabilityChangeLine answers the epoch question honestly", () => {
  const unchanged = module.capabilityChangeLine({
    version: 4,
    epoch: "ep-abc123",
    change: { version_before: 3, epoch_moved: false, epoch_before: "ep-abc123" },
  });
  assert.equal(unchanged, "v3 → v4 · epoch unchanged");

  const moved = module.capabilityChangeLine({
    version: 4,
    epoch: "ep-new",
    change: { version_before: 3, epoch_moved: true, epoch_before: "ep-old" },
  });
  assert.equal(moved, "v3 → v4 · epoch ep-old → ep-new");

  assert.equal(module.capabilityChangeLine({ version: 4, epoch: "ep-abc123", change: null }), null);
});

// ── No side doors ──────────────────────────────────────────────────────────

test("the detail view mounts the scoping panel inside the Capabilities section", () => {
  const view = readFileSync(new URL("../components/bots/BotDetailView.tsx", import.meta.url), "utf8");
  assert.match(view, /import \{ CapabilityScopePanel \} from "\.\/CapabilityScopePanel"/);
  assert.match(view, /<CapabilityScopePanel[\s\S]*?state=\{bundle\?\.capabilities \?\? \{ state: "loading" \}\}/);
});

test("the generic PATCH editor marks capabilities and toolsets read-only", () => {
  const fields = readFileSync(new URL("./bot-fields.ts", import.meta.url), "utf8");
  for (const key of ["capabilities", "toolsets"]) {
    const start = fields.indexOf(`key: "${key}"`);
    assert.ok(start >= 0, `expected a field spec for ${key}`);
    const block = fields.slice(start, start + 400);
    assert.match(block, /readOnly: true/, `${key} must be marked readOnly - the PATCH route refuses it`);
  }

  const panel = readFileSync(new URL("../components/chat-shell/BotDetailPanel.tsx", import.meta.url), "utf8");
  assert.match(panel, /const locked = disabled \|\| field\.readOnly === true/);
  assert.match(panel, /not editable here/);
});
