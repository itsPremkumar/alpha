import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

/**
 * Regression coverage for the bot routines client.
 *
 * The panel this backs renders two records per row — the profile routine and
 * the scheduled task it was created as — so the whole risk is a mapping that
 * folds one into the other: an absent task reading as "scheduled", a paused
 * task reading as the profile's create-time `enabled: true`, or a delete whose
 * task-retirement outcome is squashed into a boolean.
 *
 * The honesty contract pinned here (same as the detail page's): absent is
 * `null`, never `""` / `0` / `false`, and a payload with no `routines` key is
 * `null` — distinct from a list the server sent empty.
 */

const compiled = ts.transpileModule(readFileSync(new URL("./bot-routines.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

function load(dependencies = {}) {
  const exports = {};
  new Function("exports", "require", "module", compiled)(
    exports,
    (dependency) => {
      assert.ok(Object.hasOwn(dependencies, dependency), `Unexpected dependency: ${dependency}`);
      return dependencies[dependency];
    },
    { exports },
  );
  return exports;
}

const {
  normalizeBotRoutines,
  normalizeBotRoutine,
  normalizeRoutineTask,
  routineScheduleLine,
  deletionNote,
  browserTimezone,
} = load({
  "./http": { get: async () => ({}), send: async () => ({}) },
});

const ROUTINE = {
  name: "standup",
  schedule: "every 600s",
  action: "Post the standup summary.",
  enabled: true,
  task_id: "task-1",
  owner_user_id: "u-1",
  schedule_type: "interval",
  schedule_spec: { every_seconds: 600 },
  timezone: "UTC",
  task: {
    id: "task-1",
    status: "enabled",
    next_run_at: "2026-10-18T10:00:00+00:00",
    last_run_at: "2026-10-18T09:50:00+00:00",
    last_run_id: "run-9",
    last_error: null,
    run_count: 3,
  },
};

test("normalizeBotRoutines keeps an absent list null, never []", () => {
  // `[]` would render "this bot has no routines" — a claim about the bot that
  // a Gateway which omitted the key never made.
  const payload = normalizeBotRoutines({ bot_name: "quill" });
  assert.equal(payload.routines, null);
  assert.equal(payload.bot, "quill");
});

test("normalizeBotRoutines keeps a sent-empty list distinct from an absent one", () => {
  assert.deepEqual(normalizeBotRoutines({ bot_name: "quill", routines: [] }).routines, []);
  assert.equal(normalizeBotRoutines({}).routines, null);
  assert.equal(normalizeBotRoutines({}).bot, null);
});

test("normalizeBotRoutines maps the joined task without folding it into the routine", () => {
  const payload = normalizeBotRoutines({ bot_name: "quill", routines: [ROUTINE] });
  const routine = payload.routines[0];
  assert.equal(routine.name, "standup");
  assert.equal(routine.schedule, "every 600s");
  assert.equal(routine.task_id, "task-1");
  assert.equal(routine.task.status, "enabled");
  assert.equal(routine.task.next_run_at, "2026-10-18T10:00:00+00:00");
  assert.equal(routine.task.run_count, 3);
});

test("a routine whose task is gone reports task null, with its task_id intact", () => {
  // "created as task-1, and that task is not readable" is a different fact
  // from "created as task-1 and it is fine" and from "no task was ever
  // recorded" — the page prints all three differently.
  const routine = normalizeBotRoutine({ ...ROUTINE, task: null });
  assert.equal(routine.task, null);
  assert.equal(routine.task_id, "task-1");
});

test("a failed joined task read surfaces the server's own words", () => {
  const routine = normalizeBotRoutine({ ...ROUTINE, task: null, task_error: "task read failed: db down" });
  assert.equal(routine.task_error, "task read failed: db down");
});

test("the routine's create-time enabled flag is never read as the live state", () => {
  // The profile records `enabled: true` at creation; the task can be paused
  // afterwards without rewriting it. Both travel; the panel colours the task.
  const routine = normalizeBotRoutine({
    ...ROUTINE,
    enabled: true,
    task: { ...ROUTINE.task, status: "paused" },
  });
  assert.equal(routine.enabled, true);
  assert.equal(routine.task.status, "paused");
});

test("normalizeBotRoutine keeps a nameless row rather than shrinking the list", () => {
  // Dropping it would make "3 routines, one malformed" render as "2 routines"
  // — a count the server never sent.
  const payload = normalizeBotRoutines({ routines: [{ action: "no name here" }, ROUTINE] });
  assert.equal(payload.routines.length, 2);
  assert.equal(payload.routines[0].name, "");
});

test("normalizeBotRoutine drops only rows that are not records at all", () => {
  // A non-object cannot be rendered by any cell, so it is the one row shape
  // with no honest representation on the page.
  const payload = normalizeBotRoutines({ routines: [null, "standup", 7, ROUTINE] });
  assert.equal(payload.routines.length, 1);
});

test("normalizeBotRoutine coerces a wrongly-typed run_count to null, not 0", () => {
  const routine = normalizeBotRoutine({ ...ROUTINE, task: { ...ROUTINE.task, run_count: "3" } });
  assert.equal(routine.task.run_count, null);
});

test("normalizeRoutineTask returns null for anything that is not a record", () => {
  assert.equal(normalizeRoutineTask(null), null);
  assert.equal(normalizeRoutineTask("task-1"), null);
  assert.equal(normalizeRoutineTask([]), null);
});

test("routineScheduleLine prefers the stored label and derives from the spec otherwise", () => {
  assert.equal(routineScheduleLine(normalizeBotRoutine(ROUTINE)), "every 600s");
  assert.equal(
    routineScheduleLine(normalizeBotRoutine({ schedule_type: "cron", schedule_spec: { cron: "*/5 * * * *" } })),
    "cron: */5 * * * *",
  );
  assert.equal(
    routineScheduleLine(normalizeBotRoutine({ schedule_type: "interval", schedule_spec: { every_seconds: 30 } })),
    "every 30s",
  );
  assert.equal(
    routineScheduleLine(normalizeBotRoutine({ schedule_type: "once", schedule_spec: { run_at: "2026-11-01T09:00:00Z" } })),
    "once at 2026-11-01T09:00:00Z",
  );
});

test("routineScheduleLine falls back to the verbatim schedule_type, else null", () => {
  // A newer Gateway's schedule type is real state; the page shows the word it
  // sent rather than "not reported".
  assert.equal(routineScheduleLine(normalizeBotRoutine({ schedule_type: "lunar-cycle" })), "lunar-cycle");
  assert.equal(routineScheduleLine(normalizeBotRoutine({})), null);
});

test("deletionNote renders each task-retirement outcome the server can send", () => {
  assert.equal(
    deletionNote({ routine: "standup", removed: true, task_deletion: "deleted" }),
    "Removed standup and retired its scheduled task.",
  );
  assert.match(deletionNote({ routine: "standup", removed: true, task_deletion: "not_found" }), /already gone/);
  assert.match(deletionNote({ routine: "standup", removed: true, task_deletion: "no_task_recorded" }), /No scheduled task/);
  assert.match(deletionNote({ routine: "standup", removed: true, task_deletion: "owner_not_recorded" }), /left in place/);
});

test("deletionNote passes an unknown task_deletion word through verbatim", () => {
  // A newer Gateway's disposal word is real information; inventing a friendly
  // sentence for it would claim more than the server said.
  assert.match(
    deletionNote({ routine: "standup", removed: true, task_deletion: "quarantined" }),
    /task: quarantined/,
  );
});

test("deletionNote says so when the Gateway reported no disposal at all", () => {
  assert.match(
    deletionNote({ routine: "standup", removed: true, task_deletion: null }),
    /did not report the task's disposal/,
  );
});

test("browserTimezone always returns a non-empty zone string", () => {
  const zone = browserTimezone();
  assert.equal(typeof zone, "string");
  assert.ok(zone.length > 0);
});
