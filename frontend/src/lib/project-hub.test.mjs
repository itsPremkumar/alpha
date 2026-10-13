// project-hub.test.mjs — the reads that make a project a hub.
//
// A project used to be a name on a card. Its room, its board, its company and
// the skills of its crew each lived on a different surface, and none of them
// could be reached from the project itself: the room needed the Messages tab
// plus a search, the board the Kanban tab plus a filter, the company the
// Companies tab plus a hunt. This module is the join, and the join is where
// the lies get in — a join that fails reads as "this project has nothing",
// which is the one claim a broken read can never make.
//
// Three things are asserted here:
//   1. The exact routes. A read that silently hits a route that does not
//      exist is a permanently empty panel that looks like a quiet project.
//   2. The join's honesty: a count the Gateway did not send is `null`, never
//      `0`; an unreadable row is NAMED, never dropped; a card with no project
//      is never claimed by one; and a skill keeps the agent that carries it.
//   3. The pure helpers, driven directly — `unionProjectSkills`,
//      `projectBoardCards`, `boardColumns` — because those decide what the
//      panels claim and they rot quietly inside JSX.
//
// The transport is stubbed, so these run with no Gateway.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./project-hub.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

/**
 * Load the module against stubbed dependencies.
 *
 * Unlike the inspector (which reads only `lib/http`), this module is a join
 * over four existing clients, so each is stubbed separately and every call is
 * recorded — a panel claiming to read rooms while silently calling the board
 * is exactly the drift this file exists to catch.
 */
function loadHub(handlers = {}) {
  const calls = [];
  const record = (dependency, name, args) =>
    calls.push({ dependency, name, args: args.map((a) => a) });

  const http = {
    async get(path) {
      record("./http", "get", [path]);
      return handlers.get ? handlers.get(path) : {};
    },
  };
  const bots = {
    async fetchBot(name) {
      record("./bots", "fetchBot", [name]);
      return handlers.fetchBot ? handlers.fetchBot(name) : null;
    },
  };
  const company = {
    async listCompanies() {
      record("./company", "listCompanies", []);
      return handlers.listCompanies ? handlers.listCompanies() : [];
    },
    async getCompany(id) {
      record("./company", "getCompany", [id]);
      return handlers.getCompany ? handlers.getCompany(id) : { company: { projects: [] } };
    },
  };
  const kanban = {
    async listKanbanTasks() {
      record("./kanban", "listKanbanTasks", []);
      return handlers.listKanbanTasks ? handlers.listKanbanTasks() : [];
    },
  };
  const kanbanBoard = {
    loadCards() {
      record("./kanban-board", "loadCards", []);
      return handlers.loadCards ? handlers.loadCards() : [];
    },
    mergeServerCards(local, server) {
      record("./kanban-board", "mergeServerCards", [local, server]);
      return [...local, ...server];
    },
    COLUMNS: [
      { id: "todo", label: "To do", hint: "" },
      { id: "doing", label: "In progress", hint: "" },
      { id: "done", label: "Done", hint: "" },
    ],
  };

  const exports = {};
  const stubs = {
    "./http": http,
    "./bots": bots,
    "./company": company,
    "./kanban": kanban,
    "./kanban-board": kanbanBoard,
  };
  new Function("exports", "require", compiled)(exports, (dependency) => {
    const stub = stubs[dependency];
    assert.ok(stub, `project-hub must not reach for an unstubbed module: ${dependency}`);
    return stub;
  });
  return { hub: exports, calls };
}

/** A card with every field the board panels read, overridable per test. */
function card(overrides = {}) {
  return {
    id: "c1",
    title: "Card one",
    status: "todo",
    priority: "medium",
    agent: "alice",
    projectId: "p1",
    progress: null,
    blockedReason: null,
    ...overrides,
  };
}

// ------------------------------------------------------------------- rooms

test("project rooms read the server's own by-project route, with the id encoded", async () => {
  const { hub, calls } = loadHub({
    get: (path) => {
      assert.equal(path, "/groups/by-project/project%2F1");
      return {
        project_id: "project/1",
        count: 2,
        rooms: [
          { name: "project-project/1", status: "active", members: ["alice", "bob"] },
          { name: "project-project/1-specs", status: "active", members: ["alice"] },
        ],
      };
    },
  });

  const view = await hub.projectRooms("project/1");
  assert.deepEqual(view, {
    count: 2,
    rooms: [
      { name: "project-project/1", status: "active", memberCount: 2 },
      { name: "project-project/1-specs", status: "active", memberCount: 1 },
    ],
  });
  assert.equal(calls.filter((c) => c.name === "get").length, 1, "exactly one read");
});

