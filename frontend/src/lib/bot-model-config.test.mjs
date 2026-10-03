import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

/**
 * The real shared client is loaded so this suite pins the actual CSRF,
 * credentials and redirect behaviour, not a stand-in for it.
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
    fetchImpl || (() => {
      throw new Error("Raw fetch must not be used");
    }),
  );
  return exports;
}

const apiClient = instantiate(compile("api-client"));

function fixture(respond, { baseUrl = "/api", cookie = "other=value; csrf_token=test_token-123" } = {}) {
  const calls = [];
  // One client only: the module builds the real `createApiClient` around this
  // stub, so CSRF/credentials come from production code and the error-body
  // capture sits where it does at runtime. Wrapping a second client here
  // would make the inner one throw before the capture ever sees a response.
  const fetchImpl = async (url, init) => {
    calls.push({ url, ...init });
    return respond(url, init);
  };
  const module = instantiate(compile("bot-model-config"), {
    requireMap: { "./api-client": { ...apiClient, GATEWAY_BASE: baseUrl } },
  });
  return {
    calls,
    client: module.createModelConfigClient({ baseUrl, getCookie: () => cookie, fetch: fetchImpl }),
    module,
  };
}

function assertRequest(call, method, url) {
  assert.equal(call.url, url);
  assert.equal(call.method, method);
  assert.equal(call.credentials, "include");
  assert.equal(call.redirect, "error");
  assert.equal(call.headers.get("X-CSRF-Token"), method === "GET" ? null : "test_token-123");
}

const planEnvelope = (extra = {}) => ({
  name: "reviewer",
  model: "local-model",
  config: {},
  resolved: {
    bot: "reviewer",
    plan: {
      primary: "flagship",
      primary_source: "bot.model_config",
      fallbacks: ["cheap"],
      fallbacks_source: "bot.model_config",
      counsel: null,
      counsel_source: "default",
      mixture: null,
      mixture_source: "default",
      sampling: {},
      sampling_source: "default",
    },
    precedence: ["request", "bot.model_config", "bot.model", "custom_agent", "default"],
    limits: { max_fallbacks: 5, mixture_strategies: ["parallel", "sequential"] },
  },
  issues: [],
  valid: true,
  ...extra,
});

// ── Routes, verbs, CSRF ────────────────────────────────────────────────────

test("every route, verb and CSRF header matches the Gateway contract", async () => {
  const f = fixture(() => Response.json(planEnvelope()));

  await f.client.get("reviewer");
  assertRequest(f.calls[0], "GET", "/api/bots/reviewer/model-config");

  await f.client.put("reviewer", { primary: "flagship" });
  assertRequest(f.calls[1], "PUT", "/api/bots/reviewer/model-config");
  assert.deepEqual(JSON.parse(f.calls[1].body), { config: { primary: "flagship" } });
  assert.equal(f.calls[1].headers.get("Content-Type"), "application/json");

  await f.client.clear("reviewer");
  assertRequest(f.calls[2], "DELETE", "/api/bots/reviewer/model-config");

  await f.client.preview("reviewer", { primary: "cheap" }, { bot_model: "local-model" });
  assertRequest(f.calls[3], "POST", "/api/bots/reviewer/model-config/preview");
  assert.deepEqual(JSON.parse(f.calls[3].body), { config: { primary: "cheap" }, bot_model: "local-model" });

  // Names are encoded, never interpolated raw.
  await f.client.get("a/b");
  assertRequest(f.calls[4], "GET", "/api/bots/a%2Fb/model-config");
});

test("preview sends only the context keys it was given", async () => {
  const f = fixture(() => Response.json(planEnvelope()));
  await f.client.preview("reviewer", {});
  assert.deepEqual(JSON.parse(f.calls[0].body), { config: {} });
});

// ── Envelope mapping ───────────────────────────────────────────────────────

test("the view preserves the server's config, plan, precedence and limits verbatim", async () => {
  const f = fixture(() =>
    Response.json(
      planEnvelope({
        config: { primary: "flagship", fallbacks: ["cheap"] },
        known_models: ["cheap", "flagship"],
      }),
    ),
  );
  const view = await f.client.get("reviewer");
  assert.deepEqual(view.config, { primary: "flagship", fallbacks: ["cheap"] });
  assert.equal(view.model, "local-model");
  assert.equal(view.valid, true);
  assert.deepEqual(view.issues, []);
  assert.deepEqual(view.known_models, ["cheap", "flagship"]);
  assert.equal(view.resolved.plan.primary, "flagship");
  assert.equal(view.resolved.plan.primary_source, "bot.model_config");
  assert.deepEqual(view.resolved.precedence[0], "request");
  assert.equal(view.resolved.limits.max_fallbacks, 5);
});

test("an unreported known_models list is null, not an empty picker", async () => {
  const body = planEnvelope();
  delete body.known_models;
  const f = fixture(() => Response.json(body));
  const view = await f.client.get("reviewer");
  assert.equal(view.known_models, null);
});

test("an empty known_models list stays an empty list (no declared models)", async () => {
  const f = fixture(() => Response.json(planEnvelope({ known_models: [] })));
  const view = await f.client.get("reviewer");
  assert.deepEqual(view.known_models, []);
});

test("a malformed 200 is rejected rather than rendered as a valid plan", async () => {
  const broken = fixture(() => Response.json({ name: "reviewer" }));
  await assert.rejects(broken.client.get("reviewer"), /without a resolved plan|unreadable response/);

  const notAnObject = fixture(() => Response.json("ok"));
  await assert.rejects(notAnObject.client.get("reviewer"), /unreadable response/);
});

// ── Honesty: the 422 issue list must survive ───────────────────────────────

test("a 422 surfaces every server issue instead of a bare status", async () => {
  const issues = [
    { code: "unknown_model", field: "model_config.primary", message: "ghost is not declared in models[]", severity: "error" },
    { code: "secret_key", field: "model_config.sampling.api_key", message: "credential-shaped key", severity: "error" },
  ];
  const f = fixture(() =>
    Response.json({ detail: { message: "The model configuration is invalid.", issues } }, { status: 422 }),
  );

  await assert.rejects(
    f.client.put("reviewer", { primary: "ghost" }),
    (err) => {
      assert.equal(err.name, "ModelConfigValidationError");
      assert.equal(err.status, 422);
      assert.match(err.message, /The model configuration is invalid\./);
      assert.deepEqual(err.issues, issues);
      return true;
    },
  );
});

test("the captured error body does not leak into a later successful call", async () => {
  let first = true;
  const f = fixture(() => {
    if (first) {
      first = false;
      return Response.json(
        { detail: { message: "invalid", issues: [{ code: "x", field: "model_config.primary", message: "no", severity: "error" }] } },
        { status: 422 },
      );
    }
    return Response.json(planEnvelope());
  });

  await assert.rejects(f.client.put("reviewer", {}), /invalid/);
  const view = await f.client.put("reviewer", { primary: "flagship" });
  assert.equal(view.valid, true);
  assert.deepEqual(view.issues, []);
});

test("a non-issue 422 falls through to the shared ApiClientError", async () => {
  const f = fixture(() => Response.json({ detail: "config field not permitted" }, { status: 422 }));
  await assert.rejects(f.client.put("reviewer", {}), (err) => {
    assert.ok(err instanceof apiClient.ApiClientError);
    assert.equal(err.status, 422);
    assert.equal(err.detail, "config field not permitted");
    return true;
  });
});

test("an unreadable error body never fabricates issues", async () => {
  const f = fixture(() => new Response("not json", { status: 422 }));
  await assert.rejects(f.client.put("reviewer", {}), (err) => {
    assert.ok(err instanceof apiClient.ApiClientError);
    assert.equal(err.detail, null);
    return true;
  });
});

test("network and non-2xx reads reject with the shared error shape", async () => {
  const down = fixture(() => {
    throw new Error("Bearer secret private diagnostics");
  });
  await assert.rejects(down.client.get("reviewer"), (err) => {
    assert.ok(err instanceof apiClient.ApiClientError);
    assert.equal(err.kind, "network");
    assert.doesNotMatch(err.message, /secret|private|Bearer/);
    return true;
  });

  const forbidden = fixture(() => new Response(null, { status: 403 }));
  await assert.rejects(forbidden.client.get("reviewer"), (err) => {
    assert.ok(err instanceof apiClient.ApiClientError);
    assert.equal(err.status, 403);
    return true;
  });
});

// ── Draft helpers: pure, no invented values ────────────────────────────────

test("draftToConfig omits empty sections so inherit-everything stays unset", () => {
  const { module } = fixture(() => Response.json(planEnvelope()));
  assert.deepEqual(module.draftToConfig(module.emptyDraft()), {});

  const draft = module.emptyDraft();
  draft.primary = "  flagship  ";
  draft.fallbacks = ["cheap", "", "  "];
  draft.sampling = [
    { key: "temperature", value: "0.2" },
    { key: "  ", value: "1" },
    { key: "stream", value: "false" },
    { key: "note", value: "keep me" },
  ];
  assert.deepEqual(module.draftToConfig(draft), {
    primary: "flagship",
    fallbacks: ["cheap"],
    sampling: { temperature: 0.2, stream: false, note: "keep me" },
  });
});

test("a panel the operator never touched is not stored as a configured one", () => {
  const { module } = fixture(() => Response.json(planEnvelope()));
  const draft = module.emptyDraft();
  draft.counsel.rounds = 3;
  draft.mixture.strategy = "sequential";
  const config = module.draftToConfig(draft);
  // Both blocks differ from the defaults, so both are declared...
  assert.equal(config.counsel.rounds, 3);
  assert.equal(config.mixture.strategy, "sequential");

  const untouched = module.draftToConfig(module.emptyDraft());
  assert.equal("counsel" in untouched, false);
  assert.equal("mixture" in untouched, false);
  assert.equal("sampling" in untouched, false);
});

test("configToDraft round-trips the canonical server form", () => {
  const { module } = fixture(() => Response.json(planEnvelope()));
  const config = {
    primary: "flagship",
    fallbacks: ["cheap", "local-model"],
    sampling: { temperature: 0.2, stream: false, note: "keep me" },
    counsel: { enabled: true, members: ["cheap", "flagship"], rounds: 2, quorum: 2, effort: "high" },
    mixture: { enabled: true, references: ["cheap"], aggregator: "flagship", strategy: "sequential", max_workers: 3 },
  };
  const draft = module.configToDraft(config);
  assert.deepEqual(module.draftToConfig(draft), config);
});

test("configToDraft tolerates junk so a broken block can still be opened and fixed", () => {
  const { module } = fixture(() => Response.json(planEnvelope()));
  for (const junk of [null, undefined, "primary", 42, ["primary"], { primary: 7, fallbacks: "cheap", counsel: "x", mixture: [] }]) {
    const draft = module.configToDraft(junk);
    assert.equal(draft.primary, "");
    assert.deepEqual(draft.fallbacks, []);
    assert.equal(draft.counsel.enabled, false);
    assert.equal(typeof draft.mixture.strategy, "string");
  }
});

test("unknown enum strings survive rather than snapping to a known value", () => {
  const { module } = fixture(() => Response.json(planEnvelope()));
  const draft = module.configToDraft({ mixture: { strategy: "ring-topology" } });
  assert.equal(draft.mixture.strategy, "ring-topology");
  assert.deepEqual(module.draftToConfig(draft).mixture.strategy, "ring-topology");
});

test("sampling scalars coerce for the provider but never silently stringify", () => {
  const { module } = fixture(() => Response.json(planEnvelope()));
  assert.equal(module.coerceSamplingValue("0.2"), 0.2);
  assert.equal(module.coerceSamplingValue("-3"), -3);
  assert.equal(module.coerceSamplingValue("true"), true);
  assert.equal(module.coerceSamplingValue("false"), false);
  assert.equal(module.coerceSamplingValue("gpt-4o"), "gpt-4o");
  assert.equal(module.coerceSamplingValue("007"), "007");

  assert.equal(module.formatSamplingValue(0.2), "0.2");
  assert.equal(module.formatSamplingValue(false), "false");
  assert.equal(module.formatSamplingValue(null), "");
  assert.equal(module.formatSamplingValue(undefined), "");
  assert.equal(module.formatSamplingValue({ a: 1 }), '{"a":1}');
});

test("draftsEqual only reports real differences", () => {
  const { module } = fixture(() => Response.json(planEnvelope()));
  assert.equal(module.draftsEqual(module.emptyDraft(), module.emptyDraft()), true);
  const changed = module.emptyDraft();
  changed.primary = "flagship";
  assert.equal(module.draftsEqual(module.emptyDraft(), changed), false);
});

test("issue and message extraction ignores shapes the server never promised", () => {
  const { module } = fixture(() => Response.json(planEnvelope()));
  assert.deepEqual(module.issuesFromBody(null), []);
  assert.deepEqual(module.issuesFromBody({ detail: "plain" }), []);
  assert.deepEqual(module.issuesFromBody({ detail: { issues: "no" } }), []);
  // A malformed entry is dropped rather than rendered as a half-built issue.
  assert.deepEqual(module.issuesFromBody({ detail: { issues: [{ code: "x" }] } }), []);
  assert.equal(module.messageFromBody({ detail: { issues: [] } }), null);
  assert.equal(module.messageFromBody({ detail: "why" }), "why");
});
