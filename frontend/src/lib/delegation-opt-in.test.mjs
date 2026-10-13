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
// The mode choice, worker/category preference, and task progress channel must
// all reach the same real Gateway execution path.
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

test("the run request carries the server's autonomous opt-in for any selected mode", () => {
  // `RunCreateRequest.autonomous` is the server-owned switch; `start_run` applies
  // `subagent_enabled` only after client context is sanitized. A different field
  // name here would be accepted by the boundary and silently ignored.
  assert.match(
    chat,
    /\.\.\.\(delegationMode\s*!==\s*"off"\s*\?\s*\{\s*autonomous:\s*true\s*\}\s*:\s*\{\}\)/,
    "the request body must send `autonomous: true` when delegation is on",
  );
});

test("the selector defaults to off", () => {
  assert.match(
    chat,
    /const \[delegationMode, setDelegationMode\] = useState\("off"\)/,
    "delegation must default to false: it spends tokens and starts background workers",
  );
  assert.match(
    composer,
    /delegationMode = "off"/,
    "the composer prop must default to off too",
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

test("the composer renders a mode selector with an accessible name", () => {
  assert.match(
    composer,
    /aria-label="Subagent mode"/,
    "the selector must name its function for assistive tech",
  );
});

test("the selector exposes real worker and category choices", () => {
  assert.match(
    composer,
    /listSubagentCatalog\(\)/,
    "worker choices must come from the Gateway catalog",
  );
  assert.match(composer, /category:research/);
  assert.match(composer, /category:quick/);
  assert.match(composer, /category:deep-research/);
  assert.match(composer, /type:\$\{choice\.name\}/);
  assert.match(composer, /Concurrent subagents/);
  assert.match(composer, /Total subagents/);
  assert.match(chat, /max_total_subagents/);
  assert.doesNotMatch(composer, /<option disabled>Arena/);
});

test("the control is disabled while a run is in flight", () => {
  // Toggling mid-run would change nothing about the run already admitted, so the
  // control implying otherwise is a lie about the current run.
  const idx = composer.indexOf('aria-label="Subagent mode"');
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

test("ChatView passes the selected mode and run-scoped preferences", () => {
  assert.match(chat, /delegationMode=\{delegationMode\}/);
  assert.match(chat, /onDelegationModeChange=\{setDelegationMode\}/);
  assert.match(chat, /preferred_subagent_type:/);
  assert.match(chat, /preferred_subagent_category:/);
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
