// company.test.mjs — real Gateway routes, tenancy, and honesty pins for the Company OS.
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
  company: transpile("company.ts"),
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
  return { calls, company: load(sources.company, { "./http": http }) };
}

// The shared client reads failure text via `response.clone().json()`, so the
// fixture must be cloneable or the server's `detail` is silently dropped.
const json = (body, status = 200) => {
  const response = {
    ok: status >= 200 && status < 300,
    status,
    headers: new Map([["content-type", "application/json"]]),
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
  return { ...response, clone: () => response };
};

const COMPANY = {
  company_id: "co-abc123",
  owner_id: "u-1",
  name: "Apex Systems",
  archetype: "company",
  description: "A startup",
  state: "active",
  charter: {
    mission: "Ship reliable software",
    vision: "Boring and dependable",
    values: ["honesty"],
    constraints: [],
    autonomy_tier: "T2_execute_routine",
    budget_daily_usd: 5.0,
    budget_total_usd: 500.0,
    currency: "USD",
  },
  created_at: 1700000000,
  updated_at: 1700000100,
};

// --------------------------------------------------------------------------- //
// Routes: exact paths and verbs
// --------------------------------------------------------------------------- //

test("listCompanies reads the plural collection route", async () => {
  const f = fixture(() => json({ companies: [COMPANY], count: 1 }));
  const rows = await f.company.listCompanies();
  assert.equal(f.calls[0].url, "/api/companies");
  assert.equal(f.calls[0].method, "GET");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].company_id, "co-abc123");
});

test("createCompany POSTs to the collection root", async () => {
  const f = fixture(() => json({ company: COMPANY, hire: { ok: true, profiles_created_count: 10 } }));
  const res = await f.company.createCompany({ prompt: "Ship reliable software", name: "Apex Systems" });
  assert.equal(f.calls[0].url, "/api/companies");
  assert.equal(f.calls[0].method, "POST");
  const body = JSON.parse(f.calls[0].body);
  assert.equal(body.prompt, "Ship reliable software");
  // A company id must never be sent on create: the server assigns it.
  assert.equal(body.company_id, undefined);
  assert.equal(res.hire.profiles_created_count, 10);
});

test("getCompany reads the overview route", async () => {
  const f = fixture(() =>
    json({
      company: { ...COMPANY, roles: [], units: [], employments: [], accountabilities: [], objectives: [], projects: [], work_items: [], groups: [], schedules: [], approvals: [], loop_policy: {}, cost: {} },
      metrics: { headcount: 10, health_percent: 82.5, health_basis: "derived", measured_at: 1700000100 },
      health: { health_percent: 82.5, basis: "derived" },
      work: { reachable: true, total_items: 4 },
      workforce: { employment_count: 10, missing_profile_count: 0, missing_profiles: [], unassigned_bot_count: 0, unassigned_bots: [] },
      cost: { spent_today_usd: 1.25, budget_daily_usd: 5.0, utilization_percent: 25.0, basis: "measured", measured_run_count: 3, last_measured_at: 1700000100 },
      loop: { enabled: true, state: "active", consecutive_no_progress_ticks: 0, recent_outcomes: [], last_ritual_at: {} },
      rituals: { due: [], ran: [], last_ritual_at: {}, cadence: {} },
      approvals: [],
      invariants_ok: true,
      invariant_problems: [],
    }),
  );
  const o = await f.company.getCompany("co-abc123");
  assert.equal(f.calls[0].url, "/api/companies/co-abc123/overview");
  assert.equal(o.metrics.health_percent, 82.5);
  assert.equal(o.invariants_ok, true);
});

test("company ids are URL-encoded on every path", async () => {
  const f = fixture(() => json({ ticks: [], count: 0 }));
  await f.company.listTicks("co/with slash");
  assert.equal(f.calls[0].url, "/api/companies/co%2Fwith%20slash/loop?limit=25");
});

