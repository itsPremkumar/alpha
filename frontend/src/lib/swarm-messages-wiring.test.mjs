// swarm-messages-wiring.test.mjs — the swarm blackboard surface must be a real,
// reachable route, and a broken one must never look like an empty blackboard.
//
// Two independent proofs, because the defect was invisible from either side
// alone:
//   1. CONTRACT / AUDIT PROOF. Replays the repo's own zero-unwired-features
//      matcher (scripts/audit_frontend_wiring.py) in JS against the routes
//      parsed out of the real backend router, and requires the verdict for
//      teamops.ts's message read to be OK — 0 MISSING, 0 SHADOWED. Before the
//      fix the call site read `/swarms/${id}/messages${query}`, the matcher
//      expanded it to `/api/swarms//messages` and `/api/swarms/${id}
//      messages${query}`, and NEITHER matched `/api/swarms/{swarm_id}/messages`
//      — the single MISSING route this test now pins to zero.
//   2. HONESTY PROOF. A failed read rejects (it never resolves to []), and the
//      panel's failure branch is rendered BEFORE — and instead of — its empty
//      branch, so an unreachable route cannot be drawn as "no messages".
//
// Pure Node (node --test src/lib/*.test.mjs): teamops.ts is transpiled and its
// relative import is rewritten to a data: URL stub. No server, no browser.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");
const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;

/* ── teamops.ts under a recording http stub ──────────────────────────────── */

