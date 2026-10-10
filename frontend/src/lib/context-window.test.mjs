// context-window.test.mjs — the context-window client and its view helpers.
//
// The Gateway is the only thing that knows how big a model's window is, so the
// failure modes that matter are the ones where a client fills a gap the server
// left on purpose:
//
//   * a `null` `declared_input_window` rendered as `0` — an unmeasured window
//     shown as an empty one, which reads as "you have used none of it";
//   * `band: "unknown"` coloured green, or snapped to `nominal` — a window nobody
//     measured presented as a comfortable one;
//   * an unrecognised band string (from a newer Gateway) snapped into a colour
//     this build invented, turning an unknown word into a green tick;
//   * a clamped reserve rendered as a healthy derivation, hiding that the
//     operator over-reserved;
//   * `models: null` ("the Gateway sent no list") rendered as "no models
//     declared", which are opposite claims.
//
// The transport is a stub, so these assertions pin the real path and the real
// envelope mapping without a Gateway.
//
// node --test src/lib/*.test.mjs — no server, no browser, no DOM shim.
import assert from "node:assert/strict";
import test from "node:test";
import ts from "typescript";

const transpile = (src) =>
  ts.transpileModule(src, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;

const STUB_URL = `data:text/javascript;charset=utf-8,${encodeURIComponent(`
  let handler = () => { throw new Error("no stub configured"); };
  export function setHttpHandler(fn) { handler = fn; }
  export async function get(path) { return handler(path); }
  export async function send(path, method, payload) { return handler(path, method, payload); }
  export class ApiError extends Error {}
  export function errMsg(e) { return e instanceof Error ? e.message : String(e); }
`)}`;

const source = await import("node:fs").then((fs) =>
  fs.readFileSync(new URL("./context-window.ts", import.meta.url), "utf8"),
);
const code = transpile(source).replace(/from\s+"\.\/http"/, `from "${STUB_URL}"`);
const mod = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`);
const { toContextWindows, fetchContextWindows, bandLabel, bandTone, modelWindowView, windowSummaryText } = mod;

const { setHttpHandler } = await import(STUB_URL);

const serve = (answer) => {
  seen.length = 0;
  setHttpHandler((path) => {
    seen.push(path);
    if (answer instanceof Error) throw answer;
    return answer;
  });
};
const seen = [];

/* ── the route ─────────────────────────────────────────────────────────────── */

test("fetchContextWindows calls the one ops route and rejects with the server reason", async () => {
  serve({ reported: true, reason: "context_windows_projected", models: [], escalation_enabled: true, notes: [] });
  await fetchContextWindows();
  assert.deepEqual(seen, ["/ops/context-windows"]);

  serve(new Error("503 the store is unavailable"));
  await assert.rejects(() => fetchContextWindows(), /503 the store is unavailable/);
});

/* ── the envelope mapping ──────────────────────────────────────────────────── */

test("an undeclared window keeps every derived field null", () => {
  const windows = toContextWindows({
    reported: true,
    reason: "context_windows_projected",
    models: [{ name: "cheap", declared_input_window: null, usable_input_window: null, reserved_tokens: 0, clamped: false, reason: "context_window_not_declared", escalation_candidate: null }],
    escalation_enabled: true,
    notes: [],
  });
  const row = windows.models[0];
  assert.equal(row.declaredInputWindow, null);
  assert.equal(row.usableInputWindow, null);
  assert.equal(row.escalationCandidate, null);
});

test("a declared window maps every field it reports", () => {
  const windows = toContextWindows({
    reported: true,
    reason: "context_windows_projected",
    models: [{ name: "union-alpha", declared_input_window: 128000, usable_input_window: 121856, reserved_tokens: 6144, clamped: false, reason: "context_window_declared", escalation_candidate: "big" }],
    escalation_enabled: true,
    notes: [],
  });
  assert.deepEqual(windows.models[0], {
    name: "union-alpha",
    declaredInputWindow: 128000,
    usableInputWindow: 121856,
    reservedTokens: 6144,
    clamped: false,
    reason: "context_window_declared",
    escalationCandidate: "big",
  });
});

test("reported=false is a distinct state from an empty model list", () => {
  const disabled = toContextWindows({ reported: false, reason: "context_window_disabled", models: [], escalation_enabled: null, notes: ["x"] });
  assert.equal(disabled.reported, false);
  assert.equal(disabled.reason, "context_window_disabled");
  // `escalation_enabled: null` is "no policy section at all", not "no target".
  assert.equal(disabled.escalationEnabled, null);
  assert.deepEqual(disabled.notes, ["x"]);

  const noneReported = toContextWindows({ reported: true, reason: "context_windows_projected", models: [], escalation_enabled: true, notes: [] });
  assert.equal(noneReported.reported, true);
  assert.deepEqual(noneReported.models, []);
});

test("models:null is not models:[]", () => {
  const sent = toContextWindows({ reported: true, reason: "r", models: null, escalation_enabled: true, notes: [] });
  assert.equal(sent.models, null);
  const empty = toContextWindows({ reported: true, reason: "r", models: [], escalation_enabled: true, notes: [] });
  assert.deepEqual(empty.models, []);
});

test("a payload that is not an object degrades rather than throwing", () => {
  for (const junk of [undefined, null, 7, "x", []]) {
    const windows = toContextWindows(junk);
    assert.equal(windows.reported, false);
    assert.equal(windows.reason, "not_reported");
    assert.equal(windows.models, null);
    assert.equal(windows.escalationEnabled, null);
  }
});

/* ── band words and tone ───────────────────────────────────────────────────── */

test("an undeclared band is words, and unknown is never nominal", () => {
  assert.equal(bandLabel("unknown"), "not measured");
  assert.equal(bandTone("unknown"), "grey");
  assert.notEqual(bandLabel("unknown"), bandLabel("nominal"));
});

test("an unrecognised band renders verbatim-ish and neutral, never green", () => {
  assert.equal(bandLabel("quantum"), "not reported");
  assert.equal(bandTone("quantum"), "grey");
  assert.equal(bandLabel(undefined), "not reported");
  assert.equal(bandTone(null), "grey");
});

test("the four real bands each get their own tone", () => {
  assert.equal(bandTone("nominal"), "green");
  assert.equal(bandTone("elevated"), "amber");
  assert.equal(bandTone("critical"), "red");
  assert.equal(bandTone("over"), "red");
});

/* ── the row view ──────────────────────────────────────────────────────────── */

test("an undeclared window says so instead of deriving a number", () => {
  const view = modelWindowView({
    name: "cheap",
    declaredInputWindow: null,
    usableInputWindow: null,
    reservedTokens: 0,
    clamped: false,
    reason: "context_window_not_declared",
    escalationCandidate: null,
  });
  assert.equal(view.declaredText, "not declared");
  assert.equal(view.usableText, "not derivable");
  assert.equal(view.escalationText, "not comparable");
  assert.equal(view.tone, "grey");
});

test("a clamped reserve is disclosed, not shown as a clean derivation", () => {
  const view = modelWindowView({
    name: "tiny",
    declaredInputWindow: 4000,
    usableInputWindow: 1024,
    reservedTokens: 6000,
    clamped: true,
    reason: "context_window_declared_but_unusable",
    escalationCandidate: null,
  });
  assert.equal(view.clamped, true);
  assert.equal(view.tone, "amber");
  assert.equal(view.usableText, "1,024");
  assert.equal(view.escalationText, "none larger declared");
});

test("a healthy window renders green with its escalation target named", () => {
  const view = modelWindowView({
    name: "union-alpha",
    declaredInputWindow: 128000,
    usableInputWindow: 121856,
    reservedTokens: 6144,
    clamped: false,
    reason: "context_window_declared",
    escalationCandidate: "big",
  });
  assert.equal(view.tone, "green");
  assert.equal(view.escalationText, "big");
  assert.equal(view.declaredText, "128,000");
});

/* ── the headline ──────────────────────────────────────────────────────────── */

test("the headline names how many models declared nothing", () => {
  const mixed = windowSummaryText(
    toContextWindows({
      reported: true,
      reason: "r",
      models: [
        { name: "a", declared_input_window: 100, usable_input_window: 90, reserved_tokens: 10, clamped: false, reason: "context_window_declared", escalation_candidate: null },
        { name: "b", declared_input_window: null, usable_input_window: null, reserved_tokens: 0, clamped: false, reason: "context_window_not_declared", escalation_candidate: null },
        { name: "c", declared_input_window: null, usable_input_window: null, reserved_tokens: 0, clamped: false, reason: "context_window_not_declared", escalation_candidate: null },
      ],
      escalation_enabled: true,
      notes: [],
    }),
  );
  assert.match(mixed, /1 of 3 models declare a context window/);
});

test("the headline distinguishes an unreported policy from an empty catalog", () => {
  assert.equal(
    windowSummaryText(toContextWindows({ reported: false, reason: "context_window_disabled", models: null, escalation_enabled: null, notes: [] })),
    "the Gateway reported no context-window policy",
  );
  assert.equal(
    windowSummaryText(toContextWindows({ reported: true, reason: "no_models_declared", models: [], escalation_enabled: true, notes: [] })),
    "this deployment declares no models",
  );
});
