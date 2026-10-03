// Client contract for the group roster and message-feature surface.
//
// These pin the exact routes/verbs, the envelope mapping, and the honesty
// inversions. The bugs they exist to catch:
//
//   * `rollCall()` read an `agents` array from `/company/attendance/roll-call`,
//     which returns `{"org_id", "roll_call_digest"}` — a markdown string. The
//     map found nothing, so every participant rendered `unknown`.
//   * Presence was read from the *thread-scoped* agent roster, which is empty
//     for any room never opened as a thread.
//   * `MESSAGE_KINDS` declared thirteen kinds while the router validated six,
//     so seven composer options — including `decision`, which "Post as group
//     decision" sends — were answered with a 422.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const compiled = new Map(
  ["comm", "http", "api-client", "inbox", "group-activity"].map((name) => [
    name,
    ts.transpileModule(readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8"), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText,
  ]),
);

/** `group-activity` is pure, so it loads its real implementation. */
const groupActivity = {};
new Function("exports", compiled.get("group-activity"))(groupActivity);

class FakeApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

/**
 * `comm` over a stubbed `http`. The stub records `METHOD path` per call and
 * answers from `responder`, so a test asserts the wire shape rather than only
 * the return value.
 */
function client(responder) {
  const calls = [];
  const bodies = [];
  const http = {
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

  const exports = {};
  new Function("exports", "require", "process", "console", compiled.get("comm"))(
    exports,
    (dependency) => {
      if (dependency === "./http") return http;
      if (dependency === "./inbox") {
        return { fetchRoster: async () => [], fetchInbox: async () => [], registerRosterAgent: async () => {} };
      }
      // `./group-activity` is a pure derivation module with no transport of its
      // own, so the real implementation is safe to load here. Stubbing it would
      // defeat the point of this harness: `roomActivity` is supposed to map the
      // server envelope through those exact coercions, and a stub returning
      // `undefined` would make the envelope tests pass for the wrong reason.
      if (dependency === "./group-activity") {
        return groupActivity;
      }
      throw new Error(`Unexpected dependency: ${dependency}`);
    },
    { env: {} },
    { error: () => {} },
  );
  return { comm: exports, calls, bodies };
}

function rejectsWith(promise, fragment) {
  return promise.then(
    () => {
      throw new Error("Expected the call to reject");
    },
    (e) => {
      assert.match(String(e && e.message), fragment);
    },
  );
}

/* ---------------- Presence resolution ---------------- */

test("listRoomMembers reads the room's own membership route", async () => {
  const { comm, calls } = client(() => ({
    room: "sprint-room",
    members: [
      { name: "architect", state: "busy", source: "company_attendance", activity_at: "2026-01-01T00:00:00+00:00", detail: "working on run_1", role: "Architect" },
      { name: "coder", state: "idle", source: "bot_registry", activity_at: null, detail: "no recent activity", role: "Engineer" },
    ],
    count: 2,
  }));

  const members = await comm.listRoomMembers("sprint-room");
  assert.deepEqual(calls, ["GET /groups/sprint-room/members"]);
  assert.equal(members.length, 2);
  assert.equal(members[0].name, "architect");
  assert.equal(members[0].state, "busy");
  assert.equal(members[0].detail, "working on run_1");
  assert.equal(members[0].role, "Architect");
  assert.equal(members[1].state, "idle");
  assert.equal(members[1].activityAt, null);
});

test("a member with no measured state stays null rather than becoming online", async () => {
  const { comm } = client(() => ({
    members: [{ name: "ghost", state: "mystery", source: "bot_registry", detail: "status not reported" }],
  }));
  const [member] = await comm.listRoomMembers("r");
  // "mystery" is not one of the server's declared states, so the client keeps
  // it null instead of snapping it to a state nobody reported.
  assert.equal(member.state, null);
  assert.equal(member.detail, "status not reported");
});

test("an unread activity stamp is null, never the current time", async () => {
  const { comm } = client(() => ({ members: [{ name: "coder", state: "idle", activity_at: null }] }));
  const [member] = await comm.listRoomMembers("r");
  assert.equal(member.activityAt, null);
});

test("a failed roster read rejects instead of resolving to an empty roster", async () => {
  const { comm } = client(() => {
    throw new FakeApiError(404, "Room 'nope' not found");
  });
  // An empty array here would render as "nobody is in this group".
  await rejectsWith(comm.listRoomMembers("nope"), /not found/);
});

test("rollCall reads the liveness ledger, not the markdown digest", async () => {
  const { comm, calls } = client(() => ({
    org_id: "org-default",
    bots_count: 1,
    heartbeats: [{ bot_name: "coder", status: "present", active_task_id: "run_9" }],
  }));

  const rows = await comm.rollCall();
  assert.deepEqual(calls, ["GET /company/attendance/status"]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].name, "coder");
  assert.equal(rows[0].status, "present");
  assert.equal(rows[0].detail, "run_9");
});