test("the tick route is a POST with no invented body", async () => {
  const f = fixture(() => json({ tick: { tick_id: "t-1", outcome: "idle", reason: "No actionable work." } }));
  const t = await f.company.runTick("co-abc123");
  assert.equal(f.calls[0].method, "POST");
  assert.equal(f.calls[0].url, "/api/companies/co-abc123/loop/tick");
  assert.equal(t.outcome, "idle");
});

test("listTicks clamps the limit to the router window", async () => {
  const f = fixture(() => json({ ticks: [], count: 0 }));
  await f.company.listTicks("co-abc123", 5000);
  assert.match(f.calls[0].url, /limit=200$/);
});

test("work-item and approval mutations use their nested routes", async () => {
  const f = fixture(() => json({ ok: true }));
  await f.company.moveWorkItem("co-abc123", "TASK-1", "in_progress");
  await f.company.decideApproval("co-abc123", "appr-1", "approve", "looks good");
  assert.equal(f.calls[0].url, "/api/companies/co-abc123/work/TASK-1/move");
  assert.equal(f.calls[0].method, "POST");
  assert.equal(f.calls[1].url, "/api/companies/co-abc123/approvals/appr-1/approve");
});

test("a loop policy patch is a PATCH, never a POST", async () => {
  const f = fixture(() => json({ company: COMPANY }));
  await f.company.setLoopPolicy("co-abc123", { enabled: true });
  assert.equal(f.calls[0].method, "PATCH");
  assert.equal(f.calls[0].url, "/api/companies/co-abc123/loop");
});

// --------------------------------------------------------------------------- //
// Tenancy: the client never sends or invents an owner
// --------------------------------------------------------------------------- //

test("no client call ever sends an owner field", async () => {
  const f = fixture(() => json({ company: COMPANY }));
  await f.company.createCompany({ prompt: "a mission that is long enough", name: "x" });
  await f.company.hireEmployee("co-abc123", { handle: "h", role_title: "r", unit_name: "u" });
  await f.company.setCompanyState("co-abc123", "paused");
  for (const call of f.calls) {
    const body = call.body ? JSON.parse(call.body) : {};
    assert.equal(body.owner_id, undefined, `owner_id leaked in ${call.url}`);
    assert.equal(body.owner, undefined, `owner leaked in ${call.url}`);
  }
});

