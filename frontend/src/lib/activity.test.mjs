// Contract tests for the live-activity derivation that drives the in-chat
// status line and the collapsed tool group.
//
// These pin two things that are easy to regress and expensive to notice:
//   1. a phase is only ever reported from what the stream actually sent, and
//   2. no unreported tool result is ever counted as settled (and therefore
//      never as success) — the same honesty rule `tool-status-honesty.test.mjs`
//      enforces on the pill itself.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./activity.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const activity = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`);
const {
  deriveActivity,
  phaseLabel,
  currentTurn,
  openTiming,
  settleTiming,
  formatDuration,
  formatElapsed,
  summarizeToolNames,
  streamSilence,
  silenceNotice,
  HEARTBEAT_IDLE_SECONDS,
  SILENCE_NOTICE_MS,
} = activity;

const call = (name, status) => ({ id: `c-${name}-${status ?? "none"}`, name, args: {}, ...(status ? { status } : {}) });

test("an empty turn reports `waiting`, not a fabricated phase", () => {
  const state = deriveActivity([]);
  assert.equal(state.phase, "waiting");
  assert.equal(state.toolTotal, 0);
  assert.equal(state.toolSettled, 0);
  assert.equal(phaseLabel(state), "Starting…");
});

test("unreported tool results count as running, never as settled", () => {
  const state = deriveActivity([{ toolCalls: [call("shell"), call("search")] }]);
  assert.equal(state.phase, "tools");
  assert.equal(state.toolTotal, 2);
  assert.equal(state.toolRunning, 2);
  assert.equal(state.toolSettled, 0);
  // The label must not claim progress that no result reported.
  assert.equal(phaseLabel(state), "Running tools…");
});

test("`unknown` is a reported outcome, so it settles the call without being a success", () => {
  const state = deriveActivity([{ toolCalls: [call("shell", "unknown")] }]);
  assert.equal(state.toolSettled, 1);
  assert.equal(state.toolRunning, 0);
  // Not writing, not thinking: nothing else was reported.
  assert.equal(state.phase, "waiting");
});

test("a failed call also settles — settled means over, not passed", () => {
  const state = deriveActivity([{ toolCalls: [call("shell", "failed"), call("search", "completed")] }]);
  assert.equal(state.toolSettled, 2);
  assert.equal(state.toolRunning, 0);
});

test("a running tool outranks text already on screen", () => {
  const state = deriveActivity([
    { content: "Earlier text", toolCalls: [call("shell")] },
  ]);
  assert.equal(state.phase, "tools");
  assert.equal(state.hasContent, true);
});

test("phase walks waiting -> thinking -> writing as the stream fills in", () => {
  const thinking = deriveActivity([{ thinking: "let me look this up" }]);
  assert.equal(thinking.phase, "thinking");
  assert.equal(phaseLabel(thinking), "Thinking…");

  const writing = deriveActivity([{ thinking: "let me look this up", content: "Here is" }]);
  assert.equal(writing.phase, "writing");
  assert.equal(phaseLabel(writing), "Writing answer…");
});

test("whitespace-only content is not content", () => {
  const state = deriveActivity([{ content: "   \n  " }]);
  assert.equal(state.hasContent, false);
  assert.equal(state.phase, "waiting");
});

test("the progress label only appears once a result has actually settled", () => {
  const state = deriveActivity([{ toolCalls: [call("a", "completed"), call("b"), call("c")] }]);
  assert.equal(state.toolSettled, 1);
  assert.equal(state.toolTotal, 3);
  assert.equal(phaseLabel(state), "Running tools… 1 of 3 done");
});

test("currentTurn returns only messages after the last user prompt", () => {
  const messages = [
    { role: "user" },
    { role: "assistant" },
    { role: "user" },
    { role: "assistant" },
    { role: "assistant" },
  ];
  assert.equal(currentTurn(messages), 3, "indexes 3 and 4 are this run's work");
  assert.deepEqual(messages.slice(currentTurn(messages)).map((m) => m.role), ["assistant", "assistant"]);
});

test("currentTurn is empty when nothing has been asked yet", () => {
  assert.equal(currentTurn([]), 0);
  assert.equal(currentTurn([{ role: "assistant" }, { role: "assistant" }]), 0);
  assert.equal(currentTurn([{ role: "user" }]), 1, "the prompt itself is not the answer");
});

test("a call first seen already-settled has no honest duration and renders none", () => {
  const timing = openTiming(1000, true);
  assert.equal(timing.observedRunning, false);
  assert.equal(formatDuration(timing, 999_999), "", "history must never read as a live 0s or a huge elapsed");
  assert.equal(formatDuration(timing, 1000), "");
});

test("a call observed running measures from first sighting to its result", () => {
  const timing = settleTiming(openTiming(1000, false), 4500);
  assert.equal(timing.end, 4500);
  assert.equal(formatDuration(timing, 10_000), "3s", "a later clock must not move a settled duration");
  assert.equal(formatDuration(timing, 1000), "3s");
});

