import assert from "node:assert/strict";
import test from "node:test";
import ts from "typescript";
import { readFileSync } from "node:fs";

function moduleUrl(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
}

const companyUrl = moduleUrl(`
  export function seed(companiesInput, workInput, failure = null) {
    companies = companiesInput; work = workInput; failCompany = failure; moves = [];
  }
  let companies = []; let work = {}; let failCompany = null; let moves = [];
  export function readMoves() { return moves; }
  export async function listCompanies() { return companies; }
  export async function getCompanyWork(id) {
    if (id === failCompany) throw new Error("company work unavailable");
    return work[id] ?? [];
  }
  export async function moveWorkItem(companyId, taskId, column) { moves.push({ companyId, taskId, column }); }
`);
const httpUrl = moduleUrl(`
  let legacy = []; let legacyError = null;
  export function seedLegacy(rows, error = null) { legacy = rows; legacyError = error; }
  export async function get(path) {
    if (path === "/company/kanban/tasks?limit=100") {
      if (legacyError) throw legacyError;
      return { tasks: legacy };
    }
    throw new Error("unexpected route " + path);
  }
  export async function send() { throw new Error("unexpected mutation"); }
  export function asList(value, keys = []) {
    if (Array.isArray(value)) return value;
    for (const key of keys) if (Array.isArray(value?.[key])) return value[key];
    return [];
  }
  export function pick(value, keys, fallback) {
    for (const key of keys) if (value?.[key] != null) return value[key];
    return fallback;
  }
`);

let source = readFileSync(new URL("./kanban.ts", import.meta.url), "utf8")
  .replaceAll('from "./http"', `from "${httpUrl}"`)
  .replaceAll('import("./company")', `import("${companyUrl}")`);
const js = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const kanban = await import(moduleUrl(js));
const company = await import(companyUrl);
const http = await import(httpUrl);
let boardSource = readFileSync(new URL("./kanban-board.ts", import.meta.url), "utf8")
  .replaceAll('from "./kanban"', `from "${moduleUrl(js)}"`)
  .replaceAll('import("./company")', `import("${companyUrl}")`);
const board = await import(moduleUrl(ts.transpileModule(boardSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText));

const item = (task_id, over = {}) => ({
  task_id, board_id: "board", title: task_id, assignee_agent_handle: "coder",
  column: "todo", priority: "high", definition_of_done: ["evidence required"],
  cost_usd: null, cost_basis: "unmeasured", ...over,
});

test("the Board reads every active Company OS board and namespaces task IDs", async () => {
  company.seed(
    [
      { company_id: "co-a", state: "active" },
      { company_id: "co-b", state: "active" },
      { company_id: "co-archived", state: "archived" },
    ],
    {
      "co-a": [item("TASK-1", { column: "in_review" })],
      "co-b": [item("TASK-1", { column: "blocked" })],
      "co-archived": [item("OLD")],
    },
  );
  http.seedLegacy([{ id: "legacy-1", title: "legacy task", status: "todo" }]);
  const tasks = await kanban.listKanbanTasks();

  assert.deepEqual(tasks.map((task) => task.id), ["co-a::TASK-1", "co-b::TASK-1", "legacy-1"]);
  assert.deepEqual(tasks.slice(0, 2).map((task) => task.companyId), ["co-a", "co-b"]);
  assert.deepEqual(tasks.slice(0, 2).map((task) => task.status), ["review", "blocked"]);
  assert.equal(tasks[0].description, "evidence required");
});

test("an active Company OS board read failure propagates instead of falling back to legacy", async () => {
  company.seed([{ company_id: "co-a", state: "active" }], {}, "co-a");
  http.seedLegacy([{ id: "legacy-task", title: "must not mask failure", status: "todo" }]);
  await assert.rejects(kanban.listKanbanTasks(), /company work unavailable/);
});

test("the documented legacy no-organization 404 does not hide Company OS cards", async () => {
  company.seed([{ company_id: "co-a", state: "active" }], { "co-a": [item("TASK-1")] });
  http.seedLegacy([], Object.assign(new Error("No active organizations found."), { status: 404 }));
  assert.deepEqual((await kanban.listKanbanTasks()).map((task) => task.id), ["co-a::TASK-1"]);
});

test("unexpected legacy transport failures remain visible when Company OS is active", async () => {
  company.seed([{ company_id: "co-a", state: "active" }], { "co-a": [item("TASK-1")] });
  http.seedLegacy([], Object.assign(new Error("Gateway unavailable"), { status: 503 }));
  await assert.rejects(kanban.listKanbanTasks(), /Gateway unavailable/);
});

test("a Company OS card move targets its owning board and translates Review correctly", async () => {
  company.seed([], {});
  await board.pushStatus({ serverId: "co-a::TASK-1", serverCompanyId: "co-a" }, "review");
  assert.deepEqual(company.readMoves(), [
    { companyId: "co-a", taskId: "TASK-1", column: "in_review" },
  ]);
});

test("the legacy board remains supported when the owner has no active Company OS", async () => {
  company.seed([{ company_id: "co-archived", state: "archived" }], {});
  http.seedLegacy([{ id: "legacy-task", title: "legacy", status: "todo" }]);
  const tasks = await kanban.listKanbanTasks();
  assert.deepEqual(tasks.map((task) => task.id), ["legacy-task"]);
});
