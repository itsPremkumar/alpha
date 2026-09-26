import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { moduleUrl } from "./test-modules.mjs";

const { parseExecutionModeStatus } = await import(moduleUrl("plan"));
const { parseFleetWorkers } = await import(moduleUrl("supervision"));
const { parseLiveSubagents } = await import(moduleUrl("subagents"));

const panelSource = readFileSync(new URL("../components/AgentStatusPanel.tsx", import.meta.url), "utf8");
const dashboardSource = readFileSync(new URL("../components/sections/DashboardSection.tsx", import.meta.url), "utf8");

test("execution mode: server values pass through verbatim, defaults stay empty", () => {
  const parsed = parseExecutionModeStatus({
    mode: "work.normal",
    source: "default",
    note: "default: no persisted mode found",
    persisted: false,
    updated_at: "",
    actor: "",
    path: "/runtime/execution_mode.json",
  });
  assert.deepEqual(parsed, {
    mode: "work.normal",
    source: "default",
    note: "default: no persisted mode found",
    persisted: false,
    updated_at: "",
    actor: "",
    path: "/runtime/execution_mode.json",
  });
});

test("execution mode: persisted and context-overridden records keep their real provenance", () => {
  const persisted = parseExecutionModeStatus({
    mode: "code.plan",
    source: "persisted",
    note: "set via set_mode",
    persisted: true,
    updated_at: "2026-09-23T10:00:00+00:00",
    actor: "tester",
    path: "/runtime/execution_mode.json",
  });
  assert.equal(persisted.mode, "code.plan");
  assert.equal(persisted.persisted, true);
  assert.equal(persisted.updated_at, "2026-09-23T10:00:00+00:00");
  assert.equal(persisted.actor, "tester");

  const overridden = parseExecutionModeStatus({
    mode: "work.plan",
    source: "context",
    note: "context override active",
    persisted: false,
    updated_at: "",
    actor: "",
    path: "",
  });
  assert.equal(overridden.source, "context");
});

test("execution mode: an unknown server value is reported as-is, never coerced to a default", () => {
  assert.equal(parseExecutionModeStatus({ mode: "banana", source: "default", note: "", persisted: false }).mode, "banana");
});

test("execution mode: unusable payloads throw so the UI can show 'unavailable'", () => {
  assert.throws(() => parseExecutionModeStatus(null));
  assert.throws(() => parseExecutionModeStatus("work.normal"));
  assert.throws(() => parseExecutionModeStatus(["work.normal"]));
  assert.throws(() => parseExecutionModeStatus({ source: "default" }));
  assert.throws(() => parseExecutionModeStatus({ mode: "   " }));
  assert.throws(() => parseExecutionModeStatus({ mode: 42 }));
});

test("fleet: keyed map parses into workers with absent metrics staying null", () => {
  const workers = parseFleetWorkers({
    w1: {
      worker_id: "w1",
      status: "busy",
      last_heartbeat_elapsed_seconds: 1.25,
      progress_percent: 40.5,
      current_action: "reading",
      active_task_id: "task-1",
      unresolved_anomalies_count: 0,
    },
    w2: { worker_id: "w2", status: "idle" },
  });
  assert.equal(workers.length, 2);
  assert.deepEqual(workers[0], {
    worker_id: "w1",
    status: "busy",
    last_heartbeat_elapsed_seconds: 1.25,
    progress_percent: 40.5,
    current_action: "reading",
    active_task_id: "task-1",
    unresolved_anomalies_count: 0,
  });
  // Fields the server did not send stay null — they are not invented as zeros.
  assert.equal(workers[1].last_heartbeat_elapsed_seconds, null);
  assert.equal(workers[1].progress_percent, null);
  assert.equal(workers[1].unresolved_anomalies_count, null);
});

test("fleet: an empty map is a genuinely empty fleet, envelopes and arrays are accepted", () => {
  assert.deepEqual(parseFleetWorkers({}), []);
  assert.deepEqual(
    parseFleetWorkers([{ worker_id: "w9", status: "stalled" }]).map((w) => [w.worker_id, w.status]),
    [["w9", "stalled"]],
  );
  assert.deepEqual(
    parseFleetWorkers({ workers: [{ worker_id: "w7", status: "failed" }] }).map((w) => w.worker_id),
    ["w7"],
  );
});

test("fleet: unreadable payloads throw instead of reading as an empty fleet", () => {
  assert.throws(() => parseFleetWorkers("ok"));
  assert.throws(() => parseFleetWorkers(7));
  assert.throws(() => parseFleetWorkers({ w1: "not-a-record" }));
  assert.throws(() => parseFleetWorkers([42]));
});

test("subagents: bare arrays and envelopes map id, status and parent", () => {
  const bare = parseLiveSubagents([
    { subagent_id: "sub-1", role: "researcher", objective: "dig", status: "running", parent_agent_id: "ui" },
  ]);
  assert.deepEqual(bare, [{ id: "sub-1", role: "researcher", objective: "dig", status: "running", parent: "ui" }]);

  const enveloped = parseLiveSubagents({ subagents: [{ id: "sub-2", status: "completed", parent: "lead" }] });
  assert.deepEqual(enveloped, [{ id: "sub-2", role: "", objective: "", status: "completed", parent: "lead" }]);
});

test("subagents: unreadable payloads throw so failures are not shown as 'no subagents'", () => {
  assert.throws(() => parseLiveSubagents("nope"));
  assert.throws(() => parseLiveSubagents({ unexpected: 1 }));
  assert.throws(() => parseLiveSubagents(null));
});

test("panel honesty: no guessed mode, and an explicit 'unavailable' state while in flight", () => {
  // The component must never contain a concrete mode string to fall back on.
  assert.doesNotMatch(panelSource, /(work|code)\.(normal|plan)/);
  assert.match(panelSource, /unavailable/);
  // Every block starts in the loading phase rather than with placeholder data.
  const initialLoading = panelSource.match(/\{ phase: "loading" \}/g) ?? [];
  assert.ok(initialLoading.length >= 3, `expected >=3 loading initial states, found ${initialLoading.length}`);
  // Each block is fed by a real, strict endpoint call.
  assert.match(panelSource, /fetchExecutionMode/);
  assert.match(panelSource, /fetchFleetWorkers/);
  assert.match(panelSource, /fetchLiveSubagentsStrict/);
  // No console output from this surface (no new console errors).
  assert.doesNotMatch(panelSource, /console\./);
});

test("dashboard: renders the agent status panel and reports watchdog failures honestly", () => {
  assert.match(dashboardSource, /<AgentStatusPanel \/>/);
  assert.match(dashboardSource, /Watchdog status unavailable/);
  // The old silent failure path must not come back.
  assert.doesNotMatch(dashboardSource, /\.catch\(\(\) => setLoaded\(true\)\)/);
  assert.doesNotMatch(dashboardSource, /"all healthy"/);
});
