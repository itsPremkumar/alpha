import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

/**
 * The roster activity projection is the one place the UI shows *message
 * content* it did not ask a bot for, so the client contract matters as much as
 * the mapping: the opt-in has to actually reach the server, and an unrequested
 * projection has to stay unknown rather than collapsing into "0 unread".
 */

const compiled = new Map(
  ["api-client", "bots"].map((name) => [
    name,
    ts.transpileModule(readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8"), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText,
  ]),
);

function load(name, dependencies = {}) {
  const exports = {};
  new Function("exports", "require", "process", "console", "fetch", compiled.get(name))(
    exports,
    (dependency) => {
      assert.ok(Object.hasOwn(dependencies, dependency), `Unexpected dependency: ${dependency}`);
      return dependencies[dependency];
    },
    { env: {} },
    { error: () => {} },
    () => {
      throw new Error("Raw fetch must not be used");
    },
  );
  return exports;
}

const BASE_ROW = {
  name: "coder",
  display_name: "Coder",
  role: "Backend Engineer",
  department: "engineering",
  status: "active",
  avatar: "",
  toolsets: [],
  skills: [],
  capabilities: [],
  responsibilities: [],
  routines: [],
  task_stats: {},
};

function rosterClient(rows) {
  const calls = [];
  const bots = load("bots", {
    "./api-client": {
      apiFetch: async (path) => {
        calls.push(path);
        return { json: async () => ({ bots: rows }) };
      },
    },
  });
  return { calls, bots };
}

test("the default roster read does not request the activity projection", async () => {
  const { calls, bots } = rosterClient([BASE_ROW]);
  await bots.fetchBots();
  assert.equal(calls.length, 1);
  assert.ok(!calls[0].includes("activity"), `expected no activity param, got ${calls[0]}`);
});

test("asking for activity sends the opt-in and maps every projected field", async () => {
  const { calls, bots } = rosterClient([
    {
      ...BASE_ROW,
      unread_count: 3,
      last_message_preview: "ship it",
      last_message_at: 1750000000,
      last_message_sender: "alpha",
      last_message_withheld: false,
    },
  ]);
  const [bot] = await bots.fetchBots({ activity: true });
  assert.ok(calls[0].includes("activity=true"), calls[0]);
  assert.equal(bot.unread_count, 3);
  assert.equal(bot.last_message_preview, "ship it");
  assert.equal(bot.last_message_at, 1750000000);
  assert.equal(bot.last_message_sender, "alpha");
  assert.equal(bot.last_message_withheld, false);
});

test("an unrequested projection stays null instead of reading as zero unread", async () => {
  const { bots } = rosterClient([BASE_ROW]);
  const [bot] = await bots.fetchBots();
  // "Nobody asked" and "the server measured zero" are different claims, and
  // only the first one is true here.
  assert.equal(bot.unread_count, null);
  assert.equal(bot.last_message_preview, null);
  assert.equal(bot.last_message_at, null);
  assert.equal(bot.last_message_sender, null);
  assert.equal(bot.last_message_withheld, false);
});

test("a withheld message keeps its null preview and its withheld flag", async () => {
  const { bots } = rosterClient([
    { ...BASE_ROW, unread_count: 1, last_message_preview: null, last_message_at: 1750000000, last_message_withheld: true },
  ]);
  const [bot] = await bots.fetchBots({ activity: true });
  assert.equal(bot.last_message_preview, null, "the server withheld the body, so no text may be invented");
  assert.equal(bot.last_message_withheld, true, "the UI needs the flag to explain the gap");
  assert.equal(bot.unread_count, 1);
  assert.equal(bot.last_message_at, 1750000000, "a withheld message still happened, and its time is known");
});

test("filters and the opt-in travel together", async () => {
  const { calls, bots } = rosterClient([BASE_ROW]);
  await bots.fetchBots({ status: "active", department: "engineering", activity: true });
  assert.ok(calls[0].includes("status=active"), calls[0]);
  assert.ok(calls[0].includes("department=engineering"), calls[0]);
  assert.ok(calls[0].includes("activity=true"), calls[0]);
});
