// workshop.test.mjs — Skill Synthesis Workshop client (wave W-C).
//
// Pins the client to the REAL gateway contract in
// backend/app/gateway/routers/skills_workshop.py: exact paths, exact method
// verbs, JSON bodies, envelope mapping, the API's own honesty labels
// (validation_kind / moderation / reject_reason / notes) preserved verbatim,
// and gateway failures rejecting instead of rendering as empty success.
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
  workshop: transpile("workshop.ts"),
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
  return { calls, workshop: load(sources.workshop, { "./http": http }) };
}

test("listWorkshopProposals reads GET /api/skills/workshop/evolution and maps the proposals envelope", async () => {
  const f = fixture(() =>
    Response.json({
      proposals: [
        { id: "p1", skill_name: "research", status: "validated", candidate_version: "2", evidence: [{ ref: "run:r1" }] },
      ],
    })
  );
  const rows = await f.workshop.listWorkshopProposals();
  assert.equal(f.calls[0].url, "/api/skills/workshop/evolution");
  assert.equal(f.calls[0].method, "GET");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].id, "p1");
  assert.equal(rows[0].skill_name, "research");
  assert.equal(rows[0].status, "validated");
  assert.deepEqual(rows[0].evidence, [{ ref: "run:r1" }]);
});

test("listWorkshopProposals forwards the status filter as a query parameter", async () => {
  const f = fixture(() => Response.json({ proposals: [] }));
  assert.deepEqual(await f.workshop.listWorkshopProposals("validated"), []);
  assert.equal(f.calls[0].url, "/api/skills/workshop/evolution?status=validated");
});

test("proposal records keep the API's own validation/evidence/rejection labels verbatim", async () => {
  const f = fixture(() =>
    Response.json({
      proposals: [
        {
          id: "p9",
          skill_name: "demo",
          status: "validated",
          candidate_version: "3",
          base_version: "2",
          evaluation: { validation_kind: "structural-only", checks_passed: 7, checks_total: 8, score: 0.875 },
          moderation: { status: "not_configured", model: null, reason: "no moderation model configured" },
          notes: ["runtime behavior was NOT verified"],
          reject_reason: null,
          history: [{ event: "proposed", at: 1.0 }],
        },
      ],
    })
  );
  const [row] = await f.workshop.listWorkshopProposals();
  assert.equal(row.base_version, "2");
  assert.equal(row.evaluation?.validation_kind, "structural-only");
  assert.equal(row.evaluation?.checks_passed, 7);
  assert.equal(row.evaluation?.score, 0.875);
  assert.equal(row.moderation?.status, "not_configured");
  assert.deepEqual(row.notes, ["runtime behavior was NOT verified"]);
  assert.equal(row.reject_reason, null);
  assert.equal(row.history?.length, 1);
});

test("distill and publish hit the real workshop routes with JSON bodies", async () => {
  const f = fixture((url) =>
    Response.json(
      url.includes("distill")
        ? { name: "skill-a", description: "d", markdown_content: "", parameters: [], findings: [], is_valid: false, metadata: {} }
        : { status: "published", name: "skill-a", path: "C:/skills/skill-a" }
    )
  );
  await f.workshop.distillSkillDraft({ name: "skill-a", description: "d", steps: [{ op: "read" }] });
  assert.equal(f.calls[0].url, "/api/skills/workshop/distill");
  assert.equal(f.calls[0].method, "POST");
  assert.deepEqual(JSON.parse(f.calls[0].body), { name: "skill-a", description: "d", steps: [{ op: "read" }] });

  const published = await f.workshop.publishSkillDraft({
    name: "skill-a",
    description: "d",
    markdown_content: "---\nname: skill-a\n---\nbody",
  });
  assert.equal(f.calls[1].url, "/api/skills/workshop/publish");
  assert.equal(f.calls[1].method, "POST");
  assert.deepEqual(JSON.parse(f.calls[1].body), {
    name: "skill-a",
    description: "d",
    markdown_content: "---\nname: skill-a\n---\nbody",
  });
  assert.equal(published.status, "published");
});

test("propose/get/evaluate/promote/rollback use the real evolution pipeline routes", async () => {
  const proposal = {
    id: "abc123",
    skill_name: "research",
    status: "proposed",
    candidate_version: "2",
    evidence: [{ ref: "e" }],
  };
  const f = fixture(() => Response.json(proposal));

  const proposed = await f.workshop.proposeSkillEvolution({
    skill_name: "research",
    candidate_markdown: "# body",
    evidence: ["run:1"],
  });
  assert.equal(f.calls[0].url, "/api/skills/workshop/evolution");
  assert.equal(f.calls[0].method, "POST");
  assert.deepEqual(JSON.parse(f.calls[0].body), {
    skill_name: "research",
    candidate_markdown: "# body",
    evidence: ["run:1"],
  });
  assert.equal(proposed.id, "abc123");

  await f.workshop.getWorkshopProposal("abc 123");
  assert.equal(f.calls[1].url, "/api/skills/workshop/evolution/abc%20123");
  assert.equal(f.calls[1].method, "GET");

  await f.workshop.evaluateSkillEvolution("abc123");
  assert.equal(f.calls[2].url, "/api/skills/workshop/evolution/abc123/evaluate");
  assert.equal(f.calls[2].method, "POST");

  await f.workshop.promoteSkillEvolution("abc123");
  assert.equal(f.calls[3].url, "/api/skills/workshop/evolution/abc123/promote");
  // Explicit human approval stays OFF by default (router: auto_promote defaults off).
  assert.deepEqual(JSON.parse(f.calls[3].body), { approve: false, reason: "" });

  await f.workshop.rollbackSkillEvolution("abc123", "bad change");
  assert.equal(f.calls[4].url, "/api/skills/workshop/evolution/abc123/rollback");
  assert.deepEqual(JSON.parse(f.calls[4].body), { reason: "bad change" });
});

test("a gateway failure rejects instead of rendering an empty proposal list", async () => {
  const f = fixture(() => ({ ok: false, status: 403, json() { assert.fail("error body must not be read"); } }));
  await assert.rejects(f.workshop.listWorkshopProposals(), (err) => err.status === 403);
});

test("a gateway failure rejects instead of rendering an empty list (503)", async () => {
  const f = fixture(() => ({ ok: false, status: 503, json() { assert.fail("error body must not be read"); } }));
  await assert.rejects(f.workshop.listWorkshopProposals(), (err) => err.status === 503);
});

// ── Contract pin: every consumed path must exist in the backend router ──────
const ROUTER = new URL("../../../backend/app/gateway/routers/skills_workshop.py", import.meta.url);
const ROUTE_DECORATORS = [
  '@router.post("/distill"',
  '@router.post("/publish"',
  '@router.post("/evolution"',
  '@router.get("/evolution"',
  '@router.get("/evolution/{proposal_id}"',
  '@router.post("/evolution/{proposal_id}/evaluate"',
  '@router.post("/evolution/{proposal_id}/promote"',
  '@router.post("/evolution/{proposal_id}/rollback"',
];

test("all 8 workshop routes exist in the backend router (contract pin)", { skip: !existsSync(ROUTER) }, () => {
  const src = readFileSync(ROUTER, "utf8");
  for (const decorator of ROUTE_DECORATORS) {
    assert.ok(src.includes(decorator), `router is missing ${decorator}`);
  }
});
