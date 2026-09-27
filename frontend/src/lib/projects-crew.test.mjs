import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./projects.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

function loadProjects(overrides = {}) {
  const calls = [];
  const exports = {};
  const http = {
    async get(path) {
      calls.push({ path, method: "GET" });
      if (overrides.get) return overrides.get(path, calls.length);
      return {};
    },
    async send(path, method, payload) {
      calls.push({ path, method, payload });
      if (overrides.send) return overrides.send(path, method, payload, calls.length);
      return {};
    },
    asList(body, keys) {
      if (Array.isArray(body)) return body;
      for (const key of keys) if (Array.isArray(body?.[key])) return body[key];
      return [];
    },
    pick(body, keys, fallback) {
      for (const key of keys) if (body?.[key] !== undefined && body?.[key] !== null) return body[key];
      return fallback;
    },
  };
  new Function("exports", "require", compiled)(exports, (dependency) => {
    assert.equal(dependency, "./http");
    return http;
  });
  return { projects: exports, calls };
}

const COLLABORATION = {
  orchestration_mode: "moderated",
  moderator: "architect",
  max_concurrent_speakers: 3,
  mention_policy: "strict",
  auto_handoff: true,
  conflict_policy: "moderator",
  lock_policy: "advisory",
  require_evidence: true,
  memory_budget_chars: 6000,
  transcript_digest_n: 40,
  standup_interval_turns: 10,
};

function crew(overrides = {}) {
  return {
    project_id: "project/1",
    members: [
      {
        bot_name: "alice",
        role_in_project: "architect",
        status: "active",
        current_task_id: null,
        blocked_reason: null,
        last_activity: "now",
      },
    ],
    room: { name: "Launch crew", mode: "moderated", moderator: "alice", members: ["alice", "bob"], message_count: 4, parked: false },
    collaboration: { ...COLLABORATION },
    shared_memory: {},
    state: {},
    active_locks: [],
    recent_events: [],
    updated_at: "now",
    ...overrides,
  };
}

// ------------------------------------------------------------------- routing

test("the crew, settings, and memory clients use the real project routes", async () => {
  const { projects, calls } = loadProjects({
    get: async (path) => {
      if (path === "/projects/project%2F1/crew") return crew();
      if (path === "/projects/project%2F1/memory") return { project_id: "project/1" };
      throw new Error(`Unexpected GET ${path}`);
    },
    send: async () => crew(),
  });

  await projects.getCrew("project/1");
  await projects.updateCollaboration("project/1", { orchestration_mode: "quorum" });
  await projects.getProjectMemory("project/1");

  assert.deepEqual(calls.map(({ path, method }) => [path, method]), [
    ["/projects/project%2F1/crew", "GET"],
    ["/projects/project%2F1/collaboration", "PATCH"],
    ["/projects/project%2F1/memory", "GET"],
  ]);
  assert.deepEqual(calls[1].payload, { orchestration_mode: "quorum" });
});

test("a settings patch drops undefined keys so the server only sees real changes", async () => {
  const { projects, calls } = loadProjects({ send: async () => crew() });
  await projects.updateCollaboration("project/1", {
    mention_policy: "advisory",
    moderator: undefined,
  });
  assert.deepEqual(calls[0].payload, { mention_policy: "advisory" });
});

// ------------------------------------------------------------------- honesty

test("a solo crew with no room maps to null rather than a fabricated room", async () => {
  const { projects } = loadProjects({ get: async () => crew({ room: null, members: [] }) });
  const view = await projects.getCrew("project/1");
  assert.equal(view.room, null);
  assert.deepEqual(view.members, []);
});

test("a crew read failure rejects instead of rendering an empty crew", async () => {
  const { projects } = loadProjects({
    get: async () => {
      throw new Error("gateway unavailable");
    },
  });
  await assert.rejects(projects.getCrew("project/1"), /gateway unavailable/);
});

test("unreadable crew envelopes reject rather than looking like a project with no agents", async () => {
  const notAnObject = loadProjects({ get: async () => "moderated" });
  await assert.rejects(notAnObject.projects.getCrew("project/1"), /unreadable project crew/);

  const noMembers = loadProjects({ get: async () => ({ project_id: "project/1" }) });
  await assert.rejects(noMembers.projects.getCrew("project/1"), /without a member list/);

  const noId = loadProjects({ get: async () => crew({ project_id: "" }) });
  await assert.rejects(noId.projects.getCrew("project/1"), /without a project id/);
});

test("a crew member without a bot name is rejected, not rendered as an unremovable agent", async () => {
  const { projects } = loadProjects({
    get: async () => crew({ members: [{ role_in_project: "worker", status: "active" }] }),
  });
  await assert.rejects(projects.getCrew("project/1"), /without a bot name/);
});

test("an unreadable room is reported, not silently downgraded to 'no room'", async () => {
  // `null` legitimately means a solo project; a non-object value means this
  // build cannot read what the server said. Those are opposite claims.
  const { projects } = loadProjects({ get: async () => crew({ room: "moderated" }) });
  await assert.rejects(projects.getCrew("project/1"), /unreadable project room/);
});