test("a still-running call ticks against the supplied clock", () => {
  const timing = openTiming(1000, false);
  assert.equal(timing.end, undefined);
  assert.equal(formatDuration(timing, 14_000), "13s");
});

test("settle is idempotent so a replayed frame cannot rewrite history", () => {
  const timing = settleTiming(openTiming(0, false), 500);
  assert.equal(settleTiming(timing, 9_999).end, 500);
});

test("formatElapsed renders seconds, then minutes, and refuses invalid readings", () => {
  assert.equal(formatElapsed(0), "0s");
  assert.equal(formatElapsed(14_999), "14s");
  assert.equal(formatElapsed(65_000), "1m 05s");
  assert.equal(formatElapsed(600_000), "10m 00s");
  assert.equal(formatElapsed(-1), "", "a negative reading is not a duration");
  assert.equal(formatElapsed(Number.NaN), "");
  assert.equal(formatDuration(undefined, 1000), "");
});

test("the tool summary dedupes, caps and counts the overflow", () => {
  const calls = [call("shell"), call("shell"), call("exa"), call("read")];
  assert.equal(summarizeToolNames(calls, 3), "shell, exa, read");
  assert.equal(summarizeToolNames(calls, 2), "shell, exa +1 more");
  assert.equal(summarizeToolNames([call("shell")]), "shell");
  assert.equal(summarizeToolNames([]), "", "an empty group must not claim tools");
});

test("a nameless tool call is skipped rather than rendered as an empty label", () => {
  assert.equal(summarizeToolNames([call("   ")]), "");
});

/* ── Stream silence: the only signal that separates "quiet tool call" from
   "connection gone". Measured on bytes, not frames, because heartbeat comments
   parse to nothing but are precisely what a healthy idle stream keeps sending. */

test("the notice threshold is two missed heartbeats, not one", () => {
  // One late or dropped frame must never raise a notice.
  assert.equal(HEARTBEAT_IDLE_SECONDS, 15, "matches DEFAULT_HEARTBEAT_INTERVAL_SECONDS");
  assert.equal(SILENCE_NOTICE_MS, 30_000);
});

test("silence has no reading at all before the first byte", () => {
  // `null`, not `0`: "never observed" and "zero seconds quiet" are different
  // claims, and only the second one may ever become a displayed measurement.
  assert.equal(streamSilence(null, 1000), null);
  assert.equal(silenceNotice(null, 1000), null, "an unknown must render nothing, never a timer");
});

test("a clock reading that is nonsense yields no measurement", () => {
  assert.equal(streamSilence(Number.NaN, 1000), null);
  assert.equal(streamSilence(50, Number.POSITIVE_INFINITY), null);
});

test("silence is clamped at zero rather than reported negative", () => {
  // A clock that moved backwards between readings is not "negative quiet".
  assert.equal(streamSilence(1000, 900), 0);
});

test("a heartbeat keeps the notice away, which is the whole point", () => {
  const lastByte = 10_000;
  // 15s of quiet — exactly one missed beat's worth of nothing arriving.
  assert.equal(streamSilence(lastByte, lastByte + 15_000), 15_000);
  assert.equal(silenceNotice(lastByte, lastByte + 15_000), null, "one late frame is not a fault");
  // A fresh heartbeat byte arrives and resets it to nothing observable.
  assert.equal(streamSilence(lastByte + 15_000, lastByte + 16_000), 1_000);
  assert.equal(silenceNotice(lastByte + 15_000, lastByte + 16_000), null);
});

test("two missed heartbeats produce a factual notice, not a diagnosis", () => {
  const lastByte = 0;
  const notice = silenceNotice(lastByte, lastByte + SILENCE_NOTICE_MS);
  assert.equal(notice, "No update received for 30s");
  for (const forbidden of ["stalled", "stuck", "failed", "disconnected", "hung", "error"]) {
    assert.ok(!notice.toLowerCase().includes(forbidden), `"${forbidden}" would be an unmeasured claim`);
  }
});

test("the notice only ever states a real number of seconds", () => {
  assert.equal(silenceNotice(0, 32_400), "No update received for 32s");
  assert.equal(silenceNotice(0, 95_000), "No update received for 1m 35s");
  assert.equal(silenceNotice(0, 61_000), "No update received for 1m 01s");
});

test("silence shrinks as bytes resume, so the notice clears on the next beat", () => {
  const quietAt = SILENCE_NOTICE_MS + 5_000;
  assert.notEqual(silenceNotice(0, quietAt), null, "it must be showing before it can stop showing");
  // Server sends a byte: the very next tick reports nothing worth surfacing.
  assert.equal(silenceNotice(quietAt, quietAt + 1_000), null);
});
