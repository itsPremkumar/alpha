// Pure-derivation tests for the nested-group tree.
//
// The tree is where a nested-group UI becomes incomprehensible or dishonest, so
// the rules pinned here are the ones that matter:
//
//   * a room whose parent did not load renders as a ROOT, never disappears;
//   * an unread presence roster produces no busy indicator, not a green dot;
//   * direct and effective counts are reported as a pair, so a node cannot claim
//     "3 members" over six visible bots;
//   * `path` and `depth` are displayed from the server, never recomputed;
//   * direct / inherited / rule-matched stay three distinct buckets.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = ts.transpileModule(readFileSync(new URL("./groups-tree.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

const tree = {};
new Function("exports", "require", source)(tree, (d) => {
  throw new Error(`groups-tree must have no imports, found ${d}`);
});

function node(id, name, { parents = [], state = "active", path, depth, childCount = 0, direct = 0, effective = 0 } = {}) {
  return {
    room_id: id,
    name,
    topic: "",
    summary: "",
    project_id: null,
    mode: "mention",
    moderator: null,
    message_count: 0,
    created_at: null,
    updated_at: null,
    scope: {
      room_id: id,
      parents,
      authority_parent: parents[0] ?? null,
      path: path ?? name,
      depth: depth ?? parents.length,
      state,
      inbound: "parent",
      outbound: "none",
      max_hop: 3,
      mark_relayed: true,
    },
    child_count: childCount,
    direct_count: direct,
    effective_count: effective,
  };
}

/* ---------------- Structure ---------------- */

test("a parent with children nests them", () => {
  const entries = tree.buildTree([node("r1", "community"), node("r2", "backend", { parents: ["r1"] })]);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].children.length, 1);
  assert.equal(entries[0].children[0].node.name, "backend");
  assert.equal(entries[0].children[0].depth, 1);
});

test("a grandchild nests two levels deep", () => {
  const entries = tree.buildTree([
    node("r1", "community"),
    node("r2", "backend", { parents: ["r1"] }),
    node("r3", "api", { parents: ["r2"] }),
  ]);
  assert.equal(tree.flattenTree(entries, new Set())[2].node.name, "api");
  assert.equal(entries[0].children[0].children[0].depth, 2);
});

test("many subgroups all attach to the same parent", () => {
  const entries = tree.buildTree([
    node("r1", "community"),
    node("r2", "backend", { parents: ["r1"] }),
    node("r3", "frontend", { parents: ["r1"] }),
    node("r4", "qa", { parents: ["r1"] }),
  ]);
  assert.equal(entries[0].children.length, 3);
  assert.equal(entries[0].hasChildren, true);
});

test("a room whose parent is missing renders as a root rather than vanishing", () => {
  // Hiding a branch because its parent failed to load is how a whole
  // sub-group disappears with no error anywhere.
  const entries = tree.buildTree([node("r2", "backend", { parents: ["never-loaded"] })]);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].node.name, "backend");
  assert.equal(entries[0].depth, 0);
});

test("a payload with several visibility parents shows the room under one of them", () => {
  const entries = tree.buildTree([
    node("a", "platform"),
    node("b", "security"),
    node("c", "joint", { parents: ["a", "b"] }),
  ]);
  // Two roots — `joint` is not one, because a parent that exists was found.
  assert.deepEqual(entries.map((e) => e.node.name).sort(), ["platform", "security"]);
  // It appears exactly once, under the first parent that exists. A tree cannot
  // show a node twice, which is why visibility fan-out is a server concern.
  const under = tree.flattenTree(entries, new Set()).filter((e) => e.node.name === "joint");
  assert.equal(under.length, 1);
  assert.equal(under[0].depth, 1);
});

test("a cycle in the payload cannot produce an infinite tree", () => {
  const entries = tree.buildTree([
    node("a", "a", { parents: ["b"] }),
    node("b", "b", { parents: ["a"] }),
  ]);
  const flat = tree.flattenTree(entries, new Set());
  assert.ok(flat.length <= 2, `cycle produced ${flat.length} nodes`);
});

