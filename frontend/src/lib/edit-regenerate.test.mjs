// edit-regenerate.test.mjs — "Regenerate" and "Edit & resend" must replace a
// turn, not append one.
//
// The defect this pins, in both handlers:
//
//   handleRegenerate = () => sendMessage(lastUser.content)
//   handleEditResend = (_messageId, newContent) => sendMessage(newContent)
//
// Regenerate re-sent the question as a *new* user row, so the transcript grew
// a second copy of the prompt above a second answer while the original answer
// stayed in place. Edit accepted `messageId` and dropped it — the replacement
// was appended after the message it was meant to replace, so the UI showed
// both versions, the archived history kept both, and the model read the
// original text: the edit never reached it at all. Both buttons also bypassed
// the two Gateway endpoints built for exactly this
// (`POST /threads/{id}/runs/{regenerate,edit-regenerate}/prepare`), which hand
// back the graph input at the base checkpoint, the checkpoint to fork from,
// and the metadata that lets the paged history hide the superseded attempt.
//
// Two kinds of pin: the client's own HTTP contract (transpiled `runs.ts`
// against a stubbed transport) and source pins over `ChatView.tsx`, where the
// handlers live — React's rendering can't be executed from a plain
// `node --test` file, and the wiring is the whole bug.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

// ---------------------------------------------------------------------------
// 1. The prepare clients: exact paths, exact bodies, honest failures.
// ---------------------------------------------------------------------------

const httpStub = `
let handler = (path, method, payload) => { throw new Error("no stub response configured"); };
export function setHttpHandler(fn) { handler = fn; }
export async function get(path) { return handler(path, "GET", undefined); }
export async function send(path, method, payload) { return handler(path, method, payload); }
export function pick(obj, keys, fallback) {
  if (obj && typeof obj === "object") {
    const rec = obj;
    for (const k of keys) { if (rec[k] !== undefined && rec[k] !== null) return rec[k]; }
  }
  return fallback;
}
export function asList(body, keys) {
  if (Array.isArray(body)) return body;
  for (const k of keys) {
    if (body && typeof body === "object" && Array.isArray(body[k])) return body[k];
  }
  return [];
}
export function errMsg(err) { return err instanceof Error ? err.message : "Something went wrong."; }
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
export const DEFAULT_TIMEOUT_MS = 60000;
export const GATEWAY_BASE = "/api";
`;
const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;
const stubUrl = toDataUrl(httpStub);

const runsSource = readFileSync(new URL("./runs.ts", import.meta.url), "utf8");
let runsCode = ts.transpileModule(runsSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
runsCode = runsCode.replace(/from\s+"\.\/http"/, `from "${stubUrl}"`);

const runs = await import(toDataUrl(runsCode));
const { setHttpHandler, ApiError } = await import(stubUrl);

test("prepareRegenerate posts the target assistant message to the exact route", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/threads/t-9/runs/regenerate/prepare");
    assert.equal(method, "POST");
    assert.deepEqual(payload, { message_id: "asst-42" });
    return { input: { messages: [] }, checkpoint: { checkpoint_id: "c1" }, metadata: {}, target_run_id: "r-1" };
  });
  const prepared = await runs.prepareRegenerate("t-9", "asst-42");
  assert.equal(prepared?.target_run_id, "r-1");
});

test("prepareEditRegenerate posts the human message id and the replacement text", async () => {
  setHttpHandler((path, method, payload) => {
    assert.equal(path, "/threads/t-9/runs/edit-regenerate/prepare");
    assert.equal(method, "POST");
    assert.deepEqual(payload, { human_message_id: "human-7", replacement_text: "edited text" });
    return { input: { messages: [] }, checkpoint: {}, metadata: {}, target_run_id: "r-2" };
  });
  const prepared = await runs.prepareEditRegenerate("t-9", "human-7", "edited text");
  assert.equal(prepared?.target_run_id, "r-2");
});

test("a refusal from the Gateway is raised, not swallowed into `null`", async () => {
  // `null` means "this Gateway has no such route" and nothing else. Every
  // other failure — 409 "only the latest assistant message can be
  // regenerated", 404 "Message <id> not found", 500 — must reach the caller,
  // because a swallowed reason turns into either a duplicate turn (the old
  // bug) or a silence the user cannot act on.
  const cases = [
    new ApiError(409, "Request failed (HTTP 409). Only the latest assistant message can be regenerated"),
    new ApiError(404, "Request failed (HTTP 404). Message asst-42 not found"),
    new ApiError(500, "Request failed (HTTP 500). Failed to read latest checkpoint"),
    new ApiError(0, "Request timed out after 60s — the server may be busy."),
  ];
  for (const failure of cases) {
    setHttpHandler(() => {
      throw failure;
    });
    for (const prepare of [() => runs.prepareRegenerate("t-9", "asst-42"), () => runs.prepareEditRegenerate("t-9", "h-1", "x")]) {
      await assert.rejects(prepare, (error) => error === failure, `expected ${failure.message} to propagate`);
    }
  }
});

