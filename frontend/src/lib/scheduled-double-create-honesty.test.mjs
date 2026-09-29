// scheduled-double-create-honesty.test.mjs — shape 5, on a durable mutation.
//
// `ScheduledSection.onCreate` POSTs a schedule, which the Gateway persists as a
// row. The handler had no in-flight state, and the "Save schedule" button was
// never disabled, so two clicks inside one request window issued two POSTs and
// created TWO schedules — and each of those then fires its own prompt on its
// own timer. That is duplicate autonomous work, not a cosmetic flicker, which
// is why the row actions (Run now / Pause / Resume / Delete) needed the same
// lock.
//
// frontend/src/AGENTS.md: "When a request is retried or triggered repeatedly,
// disable the control while in flight so a double-click cannot create two
// records."
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const src = read("../components/sections/ScheduledSection.tsx");

const body = (from, to) => {
  const start = src.indexOf(from);
  assert.ok(start > 0, `missing marker: ${from}`);
  const end = to ? src.indexOf(to, start) : src.length;
  return src.slice(start, end > 0 ? end : src.length);
};

test("onCreate refuses a second click synchronously, before the await", () => {
  const fn = body("const onCreate = async", "const act = async");
  assert.match(fn, /if \(saving\) return;/, "a second click must be refused before the request is issued");
  // The guard must precede the POST, otherwise it can never observe `saving`.
  assert.ok(
    fn.indexOf("if (saving) return;") < fn.indexOf("await createScheduledTask("),
    "the guard must come before the mutation, not after it",
  );
});

test("saving is set before the POST and cleared in a finally", () => {
  const fn = body("const onCreate = async", "const act = async");
  assert.ok(fn.indexOf("setSaving(true)") < fn.indexOf("await createScheduledTask("));
  // A `finally` is what keeps a failed create from wedging the form shut.
  assert.match(fn, /finally \{[\s\S]*setSaving\(false\)/);
});

test("the Save control is disabled while a create is in flight", () => {
  assert.match(src, /<Btn onClick=\{onCreate\} disabled=\{saving\}>/);
  // The label itself must change, so a wedged request is visible rather than
  // looking like a button that simply stopped responding.
  assert.match(src, /\{saving \? "Saving…" : "Save schedule"\}/);
});

test("the row mutations take the task id and hold the same lock", () => {
  // "Run now" starts a real agent run; an unguarded double-click ran it twice.
  const fn = body("const act = async", "return (");
  assert.match(fn, /if \(actingOn\) return;/);
  assert.match(fn, /setActingOn\(id\)/);
  assert.match(fn, /finally \{[\s\S]*setActingOn\(null\)/);
});

test("every per-row mutation control is disabled during its own request", () => {
  const row = body("{tasks.map((t) => (", "EmptyState");
  const disabled = row.match(/disabled=\{actingOn === t\.id\}/g) || [];
  // Run now + (Resume|Pause) + Delete.
  assert.ok(disabled.length >= 3, `expected at least 3 guarded row controls, found ${disabled.length}`);
  assert.match(row, /act\(t\.id, \(\) => triggerTask\(t\.id\)/, "act must receive the id it is mutating");
});

test("the section does not disable one row's controls for a different row", () => {
  // Scoping by id keeps the rest of the list usable; a global boolean would
  // freeze every row during any one request.
  const row = body("{tasks.map((t) => (", "EmptyState");
  assert.doesNotMatch(row, /disabled=\{actingOn === true\}/);
  assert.match(row, /actingOn === t\.id/);
});

test("a mutation still announces only after the server confirmed it", () => {
  // Shape 4 guard: `act` flashes its success string after `await fn()` and then
  // re-reads the list. Painting the notice before the await would report work
  // the Gateway never accepted.
  const fn = body("const act = async", "return (");
  assert.ok(fn.indexOf("await fn()") < fn.indexOf("flash(ok)"), "the notice must follow the confirmed mutation");
  assert.ok(fn.indexOf("flash(ok)") < fn.indexOf("await load()"), "…and the list must then be re-read, not assumed");
});
