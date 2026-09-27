// test_fe_audit_teamops_read_honesty.test.mjs — the rest of lib/teamops.ts must
// honour the contract its own test file already declares.
//
// `teamops.test.mjs:1-2` states it: "owner-visible reads reject on failure", and
// `teamops.test.mjs:34` pins it for `listSwarms`. `listSwarms` was migrated;
// `listGroups`, `listJobs`, `listMcpTasks` and `companyKpis` were not, and each
// still ends in `catch { return [] }`.
//
// The user-visible consequence is the dead error path the board already measured
// once for `ChannelsSection.tsx` (row F11). `TeamOpsSection.load()` reads all
// four inside one `try` whose `catch` calls `setError(errMsg(e))`:
//
//   src/components/sections/TeamOpsSection.tsx:76
//     const [g, s, j] = await Promise.all([listGroups(), listSwarms(), listJobs()]);
//   src/components/sections/TeamOpsSection.tsx:81
//     setMcpTasks(await listMcpTasks(props.threadId));
//   src/components/sections/TeamOpsSection.tsx:83
//     const [d, k] = await Promise.all([executiveDigest(), companyKpis()]);
//   src/components/sections/TeamOpsSection.tsx:86-87
//     } catch (e) { setError(errMsg(e)); }
//
// Because the swallowing clients always resolve, `Promise.all` always resolves
// and that `setError` is unreachable for a /groups, /jobs, /mcp-tasks or
// /company/kpis failure. The section then renders "no groups", "no jobs",
// "no MCP tasks" or an empty KPI row for a Gateway that is DOWN, with no
// disclosure — the exact opposite of "an empty list must mean the server said
// there was nothing, not that the call failed".
//
// Pure Node test: transpiles the real teamops.ts with only its ./http import
// stubbed, the same way teamops.test.mjs does. No server, no browser.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const HERE = fileURLToPath(new URL(".", import.meta.url));
const FRONTEND = join(HERE, "..", "..");

const httpStub = `
let handler = (path, method, payload) => { throw new Error("no stub response configured"); };
export function setHttpHandler(fn) { handler = fn; }
export async function get(path) { return handler(path, "GET", undefined); }
export async function send(path, method, payload) { return handler(path, method, payload); }
export function pick(obj, keys, fallback) {
  if (obj && typeof obj === "object") {
    for (const key of keys) if (obj[key] !== undefined && obj[key] !== null) return obj[key];
  }
  return fallback;
}
export function asList(body, keys) {
  if (Array.isArray(body)) return body;
  for (const key of keys) if (body && typeof body === "object" && Array.isArray(body[key])) return body[key];
  return [];
}
`;
const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;
const stubUrl = toDataUrl(httpStub);
const source = readFileSync(join(HERE, "teamops.ts"), "utf8");
const code = ts
  .transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  .outputText.replace(/from\s+"\.\/http"/, `from "${stubUrl}"`);
const teamops = await import(toDataUrl(code));
const { setHttpHandler } = await import(stubUrl);

const GATEWAY_DOWN = () => {
  throw new Error("gateway unavailable");
};

test("listGroups rejects on a failed read instead of reporting zero rooms", async () => {
  setHttpHandler(GATEWAY_DOWN);
  await assert.rejects(
    () => teamops.listGroups(),
    /gateway unavailable/,
    "listGroups resolved [] for a down Gateway, so TeamOps renders 'no rooms' as if the server said so",
  );
});

test("listJobs rejects on a failed read instead of reporting zero jobs", async () => {
  setHttpHandler(GATEWAY_DOWN);
  await assert.rejects(
    () => teamops.listJobs(),
    /gateway unavailable/,
    "listJobs resolved [] for a down Gateway",
  );
});

test("listMcpTasks rejects on a failed read instead of reporting zero tasks", async () => {
  setHttpHandler(GATEWAY_DOWN);
  await assert.rejects(
    () => teamops.listMcpTasks("thread-1"),
    /gateway unavailable/,
    "listMcpTasks resolved [] for a down Gateway",
  );
});

test("companyKpis rejects on a failed read instead of rendering an empty KPI row", async () => {
  setHttpHandler(GATEWAY_DOWN);
  await assert.rejects(
    () => teamops.companyKpis(),
    /gateway unavailable/,
    "companyKpis resolved [] for a down Gateway",
  );
});

test("a genuinely empty response is still an empty list, and a real one still maps", async () => {
  setHttpHandler((path) => {
    if (path === "/groups") return { rooms: [] };
    if (path === "/jobs") return { jobs: [] };
    throw new Error(`unexpected path ${path}`);
  });
  assert.deepEqual(await teamops.listGroups(), [], "a server-sent empty room list must stay empty");
  assert.deepEqual(await teamops.listJobs(), [], "a server-sent empty job list must stay empty");

  setHttpHandler((path) => {
    if (path === "/groups") return { rooms: [{ name: "alpha", members: ["a", "b"], status: "active" }] };
    if (path === "/jobs") return { jobs: [{ id: "j-1", kind: "reindex", status: "running" }] };
    throw new Error(`unexpected path ${path}`);
  });
  const groups = await teamops.listGroups();
  assert.equal(groups.length, 1);
  assert.equal(groups[0].name, "alpha");
  const jobs = await teamops.listJobs();
  assert.equal(jobs.length, 1);
  assert.equal(jobs[0].id, "j-1");
  assert.equal(jobs[0].status, "running");
});

test("every TeamOps read inside load() sits in a try whose catch discloses the reason", async () => {
  // The client fix is only half of it: if the section did not surface the
  // rejection, the failure would move from a silent empty list to an unhandled
  // rejection instead.
  const src = readFileSync(join(FRONTEND, "src", "components", "sections", "TeamOpsSection.tsx"), "utf8");

  const load = src.match(/const load = async \(\) => \{[\s\S]*?\n  \};/);
  assert.ok(load, "TeamOpsSection must still have its load() function");
  const body = load[0];

  for (const call of ["listGroups()", "listSwarms()", "listJobs()", "listMcpTasks(", "companyKpis()"]) {
    assert.ok(body.includes(call), `load() must still read ${call}`);
  }
  assert.match(
    body,
    /catch\s*\(e\)\s*\{\s*setError\(errMsg\(e\)\);/,
    "load() must keep a catch that discloses the server's reason; without it a rejection is unhandled",
  );
});
