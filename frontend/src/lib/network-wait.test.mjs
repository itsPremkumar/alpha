// network-wait.test.mjs — the per-thread outage-timeline client and its bubble view.
//
// Two surfaces, tested for the same reason `network.test.mjs` tests its pair:
// the route is the only thing that knows what happened to this conversation, and
// the bubble is the only place a user will look. The failure modes that matter:
//
//   * an outage that already ended being erased from the timeline the moment it
//     resolved — the row is durable, and a view that read only the open wait
//     would erase the exact event the user came to read;
//   * `wait_seconds: null` rendered as `0s`, i.e. "the outage cost nothing"
//     presented over "we could not measure it";
//   * the recovery time read off `updated_at`, which also moves when a failed
//     resume merely writes its next backoff;
//   * `reported: false` (a memory backend) rendered as "no outages".
//
// The transport is a stub, so these assertions pin the real path and the real
// envelope mapping without a Gateway. `networkWaitTimeline` is the real exported
// derivation driven with the payloads the Gateway actually returns.
//
// node --test src/lib/*.test.mjs — no server, no browser, no DOM shim.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");
const transpile = (src) =>
  ts.transpileModule(src, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
const dataUrl = (code) => `data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`;
const load = (code) => import(`data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`);

/* ── The transport stub ───────────────────────────────────────────────────── */

const calls = [];
const STUB_URL = dataUrl(`
  let handler = () => { throw new Error("no stub configured"); };
  export function setHttpHandler(fn) { handler = fn; }
  export async function get(path) { return handler(path, "GET", undefined); }
  export async function send(path, method, payload) { return handler(path, method, payload); }
`);

const client = transpile(read("./network-wait.ts")).replace(/from\s+"\.\/http"/, `from "${STUB_URL}"`);
const view = transpile(read("./network-wait-view.ts"));
const {
  fetchThreadNetworkWaits,
  toThreadNetworkWaits,
} = await load(client);
const { networkWaitTimeline, networkWaitBubble, formatDuration, formatClock, waitUnbounded } = await load(view);

function stub(handler) {
  calls.length = 0;
  return import(`${STUB_URL}`).then((m) => m.setHttpHandler(handler));
}

/* ── Fixtures ─────────────────────────────────────────────────────────────── */

const LOST = "2026-10-10T18:42:05+00:00";
const BACK = "2026-10-10T18:47:27+00:00";

function payload(over = {}) {
  return {
    reported: true,
    reason: "",
    detail: "",
    connectivity: { reported: true, state: "online" },
    bounded: false,
    max_attempts: 0,
    waits: [
      {
        wait_id: "w2",
        run_id: "r2",
        state: "waiting",
        reason: "connectivity_lost",
        attempt: 3,
        first_waited_at: LOST,
        terminal_at: null,
        next_attempt_at: "2026-10-10T18:47:00+00:00",
        last_error: "gaierror",
        resumed_from_run_id: null,
      },
    ],
    open_wait: {
      wait_id: "w2",
      run_id: "r2",
      state: "waiting",
      reason: "connectivity_lost",
      attempt: 3,
      first_waited_at: LOST,
      terminal_at: null,
      next_attempt_at: "2026-10-10T18:47:00+00:00",
      last_error: "gaierror",
      resumed_from_run_id: null,
    },
    wait_seconds: null,
    total_waits: 1,
    returned_waits: 1,
    ...over,
  };
}

/* ── Route and verb pins ──────────────────────────────────────────────────── */

test("fetchThreadNetworkWaits reads the thread timeline and nothing else", async () => {
  await stub(async (path, method) => {
    calls.push({ path, method });
    return payload();
  });
  const result = await fetchThreadNetworkWaits("thread-1");
  assert.deepEqual(calls, [{ path: "/threads/thread-1/network-waits", method: "GET" }]);
  assert.equal(result.reported, true);
  assert.equal(result.waits.length, 1);
});

test("a thread id is escaped so it cannot break out of the path", async () => {
  await stub(async (path) => {
    calls.push({ path });
    return payload();
  });
  await fetchThreadNetworkWaits("a/../b");
  assert.equal(calls[0].path, "/threads/a%2F..%2Fb/network-waits");
});

test("a failed read rejects rather than resolving to an empty timeline", async () => {
  await stub(async () => {
    throw new Error("The parked-session store could not be read");
  });
  await assert.rejects(fetchThreadNetworkWaits("thread-1"), /parked-session store/);
});

/* ── Envelope mapping ─────────────────────────────────────────────────────── */

test("an open wait keeps terminal_at null rather than inventing a recovery time", () => {
  const mapped = toThreadNetworkWaits(payload());
  const wait = mapped.waits[0];
  assert.equal(wait.terminal_at, null);
  assert.equal(wait.first_waited_at, LOST);
  assert.equal(wait.state, "waiting");
});

test("wait_seconds stays null while the wait is open", () => {
  // The row has not ended, so no duration exists to report. A 0 here would read
  // as "the outage cost nothing" — the precise wrong number this field prevents.
  const mapped = toThreadNetworkWaits(payload());
  assert.equal(mapped.wait_seconds, null);
});

test("an absent max_attempts maps to null, never 0", () => {
  const mapped = toThreadNetworkWaits({ reported: true, bounded: null });
  assert.equal(mapped.max_attempts, null);
  assert.equal(mapped.bounded, null);
  assert.equal(mapped.total_waits, null);
});

test("an unfamiliar state string is preserved verbatim", () => {
  const mapped = toThreadNetworkWaits(payload({ waits: [{ wait_id: "w", state: "hibernating" }] }));
  assert.equal(mapped.waits[0].state, "hibernating");
});

test("a missing waits list maps to an empty array, and reported stays its own fact", () => {
  const mapped = toThreadNetworkWaits({ reported: false, reason: "network_wait_store_unavailable" });
  assert.deepEqual(mapped.waits, []);
  assert.equal(mapped.reported, false);
  assert.equal(mapped.open_wait, null);
});

/* ── The bubble derivation ────────────────────────────────────────────────── */

test("an open wait reads as waiting, not as failed", async () => {
  const [bubble] = networkWaitTimeline(toThreadNetworkWaits(payload()), false, Date.parse(BACK));
  assert.equal(bubble.kind, "waiting");
  assert.equal(bubble.tone, "amber");
  assert.match(bubble.detail, /waiting for the internet/i);
  assert.match(bubble.detail, /automatically/i);
  assert.equal(bubble.backAt, null, "an open wait has no recovery time to show");
});

test("an unbounded deployment promises patience in words", async () => {
  const [bubble] = networkWaitTimeline(toThreadNetworkWaits(payload()), false, Date.parse(BACK));
  assert.equal(bubble.unbounded, true);
  assert.match(bubble.detail, /for as long as it takes/);
});

test("a bounded deployment discloses that it will give up, rather than promising forever", async () => {
  const [bubble] = networkWaitTimeline(
    toThreadNetworkWaits(payload({ bounded: true, max_attempts: 24 })),
    false,
    Date.parse(BACK),
  );
  assert.equal(bubble.unbounded, false);
  assert.match(bubble.detail, /gives up after a set number of attempts/);
});

test("a settled wait shows BOTH timestamps and the measured duration", () => {
  const mapped = toThreadNetworkWaits({
    reported: true,
    bounded: false,
    waits: [
      {
        wait_id: "w1",
        state: "resumed",
        reason: "connectivity_lost",
        attempt: 1,
        first_waited_at: LOST,
        terminal_at: BACK,
        next_attempt_at: null,
        last_error: null,
        resumed_from_run_id: "r9",
      },
    ],
    open_wait: null,
    wait_seconds: null,
  });
  const [bubble] = networkWaitTimeline(mapped, false, Date.parse(BACK) + 60_000);
  assert.equal(bubble.kind, "resumed");
  assert.equal(bubble.tone, "green");
  assert.notEqual(bubble.lostAt, null);
  assert.notEqual(bubble.backAt, null);
  assert.equal(bubble.waited, "5m 22s", "322s measured between the two stamps");
  assert.match(bubble.detail, /5m 22s/);
  assert.match(bubble.detail, /Picking up where I left off/);
});

test("a settled wait does NOT show a live counter — the server measured it", () => {
  // The bubble for a settled row uses the two stamps, not this browser's clock:
  // the client was not running for that interval, so its arithmetic would be
  // presented as the runtime's.
  const mapped = toThreadNetworkWaits({
    reported: true,
    bounded: false,
    waits: [
      {
        wait_id: "w1",
        state: "resumed",
        reason: "connectivity_lost",
        attempt: 1,
        first_waited_at: LOST,
        terminal_at: BACK,
        next_attempt_at: null,
        last_error: null,
        resumed_from_run_id: null,
      },
    ],
    open_wait: null,
  });
  const [bubble] = networkWaitTimeline(mapped, false, Date.parse(BACK) + 3_600_000);
  assert.equal(bubble.waited, "5m 22s", "an hour of browser time must not inflate a settled duration");
});

test("a wait with an unmeasurable duration renders no duration, never 0s", () => {
  const mapped = toThreadNetworkWaits({
    reported: true,
    bounded: false,
    waits: [
      {
        wait_id: "w1",
        state: "resumed",
        reason: "connectivity_lost",
        attempt: 1,
        first_waited_at: LOST,
        terminal_at: null, // the connection time was never stamped
        next_attempt_at: null,
        last_error: null,
        resumed_from_run_id: null,
      },
    ],
    open_wait: null,
  });
  const [bubble] = networkWaitTimeline(mapped, false, Date.parse(BACK));
  assert.equal(bubble.waited, null);
  assert.ok(!/0s/.test(bubble.detail), "a settled wait must not claim it cost nothing");
});

test("a gave_up wait says the wait stopped and names the server's reason", () => {
  const mapped = toThreadNetworkWaits({
    reported: true,
    bounded: true,
    max_attempts: 3,
    waits: [
      {
        wait_id: "w1",
        state: "gave_up",
        reason: "connectivity_lost",
        attempt: 3,
        first_waited_at: LOST,
        terminal_at: BACK,
        next_attempt_at: null,
        last_error: "gave up after 3 resume attempts",
        resumed_from_run_id: null,
      },
    ],
    open_wait: null,
  });
  const [bubble] = networkWaitTimeline(mapped, false, Date.parse(BACK));
  assert.equal(bubble.kind, "gave_up");
  assert.equal(bubble.tone, "red");
  assert.match(bubble.detail, /gave up after 3 resume attempts/);
});

test("an unreported timeline says so — it is not an empty one", () => {
  const mapped = toThreadNetworkWaits({
    reported: false,
    reason: "network_wait_store_unavailable",
    detail: "A `database.backend: memory` deployment has nowhere durable to record a park.",
    bounded: false,
    max_attempts: 0,
    waits: [],
    open_wait: null,
  });
  const [bubble] = networkWaitTimeline(mapped, false, Date.now());
  assert.equal(bubble.kind, "unavailable");
  assert.equal(bubble.tone, "gray");
  assert.match(bubble.detail, /nowhere durable to record a park/);
});

test("a failed read is disclosed, not rendered as 'no outages'", () => {
  const [bubble] = networkWaitTimeline(null, true, Date.now());
  assert.equal(bubble.kind, "unavailable");
  assert.match(bubble.detail, /could not be read/);
  assert.match(bubble.detail, /unknown rather than fine/);
});

test("a thread that never lost its internet renders no bubble at all", () => {
  const mapped = toThreadNetworkWaits({ reported: true, bounded: false, waits: [], open_wait: null });
  assert.deepEqual(networkWaitTimeline(mapped, false, Date.now()), []);
});

test("every park is rendered, including the ones already over", () => {
  // The regression this pins: a UI reading only the open wait erases an outage
  // the moment it resolves, which is exactly the event the user came to read.
  const mapped = toThreadNetworkWaits({
    reported: true,
    bounded: false,
    waits: [
      { wait_id: "w3", state: "waiting", reason: "connectivity_lost", attempt: 0, first_waited_at: LOST, terminal_at: null },
      {
        wait_id: "w2",
        state: "resumed",
        reason: "connectivity_lost",
        attempt: 1,
        first_waited_at: LOST,
        terminal_at: BACK,
        resumed_from_run_id: "r2",
      },
      { wait_id: "w1", state: "resumed", reason: "connectivity_lost", attempt: 2, first_waited_at: LOST, terminal_at: BACK },
    ],
    open_wait: { wait_id: "w3", state: "waiting", reason: "connectivity_lost", attempt: 0, first_waited_at: LOST },
  });
  const bubbles = networkWaitTimeline(mapped, false, Date.parse(BACK));
  assert.equal(bubbles.length, 3);
  assert.deepEqual(bubbles.map((b) => b.kind), ["waiting", "resumed", "resumed"]);
});

test("an unfamiliar settled state does not claim the work resumed", () => {
  // It reaches the "ended" branch, but never claims a continuation was launched.
  const mapped = toThreadNetworkWaits({
    reported: true,
    bounded: false,
    waits: [{ wait_id: "w1", state: "hibernating", reason: "x", attempt: 1, first_waited_at: LOST, terminal_at: BACK }],
    open_wait: null,
  });
  const [bubble] = networkWaitTimeline(mapped, false, Date.parse(BACK));
  assert.equal(bubble.resumed_from_run_id, undefined);
  assert.match(bubble.detail, /run was not relaunched/);
});

/* ── Duration and stamp formatting ────────────────────────────────────────── */

test("formatDuration keeps null rather than inventing zero", () => {
  assert.equal(formatDuration(null), null);
  assert.equal(formatDuration(NaN), null);
  assert.equal(formatDuration(-1), null);
  assert.equal(formatDuration(0), "0s");
  assert.equal(formatDuration(45), "45s");
  assert.equal(formatDuration(322), "5m 22s");
  // A whole unit renders without padding: "1h", not "1h 00m". Padding a zero
  // into a rendered string is the same defect as rendering 0s for "unknown".
  assert.equal(formatDuration(300), "5m");
  assert.equal(formatDuration(3600), "1h");
  assert.equal(formatDuration(3900), "1h 05m");
});

test("formatClock refuses an unreadable stamp rather than showing the epoch", () => {
  assert.equal(formatClock(null), null);
  assert.equal(formatClock(""), null);
  assert.equal(formatClock("not-a-time"), null);
  // A real stamp renders; it is not compared here because the assertion would
  // depend on the runner's timezone.
  assert.match(formatClock(LOST), /^\d{2}:\d{2}:\d{2}$/);
});

/* ── The three-state patience question ────────────────────────────────────── */

test("waitUnbounded answers null when the backend did not say", () => {
  // `null` must not be read as `false` (hinting at a deadline nobody declared)
  // nor as `true` (promising patience nobody promised).
  assert.equal(waitUnbounded(null), null);
  assert.equal(waitUnbounded(toThreadNetworkWaits({ reported: true, bounded: null })), null);
  assert.equal(waitUnbounded(toThreadNetworkWaits(payload({ bounded: false }))), true);
  assert.equal(waitUnbounded(toThreadNetworkWaits(payload({ bounded: true }))), false);
});

/* ── The single-bubble helper is reachable on its own ─────────────────────── */

test("networkWaitBubble renders one park without the wrapper", () => {
  const wait = toThreadNetworkWaits(payload()).waits[0];
  const bubble = networkWaitBubble(wait, { unbounded: true, nowMs: Date.parse(LOST) + 65_000 });
  assert.equal(bubble.kind, "waiting");
  assert.equal(bubble.waited, "1m 05s");
  assert.equal(bubble.attempts, 3);
});

/* ── The mount and the component's own claims ─────────────────────────────── */

const readComponent = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");

test("the bubbles are mounted in the chat transcript", () => {
  const view = readComponent("../components/ChatView.tsx");
  assert.match(view, /import \{ NetworkWaitBubbles \} from "@\/components\/NetworkWaitBubbles"/);
  assert.match(view, /<NetworkWaitBubbles threadId=\{networkWaitThreadId\} \/>/);
});

test("the mount is not gated on isLoading", () => {
  // A run parked on a dead link is terminal from the run's point of view, so an
  // `isLoading`-only mount would hide the bubble during exactly the wait the
  // user most needs to see. The settled history must also survive a reload,
  // which is why this reads a durable timeline rather than a live stream.
  const view = readComponent("../components/ChatView.tsx");
  const index = view.indexOf("<NetworkWaitBubbles");
  const before = view.slice(Math.max(0, index - 400), index);
  assert.doesNotMatch(before, /isLoading &&/, "the bubble must not be gated on the loading state");
});

test("a browser-only local thread is never polled", () => {
  // `local-*` rows have no server id, so a park could never have been recorded
  // for them and every poll would be a guaranteed 404.
  const view = readComponent("../components/ChatView.tsx");
  assert.match(view, /networkWaitThreadId/);
  assert.match(view, /!activeThreadId\.startsWith\("local-"\)/);
});

test("the component renders the derived bubbles rather than branching on a state", () => {
  // One derivation, one source of truth: a second copy of the wording is how a
  // bubble ends up contradicting itself.
  const component = readComponent("../components/NetworkWaitBubbles.tsx");
  assert.match(component, /networkWaitTimeline/);
  assert.doesNotMatch(
    component,
    /state === "waiting"\s*&&\s*<h4>|waiting for the internet<\/h4>/i,
    "the waiting sentence must come from the view module, not from markup",
  );
});

test("a failed read keeps its error visible instead of rendering an empty timeline", () => {
  const component = readComponent("../components/NetworkWaitBubbles.tsx");
  assert.match(component, /setError\(/);
  assert.match(component, /The outage timeline could not be read/);
});

test("the retry re-reads the timeline and paints nothing from the click", () => {
  const component = readComponent("../components/NetworkWaitBubbles.tsx");
  // A recheck measures the link, not the timeline, so the timeline is re-read
  // after it — exactly the rule `WorkspaceVitals` follows for its own Retry.
  assert.match(component, /await recheckConnectivity\(\);\s*\n\s*await load\(\);/);
});

test("the poll keeps running when nothing is parked", () => {
  // The obvious optimisation — stop the interval once every park is settled,
  // because "the timeline cannot change without a new run" — is wrong: the next
  // run in this thread is exactly the thing that parks. A stopped poll means the
  // bubble appears only after a reload, which is not the live notice this is.
  const component = readComponent("../components/NetworkWaitBubbles.tsx");
  const at = component.indexOf("setInterval(() => void load()");
  assert.ok(at > 0, "the timeline poll must exist");
  const effect = component.slice(at - 200, at + 200);
  assert.match(effect, /return \(\) => window\.clearInterval\(id\)/);
  assert.doesNotMatch(
    effect,
    /open_wait/,
    "the poll interval must not be gated on an open wait existing",
  );
});