test("rollCall over the old digest envelope finds no agents, not a crash", async () => {
  // The digest route is markdown prose. It must map to zero rows rather than
  // inventing members from its keys.
  const { comm } = client(() => ({ org_id: "org-default", roll_call_digest: "## Roll call\n- coder: present" }));
  assert.deepEqual(await comm.rollCall(), []);
});

/* ---------------- Message features ---------------- */

test("editRoomMessage patches the exact message route", async () => {
  const { comm, calls } = client(() => ({ message: { id: "msg_1", content: "fixed" } }));
  await comm.editRoomMessage("sprint-room", "msg_1", "fixed");
  assert.deepEqual(calls, ["PATCH /groups/sprint-room/messages/msg_1"]);
});

test("deleteRoomMessage deletes the exact message route", async () => {
  const { comm, calls } = client(() => ({ message: { id: "msg_1", deleted: true } }));
  await comm.deleteRoomMessage("sprint-room", "msg_1");
  assert.deepEqual(calls, ["DELETE /groups/sprint-room/messages/msg_1"]);
});

test("reactToRoomMessage posts the actor and returns the server's map", async () => {
  const { comm, calls, bodies } = client(() => ({ message_id: "msg_1", reactions: { "👍": ["operator"] } }));

  const reactions = await comm.reactToRoomMessage("sprint-room", "msg_1", "operator", "👍");
  assert.deepEqual(calls, ["POST /groups/sprint-room/messages/msg_1/reactions"]);
  assert.deepEqual(bodies[0], { actor: "operator", emoji: "👍" });
  // The server's map is the result — the client never paints a reaction it
  // invented locally.
  assert.deepEqual(reactions, { "👍": ["operator"] });
});

test("a malformed reaction envelope resolves to no reactions rather than throwing", async () => {
  const { comm } = client(() => ({ message_id: "msg_1" }));
  const reactions = await comm.reactToRoomMessage("sprint-room", "msg_1", "operator", "👍");
  assert.deepEqual(reactions, {});
});

test("forwardRoomMessage names the target room explicitly", async () => {
  const { comm, calls, bodies } = client(() => ({ message: { id: "msg_2" }, target_room: "test-board" }));
  await comm.forwardRoomMessage("sprint-room", "msg_1", "test-board", "operator");
  assert.deepEqual(calls, ["POST /groups/sprint-room/messages/msg_1/forward"]);
  assert.equal(bodies[0].target_room, "test-board");
  assert.equal(bodies[0].sender, "operator");
});

test("postToRoom omits reply_to when there is no reply", async () => {
  const { comm, bodies } = client(() => ({ message: {}, next_speakers: [] }));
  await comm.postToRoom("r", "operator", "hello");
  assert.equal("reply_to" in bodies[0], false);
});

test("postToRoom carries reply_to when replying", async () => {
  const { comm, bodies } = client(() => ({ message: {}, next_speakers: [] }));
  await comm.postToRoom("r", "operator", "answer", "discussion", "msg_7");
  assert.equal(bodies[0].reply_to, "msg_7");
});