test("a company id is required rather than defaulted", async () => {
  // The client has no "first company" fallback path: every read takes an id.
  const f = fixture(() => json({ ticks: [], count: 0 }));
  await f.company.listTicks("co-explicit");
  assert.match(f.calls[0].url, /companies\/co-explicit\//);
});

// --------------------------------------------------------------------------- //
// Honesty inversions
// --------------------------------------------------------------------------- //

test("an unreadable company list throws instead of reading as none", async () => {
  const f = fixture(() => json({ unexpected: true }));
  await assert.rejects(() => f.company.listCompanies(), /unreadable company list/);
});

test("a failed read is never rendered as an empty portfolio", async () => {
  const f = fixture(() => json({ detail: "Gateway unavailable" }, 503));
  await assert.rejects(() => f.company.listCompanies(), /Gateway unavailable/);
});

test("an unreachable work board keeps reachable=false rather than zero counts", async () => {
  const f = fixture(() =>
    json({
      company: { ...COMPANY, roles: [], units: [], employments: [], accountabilities: [], objectives: [], projects: [], work_items: [], groups: [], schedules: [], approvals: [], loop_policy: {}, cost: {} },
      metrics: { headcount: 0, health_percent: null, health_basis: "unmeasured", measured_at: null },
      health: { health_percent: null, basis: "unmeasured", reason: "The work board is not readable." },
      work: { reachable: false, error: "Kanban store unreachable." },
      workforce: { employment_count: 0, missing_profile_count: 0, missing_profiles: [], unassigned_bot_count: 0, unassigned_bots: [] },
      cost: { spent_today_usd: null, budget_daily_usd: null, budget_total_usd: null, utilization_percent: null, basis: "unmeasured", measured_run_count: 0, last_measured_at: null },
      loop: { enabled: false, state: "draft", consecutive_no_progress_ticks: 0, recent_outcomes: [], last_ritual_at: {} },
      rituals: { due: [], ran: [], last_ritual_at: {}, cadence: {} },
      approvals: [],
      invariants_ok: true,
      invariant_problems: [],
    }),
  );
  const o = await f.company.getCompany("co-abc123");
  assert.equal(o.work.reachable, false);
  assert.equal(o.work.error, "Kanban store unreachable.");
  assert.equal(o.metrics.health_percent, null);
  // The formatter must not turn an unmeasured figure into a 0%.
  assert.equal(f.company.healthLabel(o.health), "Not measured — The work board is not readable.");
  assert.equal(f.company.healthTone(o.health), "gray");
});

test("an unmeasured spend renders as not measured, never $0.0000", () => {
  const f = fixture(() => json({}));
  assert.equal(f.company.usd(null), "not measured");
  assert.equal(f.company.usd(undefined), "not measured");
  assert.equal(f.company.usd(0), "$0.0000");
  assert.equal(f.company.usd(1.5), "$1.5000");
});

test("an absent tick cost stays null rather than becoming zero", async () => {
  const f = fixture(() =>
    json({
      ticks: [
        { tick_id: "t-1", outcome: "dispatched", reason: "x", cost_usd: null, cost_basis: "unmeasured", dispatched: [{}] },
        { tick_id: "t-2", outcome: "idle", reason: "y", cost_usd: 0.0, cost_basis: "measured", dispatched: [] },
      ],
    }),
  );
  const ticks = await f.company.listTicks("co-abc123");
  // A dispatching tick has no token meter, so it must not claim a cost.
  assert.equal(ticks[0].cost_usd, null);
  assert.equal(ticks[0].cost_basis, "unmeasured");
  // An idle tick genuinely cost nothing, and says so.
  assert.equal(ticks[1].cost_usd, 0.0);
  assert.equal(ticks[1].cost_basis, "measured");
});

test("a missing tick duration stays null rather than reading as 0ms", async () => {
  const f = fixture(() => json({ ticks: [{ tick_id: "t-1", outcome: "error" }] }));
  const ticks = await f.company.listTicks("co-abc123");
  assert.equal(ticks[0].duration_ms, null);
});

test("tick tones distinguish a healthy idle from a refusal", () => {
  const f = fixture(() => json({}));
  assert.equal(f.company.tickTone("dispatched"), "green");
  assert.equal(f.company.tickTone("idle"), "blue");
  assert.equal(f.company.tickTone("budget_exhausted"), "amber");
  assert.equal(f.company.tickTone("error"), "red");
  assert.equal(f.company.tickTone("something-new"), "gray");
});

test("absent boolean flags do not become a confident true", async () => {
  const f = fixture(() => json({ company: COMPANY, work: {}, workforce: {}, health: {}, cost: {}, loop: {}, rituals: {}, metrics: {}, approvals: [] }));
  const o = await f.company.getCompany("co-abc123");
  // A payload with no invariants_ok must not claim the invariants are fine.
  assert.equal(o.invariants_ok, false);
  assert.equal(o.health.health_percent, null);
});

test("an unknown server enum is preserved verbatim", async () => {
  const f = fixture(() => json({ ticks: [{ tick_id: "t-1", outcome: "brand-new-outcome", reason: "" }] }));
  const ticks = await f.company.listTicks("co-abc123");
  assert.equal(ticks[0].outcome, "brand-new-outcome");
});

test("archetypes reject on failure rather than reading as none", async () => {
  const f = fixture(() => json({ detail: "nope" }, 500));
  await assert.rejects(() => f.company.listArchetypes(), /nope/);
});

test("preview posts a blueprint without creating a company", async () => {
  const f = fixture(() => json({ blueprint: { headcount: 10 }, employees: [] }));
  const out = await f.company.previewBlueprint({ prompt: "build a trading desk", archetype: "custom" });
  assert.equal(f.calls[0].url, "/api/companies/preview");
  assert.equal(f.calls[0].method, "POST");
  assert.equal(JSON.parse(f.calls[0].body).archetype, "custom");
  assert.ok(out.blueprint);
});