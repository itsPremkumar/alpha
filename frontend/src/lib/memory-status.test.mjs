// memory-status.test.mjs — the GET /memory/status client and the surface that
// renders it.
//
// `GET /api/memory/status` returns { config, data, health? }. The `health` row
// is the extraction pipeline's own verdict (ok/degraded/disabled/unknown plus
// failure counters and a server-masked error). This file pins three things:
//   1. the route/verb and strict field mapping (absent → null, unknown → verbatim),
//   2. the honesty inversions (`health` missing is *not* health; `null` counts
//      are never coerced to 0; a failed read rejects instead of rendering healthy),
//   3. the MemorySection render claims (unavailable line, no-`status.enabled`
//      dead badge, process-restart basis, no claimed health).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

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

const source = readFileSync(new URL("./memory.ts", import.meta.url), "utf8");
let code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
code = code.replace(/from\s+"\.\/http"/, `from "${stubUrl}"`);

const memory = await import(toDataUrl(code));
const { setHttpHandler, ApiError } = await import(stubUrl);

const fullHealth = {
  status: "degraded",
  reason: "update_memory returned False on the last extraction attempt",
  extraction_enabled: true,
  model: "deepseek-chat",
  model_source: "backend_config.model",
  last_attempt_at: "2026-10-16T08:04:00+00:00",
  last_success_at: "2026-10-14T21:12:00+00:00",
  last_failure_at: "2026-10-16T08:04:00+00:00",
  last_error_type: "RejectedExtraction",
  last_error: "Bearer [redacted] was refused by the provider",
  last_error_scope: "users/default/agents/lead-agent",
  consecutive_failures: 2,
  total_successes: 41,
  total_failures: 2,
};

test("fetchMemoryStatus reads exactly GET /memory/status and maps config", async () => {
  setHttpHandler((path, method) => {
    assert.equal(path, "/memory/status");
    assert.equal(method, "GET");
    return {
      config: { enabled: true, mode: "middleware", injection_enabled: true, manager_class: "alpha.agents.memory.backends.deermem.backends.deer_mem", backend_config: {} },
      data: { facts: [{ id: "f1", content: "x" }] },
      health: fullHealth,
    };
  });
  const status = await memory.fetchMemoryStatus();
  assert.deepEqual(status.config, {
    enabled: true,
    mode: "middleware",
    injection_enabled: true,
    manager_class: "alpha.agents.memory.backends.deermem.backends.deer_mem",
  });
  assert.deepEqual(status.health, fullHealth);
});

test("a degraded health row maps counters as numbers, not strings", async () => {
  // Guards against the string/number swap that would make "not reported"
  // indistinguishable from 0 and vice versa.
  setHttpHandler(() => ({ config: { enabled: true }, health: fullHealth }));
  const status = await memory.fetchMemoryStatus();
  assert.equal(status.health.consecutive_failures, 2);
  assert.equal(typeof status.health.consecutive_failures, "number");
  assert.equal(status.health.total_failures, 2);
  assert.equal(status.health.last_error, "Bearer [redacted] was refused by the provider");
});

test("a missing health field maps to null — no health statement is not a health claim", async () => {
  // Backends before the health hook omit the field entirely
  // (response_model_exclude_none). Rendering that as ok/green would claim
  // health nobody reported.
  setHttpHandler(() => ({ config: { enabled: true, mode: "middleware" }, data: {} }));
  const status = await memory.fetchMemoryStatus();
  assert.equal(status.health, null);
  assert.equal(status.config.enabled, true);
});

test("a non-dict health payload degrades to null the way the route intends", async () => {
  setHttpHandler(() => ({ config: { enabled: true }, data: {}, health: "not-a-dict" }));
  const status = await memory.fetchMemoryStatus();
  assert.equal(status.health, null);
});

test("unknown status words and null fields pass through verbatim — never coerced", async () => {
  // extraction_enabled: null means "not determined yet" (backend not built
  // yet); coercing it to false would name a problem that does not exist, and
  // coercing it to true would claim health. A newer server status word must
  // not disappear into one of today's known ones.
  setHttpHandler(() => ({
    config: { enabled: null, mode: null },
    data: {},
    health: {
      status: "circuit-breaker-reset",
      reason: null,
      extraction_enabled: null,
      model: null,
      model_source: null,
      last_attempt_at: null,
      last_success_at: null,
      last_failure_at: null,
      last_error_type: null,
      last_error: null,
      last_error_scope: null,
      consecutive_failures: null,
      total_successes: null,
      total_failures: null,
    },
  }));
  const status = await memory.fetchMemoryStatus();
  assert.equal(status.health.status, "circuit-breaker-reset");
  assert.equal(status.health.extraction_enabled, null);
  assert.equal(status.health.total_failures, null);
  assert.equal(status.config.enabled, null);
  assert.equal(status.config.mode, null);
});

test("a failed status read rejects with the Gateway's reason — never renders as a healthy null", async () => {
  // A minimal backend answers 501 (get memory unsupported); an outage
  // answers 0/network. Both must reach the UI; the old
  // `memoryStatus().catch(() => null)` folded them into a silent blank.
  setHttpHandler((path, method) => {
    throw new ApiError(501, "This memory backend doesn't support 'get memory status'.");
  });
  await assert.rejects(
    () => memory.fetchMemoryStatus(),
    (err) => err instanceof ApiError && err.status === 501 && /get memory status/.test(err.message),
  );
});

test("MemorySection renders the extraction row honestly", () => {
  const src = readFileSync(
    new URL("../components/sections/MemorySection.tsx", import.meta.url),
    "utf8",
  );
  // config.enabled, not the dead `status.enabled` read that never rendered.
  assert.match(src, /status\.config\.enabled/);
  assert.doesNotMatch(src, /typeof status\.enabled/);
  // All four states of the extraction badge, plus the verbatim unknown word.
  assert.match(src, /"extraction ok"/);
  assert.match(src, /"extraction degraded"/);
  assert.match(src, /"extraction disabled"/);
  assert.match(src, /extraction health not reported/);
  assert.match(src, /`extraction: \$\{health\.status\}`/);
  // A failed status read names its reason instead of disappearing.
  assert.match(src, /Memory status is unavailable: \{statusError\}/);
  assert.doesNotMatch(src, /fetchMemoryStatus\(\)\.catch\(\(\) => null\)/);
  // The no-health disclosure says unknown, not healthy.
  assert.match(src, /the answer is unknown, not healthy/);
  // Counters carry their basis: counted by this process since restart.
  assert.match(src, /since the last Gateway restart/);
  // The shown error is disclosed as masked server-side — the UI adds nothing.
  assert.match(src, /credential-masked by the server/);
  // No answer yet vs answered-and-omitted are different claims: while the read
  // is in flight (or failed) the badge must say checking (or nothing) — never
  // "extraction health not reported", which is a claim about what the server
  // sent and may only render after a successful read.
  assert.match(src, /status && !statusError \? extractionHealthBadge\(status\.health\) : null/);
  assert.match(src, /extraction health: checking/);
  // The compact health badge itself only renders after a successful status read.
  assert.match(src, /status && !statusError \? extractionHealthBadge\(status\.health\) : null/);
});
