// policy.test.mjs — policy engine client (wave W-C).
//
// Pins the client to the REAL gateway contract in
// backend/app/gateway/routers/policy.py: exact paths, exact method verbs,
// JSON bodies, envelope mapping ({policies}/{approvals,count}), the engine's
// own verdict labels (allow|deny|approval + fail-closed "approval" reason)
// preserved verbatim, and gateway failures rejecting instead of rendering as
// empty success.
//
// Pure Node test (node --test): transpiles the TS modules and executes them
// against a stubbed http layer — no server, no browser.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const transpile = (file) =>
  ts.transpileModule(readFileSync(new URL(`./${file}`, import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;

const sources = {
  "api-client": transpile("api-client.ts"),
  http: transpile("http.ts"),
  policy: transpile("policy.ts"),
};

function load(source, dependencies = {}) {
  const exports = {};
  new Function("exports", "require", "process", "console", "fetch", source)(
    exports,
    (dependency) => {
      assert.ok(Object.hasOwn(dependencies, dependency), `Unexpected dependency: ${dependency}`);
      return dependencies[dependency];
    },
    { env: {} },
    console,
    () => {
      throw new Error("Raw fetch must not be used");
    },
  );
  return exports;
}

const client = load(sources["api-client"]);

function fixture(respond) {
  const calls = [];
  const api = {
    ...client,
    GATEWAY_BASE: "/api",
    apiFetch: client.createApiClient({
      baseUrl: "/api",
      getCookie: () => "",
      fetch: async (url, init) => {
        calls.push({ url, ...init });
        return respond(url, init);
      },
    }),
  };
  const http = load(sources.http, { "./api-client": api });
  return { calls, policy: load(sources.policy, { "./http": http }) };
}

test("listPolicies reads GET /api/policy/policies and maps the policies envelope", async () => {
  const f = fixture(() =>
    Response.json({
      policies: [
        { policy_id: "pol-1", action_pattern: "shell.*", actor: "*", project_id: "*", auto: "approval", note: "shell needs eyes", created_at: 1700000000.5 },
      ],
    })
  );
  const rows = await f.policy.listPolicies();
  assert.equal(f.calls[0].url, "/api/policy/policies");
  assert.equal(f.calls[0].method, "GET");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].policy_id, "pol-1");
  assert.equal(rows[0].action_pattern, "shell.*");
  // The engine's own verdict label is never rewritten in the client.
  assert.equal(rows[0].auto, "approval");
  assert.equal(rows[0].note, "shell needs eyes");
  assert.equal(rows[0].created_at, 1700000000.5);
});

test("evaluateAction POSTs /api/policy/evaluate with the documented body and keeps the verdict verbatim", async () => {
  const f = fixture(() =>
    Response.json({
      verdict: "approval",
      reason: "no rule matched; fail-closed to approval",
      matched_rule: null,
    })
  );
  const decision = await f.policy.evaluateAction("files.delete", "agent", "proj-7");
  assert.equal(f.calls[0].url, "/api/policy/evaluate");
  assert.equal(f.calls[0].method, "POST");
  assert.deepEqual(JSON.parse(f.calls[0].body), {
    action: "files.delete",
    actor: "agent",
    project_id: "proj-7",
  });
  // Fail-closed default + the API's own reason must survive to the UI.
  assert.equal(decision.verdict, "approval");
  assert.equal(decision.reason, "no rule matched; fail-closed to approval");
  assert.equal(decision.matched_rule, null);
});

test("evaluateAction defaults actor/project to '*' exactly like the router model", async () => {
  const f = fixture(() => Response.json({ verdict: "allow", reason: "base rule", matched_rule: "base:chat.*" }));
  const decision = await f.policy.evaluateAction("chat.send");
  assert.deepEqual(JSON.parse(f.calls[0].body), {
    action: "chat.send",
    actor: "*",
    project_id: "*",
  });
  assert.equal(decision.matched_rule, "base:chat.*");
});

test("addPolicy POSTs /api/policy/policies with the router's own defaults", async () => {
  const f = fixture(() =>
    Response.json({ policy_id: "pol-9", action_pattern: "net.*", actor: "*", project_id: "*", auto: "deny", note: "no egress", created_at: 1 })
  );
  const created = await f.policy.addPolicy({ action_pattern: "net.*", auto: "deny", note: "no egress" });
  assert.equal(f.calls[0].url, "/api/policy/policies");
  assert.equal(f.calls[0].method, "POST");
  assert.deepEqual(JSON.parse(f.calls[0].body), {
    action_pattern: "net.*",
    actor: "*",
    project_id: "*",
    auto: "deny",
    note: "no egress",
  });
  assert.equal(created.policy_id, "pol-9");
  assert.equal(created.auto, "deny");
});

test("removePolicy DELETEs /api/policy/policies/{id} (URL-encoded) and reads {removed}", async () => {
  const f = fixture(() => Response.json({ removed: true }));
  const removed = await f.policy.removePolicy("pol 9/x");
  assert.equal(f.calls[0].url, "/api/policy/policies/pol%209%2Fx");
  assert.equal(f.calls[0].method, "DELETE");
  assert.equal(removed, true);

  const f2 = fixture(() => Response.json({ removed: false }));
  assert.equal(await f2.policy.removePolicy("pol-gone"), false);
});

test("listApprovals reads GET /api/policy/approvals, maps the envelope and the status filter", async () => {
  const f = fixture(() =>
    Response.json({
      approvals: [
        { request_id: "ap-1", action: "shell.rm", actor: "agent", project_id: "*", reason: "cleanup", status: "pending", decided_by: null, created_at: 1700000001 },
      ],
      count: 1,
    })
  );
  const rows = await f.policy.listApprovals();
  assert.equal(f.calls[0].url, "/api/policy/approvals");
  assert.equal(f.calls[0].method, "GET");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].request_id, "ap-1");
  assert.equal(rows[0].status, "pending");
  assert.equal(rows[0].decided_by, null);

  await f.policy.listApprovals("pending");
  assert.equal(f.calls[1].url, "/api/policy/approvals?status=pending");
});

