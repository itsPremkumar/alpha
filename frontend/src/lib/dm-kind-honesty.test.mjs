// Client contract for the direct-message kind layer.
//
// A group room and a direct thread are two message surfaces with two
// *different* validation vocabularies, and the mailbox row carries the
// sender's chosen envelope type. These pin the places that used to flatten it:
//
//   * `fetchInbox` dropped `kind` from the mailbox wire, and `listDmThreads`
//     hardcoded `kind: "message"`, so every DM rendered as an untyped note —
//     a decision or warning never took its colour in a direct thread;
//   * `sendAgentMessage` posted no `kind`, so the composer's type select was
//     silently ignored on DM sends (the server defaulted to `message`);
//   * the composer offered the group intent vocabulary on a direct thread —
//     group-only intents such as `blocker` answer 422 on the A2A route.
//
// The mirror case lives in `comm-group-roster.test.mjs`, which pins the group
// intent list against the server's `MessageIntent`.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const compiled = new Map(
  ["inbox", "comm", "group-activity"].map((name) => [
    name,
    ts.transpileModule(readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8"), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText,
  ]),
);

class FakeApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

/** Shared `./http` stub: records `METHOD path` plus bodies, answers from `responder`. */
function httpStub(calls, bodies, responder) {
  return {
    __esModule: true,
    ApiError: FakeApiError,
    errMsg: (e) => (e && e.message) || "failed",
    get: async (path) => {
      calls.push(`GET ${path}`);
      return responder(path, undefined);
    },
    send: async (path, method, payload) => {
      calls.push(`${method} ${path}`);
      bodies.push(payload);
      return responder(path, payload);
    },
    asList: (body, keys) => {
      if (Array.isArray(body)) return body;
      for (const k of keys) {
        if (body && typeof body === "object" && Array.isArray(body[k])) return body[k];
      }
      return [];
    },
    pick: (obj, keys, fallback) => {
      if (obj && typeof obj === "object") {
        for (const k of keys) {
          if (obj[k] !== undefined && obj[k] !== null) return obj[k];
        }
      }
      return fallback;
    },
  };
}

/** `inbox` over a stubbed `./http`. */
function inboxClient(responder) {
  const calls = [];
  const bodies = [];
  const exports = {};
  new Function("exports", "require", "process", "console", compiled.get("inbox"))(
    exports,
    (dependency) => {
      if (dependency === "./http") return httpStub(calls, bodies, responder);
      throw new Error(`Unexpected dependency: ${dependency}`);
    },
    { env: {} },
    { error: () => {} },
  );
  return { inbox: exports, calls, bodies };
}

/**
 * `comm` over a stubbed `./inbox`. The mailbox answers with the rows a
 * per-receiver `fetchInbox` would see; `listDmThreads` dedupes them itself.
 */
function commClient({ roster, inboxRows }) {
  const exports = {};
  const groupActivity = {};
  new Function("exports", compiled.get("group-activity"))(groupActivity);
  const inbox = {
    __esModule: true,
    fetchRoster: async () => roster,
    fetchInbox: async () => inboxRows,
    registerRosterAgent: async () => {},
  };
  new Function("exports", "require", "process", "console", compiled.get("comm"))(
    exports,
    (dependency) => {
      if (dependency === "./inbox") return inbox;
      if (dependency === "./group-activity") return groupActivity;
      if (dependency === "./http") return httpStub([], [], () => ({}));
      throw new Error(`Unexpected dependency: ${dependency}`);
    },
    { env: {} },
    { error: () => {} },
  );
  return { comm: exports };
}

/* ---------------- The wire: inbox mapping ---------------- */

test("fetchInbox carries the mailbox row's kind through verbatim", async () => {
  const { inbox, calls } = inboxClient(() => ({
    messages: [
      {
        id: "m1",
        sender_name: "architect",
        receiver_name: "operator",
        content: "cutting the release",
        kind: "decision",
        created_at: "2026-01-01T10:00:00+00:00",
        read: false,
      },
    ],
  }));

  const [row] = await inbox.fetchInbox("t1", "operator");
  // The read must never consume: the poll re-reads this inbox every 10s and
  // the route's default would mark rows read and return only unread, so the
  // thread would vanish after a single cycle (same rule as protocols.ts).
  assert.deepEqual(calls, ["GET /threads/t1/agent-messages/inbox?agent_name=operator&mark_as_read=false"]);
  assert.equal(row.kind, "decision");
  assert.equal(row.createdAt, "2026-01-01T10:00:00+00:00");
});

