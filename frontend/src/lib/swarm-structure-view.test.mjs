// swarm-structure-view.test.mjs — the swarm panel's honesty rules.
//
// Found on 2026-10-06. `frontend/src/lib/teamops.ts` already exported
// `swarmDetails()` and `swarmMetrics()` against `GET /api/swarms/{id}` and
// `.../metrics`, and NOTHING imported either of them. Measured live on
// `swm-db45b4f8`: 5 tasks carrying 35 fields each, plus `metrics.team`,
// `metrics.leader_election` and a 15-type event vocabulary across the 20 swarms
// on this install. The list rendered one line per swarm and the rest of a swarm
// was unread.
//
// The payload this module reads came from `backend/scripts/probe_swarm_task_shapes.py`,
// which is why the fixtures below use `state` and not `status`: the task object
// has NO `status` key, so a view that read `task.status` would render a full DAG
// of blank rows.
//
// Each test names the payload that would make a plausible wrong word appear.

import { readFileSync } from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;

const code = ts.transpileModule(read("./swarm-structure-view.ts"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;

// This module imports nothing, so the transpiled output is self-contained.
const { swarmTaskRows, swarmLeaderView, swarmTeamView, swarmEventRows, swarmStructureView, measuredCount, isDisplayableSummary } =
  await import(toDataUrl(code));

// ---------------------------------------------------------------------------
// Fixtures shaped exactly like the live payload
// ---------------------------------------------------------------------------

/** A real completed map task, trimmed to the fields the view reads. */
const mapTask = (over = {}) => ({
  task_id: "task-map-1",
  objective: "Process target item (1/4): backend/utils/time.py",
  dependencies: [],
  assigned_worker: null,
  worker_type: "ephemeral",
  model_override: null,
  state: "completed",
  result_summary: "time.to_iso has one branch that overflows on year 9999.",
  lease_expires_at: null,
  started_at: "2026-10-05T06:15:38.134698+00:00",
  completed_at: "2026-10-05T06:15:51.625582+00:00",
  duration_seconds: 13.490883588790894,
  attempts: 1,
  max_attempts: 3,
  backup_worker_launched: false,
  error_message: null,
  capability_tags: ["backend"],
  priority: 0,
  token_usage: { input_tokens: 234, output_tokens: 50, total_tokens: 284 },
  tool_calls: 1,
  acceptance_status: "not_required",
  estimated_seconds: 15.0,
  ...over,
});

/** The real reduce task: a permanent bot, four dependencies. */
const reduceTask = (over = {}) =>
  mapTask({
    task_id: "task-reduce",
    objective: "Reconcile, deduplicate, rank evidence",
    dependencies: ["task-map-1", "task-map-2", "task-map-3", "task-map-4"],
    assigned_worker: "architect",
    worker_type: "permanent_bot",
    state: "running",
    ...over,
  });

const realDetail = () => ({
  swarm_id: "swm-db45b4f8",
  status: "completed",
  mode: "map_reduce",
  tasks: {
    "task-map-1": mapTask(),
    "task-map-2": mapTask({ task_id: "task-map-2", state: "pending" }),
    "task-reduce": reduceTask(),
  },
  metrics: {
    team: {
      roster_registered: false,
      roster_source: "",
      assigned: 1,
      unassigned: 2,
      load: {},
      decisions: [],
      unassigned_detail: [],
    },
    leader_election: {
      leader: "architect",
      score: 0.775,
      method: "capability-load-reputation-v1",
      candidates: [{ bot: "architect", score: 0.775 }],
      reason: "highest deterministic fitness score among eligible candidates",
    },
  },
});

const realEvents = () => [
  {
    event_id: "evt-70a6e473",
    swarm_id: "swm-db45b4f8",
    event_type: "SWARM_CREATED",
    sequence: 1,
    timestamp: "2026-10-05T06:15:33Z",
    task_id: null,
    worker: null,
    details: { goal: "Rank four modules", mode: "map_reduce", tasks_count: 5 },
  },
  {
    event_id: "evt-2",
    swarm_id: "swm-db45b4f8",
    event_type: "TASK_DISPATCHED",
    sequence: 2,
    timestamp: "2026-10-05T06:15:38Z",
    task_id: "task-map-1",
    worker: null,
    details: {},
  },
  {
    event_id: "evt-3",
    swarm_id: "swm-db45b4f8",
    event_type: "TASK_COMPLETED",
    sequence: 3,
    timestamp: "2026-10-05T06:15:51Z",
    task_id: "task-map-1",
    worker: null,
    details: {},
  },
];

// ---------------------------------------------------------------------------
// The field name that is easy to get wrong
// ---------------------------------------------------------------------------

test("the task state comes from `state`, and a `status` key is not one", () => {
  // The live payload has no `status`. A view reading `task.status` returns
  // undefined for EVERY task, which would render a full DAG of blank rows and
  // read as "no state information" — true, and completely misleading.
  const rows = swarmTaskRows({ "task-map-1": mapTask() });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].state, "completed");
  assert.notEqual(rows[0].state, undefined);
});