test("settings without an orchestration mode reject instead of defaulting to moderated", async () => {
  const { projects } = loadProjects({ get: async () => crew({ collaboration: { mention_policy: "strict" } }) });
  await assert.rejects(projects.getCrew("project/1"), /without an orchestration mode/);
});

test("an unknown orchestration mode is preserved verbatim, never coerced to a known one", async () => {
  const { projects } = loadProjects({
    get: async () => crew({ collaboration: { ...COLLABORATION, orchestration_mode: "byzantine" } }),
  });
  const view = await projects.getCrew("project/1");
  assert.equal(view.collaboration.orchestration_mode, "byzantine");
});

test("crew members keep the server's own status string and null out absent optionals", async () => {
  const { projects } = loadProjects({
    get: async () => crew({ members: [{ bot_name: "alice", status: "draining", role_in_project: "lead" }] }),
  });
  const [member] = (await projects.getCrew("project/1")).members;
  assert.equal(member.status, "draining");
  assert.equal(member.current_task_id, null);
  assert.equal(member.blocked_reason, null);
});

test("a settings save returns the reconciled crew, so the form renders stored state", async () => {
  const { projects } = loadProjects({
    // The route re-applies the policy to the room, so both blocks come back
    // already reconciled — the form never has to guess what stuck.
    send: async () =>
      crew({
        collaboration: { ...COLLABORATION, orchestration_mode: "quorum" },
        room: { name: "Launch crew", mode: "quorum", moderator: "alice", members: ["alice", "bob"], message_count: 4, parked: false },
      }),
  });
  const view = await projects.updateCollaboration("project/1", { orchestration_mode: "quorum" });
  assert.equal(view.collaboration.orchestration_mode, "quorum");
  assert.equal(view.room.mode, "quorum");
});

test("an unchanged settings form sends nothing instead of a 422-shaped empty patch", async () => {
  const { projects, calls } = loadProjects({ send: async () => crew() });
  await assert.rejects(
    projects.updateCollaboration("project/1", {}),
    /nothing was sent/,
  );
  assert.equal(calls.length, 0);
});

test("project memory keeps absent fields null instead of inventing a goal or digest", async () => {
  const { projects } = loadProjects({
    get: async () => ({ project_id: "project/1", recent_decisions: [], active_locks: [] }),
  });
  const memory = await projects.getProjectMemory("project/1");
  assert.equal(memory.goal, null);
  assert.equal(memory.phase, null);
  assert.equal(memory.transcript_digest, null);
  assert.equal(memory.constitution_hash, "");
  // A bot with no recorded contribution must be absent, not given an empty brief.
  assert.equal("alice" in memory.member_briefs, false);
  assert.equal("alice" in memory.pending_mentions, false);
});

test("a memory read failure rejects rather than reporting an empty shared memory", async () => {
  const { projects } = loadProjects({
    get: async () => {
      throw new Error("gateway unavailable");
    },
  });
  await assert.rejects(projects.getProjectMemory("project/1"), /gateway unavailable/);
});

test("an unreadable memory envelope rejects instead of reading as 'nothing shared yet'", async () => {
  const { projects } = loadProjects({ get: async () => ({ detail: "not memory" }) });
  await assert.rejects(projects.getProjectMemory("project/1"), /without a project id/);
});

test("pending mentions and member briefs survive a real payload", async () => {
  const { projects } = loadProjects({
    get: async () => ({
      project_id: "project/1",
      member_briefs: { alice: "shipping the fix" },
      pending_mentions: { bob: [{ text: "@bob can you review?", seq: 12 }] },
    }),
  });
  const memory = await projects.getProjectMemory("project/1");
  assert.equal(memory.member_briefs.alice, "shipping the fix");
  assert.equal(memory.pending_mentions.bob.length, 1);
  // A bot with no pending mention must be absent, not an empty fabricated queue.
  assert.equal(memory.pending_mentions.alice, undefined);
});

test("the transcript digest is read as a structured block, not stringified", async () => {
  // The bridge returns {messages, total, compacted_upto, summary} — a plain
  // string mapper silently dropped the whole conversation.
  const { projects } = loadProjects({
    get: async () => ({
      project_id: "project/1",
      transcript_digest: {
        messages: [{ from: "alice", intent: "proposal", text: "ship it", at: "now" }],
        total: 7,
        compacted_upto: 2,
        summary: "earlier: agreed the scope",
      },
    }),
  });
  const digest = (await projects.getProjectMemory("project/1")).transcript_digest;
  assert.equal(digest.total, 7);
  assert.equal(digest.compacted_upto, 2);
  assert.equal(digest.summary, "earlier: agreed the scope");
  assert.equal(digest.messages[0].from, "alice");
  assert.equal(digest.messages[0].intent, "proposal");
  assert.equal(digest.messages[0].text, "ship it");
});