test("a row carrying no kind reports the dataclass's own default", async () => {
  // An absent field is the server's default `message`, not a client word —
  // and never an empty string that would render as "kindless".
  const { inbox } = inboxClient(() => ({
    messages: [{ id: "m1", sender_name: "a", receiver_name: "b", content: "hi" }],
  }));
  const [row] = await inbox.fetchInbox("t1", "operator");
  assert.equal(row.kind, "message");
});

/* ---------------- The wire: sending ---------------- */

test("sendAgentMessage posts the chosen kind to the mailbox route", async () => {
  const { inbox, calls, bodies } = inboxClient(() => ({}));
  await inbox.sendAgentMessage("t1", "operator", "architect", "cut the release", "decision");
  assert.deepEqual(calls, ["POST /threads/t1/agent-messages/messages"]);
  assert.deepEqual(bodies[0], {
    sender_name: "operator",
    receiver_name: "architect",
    content: "cut the release",
    kind: "decision",
  });
});

test("sendAgentMessage without an explicit kind posts the server's default", async () => {
  const { inbox, bodies } = inboxClient(() => ({}));
  await inbox.sendAgentMessage("t1", "operator", "architect", "hello");
  assert.equal(bodies[0].kind, "message");
});

/* ---------------- The projection: listDmThreads ---------------- */

test("a DM thread carries each row's own kind, not one hardcoded value", async () => {
  const rows = [
    { id: "m1", from: "operator", to: "architect", content: "take the flaky suite", kind: "task_assignment", read: false, createdAt: "2026-01-01T10:00:00+00:00" },
    { id: "m2", from: "architect", to: "operator", content: "suite is green", kind: "result", read: true, createdAt: "2026-01-01T10:05:00+00:00" },
    { id: "m3", from: "operator", to: "architect", content: "seeing red again", kind: "warning", read: true, createdAt: "2026-01-01T10:07:00+00:00" },
  ];
  const { comm } = commClient({
    roster: [{ name: "architect", role: "lead", status: "idle" }],
    inboxRows: rows,
  });

  const threads = await comm.listDmThreads("t1");
  assert.equal(threads.length, 1, "one peer, one thread");
  assert.equal(threads[0].peer, "architect");
  // The same rows arrive once per queried receiver; dedup keeps one copy.
  assert.deepEqual(
    threads[0].messages.map((m) => m.kind),
    ["task_assignment", "result", "warning"],
    "each bubble takes the colour its sender chose",
  );
});

test("the two vocabularies are distinct in both directions", async () => {
  const { comm } = commClient({ roster: [], inboxRows: [] });
  const group = [...comm.MESSAGE_KINDS];
  const dm = [...comm.DM_MESSAGE_KINDS];

  assert.equal(group.length, 18, "every server MessageIntent is offered");
  assert.equal(dm.length, 16, "every A2A mailbox kind is offered");
  assert.equal(new Set(group).size, group.length, "a group kind is listed once");
  assert.equal(new Set(dm).size, dm.length, "a DM kind is listed once");

  // Group-only: would 422 on `POST .../agent-messages/messages`.
  for (const k of ["discussion", "blocker", "approval_request", "card_update"]) {
    assert.ok(!dm.includes(k), `\`${k}\` must not be offered on a direct thread`);
  }
  // DM-only: would 422 on `POST /groups/{name}/messages`.
  for (const k of ["incident", "recovery", "task_handoff", "progress"]) {
    assert.ok(!group.includes(k), `\`${k}\` must not be offered in a room`);
  }
});

/* ---------------- The composer: surface-dependent picker ---------------- */

const component = readFileSync(
  new URL("../components/sections/MessagesSection.tsx", import.meta.url),
  "utf8",
);

test("the composer's type list switches with the open surface", () => {
  assert.match(
    component,
    /\(sel\?\.kind === "dm" \? DM_MESSAGE_KINDS : MESSAGE_KINDS\)\.map\(/,
    "a direct thread must be offered the mailbox vocabulary, not the room's",
  );
});

test("the DM send passes the composed kind instead of dropping it", () => {
  assert.match(
    component,
    /sendAgentMessage\(\s*props\.threadId,\s*OPERATOR,\s*sel\.peer,\s*draft\.trim\(\),\s*kind,?\s*\)/,
    "without this argument the type select is decoration — the server would default every DM to `message`",
  );
});

test("switching surfaces clamps a kind the new surface would refuse", () => {
  assert.match(
    component,
    /const vocab = sel\?\.kind === "dm" \? DM_MESSAGE_KINDS : MESSAGE_KINDS;/,
    "the clamp has to consult the destination vocabulary",
  );
  assert.match(
    component,
    /setKind\(sel\?\.kind === "dm" \? "message" : "discussion"\)/,
    "an invalid carry-over resets to the surface's own default",
  );
});