const httpStub = `
const calls = [];
let fail = false;
let response = [];
export function setHttpFail(v) { fail = v; }
export function setResponse(body) { response = body; }
export function recorded() { return calls; }
export function reset() { calls.length = 0; }
export async function get(path) {
  calls.push({ path, method: "GET" });
  if (fail) throw new Error("gateway down");
  return response;
}
export async function send(path, method, payload) {
  calls.push({ path, method, payload });
  if (fail) throw new Error("gateway down");
  return response;
}
export function asList(body, keys) {
  if (Array.isArray(body)) return body;
  for (const key of keys) if (body && typeof body === "object" && Array.isArray(body[key])) return body[key];
  return [];
}
export function pick(obj, keys, fallback) {
  if (obj && typeof obj === "object") for (const key of keys) if (obj[key] !== undefined && obj[key] !== null) return obj[key];
  return fallback;
}
`;
const httpStubUrl = toDataUrl(httpStub);
const teamopsCode = ts
  .transpileModule(read("./teamops.ts"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  .outputText.replace(/from\s+"\.\/http"/, `from "${httpStubUrl}"`);
const teamops = await import(toDataUrl(teamopsCode));
const httpStubApi = await import(httpStubUrl);

/* ── the audit's matcher, ported so the verdict is the audit's own ───────── */

const CALL_RE = /\b(apiFetch|req|get|send|fetch|EventSource|axios(?:\.\w+)?)\s*(?:<[^()]*>)?\s*\(\s*(["'`])([^"'`]*)/g;

/** Path literals the audit captures as API call sites, keyed by path. */
function auditCallPaths(source) {
  const found = new Map();
  source.split(/\r?\n/).forEach((line, i) => {
    CALL_RE.lastIndex = 0;
    let m;
    while ((m = CALL_RE.exec(line)) !== null) found.set(m[3], i + 1);
  });
  return found;
}

/** audit_frontend_wiring.normalize(): the browser always addresses /api/... */
const normalize = (path) => {
  const head = path.split("?")[0];
  if (head === "/api") return "/api";
  const suffix = head.startsWith("/api/") ? head.slice(4) : head;
  return "/api" + (suffix.startsWith("/") ? suffix : "/" + suffix);
};

/** audit_frontend_wiring.canonical_subjects(): literal text, then ${...} removed. */
const canonicalSubjects = (norm) => {
  const truncated = norm.replace(/\$\{[^}]*$/, "");
  return [truncated, truncated.replace(/\$\{[^}]*\}/g, "")];
};

/** audit_frontend_wiring.gateway_pattern(): {param} -> exactly one segment. */
const gatewayPattern = (route) => {
  const pieces = route.replace(/\{[^}]+\}/g, "\u0000").split("\u0000");
  const escaped = pieces.map((piece) => piece.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp("^" + escaped.join("[^/]+") + "$");
};

/** The routes a FastAPI router module really declares, prefix included. */
function routerRoutes(rel) {
  const src = readFileSync(new URL(`../../../${rel}`, import.meta.url), "utf8");
  const prefix = /APIRouter\(\s*prefix="([^"]+)"/.exec(src)?.[1] ?? "";
  const routes = new Set();
  const decorator = /@router\.(?:get|post|put|patch|delete)\(\s*"([^"]*)"/g;
  let m;
  while ((m = decorator.exec(src)) !== null) routes.add(prefix + m[1]);
  return [...routes];
}

const SWARM_ROUTER = "backend/app/gateway/routers/swarms.py";
const swarmRoutes = routerRoutes(SWARM_ROUTER);

test("the swarm router really declares and mounts the blackboard read", () => {
  // The route exists in the router...
  assert.ok(
    swarmRoutes.includes("/api/swarms/{swarm_id}/messages"),
    `swarms.py no longer declares /api/swarms/{swarm_id}/messages; found: ${swarmRoutes.join(", ")}`,
  );
  // ...and the router is actually included by the Gateway app (mount point).
  const app = readFileSync(new URL("../../../backend/app/gateway/app.py", import.meta.url), "utf8");
  assert.match(app, /^\s*swarms,$/m);
  assert.match(app, /app\.include_router\(swarms\.router\)/);
});

test("the blackboard read is one complete route path, not a path plus a glued query suffix", () => {
  // The route path must be a finished path literal. A `?…`/`${query}` tail
  // inside the template is what made the call site unresolvable for the audit.
  const messageCalls = [...auditCallPaths(read("./teamops.ts")).keys()].filter(
    (path) => path.startsWith("/swarms/") && path.includes("/messages"),
  );
  assert.deepEqual(
    messageCalls,
    ["/swarms/${encodeURIComponent(id)}/messages"],
    "the swarm message route path must be a bare, complete path literal",
  );
  for (const path of messageCalls) assert.doesNotMatch(path, /[?${][^}]*$/);
});

test("audit verdict for the blackboard read is OK — 0 MISSING, 0 SHADOWED", () => {
  const source = read("./teamops.ts");
  const swarmMessagePaths = [...auditCallPaths(source).keys()].filter(
    (path) => path.startsWith("/swarms/") && path.includes("/messages"),
  );
  assert.ok(swarmMessagePaths.length > 0, "no swarm message call site found to audit");

  const patterns = swarmRoutes.map((route) => ({ route, rx: gatewayPattern(route) }));
  for (const path of swarmMessagePaths) {
    const norm = normalize(path);
    const subjects = canonicalSubjects(norm);
    const matched = patterns.filter(({ rx }) => subjects.some((subject) => rx.test(subject)));
    assert.ok(matched.length > 0, `MISSING: no swarm route matches ${norm} (subjects: ${subjects.join(" | ")})`);
    const isTemplate = norm.includes("$");
    if (!isTemplate) {
      assert.ok(
        matched.some(({ route }) => route === norm),
        `SHADOWED: ${norm} is only captured by a parameterized route`,
      );
    }
    // The matched route is the documented read, not some unrelated one.
    assert.ok(
      matched.some(({ route }) => route === "/api/swarms/{swarm_id}/messages"),
      `${norm} resolved to ${matched.map((m) => m.route).join(", ")}`,
    );
  }
});

test("the read resolves against the real router for both the bare and filtered forms", () => {
  const patterns = swarmRoutes.map((route) => gatewayPattern(route));
  const read = gatewayPattern("/api/swarms/{swarm_id}/messages");
  // What the browser actually requests, once GATEWAY_BASE is prepended.
  const emitted = [
    "/api/swarms/swm%2Fa/messages",
    "/api/swarms/swm%2Fa/messages?topic=results",
    "/api/swarms/swm%2Fa/messages?topic=results&task_id=t-1&since_sequence=4&limit=25",
  ];
  for (const url of emitted) {
    const route = url.split("?")[0];
    assert.ok(read.test(route), `${route} is not the router's declared path`);
    assert.ok(patterns.some((rx) => rx.test(route)), `no route matches ${route}`);
  }
});

/* ── behaviour: the emitted request, and failure != empty ────────────────── */

test("swarmMessages reads the declared route and keeps the filter in the query string", async () => {
  httpStubApi.reset();
  httpStubApi.setHttpFail(false);
  httpStubApi.setResponse([]);
  await teamops.swarmMessages("swm/a");
  await teamops.swarmMessages("swm/a", "results");
  await teamops.swarmMessages("swm/a", {
    topic: "results",
    taskId: "t 1",
    sinceSequence: 4.9,
    limit: 9999,
  });
  assert.deepEqual(
    httpStubApi.recorded().map((call) => `${call.method} ${call.path}`),
    [
      "GET /swarms/swm%2Fa/messages",
      "GET /swarms/swm%2Fa/messages?topic=results",
      // The router clamps limit to 1..256 and since_sequence to >= 0; the
      // client must not ask for a window the server cannot serve.
      "GET /swarms/swm%2Fa/messages?topic=results&task_id=t+1&since_sequence=4&limit=256",
    ],
  );
});

test("a failed blackboard read rejects instead of resolving to an empty list", async () => {
  httpStubApi.reset();
  httpStubApi.setHttpFail(true);
  try {
    await assert.rejects(() => teamops.swarmMessages("swm/a"), /gateway down/);
  } finally {
    httpStubApi.setHttpFail(false);
  }
});

test("an empty 200 is an empty blackboard, not a failure — and a real row is kept", async () => {
  httpStubApi.reset();
  httpStubApi.setHttpFail(false);
  httpStubApi.setResponse([]);
  assert.deepEqual(await teamops.swarmMessages("swm/a"), []);

  const row = {
    message_id: "m-1",
    swarm_id: "swm/a",
    sequence: 7,
    topic: "general",
    sender: "researcher",
    kind: "observation",
    content: "found it",
    task_id: null,
    trust: "untrusted",
    created_at: 1.5,
  };
  httpStubApi.setResponse([row]);
  const loaded = await teamops.swarmMessages("swm/a");
  assert.equal(loaded.length, 1);
  assert.equal(loaded[0].message_id, "m-1");
  assert.equal(loaded[0].sequence, 7);
  assert.equal(loaded[0].content, "found it");
});

/* ── the panel: a failure renders as an error, not as an empty blackboard ─── */

const PANEL_FILE = "../components/sections/TeamOpsSection.tsx";

/** Source of one top-level function, brace-matched. */
function functionSource(name) {
  const src = read(PANEL_FILE);
  const start = src.indexOf(`function ${name}(`);
  assert.ok(start > 0, `${PANEL_FILE} has no ${name} — the swarm blackboard surface is gone`);
  const open = src.indexOf("{", src.indexOf(")", start));
  let depth = 0;
  for (let i = open; i < src.length; i += 1) {
    if (src[i] === "{") depth += 1;
    else if (src[i] === "}") {
      depth -= 1;
      if (depth === 0) return src.slice(open, i + 1);
    }
  }
  throw new Error(`unbalanced braces while reading ${name}`);
}

test("the Team ops swarms tab renders the blackboard through the declared route", () => {
  const src = read(PANEL_FILE);
  // The surface is wired to the client at all…
  assert.match(src, /swarmMessages\(/);
  assert.match(src, /publishSwarmMessage\(/);
  // …and opened per swarm.
  assert.match(src, /<SwarmMessagesPanel swarmId=\{s\.id\} \/>/);
  assert.match(src, /openSwarmBoard === s\.id &&/);
});

test("a failed blackboard read renders an error, never the empty state", () => {
  const panel = functionSource("SwarmMessagesPanel");
  // The failure is its own state, kept next to the rows it invalidates.
  assert.match(panel, /const \[loadError, setLoadError\] = useState<string \| null>\(null\)/);
  // A rejection is recorded as a failure…
  assert.match(panel, /catch \(e\) \{\s*setMessages\(\[\]\);\s*setLoadError\(errMsg\(e\)\);/);
  // …and is never folded into a silent empty list.
  assert.doesNotMatch(panel, /\.catch\(\(\) => setMessages\(\[\]\)\)/);
  assert.doesNotMatch(panel, /swarmMessages\([^)]*\)\.catch\(/);
  // The failure branch is checked BEFORE the empty branch, so an unreachable
  // route can never be rendered as "no messages".
  const failureBranch = panel.indexOf("loadError ? (");
  const emptyBranch = panel.indexOf("messages.length === 0");
  assert.ok(failureBranch > 0, "the panel has no failed-read branch");
  assert.ok(emptyBranch > failureBranch, "the empty branch would win over the failure branch");
  // The failure branch shows the real reason and offers a retry.
  const failureSource = panel.slice(failureBranch, emptyBranch);
  assert.match(failureSource, /ErrorBox/);
  assert.match(failureSource, /loadError/);
  assert.match(failureSource, /onRetry=\{load\}/);
  // The empty state stays reserved for a read that actually succeeded.
  assert.match(
    panel.slice(emptyBranch, emptyBranch + 400),
    /No messages on this swarm blackboard yet\./,
  );
  // A bounded window is disclosed rather than passed off as the whole history.
  assert.match(panel, /newest \{SWARM_MESSAGE_WINDOW\} messages/);
});

test("a failed post is reported too — posting never renders as a silent success", () => {
  const panel = functionSource("SwarmMessagesPanel");
  assert.match(panel, /const \[postError, setPostError\] = useState<string \| null>\(null\)/);
  assert.match(panel, /catch \(e\) \{\s*setPostError\(errMsg\(e\)\);/);
  assert.match(panel, /\{postError && <ErrorBox/);
  assert.doesNotMatch(panel, /catch\(\(\) => setDraft\(""\)\)/);
});
