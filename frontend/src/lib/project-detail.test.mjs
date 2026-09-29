// project-detail.test.mjs — the Projects view's read-only digest.
//
// The Gateway exposes ~22 read-only GET /projects/{id}/* routes; this client
// reads the compact ones so a project can be understood from the list. Two
// properties are load-bearing and are pinned here:
//
//  * Every section reads INDEPENDENTLY. One route that fails must not blank the
//    eight that answered, and the failure has to carry the server's own reason —
//    a section that silently vanishes is indistinguishable from a project with
//    no decisions/events/constitution.
//  * Absent is not zero. A field the server did not send must map to `null`
//    (rendered "unknown"), never to 0 or "". An empty list means the server said
//    there is nothing; that is a real answer and is shown as one.
//
// The transport is stubbed, so the assertions run against the real request paths
// and the real mapping — not a regex over the source.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");

/**
 * Load the client with its `./http` dependency replaced by an inlined stub.
 *
 * The stub is injected as source rather than imported, because a `data:` URL
 * cannot resolve a relative specifier. It records every path the client asked
 * for, so the tests assert the real requests rather than a source regex.
 */
async function loadClient(routes) {
  // A stub route rejects by carrying `{ __error: "..." }`, not a real `Error`:
  // `JSON.stringify(new Error("x"))` is `{}`, so an Error passed here would
  // serialise to an empty object and the route would silently *resolve*,
  // inverting the very failure these tests are about.
  const serialisable = Object.fromEntries(
    Object.entries(routes ?? {}).map(([key, value]) => [
      key,
      value instanceof Error ? { __error: value.message } : value,
    ]),
  );
  const stub = `
    export const calls = [];
    const routes = ${JSON.stringify(serialisable)};
    export function get(path) {
      calls.push(path);
      const key = Object.keys(routes).find((k) => path === k || path.startsWith(k + "?"));
      if (key === undefined) return Promise.reject(new Error("No stub route for " + path));
      const value = routes[key];
      if (value && typeof value === "object" && typeof value.__error === "string") {
        return Promise.reject(new Error(value.__error));
      }
      return Promise.resolve(value);
    }
    export function pick(obj, keys, fallback) {
      if (obj && typeof obj === "object") {
        for (const k of keys) if (obj[k] !== undefined && obj[k] !== null) return obj[k];
      }
      return fallback;
    }
  `;
  const source = stub + read("./project-detail.ts").replace(/^import[^\n]*\n/m, "");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
}

const FULL_ROUTES = {
  "/projects/p/state": {
    project_id: "p",
    goal: "Ship the thing",
    phase: "build",
    active_agents: 2,
    active_tasks: 1,
    blocked_tasks: 0,
    completed_tasks: 3,
    failed_tasks: 0,
    open_conflicts: 0,
    open_risks: ["slow provider"],
    last_verified: null,
    latest_decision: null,
    arch_version: "v0.2",
    updated_at: "2026-09-28T00:00:00+00:00",
  },
  "/projects/p/decisions": { project_id: "p", decisions: [], count: 0 },
  "/projects/p/events": { project_id: "p", events: [] },
  "/projects/p/locks": { project_id: "p", locks: [], pending_requests: [] },
  "/projects/p/approvals": { project_id: "p", approvals: [] },
  "/projects/p/checkpoints": { project_id: "p", checkpoints: [] },
  "/projects/p/handoffs": { project_id: "p", handoffs: [] },
  "/projects/p/constitution": { project_id: "p", present: true, template: "# C" },
};

