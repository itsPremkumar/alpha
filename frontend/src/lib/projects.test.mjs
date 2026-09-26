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

test("project creation sends the selected bot as an initial crew member", async () => {
  const { projects, calls } = loadProjects({
    send: async (path, method) => {
      assert.equal(path, "/projects");
      assert.equal(method, "POST");
      return {
        id: "project/1",
        name: "Launch",
        instructions: "Ship safely",
        presentation: {},
        status: "active",
        created_at: "now",
        updated_at: "now",
      };
    },
  });

  const project = await projects.createProject("Launch", "Ship safely", [
    { name: "alice", role: "lead" },
    { name: "bob", role: "reviewer" },
  ]);
  assert.equal(project.id, "project/1");
  assert.deepEqual(calls[0].payload, {
    name: "Launch",
    instructions: "Ship safely",
    agents: [
      { name: "alice", role: "lead" },
      { name: "bob", role: "reviewer" },
    ],
  });
});

test("project team clients use the real presence, attach, and detach routes", async () => {
  const member = {
    project_id: "project/1",
    bot_name: "alice",
    role_in_project: "lead",
    status: "active",
    current_task_id: null,
    blocked_reason: null,
    joined_at: "now",
    last_activity: "now",
  };
  const { projects, calls } = loadProjects({
    get: async (path) => {
      if (path === "/projects/project%2F1/presence") return { members: [member] };
      throw new Error(`Unexpected GET ${path}`);
    },
    send: async () => ({ crew: true }),
  });

  assert.deepEqual(await projects.listProjectAgents("project/1"), [member]);
  await projects.attachProjectAgents("project/1", [
    { name: "bob", role: "reviewer" },
    { name: "carol", role: "worker" },
  ], "reviewer");
  await projects.detachProjectAgent("project/1", "bob/name");

  assert.deepEqual(calls.map(({ path, method }) => [path, method]), [
    ["/projects/project%2F1/presence", "GET"],
    ["/projects/project%2F1/agents", "POST"],
    ["/projects/project%2F1/agents/bob%2Fname", "DELETE"],
  ]);
  assert.deepEqual(calls[1].payload, {
    agents: [
      { name: "bob", role: "reviewer" },
      { name: "carol", role: "worker" },
    ],
    role: "reviewer",
  });
});

test("project thread load failures reject instead of looking like an empty project", async () => {
  const { projects } = loadProjects({
    get: async () => {
      throw new Error("gateway unavailable");
    },
  });
  await assert.rejects(projects.projectThreads("project/1"), /gateway unavailable/);
});

test("unreadable project and team envelopes reject rather than becoming empty rosters", async () => {
  const { projects } = loadProjects({ get: async () => ({ detail: "not an envelope" }) });
  await assert.rejects(projects.listProjects(), /unreadable project list/);
  await assert.rejects(projects.listProjectAgents("project/1"), /unreadable project team/);
});

test("project conversations follow every offset page instead of the route's first page", async () => {
  const firstPage = Array.from({ length: 500 }, (_, index) => ({
    thread_id: `thread-${index + 1}`,
    display_name: `Chat ${index + 1}`,
  }));
  const { projects, calls } = loadProjects({
    get: async (path) => {
      const offset = Number(new URL(path, "http://x").searchParams.get("offset"));
      return offset === 0 ? firstPage : [{ thread_id: "thread-501", display_name: "Chat 501" }];
    },
  });
  const threads = await projects.projectThreads("project/1");
  assert.equal(threads.length, 501);
  assert.equal(threads.at(-1).thread_id, "thread-501");
  // The page boundary must actually advance; otherwise this would loop.
  assert.equal(calls.length, 2);
  assert.match(calls[0].path, /limit=500&offset=0/);
  assert.match(calls[1].path, /limit=500&offset=500/);
});

test("a project conversation without an id rejects instead of getting a synthetic one", async () => {
  const { projects } = loadProjects({ get: async () => [{ display_name: "nameless" }] });
  await assert.rejects(projects.projectThreads("project/1"), /without an id/);
});

test("project pagination that does not advance stops instead of looping forever", async () => {
  const stuck = Array.from({ length: 500 }, (_, index) => ({ thread_id: `t-${index}`, display_name: "x" }));
  const { projects } = loadProjects({ get: async () => stuck });
  await assert.rejects(projects.projectThreads("project/1"), /did not advance/);
});

test("a project team row without a bot name is rejected, not rendered as an unremovable member", async () => {
  const { projects } = loadProjects({
    get: async () => ({ members: [{ project_id: "project/1", role_in_project: "worker" }] }),
  });
  await assert.rejects(projects.listProjectAgents("project/1"), /without a bot name/);
});

test("presence rows keep the server's own status string and null out absent optionals", async () => {
  const { projects } = loadProjects({
    get: async () => ({ members: [{ bot_name: "alice", status: "draining", role_in_project: "lead" }] }),
  });
  const [member] = await projects.listProjectAgents("project/1");
  assert.equal(member.status, "draining");
  assert.equal(member.project_id, "project/1");
  assert.equal(member.current_task_id, null);
  assert.equal(member.blocked_reason, null);
});

test("a membership change is only reported as successful once the roster confirms it", async () => {
  let roster = [{ bot_name: "alice", status: "active" }];
  const { projects } = loadProjects({ get: async () => ({ members: roster }) });

  // Server accepted the attach but did not add the bot → not a success.
  roster = [{ bot_name: "alice", status: "active" }];
  await assert.rejects(
    projects.confirmProjectAgents("project/1", ["bob"], true),
    /did not confirm bob/,
  );

  // Server accepted the detach but the bot is still listed → not a success.
  roster = [{ bot_name: "alice", status: "active" }, { bot_name: "bob", status: "active" }];
  await assert.rejects(
    projects.confirmProjectAgents("project/1", ["bob"], false),
    /still lists bob/,
  );

  // Confirmed states pass.
  roster = [{ bot_name: "alice", status: "active" }, { bot_name: "bob", status: "active" }];
  assert.equal((await projects.confirmProjectAgents("project/1", ["bob"], true)).length, 2);
  roster = [{ bot_name: "alice", status: "active" }];
  assert.equal((await projects.confirmProjectAgents("project/1", ["bob"], false)).length, 1);
});

test("a failed membership re-read reports the failure instead of a stale roster", async () => {
  const { projects } = loadProjects({ get: async () => { throw new Error("gateway unavailable"); } });
  await assert.rejects(projects.confirmProjectAgents("project/1", ["bob"], true), /gateway unavailable/);
});

test("the Projects view confirms membership and re-reads the parent's thread list", () => {
  const section = readFileSync(new URL("../components/sections/ProjectsSection.tsx", import.meta.url), "utf8");
  // The success notice is only reached through a confirmed roster read.
  assert.match(section, /confirmProjectAgents\(projectId, names, true\)/);
  assert.match(section, /confirmProjectAgents\(projectId, \[botName\], false\)/);
  // A mutation whose confirmation fails must fall back to a real re-read.
  const attach = section.slice(
    section.indexOf("const attachSelectedBots"),
    section.indexOf("const removeBot"),
  );
  assert.match(attach, /await refreshTeam\(projectId\)/);
  // Moving a conversation out of a project changes the parent's grouping.
  assert.match(section, /props\.onThreadsChanged\?\.\(\)/);
  assert.match(section, /onThreadsChanged\?: \(\) => void/);
});