test("a task carrying only `status` reports no state rather than borrowing it", () => {
  const rows = swarmTaskRows({ t: { task_id: "t", status: "running", state: undefined } });
  assert.equal(rows[0].state, null);
  assert.equal(rows[0].stateNote, "state not reported by the server");
  assert.notEqual(rows[0].stateNote, "");
});

// ---------------------------------------------------------------------------
// Absence must never become a value
// ---------------------------------------------------------------------------

test("an unassigned task says unassigned, never the leader's name", () => {
  const rows = swarmTaskRows({ t: mapTask({ assigned_worker: null, worker_type: "permanent_bot" }) });
  assert.equal(rows[0].worker, null);
  assert.match(rows[0].workerNote, /unassigned/);
  assert.doesNotMatch(rows[0].workerNote, /architect/);
});

test("an ephemeral worker explains why no bot name is expected", () => {
  // Four of the five tasks in `swm-db45b4f8` are ephemeral with
  // `assigned_worker: null`. That is the design, not a gap, and "unassigned —
  // no worker has claimed this" would read as a fault.
  const rows = swarmTaskRows({ t: mapTask({ assigned_worker: null, worker_type: "ephemeral" }) });
  assert.match(rows[0].workerNote, /ephemeral/);
});

test("a missing duration reads as unreported, never 0s", () => {
  const rows = swarmTaskRows({ t: mapTask({ duration_seconds: null }) });
  assert.equal(rows[0].durationSeconds, null);
  assert.match(rows[0].durationNote, /no duration reported/);
  assert.doesNotMatch(rows[0].durationNote, /\b0s\b/);
});

test("a real zero duration is a measurement and survives", () => {
  // The opposite error: collapsing a genuine 0 into "not reported" would hide a
  // task that completed inside the clock resolution.
  const rows = swarmTaskRows({ t: mapTask({ duration_seconds: 0 }) });
  assert.equal(rows[0].durationSeconds, 0);
  assert.equal(rows[0].durationNote, "");
});

test("absent token usage reads as unreported, never 0 tokens", () => {
  const rows = swarmTaskRows({ t: mapTask({ token_usage: undefined }) });
  assert.equal(rows[0].totalTokens, null);
  assert.match(rows[0].tokenNote, /tokens not reported/);
  assert.doesNotMatch(rows[0].tokenNote, /\b0\b/);
});

test("a real zero token count is a measurement and survives", () => {
  const rows = swarmTaskRows({ t: mapTask({ token_usage: { total_tokens: 0 } }) });
  assert.equal(rows[0].totalTokens, 0);
  assert.equal(rows[0].tokenNote, "");
});

test("a NaN or string counter is not a measurement", () => {
  for (const bad of [NaN, "13", null, Infinity, -1]) {
    assert.equal(measuredCount(bad), null, `${String(bad)} must not read as a count`);
  }
});

// ---------------------------------------------------------------------------
// Structure: dependencies are the reason a swarm is a swarm
// ---------------------------------------------------------------------------

test("an empty dependency list is a root task, not a wait", () => {
  const rows = swarmTaskRows({ t: mapTask({ dependencies: [] }) });
  assert.deepEqual(rows[0].dependsOn, []);
  assert.match(rows[0].dependencyNote, /root task/);
  assert.doesNotMatch(rows[0].dependencyNote, /wait/i);
});

