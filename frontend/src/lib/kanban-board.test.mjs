// kanban-board.test.mjs — the local<->server board merge must not lose cards.
//
// The project kanban is local-first: cards created in the UI live in
// localStorage with serverId === null, and only server-owned cards carry a
// serverId. mergeServerCards() runs on every KanbanSection load whenever the
// company board is reachable, so a keying mistake there silently deletes the
// user's own cards (and only while the server is up, because the failure branch
// takes the untouched `local` array).
//
// Pure Node test (node --test src/lib/kanban-board.test.mjs): transpiles
// kanban.ts and kanban-board.ts and rewrites their imports to data: URLs so no
// network, localStorage or browser runs.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

function compile(name, deps) {
  let source = readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8");
  for (const [spec, url] of Object.entries(deps)) {
    source = source.replaceAll(`from "./${spec}"`, `from "${url}"`);
  }
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  });
  return `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`;
}

// kanban.ts -> ./http stub; kanban-board.ts -> the compiled kanban.ts.
const apiClientUrl = compile("api-client", {});
const httpStub = compile("http", { "api-client": apiClientUrl });
const kanbanUrl = compile("kanban", { http: httpStub });
const board = await import(compile("kanban-board", { kanban: kanbanUrl }));

const serverTask = (id, over = {}) => ({
  id,
  title: `server ${id}`,
  status: "ready",
  assignee: "",
  description: "",
  ...over,
});

const localCard = (id, over = {}) => ({
  ...board.emptyCard(),
  id,
  title: `local ${id}`,
  serverId: null,
  ...over,
});

test("mergeServerCards keeps every local-only card when the server board is reachable", () => {
  const local = [localCard("a"), localCard("b"), localCard("c")];
  const merged = board.mergeServerCards(local, [serverTask("srv-1")]);

  const kept = merged.filter((c) => c.serverId === null).map((c) => c.id);
  assert.deepEqual(
    kept,
    ["a", "b", "c"],
    "local cards have no serverId, so they are not mirrors and must all survive the merge",
  );
  assert.equal(merged.length, 4, "3 local cards + 1 server card");
});

test("mergeServerCards keeps local-only cards even when the server board is empty", () => {
  const local = [localCard("a"), localCard("b")];
  const merged = board.mergeServerCards(local, []);

  assert.deepEqual(
    merged.map((c) => c.id),
    ["a", "b"],
    "an empty server board must not collapse the local board to one card",
  );
});

test("mergeServerCards still drops a mirror whose server card disappeared", () => {
  const local = [localCard("keep"), localCard("gone", { serverId: "srv-gone" })];
  const merged = board.mergeServerCards(local, []);

  assert.deepEqual(
    merged.map((c) => c.id),
    ["keep"],
    "a local mirror of a server card the server no longer lists is dropped",
  );
});

test("mergeServerCards reuses the local mirror for a server card and keeps its own fields", () => {
  const local = [localCard("m", { serverId: "srv-1", title: "edited locally", agent: null })];
  const merged = board.mergeServerCards(local, [serverTask("srv-1", { assignee: "scout" })]);

  assert.equal(merged.length, 1);
  assert.equal(merged[0].id, "m", "the local mirror is reused, not duplicated as srv-srv-1");
  assert.equal(merged[0].title, "edited locally", "local edits win");
  assert.equal(merged[0].agent, "scout", "a null local assignee adopts the server's");
});

test("mergeServerCards does not confuse two local-only cards that share a serverId of ''", () => {
  // The regression itself: serverId was coerced to "" for every local card and
  // all of them landed on one Map key.
  const local = [
    { ...board.emptyCard(), id: "x", title: "x", serverId: "" },
    { ...board.emptyCard(), id: "y", title: "y", serverId: "" },
  ];
  const merged = board.mergeServerCards(local, [serverTask("srv-1")]);
  assert.deepEqual(
    merged.filter((c) => !c.serverId).map((c) => c.id),
    ["x", "y"],
  );
});

test("mergeServerCards is idempotent across repeated syncs", () => {
  const local = [localCard("a"), localCard("b")];
  const server = [serverTask("srv-1")];

  const once = board.mergeServerCards(local, server);
  const twice = board.mergeServerCards(once, server);
  const thrice = board.mergeServerCards(twice, server);

  assert.deepEqual(once.map((c) => c.id).sort(), ["a", "b", "srv-srv-1"]);
  assert.deepEqual(
    twice.map((c) => c.id).sort(),
    once.map((c) => c.id).sort(),
    "syncing again must not delete anything",
  );
  assert.deepEqual(
    thrice.map((c) => c.id).sort(),
    once.map((c) => c.id).sort(),
    "a board that is re-synced on every load must converge, not decay",
  );
});