/* ---------------- Envelope mapping ---------------- */

test("a message row maps its reply, forward, edit and reaction fields", async () => {
  const { comm } = client(() => ({
    room: {
      name: "r",
      members: [],
      messages: [
        {
          id: "msg_9",
          sender: "architect",
          content: "body",
          created_at: "2026-01-01T10:00:00+00:00",
          intent: "proposal",
          reply_to: "msg_1",
          forwarded_from: { room: "test-board", sender: "coder" },
          edited_at: "2026-01-01T11:00:00+00:00",
          deleted: false,
          reactions: { "🎉": ["operator", "coder"] },
        },
      ],
    },
  }));

  const [m] = (await comm.getRoom("r")).messages;
  assert.equal(m.replyTo, "msg_1");
  assert.deepEqual(m.forwardedFrom, { room: "test-board", sender: "coder" });
  assert.equal(m.editedAt, "2026-01-01T11:00:00+00:00");
  assert.equal(m.deleted, false);
  assert.deepEqual(m.reactions, { "🎉": ["operator", "coder"] });
  assert.equal(m.kind, "proposal");
});

test("a row with no optional fields maps them to null, not to empty strings", async () => {
  const { comm } = client(() => ({ room: { name: "r", members: [], messages: [{ id: "m1", sender: "s", content: "c" }] } }));
  const [m] = (await comm.getRoom("r")).messages;
  assert.equal(m.replyTo, null);
  assert.equal(m.forwardedFrom, null);
  assert.equal(m.editedAt, null);
  assert.equal(m.reactions, undefined);
  // No timestamp on the row: null, never "" rendered as a time.
  assert.equal(m.at, null);
});

test("a deleted message keeps its id so replies still resolve", async () => {
  const { comm } = client(() => ({
    room: { name: "r", members: [], messages: [{ id: "m2", sender: "s", content: "", deleted: true }] },
  }));
  const [m] = (await comm.getRoom("r")).messages;
  assert.equal(m.id, "m2");
  assert.equal(m.deleted, true);
});

test("an unrecognised message kind is preserved verbatim", async () => {
  const { comm } = client(() => ({
    room: { name: "r", members: [], messages: [{ id: "m3", sender: "s", content: "c", intent: "future_kind" }] },
  }));
  const [m] = (await comm.getRoom("r")).messages;
  assert.equal(m.kind, "future_kind");
});

/* ---------------- Vocabulary ---------------- */

test("kindTone classifies the kinds the composer offers", async () => {
  const { comm } = client(() => ({}));
  assert.equal(comm.kindTone("discussion"), "gray");
  assert.equal(comm.kindTone("decision"), "green");
  assert.equal(comm.kindTone("blocker"), "amber");
  assert.equal(comm.kindTone("handoff"), "blue");
});

test("the composer offers `decision`, the kind the verdict path posts", () => {
  // The regression this file exists for: the composer listed `decision` while
  // the router validated a six-value tuple that excluded it, so "Post as group
  // decision" was answered with a 422.
  assert.ok(KINDS.includes("decision"));
  assert.ok(KINDS.includes("handoff"));
  assert.ok(KINDS.includes("escalation"));
  assert.equal(new Set(KINDS).size, KINDS.length, "a kind is listed once");
});

const KINDS = [
  "discussion", "question", "answer", "request", "status", "handoff", "decision",
  "blocker", "warning", "approval_request", "escalation", "task_assignment", "task_completion",
];

test("the reaction palette is a small bounded vocabulary", () => {
  assert.ok(REACTIONS.length > 0);
  assert.ok(REACTIONS.length <= 12, "the server bounds the reaction set too");
});

const REACTIONS = ["👍", "👎", "🎉", "🚀", "👀", "❤️", "🔥", "🤔"];

test("the client's exported kind list matches the pinned vocabulary", async () => {
  const { comm } = client(() => ({}));
  assert.deepEqual([...comm.MESSAGE_KINDS].sort(), [...KINDS].sort());
});