test("createApproval POSTs /api/policy/approvals with the router's defaults", async () => {
  const f = fixture(() =>
    Response.json({ request_id: "ap-2", action: "db.write", actor: "agent", project_id: "*", reason: "", status: "pending", decided_by: null, created_at: 2 })
  );
  const created = await f.policy.createApproval({ action: "db.write" });
  assert.equal(f.calls[0].url, "/api/policy/approvals");
  assert.equal(f.calls[0].method, "POST");
  assert.deepEqual(JSON.parse(f.calls[0].body), {
    action: "db.write",
    actor: "agent",
    project_id: "*",
    reason: "",
  });
  assert.equal(created.request_id, "ap-2");
  assert.equal(created.status, "pending");
});

test("decideApproval POSTs /api/policy/approvals/{id}/decide with approved + decided_by", async () => {
  const f = fixture(() =>
    Response.json({ request_id: "ap-2", action: "db.write", status: "approved", decided_by: "prem", created_at: 2 })
  );
  const decided = await f.policy.decideApproval("ap-2", true, "prem");
  assert.equal(f.calls[0].url, "/api/policy/approvals/ap-2/decide");
  assert.equal(f.calls[0].method, "POST");
  assert.deepEqual(JSON.parse(f.calls[0].body), { approved: true, decided_by: "prem" });
  assert.equal(decided.status, "approved");
  assert.equal(decided.decided_by, "prem");

  await f.policy.decideApproval("ap-2", false);
  assert.deepEqual(JSON.parse(f.calls[1].body), { approved: false, decided_by: "operator" });
});

test("a gateway failure rejects instead of rendering an empty policy list", async () => {
  const f = fixture(() => ({ ok: false, status: 403, json() { assert.fail("error body must not be read"); } }));
  await assert.rejects(f.policy.listPolicies(), (err) => err.status === 403);
  await assert.rejects(f.policy.listApprovals(), (err) => err.status === 403);
});

test("a gateway failure rejects instead of rendering an empty approvals list (503)", async () => {
  const f = fixture(() => ({ ok: false, status: 503, json() { assert.fail("error body must not be read"); } }));
  await assert.rejects(f.policy.listApprovals(), (err) => err.status === 503);
  await assert.rejects(
    f.policy.evaluateAction("chat.send"),
    (err) => err.status === 503
  );
});

// ── Contract pin: every consumed path must exist in the backend router ──────
const ROUTER = new URL("../../../backend/app/gateway/routers/policy.py", import.meta.url);
const ROUTE_DECORATORS = [
  '@router.post("/evaluate"',
  '@router.get("/policies"',
  '@router.post("/policies"',
  '@router.delete("/policies/{policy_id}"',
  '@router.get("/approvals"',
  '@router.post("/approvals"',
  '@router.post("/approvals/{request_id}/decide"',
];

test("all 7 policy routes exist in the backend router (contract pin)", { skip: !existsSync(ROUTER) }, () => {
  const src = readFileSync(ROUTER, "utf8");
  for (const decorator of ROUTE_DECORATORS) {
    assert.ok(src.includes(decorator), `router is missing ${decorator}`);
  }
});
