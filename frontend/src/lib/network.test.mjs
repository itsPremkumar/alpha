// network.test.mjs — the internet-connectivity client and its strip view.
//
// Two surfaces, tested for the same reason: `GET /api/ops/network` is the only
// thing that knows what the machine's link is doing, and the workspace header is
// the only place an operator will look. The failure modes that matter are the
// ones where those two disagree:
//
//   * a `null` latency quietly rendered as `0` — the worst possible link shown as
//     the best possible one;
//   * `unknown` rendered as `offline`, which parks work on a broken probe;
//   * "the backend is not measuring this" rendered as a healthy green link;
//   * a manual Retry that reports success the server never confirmed.
//
// The transport is a stub, so these assertions pin the real paths and the real
// envelope mapping without a Gateway. `connectivityView` is the real exported
// function driven with the payloads the Gateway actually returns.
//
// node --test src/lib/*.test.mjs — no server, no browser, no DOM shim.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const here = (rel) => fileURLToPath(new URL(rel, import.meta.url));
const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");
const transpile = (src) =>
  ts.transpileModule(src, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
const dataUrl = (code) => `data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`;
const load = (code) => import(`data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`);

/* ── The transport stub: one shared instance, so every caller sees the handler ── */

const calls = [];
const STUB_URL = dataUrl(`
  let handler = () => { throw new Error("no stub configured"); };
  export function setHttpHandler(fn) { handler = fn; }
  export async function get(path) { return handler(path, "GET", undefined); }
  export async function send(path, method, payload) { return handler(path, method, payload); }
  export class ApiError extends Error {}
  export function errMsg(e) { return e instanceof Error ? e.message : String(e); }
  export const DEFAULT_TIMEOUT_MS = 60000;
  export function pick(obj, keys, fallback) {
    if (obj && typeof obj === "object") for (const k of keys) if (obj[k] !== undefined && obj[k] !== null) return obj[k];
    return fallback;
  }
  export function asList(body, keys) {
    if (Array.isArray(body)) return body;
    if (body && typeof body === "object") for (const k of keys) if (Array.isArray(body[k])) return body[k];
    return [];
  }
`);

const code = transpile(read("./network.ts")).replace(/from\s+"\.\/http"/, `from "${STUB_URL}"`);
const { CONNECTIVITY_STATES, toConnectivity, fetchConnectivity, recheckConnectivity, connectivityView } = await load(code);

const { setHttpHandler } = await import(STUB_URL);

/**
 * Record every call and answer with `answer`.
 *
 * The handler deliberately ignores the arguments the stub passes it and closes
 * over `answer` instead. Naming the handler's third parameter `payload` would
 * shadow this function's own parameter, so `serve(new Error(...))` would hand
 * the handler `undefined`, never throw, and the "a failed read rejects" test
 * would pass for the wrong reason — by resolving.
 */
function serve(answer) {
  calls.length = 0;
  setHttpHandler((path, method) => {
    calls.push({ path, method });
    if (answer instanceof Error) throw answer;
    return answer;
  });
}

/* ── Fixtures: the exact payloads the Gateway returns ────────────────────── */

const ONLINE = {
  reported: true,
  reason: "",
  state: "online",
  state_detail: "Connectivity confirmed.",
  allows_network_attempt: true,
  latency_ms: 18.4,
  monitoring: true,
  observed_age_seconds: 2.1,
  targets: [
    { name: "cloudflare", reachable: true, latency_ms: 16.2, failure_kind: "unknown", detail: "" },
    { name: "google", reachable: true, latency_ms: 20.6, failure_kind: "unknown", detail: "" },
  ],
  retry: { automatic: true, retrying: false, next_probe_seconds: 15.0, poll_interval_seconds: 15.0, backoff_max_seconds: 300.0 },
  parked_durability: "installed",
  parked_sessions: { reported: true, reason: "", detail: "", open_waits: 0, claimed: 0, resumed: 0, gave_up: 0 },
  last_observation: { state: "online" },
  notes: [],
};

const OFFLINE = {
  ...ONLINE,
  state: "offline",
  state_detail: "No connectivity. Work is paused and will resume automatically when the link returns.",
  latency_ms: null,
  allows_network_attempt: false,
  targets: [
    { name: "cloudflare", reachable: false, latency_ms: 2000.0, failure_kind: "timeout", detail: "connect timeout" },
    { name: "google", reachable: false, latency_ms: 2000.0, failure_kind: "timeout", detail: "connect timeout" },
  ],
  retry: { automatic: true, retrying: true, next_probe_seconds: 240.0, poll_interval_seconds: 15.0, backoff_max_seconds: 300.0 },
  notes: [],
};

const UNKNOWN = {
  ...ONLINE,
  state: "unknown",
  state_detail: "Connectivity has not been determined yet; network operations will be attempted and handled by the ordinary retry path.",
  // The load-bearing asymmetry: UNKNOWN still permits an attempt.
  allows_network_attempt: true,
  latency_ms: null,
  targets: [],
  notes: [],
};

const NOT_MEASURED = {
  reported: false,
  reason: "network monitoring is disabled by configuration",
  state: null,
  state_detail: "",
  allows_network_attempt: null,
  latency_ms: null,
  monitoring: false,
  observed_age_seconds: null,
  targets: [],
  retry: { automatic: false, retrying: false, next_probe_seconds: null, poll_interval_seconds: null, backoff_max_seconds: null },
  parked_durability: "unavailable",
  parked_sessions: null,
  notes: [],
};

/* ══ 1. The routes are the real ones, with the real verbs ════════════════ */

test("fetchConnectivity reads GET /ops/network and nothing else", async () => {
  serve(ONLINE);
  const status = await fetchConnectivity();
  assert.deepEqual(calls, [{ path: "/ops/network", method: "GET" }]);
  assert.equal(status.state, "online");
  assert.equal(status.latency_ms, 18.4);
});

test("recheckConnectivity posts to /ops/network/recheck", async () => {
  serve({ ...OFFLINE, recheck: { performed: true, reason: "", detail: "", changed: true, consecutive_agreeing: 2, confirmations_required: 2 } });
  const status = await recheckConnectivity();
  assert.deepEqual(calls, [{ path: "/ops/network/recheck", method: "POST" }]);
  assert.equal(status.recheck.performed, true);
  assert.equal(status.recheck.changed, true);
  assert.equal(status.state, "offline");
});

test("a failed read rejects instead of resolving to a healthy object", async () => {
  serve(new Error("Request failed (HTTP 503). Network monitor unavailable."));
  await assert.rejects(() => fetchConnectivity(), /Network monitor unavailable/);
});

/* ══ 2. The envelope is mapped, and every absence stays null ═════════════ */

test("null latency is never coerced to zero", () => {
  // The single most dangerous mapping in this file: `Number(null) ?? 0` renders
  // the worst possible link as the fastest possible one.
  const status = toConnectivity({ ...ONLINE, latency_ms: null });
  assert.equal(status.latency_ms, null);
  assert.notEqual(status.latency_ms, 0);
  const missing = toConnectivity({ ...ONLINE, latency_ms: undefined });
  assert.equal(missing.latency_ms, null);
  const junk = toConnectivity({ ...ONLINE, latency_ms: "fast" });
  assert.equal(junk.latency_ms, null);
});

test("an absent state stays null and is never invented", () => {
  const status = toConnectivity({ ...ONLINE, state: null });
  assert.equal(status.state, null);
  assert.equal(toConnectivity({}).state, null);
});

test("an unknown enum value is preserved verbatim, not snapped to a known state", () => {
  // A newer Gateway may add a state. Mapping it onto one of the four we know
  // would be a claim about the machine that the server did not make.
  const status = toConnectivity({ ...ONLINE, state: "throttled" });
  assert.equal(status.state, "throttled");
  assert.ok(!CONNECTIVITY_STATES.includes(status.state));
});

test("the whole retry and target blocks survive the mapping", async () => {
  serve(OFFLINE);
  const status = await fetchConnectivity();
  assert.equal(status.retry.automatic, true);
  assert.equal(status.retry.retrying, true);
  assert.equal(status.retry.next_probe_seconds, 240.0);
  assert.equal(status.retry.backoff_max_seconds, 300.0);
  assert.deepEqual(
    status.targets.map((t) => [t.name, t.reachable, t.failure_kind]),
    [
      ["cloudflare", false, "timeout"],
      ["google", false, "timeout"],
    ],
  );
  // An unreachable target still reports the time it took to fail: a fast refusal
  // and a black-holed route are different faults.
  assert.equal(status.targets[0].latency_ms, 2000.0);
});

test("a parked-sessions block that the server omitted is null, not zeroed", () => {
  const status = toConnectivity({ ...ONLINE, parked_sessions: null });
  assert.equal(status.parked_sessions, null);
});

/* ══ 3. The four states, and the three things that are not states ══════════ */

test("an online link reports its measured round-trip and offers no Retry", () => {
  const view = connectivityView(toConnectivity(ONLINE), false);
  assert.equal(view.tone, "green");
  // Whole milliseconds read better in a dense strip; a sub-millisecond reading
  // is real on loopback and must never be rounded away to a bare "0 ms".
  assert.equal(view.value, "18 ms");
  assert.equal(view.label, "internet");
  assert.equal(view.canRetry, false);
  assert.match(view.title, /latency_ms/);
  // A healthy row stays quiet: the server's own "confirmed" sentence is not
  // repeated next to a green reading.
  assert.equal(view.detail, null);
});

test("an offline link is red, names the unreachable endpoints, and offers Retry", () => {
  const view = connectivityView(toConnectivity(OFFLINE), false);
  assert.equal(view.tone, "red");
  assert.equal(view.value, "offline");
  assert.equal(view.canRetry, true);
  // The backend's own automatic retry is disclosed in words, with the real
  // drawn delay rather than a bare "retrying".
  assert.match(view.detail, /re-probing automatically in ~240s/);
  assert.match(view.detail, /keeps trying until the link returns/);
  assert.match(view.detail, /Unreachable: cloudflare, google/);
  assert.match(view.title, /resumes automatically when it returns/);
});

test("unknown is never drawn as offline", () => {
  const view = connectivityView(toConnectivity(UNKNOWN), false);
  assert.notEqual(view.value, "offline");
  assert.equal(view.value, "unknown");
  assert.notEqual(view.tone, "red");
  assert.equal(view.canRetry, true);
  // The load-bearing asymmetry, stated on screen: unknown still allows a try.
  assert.match(view.title, /never reported as offline/);
});

test("a degraded link is amber, not red, and says a partial link is not an outage", () => {
  const degraded = toConnectivity({
    ...ONLINE,
    state: "degraded",
    latency_ms: 20.6,
    targets: [
      { name: "cloudflare", reachable: true, latency_ms: 20.6, failure_kind: "unknown", detail: "" },
      { name: "google", reachable: false, latency_ms: 2000.0, failure_kind: "timeout", detail: "connect timeout" },
    ],
  });
  const view = connectivityView(degraded, false);
  assert.equal(view.tone, "amber");
  assert.equal(view.label, "internet partial");
  assert.equal(view.canRetry, true);
  assert.match(view.detail, /Unreachable: google/);
  assert.match(view.title, /never parks work/);
  assert.equal(view.title.includes("degraded link is not an outage"), true);
});

test("an unrecognised state renders the server's own word", () => {
  const view = connectivityView(toConnectivity({ ...ONLINE, state: "throttled" }), false);
  assert.equal(view.value, "throttled");
  assert.match(view.label, /state reported by the Gateway/);
});

test("a reading older than the poll cadence is disclosed, not presented as current", () => {
  // Reachable, not theoretical: while the link is down the backend backs off
  // toward a five-minute ceiling, so the strip's ten-second refresh can be
  // showing a reading that is minutes old. A stale green link is exactly the
  // case where offering Retry *does* help, so it is offered there.
  const fresh = connectivityView(toConnectivity({ ...ONLINE, observed_age_seconds: 5 }), false);
  assert.equal(fresh.detail, null, "a fresh reading adds no sentence");
  assert.equal(fresh.canRetry, false);

  const stale = connectivityView(toConnectivity({ ...ONLINE, observed_age_seconds: 240, retry: { automatic: true, retrying: false, next_probe_seconds: 240, poll_interval_seconds: 15, backoff_max_seconds: 300 } }), false);
  assert.match(stale.detail, /240s old/);
  assert.match(stale.detail, /may be out of date/);
  assert.equal(stale.canRetry, true, "a stale reading is the one healthy case worth re-measuring");
  // …and the figure itself is still rendered, with its noun.
  assert.equal(stale.value, "18 ms");
});

test("a measuring process that named no state is disclosed, not printed as \"null\"", () => {
  // `reported: true` with `state: null` is a third claim: the process says it is
  // measuring and named nothing. It must not render as a state called "null", and
  // it must not borrow the amber of an unrecognised *word* — there is no word.
  // The wording is the panel's; these assertions pin the behaviour.
  const view = connectivityView(toConnectivity({ ...ONLINE, state: null }), false);
  assert.equal(view.value, "—");
  assert.doesNotMatch(view.value, /null/, "the raw null must never be shown as if it were a state name");
  // Grey, not amber: amber is the tone for an unrecognised *word*, and there is
  // no word here to recognise.
  assert.equal(view.tone, "gray");
  // The label carries words, because the strip renders a bare dash with a noun.
  assert.match(view.label, /state/);
  assert.match(view.label, /not reported/);
  assert.match(view.title, /sent no state/);
  assert.match(view.title, /NOT a measured value, NOT a healthy link, and NOT a confirmed outage/);
  assert.doesNotMatch(view.title, /state "null"/);
  assert.doesNotMatch(view.title, /does not recognise/);
  // A re-probe is worth offering: something *is* being measured, so asking again
  // is the one action that might produce a name.
  assert.equal(view.canRetry, true);
});

/* ══ 4. "Not measured" is three different claims, and stays three ═════════ */

test("a Gateway that reports nothing measured is grey and refuses Retry", () => {
  const view = connectivityView(toConnectivity(NOT_MEASURED), false);
  assert.equal(view.tone, "gray");
  // A recheck cannot help: there is no probe in this process. A button that
  // could only come back with the same refusal implies a pending state that
  // does not exist.
  assert.equal(view.canRetry, false);
  assert.match(view.detail, /network monitoring is disabled by configuration/);
  assert.match(view.title, /NOT a healthy link, and NOT a confirmed outage/);
});

test("a failed read is its own state, and it IS worth retrying", () => {
  const view = connectivityView(null, true);
  assert.equal(view.tone, "gray");
  assert.equal(view.canRetry, true);
  assert.match(view.label, /not reported/);
  assert.match(view.title, /NOT a measured 0 ms/);
});

test("a view that has not loaded yet does not offer a control that does nothing", () => {
  const view = connectivityView(null, false);
  assert.equal(view.canRetry, false);
  assert.match(view.label, /not reported yet/);
});

test("every dash names the absence instead of reading as a value", () => {
  // The rule the whole strip is built around, applied to every branch that can
  // render a dash. `absent` is not `0`.
  const branches = {
    "read failed": connectivityView(null, true),
    "not loaded": connectivityView(null, false),
    "not measured": connectivityView(toConnectivity(NOT_MEASURED), false),
    "online, latency absent": connectivityView(toConnectivity({ ...ONLINE, latency_ms: null }), false),
    "degraded, latency absent": connectivityView(
      toConnectivity({ ...ONLINE, state: "degraded", latency_ms: null, targets: [] }),
      false
    ),
  };
  for (const [name, view] of Object.entries(branches)) {
    assert.equal(view.value, "—", `${name}: expected the absent glyph, got ${JSON.stringify(view.value)}`);
    assert.match(
      view.label,
      /not reported|unknown|unavailable|never/i,
      `${name}: a dash for "${view.label}" does not say what it means`
    );
  }
  // And the inverse: a measured value never renders as a dash.
  assert.notEqual(connectivityView(toConnectivity(ONLINE), false).value, "—");
  // A sub-millisecond measurement is real on loopback and must not round to 0.
  assert.equal(connectivityView(toConnectivity({ ...ONLINE, latency_ms: 0.4 }), false).value, "0.4 ms");
});

/* ══ 5. The strip actually mounts the control ════════════════════════════ */

test("the control lives in the strip and is disabled while a probe is in flight", () => {
  // A source pin rather than a render: mounting `VitalsStrip` needs the whole
  // lucide + ui graph, and what this asserts is that the retry control is
  // reachable from the strip at all, is a real button with a real handler, and
  // is disabled in flight. A double-click cannot create two probes of one link.
  const src = read("../components/WorkspaceVitals.tsx");
  const button = src.slice(src.indexOf("function ConnectivityRetry("));
  assert.match(button, /<button/, "the retry control must be a real button");
  assert.match(button, /onClick=\{onRetry\}/);
  assert.match(button, /disabled=\{pending\}/);
  assert.match(button, /aria-label=/, "an icon-plus-word control still needs an accessible name");
  assert.match(button, /Retrying…/, "an in-flight probe must say so rather than look idle");
  // …and it is only offered when the view says a recheck could change the answer.
  assert.match(src, /link\.canRetry && onRetryConnectivity/);
});

test("the retry handler does not shadow the client function it calls", () => {
  // A real bug this file was written against. The handler was first named
  // `retryConnectivity`, which is also the imported client function — and a
  // local `const` is in scope inside its own initialiser, so the probe call
  // inside it resolved to the handler itself. It type-checked, it read
  // correctly, and it would have recursed instead of ever probing the link.
  const src = read("../components/WorkspaceVitals.tsx");
  assert.doesNotMatch(
    src,
    /const retryConnectivity = useCallback/,
    "the handler must not take the imported client's name — it would call itself",
  );
  assert.match(src, /const onRetryConnectivity = useCallback/);
  assert.match(src, /await recheckConnectivity\(\)/);
});