test("a count the Gateway skipped stays null — never a zero read off the array", async () => {
  const { hub } = loadHub({
    get: () => ({ project_id: "p1", rooms: [{ name: "r", status: "active", members: [] }] }),
  });
  const view = await hub.projectRooms("p1");
  assert.equal(view.count, null, "an unreported count is null, not rooms.length");
  assert.equal(view.rooms.length, 1, "the rooms that did arrive still render");
});

test("an unreadable room envelope rejects rather than reading as zero rooms", async () => {
  const { hub } = loadHub({ get: () => ({ project_id: "p1", rooms: "not-a-list" }) });
  await assert.rejects(() => hub.projectRooms("p1"), /unreadable room list/);
});

// ---------------------------------------------------------------- company

test("the company index is built by a reverse read and keyed by project id", async () => {
  const { hub, calls } = loadHub({
    listCompanies: () => [
      { company_id: "c1", name: "Acme" },
      { company_id: "c2", name: "Globex" },
    ],
    getCompany: (id) => ({
      company: {
        company_id: id,
        projects:
          id === "c1"
            ? [
                { project_id: "p1", title: "Launch" },
                { project_id: "p2", title: "Ignite" },
              ]
            : [{ project_id: "p1", title: "Launch" }],
      },
    }),
  });

  const { byProject, unreadable } = await hub.projectCompanyIndex();
  assert.deepEqual(byProject.get("p1"), [
    { company_id: "c1", name: "Acme" },
    { company_id: "c2", name: "Globex" },
  ]);
  assert.deepEqual(byProject.get("p2"), [{ company_id: "c1", name: "Acme" }]);
  assert.deepEqual(unreadable, []);
  assert.equal(calls.filter((c) => c.name === "listCompanies").length, 1);
  assert.equal(calls.filter((c) => c.name === "getCompany").length, 2, "one read per company");
});

test("a company that will not answer is NAMED — 'in no company' is the one claim it cannot make", async () => {
  const { hub } = loadHub({
    listCompanies: () => [
      { company_id: "c1", name: "Acme" },
      { company_id: "c2", name: "Globex" },
    ],
    getCompany: (id) => {
      if (id === "c2") throw new Error("HTTP 403 forbidden");
      return { company: { company_id: id, projects: [{ project_id: "p1", title: "Launch" }] } };
    },
  });

  const { byProject, unreadable } = await hub.projectCompanyIndex();
  assert.deepEqual(unreadable, ["c2"], "the failed company is named, not dropped");
  assert.deepEqual(byProject.get("p1"), [{ company_id: "c1", name: "Acme" }]);
});

test("the company scan is bounded, so one slow registry cannot fan out forever", async () => {
  const { hub, calls } = loadHub({
    listCompanies: () =>
      Array.from({ length: 60 }, (_, i) => ({ company_id: `c${i}`, name: `Co ${i}` })),
    getCompany: (id) => ({ company: { company_id: id, projects: [] } }),
  });

  await hub.projectCompanyIndex();
  assert.equal(
    calls.filter((c) => c.name === "getCompany").length,
    25,
    "the scan stops at the documented ceiling",
  );
});

// ---------------------------------------------------------------- skills

test("skills union with the agent that carries each one", () => {
  const { hub } = loadHub();
  const skills = hub.unionProjectSkills([
    { name: "alice", skills: ["python", "review"] },
    { name: "bob", skills: ["python", "deploy"] },
  ]);
  assert.deepEqual(skills, [
    { skill: "deploy", agents: ["bob"] },
    { skill: "python", agents: ["alice", "bob"] },
    { skill: "review", agents: ["alice"] },
  ]);
});

test("a blank skill is skipped and the same agent is never listed twice", () => {
  const { hub } = loadHub();
  const skills = hub.unionProjectSkills([
    { name: "alice", skills: ["  ", "python", "python"] },
    { name: "alice", skills: ["python"] },
  ]);
  assert.deepEqual(skills, [{ skill: "python", agents: ["alice"] }]);
});

