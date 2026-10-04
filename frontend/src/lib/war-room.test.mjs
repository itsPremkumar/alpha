/**
 * War Room client: the pure helpers, which carry the honesty rules.
 *
 * These are the functions that decide what a reader is told. A regression here
 * is not a cosmetic bug: it would let a failed quorum, a preserved minority
 * view, or a tainted contribution disappear from the screen.
 *
 * Network calls are out of scope. The backend contract is pinned by
 * backend/tests/test_war_rooms_api.py.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  agreeingMembers,
  dissentEntries,
  formatDuration,
  statusTone,
  summariseRun,
  taintedReceipts,
  unmetStages,
  VERIFICATION_BADGE,
} from "./war-room-model.ts";

// The client contract lives in a separate file (it imports @/lib/http, which
// plain Node cannot resolve), so its routes and verbs are pinned by reading the
// source. This is the same technique branding.test.mjs uses.
const clientSource = readFileSync(new URL("./war-room.ts", import.meta.url), "utf8");

test("the client calls only real war-room routes", () => {
  for (const path of [
    "/api/war-rooms",
    "/api/war-rooms/analytics",
    "/api/war-rooms/trigger-policy",
    "/api/war-rooms/evaluate",
    "/api/war-rooms/${encodeURIComponent(runId)}",
    "/api/war-rooms/${encodeURIComponent(runId)}/transcript",
  ]) {
    assert.ok(clientSource.includes(path), `missing route: ${path}`);
  }
});

test("only evaluate is a POST, and it goes through send not get", () => {
  assert.match(clientSource, /send<\{ ok: boolean; decision: TriggerDecision \}>\("\/api\/war-rooms\/evaluate", "POST"/);
  // The room is opened by the model-facing tool, never by the UI.
  assert.doesNotMatch(clientSource, /method:\s*"POST"[\s\S]{0,80}war-rooms\/open/);
  assert.ok(!clientSource.includes("/api/war-rooms/open"), "the UI must not be able to open a room");
});

test("a failed read is not turned into an empty list", () => {
  // `get`/`send` reject; the client must not catch and default to [].
  assert.doesNotMatch(clientSource, /catch[\s\S]{0,120}return \[\]/);
  assert.doesNotMatch(clientSource, /\.catch\(\(\) => \[\]\)/);
});

test("the view id is registered in all three required places", () => {
  const views = readFileSync(new URL("./workspace-view.ts", import.meta.url), "utf8");
  const nav = readFileSync(new URL("../components/NavTabs.tsx", import.meta.url), "utf8");
  const chat = readFileSync(new URL("../components/ChatView.tsx", import.meta.url), "utf8");
  for (const [name, source] of [
    ["workspace-view.ts", views],
    ["NavTabs.tsx", nav],
    ["ChatView.tsx", chat],
  ]) {
    assert.ok(source.includes('"deliberation"'), `deliberation is not registered in ${name}`);
  }
  // The section must load on demand, not be eagerly pulled into the bundle.
  // `next/dynamic` replaced `React.lazy` here: a lazy section cannot be
  // server-rendered, so the server shipped the Suspense fallback and React
  // discarded the whole server tree on hydration. Both forms are on-demand
  // loads; only `dynamic` participates in the App Router module graph.
  assert.match(chat, /dynamic\(\(\) => import\("@\/components\/sections\/WarRoomRunsSection"\)/);
  assert.doesNotMatch(
    chat,
    /lazy\(\(\) => import\("@\/components\/sections\/WarRoomRunsSection"\)/,
    "a React.lazy section cannot be server-rendered and reintroduces the hydration mismatch",
  );
  assert.match(chat, /view === "deliberation"/);
});

test("the enterprise war room tab is not hijacked", () => {
  const nav = readFileSync(new URL("../components/NavTabs.tsx", import.meta.url), "utf8");
  // The pre-existing `warroom` view belongs to the enterprise platform; the
  // deliberation surface gets its own id rather than overwriting it.
  assert.match(nav, /id: "warroom", label: "War Room"/);
  assert.match(nav, /id: "deliberation", label: "Deliberation"/);
});

function quorum(overrides = {}) {
  return {
    stage: "positions",
    policy: "majority",
    required_votes: 2,
    eligible_voters: 2,
    agree: 2,
    disagree: 0,
    amend: 0,
    total_votes: 2,
    passed: true,
    proposal_id: "p1",
    engine_status: "approved",
    engine_agrees_with_policy: true,
    consensus: {
      agreeing: ["a", "b"],
      dissenting: [],
      isolated: [],
      unparsed: [],
      agreed_claims: ["adopt postgres"],
      dissent_text: {},
      correlated_groups: {},
      correlation_adjusted: false,
      agreement_ratio: 1,
    },
    collusion: { flagged: false, similarity: 0, reasons: [], redundant_members: [] },
    taint_findings: [],
    human_line: "agree=2",
    ...overrides,
  };
}

function run(overrides = {}) {
  return {
    run_id: "wrun_1",
    room: "war-room",
    status: "succeeded",
    strategy: "council",
    strategy_rationale: "high-impact decision",
    synthesis: "adopt postgres",
    error: "",
    failure_reason: "",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:00:05Z",
    killed_by: "",
    stages: [{ name: "positions", status: "completed", budget: {}, receipts: [], quorum: quorum(), synthesis: "", error: "", started_at: "", finished_at: "" }],
    minority_dissent: {},
    taint_findings: [],
    tainted: false,
    final_quorum: null,
    transcript_errors: [],
    receipt_loss: [],
    ...overrides,
  };
}

function receipt(overrides = {}) {
  return {
    stage: "positions",
    participant: "a",
    status: "contributed",
    output: "",
    error: "",
    error_type: "",
    duration_ms: 0,
    seq: 1,
    persistence_error: "",
    claims: [],
    self_confidence: null,
    taint: "clean",
    taint_categories: [],
    model_id: "",
    tainted: false,
    ...overrides,
  };
}

test("statusTone maps every run status to a badge tone", () => {
  assert.equal(statusTone("succeeded"), "ok");
  assert.equal(statusTone("partial"), "warn");
  assert.equal(statusTone("failed"), "bad");
  assert.equal(statusTone("timeout"), "bad");
  assert.equal(statusTone("cancelled"), "idle");
  assert.equal(statusTone("running"), "idle");
  assert.equal(statusTone("unreadable"), "idle");
});

test("summariseRun reports a clean agreement with the strategy", () => {
  const line = summariseRun(run());
  assert.match(line, /succeeded/);
  assert.match(line, /2\/2 agreed/);
  assert.match(line, /council/);
});

test("summariseRun surfaces dissent and isolation rather than only the winner", () => {
  const dissenting = run({
    stages: [
      {
        name: "positions",
        status: "completed",
        budget: {},
        receipts: [],
        synthesis: "",
        error: "",
        started_at: "",
        finished_at: "",
        quorum: quorum({
          agree: 1,
          required_votes: 2,
          passed: false,
          consensus: {
            agreeing: ["a"],
            dissenting: ["b"],
            isolated: ["c"],
            unparsed: [],
            agreed_claims: ["adopt postgres"],
            dissent_text: { b: "adopt mysql" },
            correlated_groups: {},
            correlation_adjusted: false,
            agreement_ratio: 0.33,
          },
        }),
      },
    ],
  });
  const line = summariseRun(dissenting);
  assert.match(line, /1\/2 agreed/);
  assert.match(line, /1 dissenting/);
  assert.match(line, /1 isolated/);
});

test("summariseRun labels a tainted run instead of presenting a clean pass", () => {
  assert.match(summariseRun(run({ tainted: true })), /tainted/);
});

test("summariseRun discloses an unreadable record instead of inventing a verdict", () => {
  const line = summariseRun({ status: "unreadable", error: "JSONDecodeError", stages: [] });
  assert.match(line, /unreadable/);
  assert.match(line, /JSONDecodeError/);
  assert.doesNotMatch(line, /agreed/);
});

test("summariseRun never calls a run verified", () => {
  const line = summariseRun(run({ status: "succeeded" }));
  assert.doesNotMatch(line, /verified/i);
});

test("summariseRun says so when no quorum was recorded at all", () => {
  assert.match(summariseRun(run({ stages: [] })), /no quorum recorded/);
});

test("unmetStages lists every stage whose quorum did not pass", () => {
  const withFailure = run({
    stages: [
      { name: "positions", status: "completed", budget: {}, receipts: [], quorum: quorum(), synthesis: "", error: "", started_at: "", finished_at: "" },
      {
        name: "cross_exam",
        status: "failed",
        budget: {},
        receipts: [],
        synthesis: "",
        error: "",
        started_at: "",
        finished_at: "",
        quorum: quorum({ passed: false, agree: 0 }),
      },
    ],
  });
  assert.deepEqual(unmetStages(withFailure), ["cross_exam"]);
  assert.deepEqual(unmetStages(run()), []);
});

test("unmetStages tolerates a stage with no quorum", () => {
  const synthesisOnly = run({
    stages: [{ name: "synthesis", status: "completed", budget: {}, receipts: [], quorum: null, synthesis: "x", error: "", started_at: "", finished_at: "" }],
  });
  assert.deepEqual(unmetStages(synthesisOnly), []);
});

test("taintedReceipts collects flagged contributions across every stage", () => {
  const contaminated = run({
    tainted: true,
    stages: [
      {
        name: "positions",
        status: "completed",
        budget: {},
        synthesis: "",
        error: "",
        started_at: "",
        finished_at: "",
        receipts: [receipt({ participant: "a" }), receipt({ participant: "evil", taint: "infected", tainted: true })],
        quorum: quorum(),
      },
      {
        name: "cross_exam",
        status: "completed",
        budget: {},
        synthesis: "",
        error: "",
        started_at: "",
        finished_at: "",
        receipts: [receipt({ participant: "b", stage: "cross_exam" })],
        quorum: quorum({ stage: "cross_exam" }),
      },
    ],
  });
  const flagged = taintedReceipts(contaminated);
  assert.equal(flagged.length, 1);
  assert.equal(flagged[0].participant, "evil");
  assert.equal(taintedReceipts(run()).length, 0);
});

test("agreeingMembers de-duplicates across stages and sorts", () => {
  const multi = run({
    stages: [
      { name: "positions", status: "completed", budget: {}, receipts: [], synthesis: "", error: "", started_at: "", finished_at: "", quorum: quorum() },
      {
        name: "cross_exam",
        status: "completed",
        budget: {},
        receipts: [],
        synthesis: "",
        error: "",
        started_at: "",
        finished_at: "",
        quorum: quorum({
          stage: "cross_exam",
          consensus: { ...quorum().consensus, agreeing: ["b", "c"] },
        }),
      },
    ],
  });
  assert.deepEqual(agreeingMembers(multi), ["a", "b", "c"]);
  assert.deepEqual(agreeingMembers(run()), ["a", "b"]);
});

test("dissentEntries flattens every preserved non-agreeing view", () => {
  const withDissent = run({
    minority_dissent: {
      positions: { bob: "adopt mysql" },
      cross_exam: { carol: "the queue will drop events" },
    },
  });
  const entries = dissentEntries(withDissent);
  assert.equal(entries.length, 2);
  assert.deepEqual(entries[0], { stage: "positions", member: "bob", text: "adopt mysql" });
  assert.deepEqual(dissentEntries(run()), []);
});

test("formatDuration handles sub-second, second, minute and nonsense input", () => {
  assert.equal(formatDuration(0), "0ms");
  assert.equal(formatDuration(420), "420ms");
  assert.equal(formatDuration(1500), "1.5s");
  assert.equal(formatDuration(95_000), "1m 35s");
  assert.equal(formatDuration(-1), "-");
  assert.equal(formatDuration(Number.NaN), "-");
});

test("every verification label has a badge colour", () => {
  for (const key of ["consensus_supported", "consensus_degraded", "tainted", "unverified"]) {
    assert.ok(VERIFICATION_BADGE[key], `missing badge colour for ${key}`);
  }
});
