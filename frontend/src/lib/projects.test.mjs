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

// ------------------------------------------------------------- create/edit surface

test("the roster sits behind the create form's settings disclosure", () => {
  const section = readFileSync(new URL("../components/sections/ProjectsSection.tsx", import.meta.url), "utf8");

  // Creating is one field; the roster, template and instructions are gated.
  assert.match(section, /\{createSettingsOpen && \(/, "the advanced block must be gated on the disclosure");
  const create = section.slice(section.indexOf("Create project session"), section.indexOf("{createSettingsOpen && ("));
  assert.match(create, /aria-label="New project name"/, "the name field is reachable without opening anything");
  assert.match(create, /creatingProject \? "Creating…" : "Create"/, "Create sits beside the name");
  // What is hidden is still announced: a staged crew cannot become invisible.
  assert.match(create, /agent\$\{draftBots\.length === 1 \? "" : "s"\} staged/);
  assert.match(create, /name only — nothing staged/);
  // The whole roster must be searchable, and "Select shown" stages exactly the
  // rows the search currently displays — never the roster behind it.
  assert.match(section, /const staffRows = \(bots: ProjectBot\[\], query: string, selected: string\[\]\)/);
  assert.match(section, /staffRows\(props\.bots, draftBotQuery, draftBots\)/);
});

test("an opened project leads with its staff list and can be renamed", () => {
  const section = readFileSync(new URL("../components/sections/ProjectsSection.tsx", import.meta.url), "utf8");

  // Staff first: opening a project is how you find out who is on it, and the
  // roster used to sit below the instructions block.
  const expanded = section.slice(section.indexOf("{open === p.id && ("), section.indexOf("<ProjectOverviewPanel"));
  assert.ok(expanded.indexOf("Staff &amp; agents") >= 0, "the expanded card names its staff block");
  assert.ok(
    expanded.indexOf("Staff &amp; agents") < expanded.indexOf("Project details"),
    "the staff list must come before the details block",
  );
  // The add-agents roster is searchable per project, and the filter typed in
  // one project never narrows the next one's list.
  assert.match(expanded, /staffQuery/);
  assert.match(
    section.slice(section.indexOf("const toggle = async"), section.indexOf("const act = async")),
    /setStaffQuery\(""\)/,
    "each project's roster search starts empty",
  );

  // The name travels with the instructions, because renaming used to mean
  // deleting the project and stranding its conversations.
  assert.ok(
    section.includes("useState<{ id: string; name: string; instructions: string } | null>"),
    "the edit draft carries the project name",
  );
  const save = section.slice(section.indexOf("updateProject(p.id, {"), section.indexOf("Cancel"));
  assert.match(save, /updateProject\(p\.id, \{\s*name: editing\.name\.trim\(\) \|\| p\.name,/);
  assert.match(save, /instructions: editing\.instructions/,);
  // A blank name would silently rename the project to nothing, so it is refused
  // locally rather than discovered as a 422.
  assert.match(save, /disabled=\{!editing\.name\.trim\(\)/);
  // The editor is reachable straight from the card, not only once expanded.
  assert.match(
    section,
    /setEditing\(\{ id: p\.id, name: p\.name, instructions: p\.instructions \}\)/,
  );
});

test("the add-agent roster shows only where adding an agent is the task", () => {
  const section = readFileSync(
    new URL("../components/sections/ProjectsSection.tsx", import.meta.url),
    "utf8",
  );
  const expanded = section.slice(
    section.indexOf("{open === p.id && ("),
    section.indexOf("<ProjectOverviewPanel"),
  );

  // The chips answer "who is on this project" and stay on screen. The roster
  // — every agent who is NOT on it — is a decision, so it sits behind an
  // explicit control rather than under them as a wall of checkboxes.
  assert.ok(
    expanded.indexOf("Staff &amp; agents") < expanded.indexOf("{addAgentOpen && ("),
    "the staff list must lead; the roster is what follows it",
  );
  assert.match(
    section,
    /const \[addAgentOpen, setAddAgentOpen\] = useState\(false\)/,
    "the roster starts closed — nothing opens it but the operator",
  );
  assert.match(expanded, /setAddAgentOpen\(\(previous\) => !previous\)/, "one control toggles it");
  assert.match(expanded, /\{addAgentOpen && \(/, "the roster must be gated, not merely scrolled");

  // The gate holds the roster itself, not just its heading.
  const gated = expanded.slice(expanded.indexOf("{addAgentOpen && ("));
  assert.match(gated, /Search agents not on this project/, "the search lives inside the gate");
  assert.match(gated, /type="checkbox"/, "the agent checkboxes live inside the gate");
  assert.match(gated, /New bot role/, "the role field belongs to adding, so it lives there too");
  assert.ok(
    expanded.indexOf("type=\"checkbox\"") > expanded.indexOf("{addAgentOpen && ("),
    "no agent checkbox may render while the roster is closed",
  );

  // Switching projects must not carry the previous project's open picker.
  assert.match(
    section.slice(section.indexOf("const toggle = async"), section.indexOf("const act = async")),
    /setAddAgentOpen\(false\)/,
    "each project's roster starts closed",
  );

  // Hiding the list must not hide work already staged in it.
  assert.match(expanded, /staged, not added yet/, "a staged selection stays announced");
  assert.match(
    expanded,
    /!addAgentOpen && selectedCount > 0 &&/,
    "the attach action stays available while the list is closed",
  );
});
