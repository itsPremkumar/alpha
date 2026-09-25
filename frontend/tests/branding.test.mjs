import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = (path) => readFile(new URL(`../src/${path}`, import.meta.url), "utf8");

async function loadModule(path) {
  const { outputText } = ts.transpileModule(await source(path), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}

const { branding } = await loadModule("lib/branding.ts");
const history = await loadModule("lib/history-store.ts");

test("neutral branding has a single immutable display name", () => {
  assert.equal(branding.name, "Alpha");
  assert.equal(branding.assistantLabel, `${branding.name} Assistant`);
  assert.ok(Object.isFrozen(branding));
  for (const value of Object.values(branding)) {
    assert.equal(typeof value, "string");
    assert.ok(value.trim());
  }
});

test("metadata and visible UI consume centralized branding", async () => {
  const consumers = {
    "app/layout.tsx": [
      "default: branding.name",
      "description: branding.description",
      "${branding.name}",
    ],
    "components/ChatView.tsx": ["branding.name", "branding.intro", "branding.assistantLabel"],
    "components/Composer.tsx": ["branding.name"],
    "components/MessageItem.tsx": ["branding.assistantLabel"],
    "components/ThreadSidebar.tsx": ["branding.name"],
  };
  for (const [path, references] of Object.entries(consumers)) {
    const text = await source(path);
    assert.ok(text.includes('import { branding } from "@/lib/branding";'), path);
    for (const reference of references) assert.ok(text.includes(reference), `${path}: ${reference}`);
  }
});

test("chat history storage key, content and export identity work properly", (t) => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const storage = new Map();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
  });
  t.after(() => {
    if (descriptor) Object.defineProperty(globalThis, "localStorage", descriptor);
    else delete globalThis.localStorage;
  });
  const fixture = {
    app: "agent-workspace-chat-history",
    version: 1,
    threads: [{ thread_id: "agent-workspace-thread", title: "Alpha conversation" }],
    messages: { "agent-workspace-thread": [{ id: "agent-workspace-message", role: "assistant", content: "Alpha saved message" }] },
    meta: { "agent-workspace-thread": { botName: "lead_agent", goal: null } },
  };
  storage.set("alpha.chatstore.v1", JSON.stringify(fixture));
  assert.deepEqual(history.loadStore().threads, fixture.threads);
  history.clearLocalStore();
  assert.deepEqual(history.importStoreJson(JSON.stringify(fixture)), { threads: 1, messages: 1 });
  assert.deepEqual([...storage.keys()], ["alpha.chatstore.v1"]);
  const exported = JSON.parse(history.exportStoreJson());
  assert.equal(exported.app, fixture.app);
  assert.equal(exported.version, fixture.version);
  assert.deepEqual(exported.threads, fixture.threads);
  assert.deepEqual(exported.messages, fixture.messages);
  assert.deepEqual(exported.meta, fixture.meta);
  assert.deepEqual(history.importStoreJson(JSON.stringify(exported)), { threads: 0, messages: 0 });
});

test("invalid history reports a neutral error", () => {
  assert.throws(() => history.importStoreJson("{}"), {
    message: "That file is not a valid chat history export.",
  });
});