test("a crew that has not talked reports an empty digest rather than a missing one", async () => {
  const { projects } = loadProjects({
    get: async () => ({
      project_id: "project/1",
      transcript_digest: { messages: [], total: 0, compacted_upto: 0, summary: null },
    }),
  });
  const digest = (await projects.getProjectMemory("project/1")).transcript_digest;
  assert.ok(digest, "an empty digest is a real state, not an absent one");
  assert.deepEqual(digest.messages, []);
  assert.equal(digest.summary, null);
});

test("a digest that is not an object stays null instead of being stringified", async () => {
  const { projects } = loadProjects({
    get: async () => ({ project_id: "project/1", transcript_digest: "ship it" }),
  });
  assert.equal((await projects.getProjectMemory("project/1")).transcript_digest, null);
});

// ------------------------------------------------------------------ templates

test("quick-start templates declare roles, never hard-coded bot names", async () => {
  const { projects } = loadProjects();
  const templates = projects.PROJECT_TEMPLATES;
  assert.ok(templates.length >= 4, "expected several starting points");
  for (const template of templates) {
    assert.ok(template.id && template.name && template.summary, `template ${template.id} is incomplete`);
    // Bots are runtime roster data, so a template may only ask for roles.
    for (const role of template.roles) {
      assert.doesNotMatch(role, /@/, `template ${template.id} hard-codes a bot handle`);
    }
  }
});

test("every template's collaboration patch is a subset of the real settings", async () => {
  const { projects } = loadProjects();
  const known = new Set(projects.COLLABORATION_KEYS);
  for (const template of projects.PROJECT_TEMPLATES) {
    for (const key of Object.keys(template.collaboration)) {
      assert.ok(known.has(key), `template ${template.id} sets unknown setting '${key}'`);
    }
  }
});

test("an unknown template id falls back to blank rather than throwing", async () => {
  const { projects } = loadProjects();
  assert.equal(projects.getProjectTemplate("nope").id, "blank");
  assert.equal(projects.getProjectTemplate("feature").name, "Feature build");
});

// ------------------------------------------------------------ component wiring

test("the crew panel is reachable from the Projects view and never claims a save it did not make", () => {
  const panel = readFileSync(new URL("../components/sections/ProjectCrewPanel.tsx", import.meta.url), "utf8");
  const section = readFileSync(new URL("../components/sections/ProjectsSection.tsx", import.meta.url), "utf8");

  // A panel nothing renders is dead code.
  assert.match(section, /ProjectCrewPanel/);
  // The saved state must come from the server's reconciled response.
  assert.match(panel, /await updateCollaboration\(/);
  // An in-flight save must disable the control so a double-click cannot double-apply.
  assert.match(panel, /saving/);
  // Settings that never reached the server must not be painted as saved.
  assert.doesNotMatch(panel, /setCollab\(\{[^}]*orchestration_mode: "moderated"\}\);\s*setNotice\(/);
});

test("a template's coordination settings are actually applied, and a failed half is disclosed", () => {
  const section = readFileSync(new URL("../components/sections/ProjectsSection.tsx", import.meta.url), "utf8");

  // The project is created first, then its policy is written as a second call.
  assert.match(section, /const created = await createProject\(/);
  assert.match(section, /await updateCollaboration\(created\.id, template\.collaboration\)/);
  // A policy write that failed must not be reported as a clean creation.
  const create = section.slice(
    section.indexOf("const createGlobalProject"),
    section.indexOf("const attachSelectedBots"),
  );
  assert.match(create, /coordination settings were not applied/);
  // A template with no settings must not fire a pointless (and 422) empty PATCH.
  assert.match(create, /Object\.keys\(template\.collaboration\)\.length > 0/);
});

test("the sidebar groups conversations under their project and keeps a no-project group", () => {
  const sidebar = readFileSync(new URL("../components/ThreadSidebar.tsx", import.meta.url), "utf8");
  // Grouping is driven by the server's project_id on the thread, not a guess.
  assert.match(sidebar, /t\.projectId/);
  assert.match(sidebar, /No project/);
  // Search results must still be able to open a conversation.
  assert.match(sidebar, /onSelectThread\(id\)/);
});

test("a failed shared-memory read is shown as an error, never as a pending read", () => {
  const panel = readFileSync(new URL("../components/sections/ProjectCrewPanel.tsx", import.meta.url), "utf8");
  // "Loading" must come from real request state, not from `memory` being null,
  // or a failed read spins on "Reading…" and hides the error behind it.
  assert.match(panel, /const \[memoryLoading, setMemoryLoading\] = useState\(false\)/);
  const open = panel.slice(panel.indexOf("const openMemory"), panel.indexOf("const save = async"));
  assert.match(open, /setMemoryLoading\(false\)/);
  assert.match(open, /finally \{/);
  assert.match(open, /setMemoryError\(/);
  assert.doesNotMatch(panel, /loading=\{tab === "memory" && !memory\}/);
});
