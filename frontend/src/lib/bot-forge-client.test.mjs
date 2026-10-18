import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

/**
 * The create dialog's client (`lib/bots.ts` → `forgeBot` / `fetchBotTemplates`)
 * pinned against the routes the Gateway really mounts:
 *
 *  - `POST /api/bots` answers 200 for BOTH directions: a forge refusal is a
 *    result body (`ok: false` + reason), so the client must resolve a refusal
 *    and keep `reason`/`steps`/`rollback_report` — treating it as a transport
 *    error would drop exactly the evidence the forge produced;
 *  - `alive` is true only when the server reported a smoke probe answered;
 *    a clean build with no probe is `ok: true` + `alive: false`;
 *  - `GET /api/bots/templates` keys its catalog by `slug`
 *    (`alpha/bots/templates.py` `list_templates`), the field the previous
 *    projection misread as `name`;
 *  - a non-2xx (403 non-admin, 422 malformed handle) rejects with the server's
 *    own text — it is never painted as a forge result;
 *  - a failed template read rejects: `[]` must mean "the server listed none".
 */

const compiled = new Map(
  ["api-client", "bots"].map((name) => [
    name,
    ts.transpileModule(readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8"), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText,
  ]),
);

function load(name, dependencies = {}) {
  const exports = {};
  new Function("exports", "require", "process", "console", "fetch", compiled.get(name))(
    exports,
    (dependency) => {
      assert.ok(Object.hasOwn(dependencies, dependency), `Unexpected dependency: ${dependency}`);
      return dependencies[dependency];
    },
    { env: {} },
    { error: () => {} },
    () => {
      throw new Error("Raw fetch must not be used");
    },
  );
  return exports;
}

/** The bodies a queued stub answers with, in order. Mirrors apiFetch: non-2xx throws. */
function client(responses) {
  const queue = [...responses];
  const calls = [];
  const bots = load("bots", {
    "./api-client": {
      apiFetch: async (path, init = {}) => {
        calls.push({ path, method: init.method || "GET", body: init.body ? JSON.parse(init.body) : undefined });
        const r = queue.shift();
        assert.ok(r, `no canned response queued for ${path}`);
        if (r.status && (r.status < 200 || r.status >= 300)) {
          throw new Error(`Request failed (HTTP ${r.status}).${r.detail ? ` ${r.detail}` : ""}`);
        }
        return { json: async () => r.body };
      },
    },
  });
  return { calls, bots };
}

const response = (body, status = 200) => ({ body, status });

const FORGE_OK = {
  ok: true,
  name: "quill",
  reason: "",
  alive: false,
  smoke_tested: false,
  rolled_back: false,
  steps: ["profile", "journal"],
  warnings: [],
  rollback_report: [],
  elapsed_seconds: 1.25,
  reply: "@quill was forged; runtime behavior is unverified.",
};

test("forgeBot POSTs the forge route with a snake_case body and omits fields it was not given", async () => {
  const { calls, bots } = client([response(FORGE_OK)]);
  await bots.forgeBot({ name: "quill", role: "write scheduled posts" });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, "/bots");
  assert.equal(calls[0].method, "POST");
  assert.deepEqual(calls[0].body, { name: "quill", role: "write scheduled posts" });
});

test("every optional field travels under the server's own key", async () => {
  const { calls, bots } = client([response(FORGE_OK)]);
  await bots.forgeBot({
    name: "quill",
    role: "posts",
    template: "coder",
    displayName: "Quill",
    department: "growth",
    model: "gpt-4o-mini",
    skills: ["copywriting", "scheduling"],
  });
  assert.deepEqual(calls[0].body, {
    name: "quill",
    role: "posts",
    template: "coder",
    display_name: "Quill",
    department: "growth",
    model: "gpt-4o-mini",
    skills: ["copywriting", "scheduling"],
  });
});

test("a forge refusal RESOLVES with ok:false and keeps the reason, steps and rollback report", async () => {
  const { bots } = client([
    response({
      ok: false,
      name: "quill",
      reason: "@quill already exists — use update, not create",
      alive: false,
      smoke_tested: false,
      rolled_back: false,
      steps: [],
      warnings: [],
      rollback_report: [],
      elapsed_seconds: 0.1,
      reply: "⛔ @quill was not created: @quill already exists — use update, not create",
    }),
  ]);
  const result = await bots.forgeBot({ name: "quill", role: "posts" });
  assert.equal(result.ok, false);
  assert.match(result.reason, /already exists/);
  assert.equal(result.rolledBack, false);
});