test("an agent whose profile failed is NAMED — its skills are unknown, not absent", async () => {
  const { hub } = loadHub({
    fetchBot: (name) => {
      if (name === "bob") return null; // fetchBot resolves null for an unknown name
      return { name, skills: ["python"] };
    },
  });

  const { skills, unreadable } = await hub.projectSkillUnion(["alice", "bob"]);
  assert.deepEqual(unreadable, ["bob"], "the unread agent is named");
  assert.deepEqual(skills, [{ skill: "python", agents: ["alice"] }]);
});

test("a rejecting profile read is also named, not folded into an empty union", async () => {
  const { hub } = loadHub({
    fetchBot: (name) => {
      if (name === "bob") throw new Error("HTTP 503");
      return { name, skills: ["python"] };
    },
  });

  const { skills, unreadable } = await hub.projectSkillUnion(["alice", "bob"]);
  assert.deepEqual(unreadable, ["bob"]);
  assert.deepEqual(skills, [{ skill: "python", agents: ["alice"] }]);
});

// ---------------------------------------------------------------- board

test("a board card claims only the project whose id it carries", () => {
  const { hub } = loadHub();
  const cards = [
    card({ id: "c1", projectId: "p1" }),
    card({ id: "c2", projectId: "p2" }),
    card({ id: "c3", projectId: null }),
    card({ id: "c4", projectId: "" }),
  ];
  assert.deepEqual(
    hub.projectBoardCards(cards, "p1").map((c) => c.id),
    ["c1"],
    "a card naming no project is never claimed by one",
  );
});

test("board columns keep the board's own order and its empty columns visible", () => {
  const { hub } = loadHub();
  const columns = hub.boardColumns([card({ status: "done" }), card({ id: "c2", status: "todo" })]);
  assert.deepEqual(columns.map((c) => c.id), ["todo", "doing", "done"]);
  assert.deepEqual(columns.map((c) => c.cards.length), [1, 0, 1]);
});

test("the project board merges the shared board, then narrows to the project", async () => {
  const { hub } = loadHub({
    loadCards: () => [card({ id: "local1", projectId: "p1" })],
    listKanbanTasks: () => [card({ id: "srv1", projectId: "p1" }), card({ id: "srv2", projectId: "p2" })],
  });

  const { cards, serverBoardError } = await hub.loadProjectBoard("p1");
  assert.deepEqual(cards.map((c) => c.id), ["local1", "srv1"]);
  assert.equal(serverBoardError, null);
});

test("a failed server board is reported — 'no cards' must not mean 'no answer'", async () => {
  const { hub } = loadHub({
    loadCards: () => [card({ id: "local1", projectId: "p1" })],
    listKanbanTasks: () => {
      throw new Error("HTTP 503 company board unavailable");
    },
  });

  const { cards, serverBoardError } = await hub.loadProjectBoard("p1");
  assert.match(serverBoardError, /company board unavailable/);
  assert.deepEqual(cards.map((c) => c.id), ["local1"], "the local half still answers");
});

// ------------------------------------------------------- source pins: client

test("the hub's routes are the Gateway's real ones", () => {
  assert.match(source, /\/groups\/by-project\/\$\{encodeURIComponent\(projectId\)\}/);
  // The company and bot reads are delegated to the clients that already own
  // those routes — a second mapper for `/companies` or `/bots` here is how two
  // surfaces end up disagreeing about the same payload.
  assert.match(
    source,
    /import \{ getCompany, listCompanies, ProjectLink \} from "\.\/company"/,
    "attribution reuses lib/company, never a second company mapper",
  );
  assert.match(source, /import \{ fetchBot \} from "\.\/bots"/, "skills reuse lib/bots");
});