test("only a route-level 404 reports the endpoint as unsupported", async () => {
  setHttpHandler(() => {
    // Starlette's own body for an unmatched path.
    throw new ApiError(404, "Request failed (HTTP 404). Not Found");
  });
  assert.equal(await runs.prepareRegenerate("t-9", "asst-42"), null);
  assert.equal(await runs.prepareEditRegenerate("t-9", "h-1", "x"), null);
});

// ---------------------------------------------------------------------------
// 2. ChatView: the handlers prepare, and sendMessage replays.
// ---------------------------------------------------------------------------

const view = readFileSync(new URL("../components/ChatView.tsx", import.meta.url), "utf8");

/** The slice of ChatView between two markers (both required to exist). */
function between(start, end) {
  const from = view.indexOf(start);
  assert.notEqual(from, -1, `ChatView.tsx must contain ${JSON.stringify(start)}`);
  const to = view.indexOf(end, from);
  assert.notEqual(to, -1, `ChatView.tsx must contain ${JSON.stringify(end)} after ${JSON.stringify(start)}`);
  return view.slice(from, to);
}

const regenerateHandler = between("const handleRegenerate", "const handleEditResend");
const editHandler = between("const handleEditResend", "const handleRate");
const sendMessage = between("const sendMessage = async", "const handleRegenerate = async");

test("edit and regenerate both ask the Gateway to prepare a replay", () => {
  assert.match(
    editHandler,
    /prepareEditRegenerate\(activeThreadId, messageId,/,
    "edit must pass the edited message id — dropping it is what made the edit an append",
  );
  assert.match(editHandler, /appendUserMessage: true/, "an edit adds the replacement row");
  assert.match(
    editHandler,
    /messages\.slice\(index\)/,
    "the edited message and everything after it is superseded by the replay",
  );
  assert.doesNotMatch(
    editHandler,
    /sendMessage\(newContent\);/,
    "appending the replacement after the message it replaces is the defect",
  );

  assert.match(regenerateHandler, /prepareRegenerate\(activeThreadId,/, "regenerate must prepare, not re-send");
  assert.match(regenerateHandler, /appendUserMessage: false/, "regenerate re-asks the question already on screen");
  assert.match(
    regenerateHandler,
    /messages\.slice\(assistantIndex\)/,
    "the answer being regenerated is superseded, so a second answer cannot stack under it",
  );
});

test("sendMessage sends the prepared input, checkpoint and metadata", () => {
  assert.match(sendMessage, /input: replay\.prepared\.input/);
  assert.match(sendMessage, /checkpoint: replay\.prepared\.checkpoint/);
  assert.match(sendMessage, /metadata: replay\.prepared\.metadata/);
  // The ordinary turn must keep its plain single-message input.
  assert.match(
    sendMessage,
    /: \{ input: \{ messages: \[\{ role: "user", content \}\] \} \}/,
    "a non-replay turn still sends exactly one user message",
  );
});

test("a replay neither duplicates the prompt nor re-fires its slash command", () => {
  const triggerAt = sendMessage.indexOf("autoTriggerCommand");
  const guardAt = sendMessage.indexOf("if (!replay)");
  assert.notEqual(triggerAt, -1, "the slash-command trigger must still exist");
  assert.notEqual(guardAt, -1, "a replay must skip the slash-command trigger");
  assert.ok(
    guardAt < triggerAt,
    "the guard has to wrap the trigger — re-detecting a regenerated prompt fires its command twice",
  );

  // The replacement row is appended only when this turn adds one.
  assert.match(
    sendMessage,
    /if \(!replay \|\| replay\.appendUserMessage\) \{[\s\S]{0,200}appendLocalMessages/,
    "a regenerate must not append a second copy of the question",
  );
});

test("a failed replay restores the turn it superseded", () => {
  // Dropping the original without putting it back on failure would make a
  // failed regenerate a silent deletion of the user's own answer — and would
  // disagree with the server, which hides superseded rows only while the
  // replay attempt is pending or successful.
  assert.match(
    sendMessage,
    /\.\.\.replaySuperseded|supersededMessages/,
    "the superseded messages are captured before the drop",
  );
  assert.match(
    sendMessage,
    /\[\.\.\.kept\.filter\(\(message\) => message\.id !== appendedId\), \.\.\.supersededMessages\]/,
    "failure re-appends the original tail after the streamed rows are removed",
  );
});