test("a rolled-back refusal keeps the rollback report instead of dropping it into an exception", async () => {
  const { bots } = client([
    response({
      ok: false,
      name: "quill",
      reason: "could not schedule routines: ValueError: bad schedule",
      alive: false,
      smoke_tested: false,
      rolled_back: true,
      steps: ["profile", "sandbox"],
      warnings: [],
      rollback_report: ["journal: rollback failed (locked)"],
      elapsed_seconds: 0.7,
      reply: "rolled back",
    }),
  ]);
  const result = await bots.forgeBot({ name: "quill", role: "posts" });
  assert.equal(result.ok, false);
  assert.equal(result.rolledBack, true);
  assert.deepEqual(result.rollbackReport, ["journal: rollback failed (locked)"]);
  assert.deepEqual(result.steps, ["profile", "sandbox"]);
});

test("a build with no smoke probe is ok:true but NEVER alive", async () => {
  const { bots } = client([response(FORGE_OK)]);
  const result = await bots.forgeBot({ name: "quill", role: "posts" });
  assert.equal(result.ok, true);
  assert.equal(result.smokeTested, false);
  assert.equal(result.alive, false, "no probe answered, so 'alive' would be a fabricated claim");
  assert.match(result.reply, /unverified/);
});

test("alive is true only when the server reported the smoke test passed", async () => {
  const { bots } = client([response({ ...FORGE_OK, alive: true, smoke_tested: true })]);
  const result = await bots.forgeBot({ name: "quill", role: "posts" });
  assert.equal(result.alive, true);
  assert.equal(result.smokeTested, true);
});

test("a non-admin create rejects with the Gateway's own refusal text, not a forge result", async () => {
  const { bots } = client([{ status: 403, detail: "Admin privileges are required to manage bots." }]);
  await assert.rejects(() => bots.forgeBot({ name: "quill", role: "posts" }), /Admin privileges are required/);
});

test("fetchBotTemplates reads the catalog's slug handle instead of leaving name undefined", async () => {
  // Verbatim shape from `list_templates()` (alpha/bots/templates.py:265-267).
  const { calls, bots } = client([
    response({
      templates: [
        {
          slug: "coder",
          display: "Coder",
          role: "Software Engineer & Backend Developer",
          avatar: "💻",
          department: "engineering",
          reports_to: "cto",
          responsibilities: ["Ship code"],
          capabilities: ["backend"],
        },
      ],
    }),
  ]);
  const templates = await bots.fetchBotTemplates();
  assert.equal(calls[0].path, "/bots/templates");
  assert.equal(templates.length, 1);
  assert.equal(templates[0].name, "coder", "the live route keys the handle as `slug`");
  assert.equal(templates[0].department, "engineering");
  assert.deepEqual(templates[0].capabilities, ["backend"]);
  assert.equal(templates[0].reports_to, "cto");
});

test("a sparse template row yields empty lists, never undefined that renders as blank", async () => {
  const { bots } = client([response({ templates: [{ slug: "minimal" }] })]);
  const [t] = await bots.fetchBotTemplates();
  assert.equal(t.name, "minimal");
  assert.deepEqual(t.responsibilities, []);
  assert.deepEqual(t.capabilities, []);
  assert.equal(t.reports_to, null);
});

test("a failed template read rejects — [] must mean the server listed none", async () => {
  const { bots } = client([{ status: 503, detail: "templates unavailable" }]);
  await assert.rejects(() => bots.fetchBotTemplates(), /HTTP 503/);
});

test("an object-keyed catalog (older shape) still yields named templates", async () => {
  const { bots } = client([
    response({ templates: { reviewer: { display: "Reviewer", role: "Code Reviewer", avatar: "🔍" } } }),
  ]);
  const [t] = await bots.fetchBotTemplates();
  assert.equal(t.name, "reviewer");
  assert.equal(t.display, "Reviewer");
});
