// delegation-opt-in.test.mjs — a prompt can only delegate if the composer asks.
//
// Found on 2026-10-06. The composer sent `model_name`, `is_plan_mode` and
// `reasoning_effort`, and never the server's `autonomous` flag, so
// `subagent_enabled` stayed false and the `task` tool was never in the toolset.
// Measured against the live Gateway:
//
//     autonomous=false -> tools: alpha_capability, catalog_tool_search
//     autonomous=true  -> tools: alpha_capability, task
//
// So typing a prompt could not produce a working subagent, in any wording. That
// is the wiring, not the operator — and it is invisible from the UI, because a
// missing capability and an idle one look identical on screen.
//
// The three properties pinned here are the ones a refactor would quietly break:
// the flag reaches the request body, it is absent unless asked for, and the
// control cannot be "on" while the request is silent.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (relative) =>
  readFileSync(new URL(relative, import.meta.url), "utf8");

const chat = read("../components/ChatView.tsx");
const composer = read("../components/Composer.tsx");

// ---------------------------------------------------------------------------
// The flag reaches the server
// ---------------------------------------------------------------------------

test("the run request carries the server's autonomous opt-in", () => {
  // `RunCreateRequest.autonomous` is the server-owned switch; `start_run` applies
  // `subagent_enabled` only after client context is sanitized. A different field
  // name here would be accepted by the boundary and silently ignored.
  assert.match(
    chat,
    /\.\.\.\(delegationEnabled\s*\?\s*\{\s*autonomous:\s*true\s*\}\s*:\s*\{\}\)/,
    "the request body must send `autonomous: true` when delegation is on",
  );
});

test("the opt-in defaults to off", () => {
  assert.match(
    chat,
    /const \[delegationEnabled, setDelegationEnabled\] = useState\(false\)/,
    "delegation must default to false: it spends tokens and starts background workers",
  );
  assert.match(
    composer,
    /delegationEnabled = false/,
    "the composer prop must default to false too",
  );
});

// ---------------------------------------------------------------------------
// Absent unless asked for
// ---------------------------------------------------------------------------

test("the literal false is never sent", () => {
  // `autonomous: false` and an absent field are different requests. Writing the
  // literal would make an explicit opt-out indistinguishable from a client that
  // never considered the question.
  assert.doesNotMatch(
    chat,
    /autonomous:\s*false/,
    "send the field only when on; `false` would assert an opt-out the user never chose",
  );
});

// ---------------------------------------------------------------------------
// The control is visible and honest
// ---------------------------------------------------------------------------

test("the composer renders a delegation control with an accessible name", () => {
  assert.match(
    composer,
    /aria-label="Allow this message to delegate work to subagents"/,
    "the control must name what it does for assistive tech, not just show an icon",
  );
});

test("the control renders its pressed state rather than implying a capability", () => {
  // A control that is off must look off. `aria-pressed` plus a visual class is
  // what separates "you may delegate" from "delegation is happening".
  assert.match(composer, /aria-pressed=\{delegationEnabled\}/);
  assert.match(
    composer,
    /delegationEnabled\s*\?\s*"text-primary bg-primary\/10"/,
    "the ON state must be visually distinct, not the same neutral icon",
  );
});

test("both states explain the consequence in the tooltip", () => {
  const off = composer.match(/Subagents OFF[^"]*/);
  const on = composer.match(/Subagents ON[^"]*/);
  assert.ok(off, "the OFF state must say delegation is off");
  assert.ok(on, "the ON state must say delegation is on");
  assert.match(
    off[0],
    /answers? this itself/i,
    "OFF must say what will happen instead",
  );
  assert.match(on[0], /Costs extra tokens/i, "ON must disclose the cost");
});

test("the control is disabled while a run is in flight", () => {
  // Toggling mid-run would change nothing about the run already admitted, so the
  // control implying otherwise is a lie about the current run.
  const idx = composer.indexOf(
    'aria-label="Allow this message to delegate work to subagents"',
  );
  assert.ok(idx > 0);
  const window = composer.slice(Math.max(0, idx - 700), idx);
  assert.match(
    window,
    /disabled=\{isLoading\}/,
    "the control must be locked while a run is in flight",
  );
});

// ---------------------------------------------------------------------------
// The wiring between the two files
// ---------------------------------------------------------------------------

test("ChatView passes both the state and the setter to the composer", () => {
  assert.match(chat, /delegationEnabled=\{delegationEnabled\}/);
  assert.match(chat, /onDelegationChange=\{setDelegationEnabled\}/);
});

// ---------------------------------------------------------------------------
// Why the existing transcript plumbing already works
// ---------------------------------------------------------------------------

test("the transcript already requests custom events for delegation progress", () => {
  // `task_*` rides the root namespace's `custom` stream mode. It was already
  // requested, so a delegation's progress reaches `SubagentList` once the tool
  // exists - the missing piece was only the tool itself.
  assert.match(
    chat,
    /stream_mode:\s*\["messages-tuple",\s*"values",\s*"custom"\]/,
    "subagent progress needs `custom` in stream_mode or the transcript shows only a spinner",
  );
});