test("every path is a real read-only project route, with the id as one segment", async () => {
  const client = await loadClient(FULL_ROUTES);
  await client.fetchProjectDetail("proj 1/../x");
  assert.ok(client.calls.length > 0, "no requests were made");
  // The id is hostile on purpose. `encodeURIComponent` does not encode `.`, so the
  // literal is `proj%201%2F..%2Fx` — but the `/` and space ARE encoded, so it stays
  // a single path segment and cannot climb out of /projects/.
  const encoded = encodeURIComponent("proj 1/../x");
  for (const path of client.calls) {
    assert.equal(
      path,
      `/projects/${encoded}${path.slice(path.lastIndexOf("/"))}`,
      `the id must be one encoded segment in ${path}`,
    );
    assert.equal(
      path.split("/").filter(Boolean).length,
      3,
      `path must not gain a segment from the id: ${path}`,
    );
  }
  for (const suffix of [
    "/state",
    "/decisions",
    "/events",
    "/locks",
    "/approvals",
    "/checkpoints",
    "/handoffs",
    "/constitution",
  ]) {
    assert.ok(client.calls.includes(`/projects/${encoded}${suffix}`), `missing ${suffix}`);
  }
  // Read-only: the digest must never mutate the project.
  const source = read("./project-detail.ts");
  assert.doesNotMatch(source, /\bsend\s*[<(]/, "the digest must not write to the project");
  assert.doesNotMatch(source, /method:\s*"(POST|PATCH|PUT|DELETE)"/, "no mutating verb");
});

test("a failing section carries the server reason and the rest still answer", async () => {
  const client = await loadClient({
    ...FULL_ROUTES,
    "/projects/p/events": new Error("502 upstream is down"),
  });
  const detail = await client.fetchProjectDetail("p");

  assert.equal(detail.events.status, "error");
  assert.equal(detail.events.error, "502 upstream is down", "the server's own reason must survive");
  assert.equal(detail.state.status, "ok", "one failure must not blank the sections that answered");
  assert.equal(detail.state.data.goal, "Ship the thing");
  assert.equal(detail.decisions.status, "ok");
  assert.equal(detail.constitution.status, "ok");
  assert.equal(detail.partial, true, "the panel must be able to say up front that it is partial");
});

test("a completely unreadable section set is all-errors, not an empty project", async () => {
  const client = await loadClient({});
  const detail = await client.fetchProjectDetail("p");
  for (const key of ["state", "decisions", "events", "pending", "constitution"]) {
    assert.equal(detail[key].status, "error", `${key} should report a failure`);
  }
  assert.equal(detail.partial, true);
  assert.match(detail.state.error, /No stub route/, "the reason is a real one, not an empty state");
  assert.ok(client.calls.length >= 8);
});

test("a count the server did not send is null, not zero", async () => {
  const client = await loadClient({ "/projects/p/state": { project_id: "p", phase: "create" } });
  const { data } = await client.fetchProjectStateDigest("p");
  for (const key of [
    "active_agents",
    "active_tasks",
    "blocked_tasks",
    "completed_tasks",
    "failed_tasks",
    "open_conflicts",
  ]) {
    assert.equal(data[key], null, `${key} must be unknown, not 0`);
  }
  // Strings differ: an empty goal is a real "no goal recorded".
  assert.equal(data.goal, "");
  assert.equal(data.phase, "create");
  assert.equal(data.arch_version, "", "absent arch version reads as unknown, not as a version");
});

test("a real zero count is preserved as zero", async () => {
  const client = await loadClient({
    "/projects/p/state": { project_id: "p", failed_tasks: 0, open_conflicts: 0 },
  });
  const { data } = await client.fetchProjectStateDigest("p");
  assert.equal(data.failed_tasks, 0, "a measured zero is a fact, not a missing value");
  assert.equal(data.open_conflicts, 0);
});

test("never-verified stays null so it cannot read as verified", async () => {
  const client = await loadClient({
    "/projects/p/state": { project_id: "p", last_verified: null, latest_decision: null },
  });
  const { data } = await client.fetchProjectStateDigest("p");
  assert.equal(data.last_verified, null);
  assert.equal(data.latest_decision, null);
});

test("open risks keep the server's text and drop the entries with none", async () => {
  const client = await loadClient({
    "/projects/p/state": { project_id: "p", open_risks: [{ description: "slow provider" }, {}, "plain string"] },
  });
  const { data } = await client.fetchProjectStateDigest("p");
  assert.deepEqual(data.open_risks, ["slow provider", "", "plain string"]);
});

test("decisions keep the server's fields, and an absent approver stays null", async () => {
  const client = await loadClient({
    "/projects/p/decisions": {
      project_id: "p",
      count: 2,
      decisions: [
        {
          decision_id: "d1",
          title: "Use Postgres",
          body: "b",
          reason: "r",
          made_by: "coder",
          approved_by: "architect",
          created_at: "t",
        },
        { decision_id: "d2", title: "Skip cache" },
      ],
    },
  });
  const { data } = await client.fetchProjectDecisions("p");
  assert.equal(data.length, 2);
  assert.deepEqual(data[0], {
    id: "d1",
    title: "Use Postgres",
    body: "b",
    reason: "r",
    made_by: "coder",
    approved_by: "architect",
    created_at: "t",
  });
  assert.equal(data[1].approved_by, null, "an unapproved decision is not an approved one");
  assert.equal(data[1].body, "", "an absent body is empty text, not a fabricated summary");
});

test("events sort newest first and an undated row cannot displace a real one", async () => {
  const client = await loadClient({
    "/projects/p/events": {
      project_id: "p",
      events: [
        { event_id: "e-old", type: "created", actor: "sys", created_at: "2026-01-01T00:00:00+00:00", seq: 1 },
        {
          event_id: "e-new",
          type: "decided",
          actor: "coder",
          created_at: "2026-09-01T00:00:00+00:00",
          seq: 9,
          payload: { k: 1 },
        },
        { event_id: "e-undated", type: "ping", actor: "sys" },
      ],
    },
  });
  const { data } = await client.fetchProjectEvents("p");
  assert.deepEqual(
    data.map((e) => e.id),
    ["e-new", "e-old", "e-undated"],
  );
  assert.deepEqual(data[0].payload, { k: 1 });
  assert.equal(data[0].seq, 9);
  assert.equal(data[2].seq, null, "an absent sequence is unknown");
  assert.equal(data[2].created_at, "");
});

test("pending work counts what the server reported, per collection", async () => {
  const client = await loadClient({
    "/projects/p/locks": { project_id: "p", locks: [{ id: "l1" }], pending_requests: [{ id: "r1" }, { id: "r2" }] },
    "/projects/p/approvals": { project_id: "p", approvals: [] },
    "/projects/p/checkpoints": { project_id: "p", checkpoints: [{ id: "c1" }, { id: "c2" }, { id: "c3" }] },
    "/projects/p/handoffs": { project_id: "p", handoffs: [{ id: "h1" }] },
  });
  const { data } = await client.fetchProjectPendingWork("p");
  assert.deepEqual(data, {
    locks: 1,
    lock_requests: 2,
    approvals: 0,
    checkpoints: 3,
    handoffs: 1,
  });
});

test("the decision count is read once, from the decisions route the panel already reads", async () => {
  // Two reads of the same route meant two requests per project render and two
  // answers that could disagree. The count now comes from the full read.
  const client = await loadClient(FULL_ROUTES);
  await client.fetchProjectDetail("p");
  const decisionReads = client.calls.filter((p) => p === "/projects/p/decisions");
  assert.equal(decisionReads.length, 1, `/decisions was read ${decisionReads.length} times`);
  assert.ok(
    !client.calls.includes("/projects/p/decisions?x"),
    "the pending read must not add a second /decisions request",
  );
  // And no field is a hardcoded 0 standing in for a read.
  const source = read("./project-detail.ts");
  assert.doesNotMatch(source, /events:\s*0\s*,/, "a constant 0 is not a measurement");
});

test("one failed count route fails the whole pending section with the server's reason", async () => {
  const client = await loadClient({
    "/projects/p/locks": { project_id: "p", locks: [{ id: "l1" }], pending_requests: [] },
    "/projects/p/approvals": new Error("403 admin only"),
    "/projects/p/checkpoints": { project_id: "p", checkpoints: [] },
    "/projects/p/handoffs": { project_id: "p", handoffs: [] },
  });
  const { status, error } = await client.fetchProjectPendingWork("p");
  assert.equal(status, "error");
  assert.match(error, /403 admin only/);
});

test("an absent collection is an empty list, not a failed read", async () => {
  const client = await loadClient({
    "/projects/p/locks": { project_id: "p" },
    "/projects/p/approvals": { project_id: "p" },
    "/projects/p/checkpoints": { project_id: "p" },
    "/projects/p/handoffs": { project_id: "p" },
  });
  const { status, data } = await client.fetchProjectPendingWork("p");
  assert.equal(status, "ok");
  assert.equal(data.locks, 0);
  assert.equal(data.checkpoints, 0);
});

test("constitution presence is the server's claim; absent is not 'false'", async () => {
  const client = await loadClient({ "/projects/p/constitution": { project_id: "p", template: "# T" } });
  const { data } = await client.fetchProjectConstitution("p");
  assert.equal(data.present, false, "no `present` key must not be reported as an explicit false");
  assert.equal(data.template, "# T");
});

test("a non-object body is a read failure, not a blank project", async () => {
  const client = await loadClient({ "/projects/p/state": "not-an-object" });
  const { status, error } = await client.fetchProjectStateDigest("p");
  assert.equal(status, "error");
  assert.match(error, /unreadable/i);
});