test("a dependent task names what it waits for", () => {
  const rows = swarmTaskRows({ t: reduceTask() });
  assert.equal(rows[0].dependsOn.length, 4);
  assert.match(rows[0].dependencyNote, /waits for task-map-1/);
  assert.match(rows[0].dependencyNote, /task-map-4/);
});

test("a non-array dependencies value yields no claims rather than throwing", () => {
  const rows = swarmTaskRows({ t: mapTask({ dependencies: "task-map-1" }) });
  assert.deepEqual(rows[0].dependsOn, []);
});

// ---------------------------------------------------------------------------
// Unknown enums stay unknown
// ---------------------------------------------------------------------------

test("an unrecognised task state renders verbatim and neutrally", () => {
  const rows = swarmTaskRows({ t: mapTask({ state: "quarantined" }) });
  assert.equal(rows[0].state, "quarantined");
  assert.equal(rows[0].stateTone, "gray");
  assert.equal(rows[0].stateNote, "", "a known state needs no absence note");
});

test("the five states this build knows each get their own tone", () => {
  const tones = {
    completed: "green",
    running: "blue",
    failed: "red",
    cancelled: "amber",
    pending: "gray",
  };
  for (const [state, tone] of Object.entries(tones)) {
    assert.equal(swarmTaskRows({ t: mapTask({ state }) })[0].stateTone, tone, state);
  }
});

// ---------------------------------------------------------------------------
// The result summary: the failure this whole panel could repeat
// ---------------------------------------------------------------------------

test("raw model output is not presented as the worker's answer", () => {
  // THE live value. `swm-db45b4f8`'s `task-map-1.result_summary` on this install
  // is `]<]minimax[>[<tool_call>ly]<]...` — model channel markup, written by the
  // provider rather than composed for a person. Rendering it as a summary repeats
  // the absent-as-zero defect one layer out: the UI would confidently quote
  // noise as the finding.
  const noise = "]<]\u200bminimax[>[<\u200btool_call>ly]<]\u200bminimax[>[</tool_call>";
  const rows = swarmTaskRows({ t: mapTask({ result_summary: noise }) });
  assert.equal(rows[0].summary, null);
  assert.match(rows[0].summaryNote, /raw model output/);
});

test("a human-written summary is kept verbatim", () => {
  const rows = swarmTaskRows({ t: mapTask() });
  assert.match(rows[0].summary, /overflows on year 9999/);
  assert.equal(rows[0].summaryNote, "");
});

test("an absent result is 'no result recorded', distinct from noise", () => {
  const absent = swarmTaskRows({ t: mapTask({ result_summary: undefined }) });
  assert.match(absent[0].summaryNote, /no result recorded/);
  // Whitespace-only is the SAME fact as absent: no text was stored. The
  // distinction that matters is against NOISE, not against a blank string.
  const blank = swarmTaskRows({ t: mapTask({ result_summary: "   " }) })[0];
  assert.equal(blank.summary, null);
  assert.match(blank.summaryNote, /no result recorded/);
  // And noise must not collapse into the same sentence as silence.
  const noise = swarmTaskRows({ t: mapTask({ result_summary: "<tool_call>x</tool_call>" }) })[0];
  assert.match(noise.summaryNote, /raw model output/);
  assert.notEqual(noise.summaryNote, absent[0].summaryNote);
});

test("an empty string is not displayable", () => {
  assert.equal(isDisplayableSummary("   "), false);
  assert.equal(isDisplayableSummary(42), false);
  assert.equal(isDisplayableSummary(null), false);
  assert.equal(isDisplayableSummary("a real finding"), true);
});

// ---------------------------------------------------------------------------
// Team and leader
// ---------------------------------------------------------------------------

test("a swarm with no metrics reports that, not zero unassigned", () => {
  const team = swarmTeamView({ tasks: {} });
  assert.equal(team.assigned, null);
  assert.equal(team.unassigned, null);
  assert.match(team.note, /not reported/);
  assert.doesNotMatch(team.note, /\b0\b/);
});

test("real team counts are reported", () => {
  const team = swarmTeamView(realDetail());
  assert.equal(team.assigned, 1);
  assert.equal(team.unassigned, 2);
  assert.equal(team.note, "");
});