test("an empty payload yields no roots rather than throwing", () => {
  assert.deepEqual(tree.buildTree([]), []);
});

/* ---------------- Collapsing ---------------- */

test("a collapsed branch hides its descendants from the flat list", () => {
  const entries = tree.buildTree([
    node("r1", "community"),
    node("r2", "backend", { parents: ["r1"] }),
    node("r3", "api", { parents: ["r2"] }),
  ]);
  assert.equal(tree.flattenTree(entries, new Set()).length, 3);
  assert.equal(tree.flattenTree(entries, new Set(["r1"])).length, 1);
});

test("collapsing an inner branch keeps its ancestors", () => {
  const entries = tree.buildTree([
    node("r1", "community"),
    node("r2", "backend", { parents: ["r1"] }),
    node("r3", "api", { parents: ["r2"] }),
  ]);
  assert.deepEqual(
    tree.flattenTree(entries, new Set(["r2"])).map((e) => e.node.name),
    ["community", "backend"],
  );
});

test("subtreeRooms returns a branch and everything under it", () => {
  const entries = tree.buildTree([
    node("r1", "community"),
    node("r2", "backend", { parents: ["r1"] }),
    node("r3", "api", { parents: ["r2"] }),
    node("r4", "qa", { parents: ["r1"] }),
  ]);
  assert.deepEqual(tree.subtreeRooms(entries, "r2").sort(), ["api", "backend"]);
  assert.deepEqual(tree.subtreeRooms(entries, "unknown"), []);
});

/* ---------------- Presence honesty ---------------- */

test("a room with no presence read reports null busy, never zero as a claim", () => {
  const entries = tree.buildTree([node("r1", "community")]);
  // Absent from the map: the caller did not ask.
  assert.equal(entries[0].busyCount, null);
});

test("a measured zero is distinguishable from an unread roster", () => {
  const entries = tree.buildTree([node("r1", "community")], { community: 0 });
  assert.equal(entries[0].busyCount, 0);
});

test("a measured busy count is carried through", () => {
  const entries = tree.buildTree([node("r1", "community")], { community: 3 });
  assert.equal(entries[0].busyCount, 3);
});

/* ---------------- Lifecycle ---------------- */

test("lifecycle states get honest labels and tones", () => {
  assert.equal(tree.stateLabel("active"), "Active");
  assert.equal(tree.stateLabel("parked"), "Parked");
  assert.equal(tree.stateLabel("draft"), "Draft");
  assert.equal(tree.stateLabel("archived"), "Archived");
  assert.equal(tree.stateLabel("hibernating"), "state not reported");
});

test("an unknown lifecycle state is neutral, never green", () => {
  assert.equal(tree.stateTone("active"), "green");
  assert.equal(tree.stateTone("hibernating"), "gray");
});

test("only live states can take subgroups", () => {
  const entries = tree.buildTree([
    node("r1", "community"),
    node("r2", "draft-room", { parents: ["r1"], state: "draft" }),
    node("r3", "parked-room", { parents: ["r1"], state: "parked" }),
    node("r4", "gone", { parents: ["r1"], state: "dissolved" }),
  ]);
  const byName = Object.fromEntries(entries[0].children.map((c) => [c.node.name, c]));
  assert.equal(tree.acceptsSubgroups(byName["parked-room"]), true);
  assert.equal(tree.acceptsSubgroups(byName["draft-room"]), false);
  assert.equal(tree.acceptsSubgroups(byName["gone"]), false);
  assert.equal(tree.acceptsSubgroups(null), false);
});

/* ---------------- Breadcrumbs ---------------- */