test("the hub reads the network only through lib/http, never fetch", () => {
  assert.doesNotMatch(source, /\bfetch\s*\(/, "components and clients go through lib/http");
});

// ------------------------------------------------------- source pins: panels

const inspector = readFileSync(
  new URL("../components/sections/ProjectInspectorSection.tsx", import.meta.url),
  "utf8",
);
const projects = readFileSync(
  new URL("../components/sections/ProjectsSection.tsx", import.meta.url),
  "utf8",
);
const chatView = readFileSync(new URL("../components/ChatView.tsx", import.meta.url), "utf8");
const kanbanSection = readFileSync(
  new URL("../components/sections/KanbanSection.tsx", import.meta.url),
  "utf8",
);
const messagesSection = readFileSync(
  new URL("../components/sections/MessagesSection.tsx", import.meta.url),
  "utf8",
);
const companySection = readFileSync(
  new URL("../components/sections/CompanySection.tsx", import.meta.url),
  "utf8",
);

test("the inspector carries a Board tab beside the crew and the conversations", () => {
  assert.match(inspector, /"board"/, "the board is a first-class tab, not a footnote");
  assert.match(inspector, /BoardPanel/, "the tab has a panel behind it");
  assert.match(inspector, /ProjectRoomsPanel/, "rooms are listed, subgroups included");
  assert.match(inspector, /CompanyPanel/, "the company is answered on the project side");
  assert.match(inspector, /SkillsPanel/, "the crew's skills are answered on the project side");
});

test("a crew member is one click from that agent's full record", () => {
  assert.match(
    inspector,
    /\/bots\/\$\{encodeURIComponent\(m\.bot_name\)\}/,
    "the crew row links to /bots/[name], the agent's own page",
  );
});

test("the inspector offers a way into every surface it names", () => {
  assert.match(inspector, /onOpenRoom/, "rooms open in the Messages view");
  assert.match(inspector, /onOpenProjectBoard/, "the board opens in the Kanban view");
  assert.match(inspector, /onOpenCompany/, "the company opens in the Companies view");
});

test("the project card states its company and offers the hub's shortcuts", () => {
  assert.match(projects, /companiesByProject/, "the reverse index is loaded once for the list");
  assert.match(projects, /projectCompanyIndex/, "attribution is a real read, not a guess");
  assert.match(projects, /onOpenProjectBoard/, "the card can reach this project's board");
  assert.match(projects, /openProjectRoom/, "the card reaches the room through the server's own name");
});

test("the room shortcut never composes a room name in the client", () => {
  assert.doesNotMatch(
    projects,
    /project-\$\{p\.id\}/,
    "the crew service names the room; composing project-{id} would break on a rename",
  );
});

test("ChatView owns one one-shot request per destination, each with its nonce", () => {
  assert.match(chatView, /projectBoardRequest/, "the board deep link");
  assert.match(chatView, /roomOpenRequest/, "the room deep link");
  assert.match(chatView, /companyOpenRequest/, "the company deep link");
  assert.match(chatView, /const openProjectBoard = /);
  assert.match(chatView, /const openRoomDetail = /);
  assert.match(chatView, /const openCompany = /);
});

test("the three receivers consume and acknowledge their request", () => {
  assert.match(kanbanSection, /openProjectRequest/);
  assert.match(kanbanSection, /onOpenProjectRequestHandled/);
  assert.match(messagesSection, /openRoomRequest/);
  assert.match(messagesSection, /onOpenRoomRequestHandled/);
  assert.match(companySection, /openCompanyRequest/);
  assert.match(companySection, /onOpenCompanyRequestHandled/);
});

test("a company a request names that the registry does not carry is reported", () => {
  assert.match(
    companySection,
    /does not contain/,
    "silently keeping the previous selection would present it as the one asked for",
  );
});

test("a linked project row offers both the board and the full read", () => {
  assert.match(companySection, /onOpenProjectBoard/);
  assert.match(companySection, /Board/, "the board is reachable from the company portfolio");
});

// ------------------------------------------- source pins: placeholder removal

// The audit that produced this module also found four places where the project
// UI invented a fact. Each is pinned here so a "temporary" value cannot come
// back: a fabricated date and a green dot are the two claims a reader trusts
// without checking, which is exactly why they must never be invented.

const detailPanel = readFileSync(
  new URL("../components/chat-shell/ProjectDetailPanel.tsx", import.meta.url),
  "utf8",
);
const projectsLib = readFileSync(new URL("./projects.ts", import.meta.url), "utf8");
const chatShellLib = readFileSync(new URL("./chat-shell.ts", import.meta.url), "utf8");
const omniSearch = readFileSync(
  new URL("../components/chat-shell/OmnisearchModal.tsx", import.meta.url),
  "utf8",
);
const projectDropdown = readFileSync(
  new URL("../components/chat-shell/ProjectDropdownMenu.tsx", import.meta.url),
  "utf8",
);

test("no fabricated date stands in for a creation time the server did not send", () => {
  assert.doesNotMatch(
    detailPanel,
    /"Apr 26, 2025"/,
    "a hardcoded date presented itself as when the project was created",
  );
  assert.match(detailPanel, /"not reported"/, "the absence is stated, not filled in");
});

test("a project status the server never sent wears no green", () => {
  assert.doesNotMatch(
    detailPanel,
    /bg-emerald-500/,
    "the dot asserted a liveness verdict on a field that may be empty",
  );
  assert.doesNotMatch(
    projects,
    /status === "archived" \? "gray" : "green"/,
    "everything-but-archived was green, including an empty status",
  );
  // The one place that decides the colour is the pure helper, and only
  // `active` earns it.
  assert.match(chatShellLib, /export function projectStatusTone/);
  assert.match(chatShellLib, /status\.trim\(\) === "active" \? "green" : undefined/);
});

test("toProject stops inventing an active status for an absent one", () => {
  assert.doesNotMatch(
    projectsLib,
    /pick\(p, \["status"\], "active"\)/,
    "the fallback made 'reported nothing' and 'reported active' the same value",
  );
  assert.match(projectsLib, /pick\(p, \["status"\], ""\)/, "the absence is preserved");
});

test("no surface coaxes an empty status into the word active", () => {
  for (const [name, src] of [
    ["OmnisearchModal", omniSearch],
    ["ProjectDropdownMenu", projectDropdown],
    ["ProjectsSection", projects],
  ]) {
    assert.doesNotMatch(
      src,
      /status \|\| "active"/,
      `${name} turned a missing status into the claim "active"`,
    );
  }
});

// ---------------------------------------- source pins: the shortcuts that land
//
// The first pass made each destination reachable from the project. This pass
// made the chips actually *land* where they named, closed the loop on the
// agent's own page, and removed the one control whose label disagreed with
// what it filtered.

const crewPanel = readFileSync(
  new URL("../components/sections/ProjectCrewPanel.tsx", import.meta.url),
  "utf8",
);
const botDetailView = readFileSync(
  new URL("../components/bots/BotDetailView.tsx", import.meta.url),
  "utf8",
);
const botDetailLib = readFileSync(new URL("./bot-detail.ts", import.meta.url), "utf8");

test("the hub row's Memory and Conversations chips are destinations, not a hint", () => {
  assert.match(
    projects,
    /openInspector\(p\.id, "memory"\)/,
    "the chip opens the hub ON the memory tab",
  );
  assert.match(
    projects,
    /openInspector\(p\.id, "conversations"\)/,
    "the chip opens the hub ON the conversations tab",
  );
  assert.match(
    projects,
    /initialTab=\{inspectedTab\}/,
    "the requested tab travels with the project id into the inspector",
  );
  assert.match(
    inspector,
    /initialTab\?: InspectorTab/,
    "the inspector accepts a destination tab instead of hardcoding Overview",
  );
  // The text that used to stand in for those controls is gone: a hint naming
  // four destinations while offering none of them made the operator do the
  // second half of the trip by hand.
  assert.doesNotMatch(
    projects,
    /Memory, skills, conversations and company live in the hub\./,
    "a sentence listing what the row could not reach is not a control",
  );
});

test("the Company chip opens a company only when one is actually linked", () => {
  assert.match(
    projects,
    /props\.onOpenCompany && companiesByProject/,
    "both the handler and an answered reverse read are required",
  );
  assert.match(projects, /companiesByProject\?\.get\(p\.id\)\?\.\[0\]/, "it names the first linked company");
});

test("every surface that lists a project's agents links to the agent", () => {
  for (const [name, src] of [
    ["ProjectsSection", projects],
    ["ProjectCrewPanel", crewPanel],
  ]) {
    assert.match(
      src,
      /\/bots\/\$\{encodeURIComponent\(member\.bot_name\)\}/,
      `${name} renders a project's agents without a route to their own page`,
    );
  }
});

test("a board filter set by a deep link cannot display a different project than it filters", () => {
  assert.match(
    kanbanSection,
    /!projects\.some\(\(p\) => p\.id === projectFilter\)/,
    "the option list covers the value the select is actually holding",
  );
  assert.match(
    kanbanSection,
    /`project \$\{projectFilter\}`/,
    "an id with no resolved name is shown by id rather than hidden",
  );
});

test("the bot's own page reaches back to the projects it is on", () => {
  assert.match(
    botDetailLib,
    /\/bots\/\$\{key\}\/projects/,
    "the real route — the project side already reaches the agent",
  );
  assert.match(
    botDetailView,
    /\/\?view=projects&project=/,
    "the link lands on the drill-down, not on the project list",
  );
  assert.match(
    botDetailView,
    /The server reported no projects for this bot/,
    "an empty list is the server's answer, stated as one",
  );
  assert.match(
    botDetailView,
    /could not be read/,
    "a project the crew scan could not read is named, never dropped",
  );
});