test("an election that named no leader is a reported outcome, not a missing field", () => {
  const leader = swarmLeaderView({
    metrics: { leader_election: { leader: null, score: null, method: null, reason: null } },
  });
  assert.equal(leader.leader, null);
  assert.match(leader.note, /named no leader/);
  assert.doesNotMatch(leader.note, /not reported/);
});

test("no election at all reads differently from an election with no winner", () => {
  assert.match(swarmLeaderView({}).note, /no leader election reported/);
});

test("the elected leader carries its method and reason", () => {
  const leader = swarmLeaderView(realDetail());
  assert.equal(leader.leader, "architect");
  assert.equal(leader.method, "capability-load-reputation-v1");
  assert.equal(leader.score, 0.775);
  assert.match(leader.reason, /highest deterministic fitness/);
  assert.equal(leader.note, "");
});

// ---------------------------------------------------------------------------
// The event timeline
// ---------------------------------------------------------------------------

test("an unknown event type is shown, never dropped", () => {
  // Fifteen types exist across the 20 swarms on this install. A timeline that
  // filters to a known list loses evidence, and its absence reads as "nothing
  // happened".
  const rows = swarmEventRows([{ event_type: "WORKER_QUARANTINED_BY_CONSENSUS", sequence: 9 }]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].eventType, "WORKER_QUARANTINED_BY_CONSENSUS");
});

test("events sort by sequence, oldest first", () => {
  const rows = swarmEventRows([
    { event_type: "SWARM_COMPLETED", sequence: 7 },
    { event_type: "SWARM_CREATED", sequence: 1 },
    { event_type: "TASK_DISPATCHED", sequence: 3 },
  ]);
  assert.deepEqual(rows.map((r) => r.sequence), [1, 3, 7]);
  assert.equal(rows[0].eventType, "SWARM_CREATED");
});

test("an event with no sequence is kept, and kept last", () => {
  // Dropping it would look like the event never happened.
  const rows = swarmEventRows([
    { event_type: "MYSTERY" },
    { event_type: "SWARM_CREATED", sequence: 1 },
  ]);
  assert.equal(rows.length, 2);
  assert.equal(rows[1].eventType, "MYSTERY");
});

test("an unreadable event log says so rather than showing an empty timeline", () => {
  const view = swarmStructureView(realDetail(), undefined);
  assert.match(view.eventReadNote, /could not be read/);
});

test("an empty event log is distinguished from an unreadable one", () => {
  const view = swarmStructureView(realDetail(), []);
  assert.match(view.eventReadNote, /no events for this swarm/);
  assert.notEqual(view.eventReadNote, swarmStructureView(realDetail(), undefined).eventReadNote);
});

// ---------------------------------------------------------------------------
// The panel as a whole
// ---------------------------------------------------------------------------

test("the real payload renders three task rows with their real structure", () => {
  const view = swarmStructureView(realDetail(), realEvents());
  assert.equal(view.tasks.length, 3);
  assert.equal(view.taskReadNote, null);
  assert.equal(view.events.length, 3);
  const reduce = view.tasks.find((t) => t.taskId === "task-reduce");
  assert.equal(reduce.worker, "architect");
  assert.equal(reduce.workerType, "permanent_bot");
  assert.equal(reduce.dependsOn.length, 4);
});

test("a swarm with no tasks says the plan is empty rather than rendering nothing", () => {
  const view = swarmStructureView({ tasks: {} }, []);
  assert.equal(view.tasks.length, 0);
  assert.match(view.taskReadNote, /no tasks/);
});

test("a detail payload that is not an object degrades instead of throwing", () => {
  for (const bad of [null, undefined, "nope", 7, []]) {
    const view = swarmStructureView(bad, bad);
    assert.equal(view.tasks.length, 0, JSON.stringify(bad));
    assert.ok(view.taskReadNote);
  }
});

test("a tasks field sent as a list is read as tasks, not discarded", () => {
  const view = swarmStructureView({ tasks: [mapTask(), reduceTask()] }, []);
  assert.equal(view.tasks.length, 2);
  assert.deepEqual(view.tasks.map((t) => t.taskId), ["task-map-1", "task-reduce"]);
});

test("a task with no task_id falls back to its key, not 'undefined'", () => {
  const rows = swarmTaskRows({ "task-map-9": { objective: "x" } });
  assert.equal(rows[0].taskId, "task-map-9");
});