test("a breadcrumb joins the ancestor chain to the current room", () => {
  const chain = [
    { room_id: "r1", name: "community", path: "community", depth: 0, inherited_count: 2 },
    { room_id: "r2", name: "backend", path: "community/backend", depth: 1, inherited_count: 1 },
  ];
  assert.equal(tree.breadcrumbText(chain, "api"), "community › backend › api");
  assert.equal(tree.breadcrumbText([], "solo"), "solo");
});

/* ---------------- Membership honesty ---------------- */

function roster(over = {}) {
  return {
    room_id: "r2",
    direct: ["architect"],
    rule_matched: ["tester"],
    inherited: ["reviewer"],
    inherited_from: { r1: ["reviewer"] },
    rule_sources: { rule_1: ["tester"] },
    excluded: ["coder"],
    expired: [],
    effective: ["architect", "tester", "reviewer"],
    effective_count: 3,
    direct_count: 1,
    scope: {},
    rules: [],
    ...over,
  };
}

test("a room whose roster diverges reports both counts", () => {
  // "1 direct · 3 visible" — never just "3 members", which hides where they came from.
  assert.equal(tree.membershipHeadline(roster()), "1 direct · 3 visible");
});

test("a room whose counts agree reports one number", () => {
  assert.equal(tree.membershipHeadline(roster({ direct_count: 3 })), "3 members");
  assert.equal(
    tree.membershipHeadline(roster({ direct_count: 1, effective_count: 1 })),
    "1 member",
  );
});

test("an unread roster says so instead of claiming nobody is present", () => {
  assert.equal(tree.membershipHeadline(null), "Roster not read yet");
  assert.equal(tree.membershipHeadline(null, "Gateway unreachable"), "Roster could not be read");
});

test("roster members split into direct, inherited and rule-matched", () => {
  const buckets = tree.rosterBuckets(roster(), () => "community");
  assert.deepEqual(buckets.direct.map((b) => b.name), ["architect"]);
  assert.deepEqual(buckets.inherited.map((b) => b.name), ["reviewer"]);
  assert.equal(buckets.inherited[0].via, "community", "inheritance names its source room");
  assert.deepEqual(buckets.ruleMatched.map((b) => b.name), ["tester"]);
  assert.equal(buckets.ruleMatched[0].rule, "rule_1");
});

test("an excluded member leaves the live buckets and is named separately", () => {
  const r = roster({ direct: ["architect", "coder"], excluded: ["coder"] });
  const buckets = tree.rosterBuckets(r, () => "community");
  assert.deepEqual(buckets.direct.map((b) => b.name), ["architect"]);
  assert.deepEqual(buckets.excluded, ["coder"]);
});

test("an expired borrowed member is named as expired rather than silently dropped", () => {
  const r = roster({ direct: ["secops"], expired: ["secops"] });
  const buckets = tree.rosterBuckets(r, () => "community");
  assert.deepEqual(buckets.direct, []);
  assert.deepEqual(buckets.expired, ["secops"]);
});

test("an unread roster yields empty buckets, not a fake empty membership", () => {
  const buckets = tree.rosterBuckets(null, () => "x");
  assert.deepEqual(buckets.direct, []);
  assert.deepEqual(buckets.inherited, []);
});

/* ---------------- Server-derived values ---------------- */

test("depth and path come from the server untouched", () => {
  const entries = tree.buildTree([
    node("r1", "community"),
    // The server says depth 3 with a path the client could not have derived.
    node("r2", "backend", { parents: ["r1"], depth: 3, path: "a/b/c/backend" }),
  ]);
  assert.equal(entries[0].children[0].node.scope.depth, 3);
  assert.equal(entries[0].children[0].node.scope.path, "a/b/c/backend");
});

test("an absent membership count stays null rather than becoming zero", () => {
  const bare = node("r1", "community");
  bare.direct_count = null;
  bare.effective_count = null;
  const entries = tree.buildTree([bare]);
  assert.equal(entries[0].node.direct_count, null);
  assert.equal(entries[0].node.effective_count, null);
});