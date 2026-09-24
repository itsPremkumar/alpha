// moa.test.mjs — Mixture-of-Agents client (wave W-D).
//
// The evidence contract is the point of this suite: the client must carry the
// server's `evidence_kind` and `evidence_note` through untouched. A simulated
// run (stub client) can never be rendered as model output, a failed run can
// never look successful, and the engine's redacted prompt is what gets shown
// (the raw draft is not resurrected client-side).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const transpile = (file) =>
  ts.transpileModule(readFileSync(new URL(`./${file}`, import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;

const sources = {
  "api-client": transpile("api-client.ts"),
  http: transpile("http.ts"),
  moa: transpile("moa.ts"),
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
  return { calls, moa: load(sources.moa, { "./http": http }) };
}

test("getMoaStatus maps every slot and keeps unavailable sources as null + real error", async () => {
  const f = fixture(() =>
    Response.json({
      capability_id: "moa_engine",
      engine: null,
      engine_error: "RuntimeError: capability registry offline",
      command: {
        command: "/moa",
        category: "reasoning",
        description: "Multi-agent deliberation",
        usage: "/moa <question>",
        handler_available: true,
      },
      command_error: null,
      limits: { max_advisors: 4, max_reference_chars: 4000 },
      limits_error: null,
      orchestrator: { module: "alpha.models.moa.orchestrator", max_workers: 4 },
      redaction: { module: "alpha.models.moa.redact", email_masked: true, phone_masked: true, secret_masked: true },
      tool: { name: "moa_multi_model_reasoning", registered: true },
    }),
  );
  const s = await f.moa.getMoaStatus();
  assert.equal(f.calls[0].url, "/api/models/moa");
  assert.equal(s.capability_id, "moa_engine");
  assert.equal(s.engine, null);
  assert.equal(s.engine_error, "RuntimeError: capability registry offline");
  assert.equal(s.command.handler_available, true);
  assert.equal(s.limits.max_advisors, 4);
  assert.equal(s.redaction.secret_masked, true);
  assert.equal(s.tool.registered, true);
});

test("runMoaRound posts exactly {prompt, candidate_models}", async () => {
  const f = fixture(() =>
    Response.json({
      prompt: "p",
      consensus_response: "c",
      candidates: [],
      candidate_models: ["alpha-1"],
      total_duration_ms: 3.2,
      evidence_kind: "real",
      evidence_note: "Production model client generated 1/1 candidate outputs in this request (engine-redacted).",
    }),
  );
  const r = await f.moa.runMoaRound({ prompt: "p", candidate_models: ["alpha-1"] });
  assert.equal(f.calls[0].url, "/api/models/moa/run");
  assert.deepEqual(JSON.parse(f.calls[0].body), { prompt: "p", candidate_models: ["alpha-1"] });
  assert.equal(r.evidence_kind, "real");
  assert.deepEqual(r.candidate_models, ["alpha-1"]);
});

test("a simulated run keeps evidence_kind=simulated and its note — never re-labelled", async () => {
  const f = fixture(() =>
    Response.json({
      prompt: "reach me at [redacted email]",
      consensus_response: "stub consensus",
      candidates: [
        { model_name: "alpha-1", success: true, response: "stub answer", error: null, duration_ms: 1.0 },
      ],
      candidate_models: ["alpha-1"],
      total_duration_ms: 1.0,
      evidence_kind: "simulated",
      evidence_note:
        "The model client in use is a stub/injection - candidate outputs are simulated, not model generations.",
    }),
  );
  const r = await f.moa.runMoaRound({ prompt: "p", candidate_models: ["alpha-1"] });
  assert.equal(r.evidence_kind, "simulated");
  assert.match(r.evidence_note, /not model generations/);
  assert.equal(r.candidates[0].response, "stub answer");
  assert.equal(r.candidates[0].error, null);
});

test("a failed run maps every candidate as unsuccessful with its error verbatim", async () => {
  const f = fixture(() =>
    Response.json({
      prompt: "p",
      consensus_response: "",
      candidates: [
        { model_name: "alpha-1", success: false, response: "", error: "RuntimeError: provider unavailable", duration_ms: 12.0 },
      ],
      candidate_models: ["alpha-1"],
      total_duration_ms: 12.0,
      evidence_kind: "failed",
      evidence_note: "The production model client ran, but every candidate failed - no model output exists in this result.",
    }),
  );
  const r = await f.moa.runMoaRound({ prompt: "p", candidate_models: ["alpha-1"] });
  assert.equal(r.evidence_kind, "failed");
  assert.match(r.evidence_note, /no model output exists/);
  assert.equal(r.candidates[0].success, false);
  assert.equal(r.candidates[0].error, "RuntimeError: provider unavailable");
  assert.equal(r.candidates[0].response, "", "an absent response stays an empty string, not undefined text");
});

test("an unknown model (404) rejects with the gateway's reason", async () => {
  const f = fixture(() =>
    new Response(JSON.stringify({ detail: "Model 'not-a-model' not found" }), {
      status: 404,
      headers: { "content-type": "application/json" },
    }),
  );
  await assert.rejects(
    () => f.moa.runMoaRound({ prompt: "p", candidate_models: ["not-a-model"] }),
    /not found/,
  );
});

test("a permission denial (403) rejects instead of returning an empty result", async () => {
  const f = fixture(() =>
    new Response(JSON.stringify({ detail: "Model 'gpt-4' is not available for your role" }), {
      status: 403,
      headers: { "content-type": "application/json" },
    }),
  );
  await assert.rejects(
    () => f.moa.runMoaRound({ prompt: "p", candidate_models: ["gpt-4"] }),
    /not available for your role/,
  );
});

test("contract pin: the MoA routes + evidence model really exist in models.py", () => {
  const router = readFileSync(new URL("../../../backend/app/gateway/routers/models.py", import.meta.url), "utf8");
  assert.match(router, /"\/models\/moa"/);
  assert.match(router, /"\/models\/moa\/run"/);
  assert.match(router, /class MoaStatusResponse/);
  assert.match(router, /class MoaRunResponse/);
  assert.match(router, /evidence_kind: Literal\["real", "simulated", "failed"\]/);
  // The run route must reuse the existing per-model authorization path.
  assert.match(router, /await get_model\(name, request, config\)/);
});
