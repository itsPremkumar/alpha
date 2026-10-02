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

/**
 * Minimal in-memory IndexedDB stand-in, faithful to the subset of the API that
 * `src/lib/history-store.ts` actually uses:
 *
 * - `indexedDB.open` fires `onupgradeneeded` (with `request.result` live so
 *   the store-creation guard runs) and then `onsuccess`.
 * - A transaction stays open while requests are pending — including requests
 *   issued from *inside* another request's `onsuccess` handler (the
 *   `updateRecord` discipline) — and reports `oncomplete` only once the
 *   microtask queue has drained with nothing left to do. A timer-based idle
 *   check reproduces that ordering: request callbacks run as microtasks, and
 *   timers only fire after the microtask queue is empty.
 * - Object stores persist on the database (not the transaction), keyed by the
 *   declared `keyPath`.
 *
 * It deliberately does not model versioning beyond first-open upgrade, cursors,
 * or error injection: the store contract under test is the happy path plus the
 * migration marker.
 */
function createFakeIndexedDB() {
  const registry = new Map(); // db name -> { version, stores: Map<name, { keyPath, data }> }

  class FakeRequest {
    constructor() {
      this.result = undefined;
      this.error = null;
      this.onsuccess = null;
      this.onerror = null;
    }
  }

  class FakeTransaction {
    constructor(names) {
      this.names = names;
      this.pending = 0;
      this.finished = false;
      this.error = null;
      this.oncomplete = null;
      this.onerror = null;
      this.onabort = null;
      this.timer = null;
      this.arm();
    }

    issue(work, request) {
      if (this.finished) {
        request.error = new Error("TransactionInactiveError");
        if (request.onerror) request.onerror();
        return;
      }
      this.pending += 1;
      queueMicrotask(() => {
        if (this.finished) return;
        let result;
        try {
          result = work();
        } catch (error) {
          this.pending -= 1;
          request.error = error;
          if (request.onerror) request.onerror();
          else this.fail(error);
          this.arm();
          return;
        }
        this.pending -= 1;
        request.result = result;
        // The handler may synchronously issue the next request (put inside
        // get's onsuccess), which keeps `pending` above zero before the idle
        // check can run.
        if (request.onsuccess) request.onsuccess();
        this.arm();
      });
    }

    fail(error) {
      if (this.finished) return;
      this.finished = true;
      this.error = error;
      if (this.onerror) this.onerror();
    }

    abort() {
      if (this.finished) return;
      this.finished = true;
      if (this.onabort) this.onabort();
    }

    arm() {
      if (this.finished || this.timer !== null) return;
      this.timer = setTimeout(() => {
        this.timer = null;
        if (this.finished) return;
        if (this.pending === 0) {
          this.finished = true;
          if (this.oncomplete) this.oncomplete();
        } else {
          this.arm();
        }
      }, 0);
    }
  }

  class FakeObjectStore {
    constructor(transaction, record) {
      this.transaction = transaction;
      this.record = record;
    }

    get(key) {
      const request = new FakeRequest();
      this.transaction.issue(() => this.record.data.get(key), request);
      return request;
    }

    getAll() {
      const request = new FakeRequest();
      this.transaction.issue(() => Array.from(this.record.data.values()), request);
      return request;
    }

    put(value) {
      const request = new FakeRequest();
      this.transaction.issue(() => {
        const key = value?.[this.record.keyPath];
        if (key === undefined) throw new Error(`No key for keyPath "${this.record.keyPath}"`);
        this.record.data.set(key, value);
        return key;
      }, request);
      return request;
    }

    delete(key) {
      const request = new FakeRequest();
      this.transaction.issue(() => this.record.data.delete(key), request);
      return request;
    }

    clear() {
      const request = new FakeRequest();
      this.transaction.issue(() => {
        this.record.data.clear();
        return undefined;
      }, request);
      return request;
    }
  }

  class FakeDatabase {
    constructor(entry) {
      this.entry = entry;
      this.onversionchange = null;
      this.onclose = null;
    }

    get objectStoreNames() {
      const names = new Set(this.entry.stores.keys());
      return { contains: (name) => names.has(name) };
    }

    createObjectStore(name, options) {
      if (this.entry.stores.has(name)) throw new Error(`Store "${name}" already exists`);
      this.entry.stores.set(name, { keyPath: options?.keyPath, data: new Map() });
    }

    record(name) {
      const record = this.entry.stores.get(name);
      if (!record) throw new Error(`Store "${name}" not found`);
      return record;
    }

    transaction(nameOrNames, _mode) {
      const names = Array.isArray(nameOrNames) ? nameOrNames : [nameOrNames];
      for (const name of names) this.record(name); // fail on an unknown store, like the real API
      const database = this;
      const transaction = new FakeTransaction(names);
      transaction.objectStore = (name) => {
        if (!transaction.names.includes(name)) throw new Error(`Store "${name}" is not in this transaction`);
        return new FakeObjectStore(transaction, database.record(name));
      };
      return transaction;
    }

    close() {
      /* Nothing holds resources; the module's onversionchange wiring is unused here. */
    }
  }

  return {
    open(name, version) {
      const request = new FakeRequest();
      let entry = registry.get(name);
      const needsUpgrade = !entry || entry.version < version;
      if (!entry) {
        entry = { version: 0, stores: new Map() };
        registry.set(name, entry);
      }
      const database = new FakeDatabase(entry);
      request.result = database;
      setTimeout(() => {
        if (needsUpgrade) {
          entry.version = version;
          request.result = database;
          if (request.onupgradeneeded) request.onupgradeneeded();
        }
        setTimeout(() => {
          request.result = database;
          if (request.onsuccess) request.onsuccess();
        }, 0);
      }, 0);
      return request;
    },
  };
}

const { branding } = await loadModule("lib/branding.ts");
const history = await loadModule("lib/history-store.ts");

test("neutral branding has a single immutable display name", () => {
  assert.equal(branding.name, "Alpha");
  assert.equal(branding.assistantLabel, `${branding.name} Assistant`);
  assert.ok(Object.isFrozen(branding));
  for (const [key, value] of Object.entries(branding)) {
    if (key === "icons") continue; // the one structured entry, checked below
    assert.equal(typeof value, "string", key);
    assert.ok(value.trim(), key);
  }
  // The browser/PWA icon map is frozen like the rest, and every surface it
  // names is a non-empty path — an empty path would 404 the tab icon.
  assert.ok(Object.isFrozen(branding.icons));
  assert.ok(Object.keys(branding.icons).length > 0);
  for (const [surface, path] of Object.entries(branding.icons)) {
    assert.equal(typeof path, "string", `icons.${surface}`);
    assert.ok(path.trim(), `icons.${surface}`);
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

test("chat history storage key, content and export identity work properly", async (t) => {
  const localStorageDescriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const storage = new Map();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
  });
  const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { indexedDB: createFakeIndexedDB() },
  });
  t.after(() => {
    if (localStorageDescriptor) Object.defineProperty(globalThis, "localStorage", localStorageDescriptor);
    else delete globalThis.localStorage;
    if (windowDescriptor) Object.defineProperty(globalThis, "window", windowDescriptor);
    else delete globalThis.window;
  });

  const fixture = {
    app: "alpha-chat-history",
    version: 1,
    threads: [{ thread_id: "alpha-thread", title: "Alpha conversation" }],
    messages: { "alpha-thread": [{ id: "alpha-message", role: "assistant", content: "Alpha saved message" }] },
    meta: { "alpha-thread": { botName: "lead_agent", goal: null } },
  };
  storage.set("alpha.chatstore.v1", JSON.stringify(fixture));

  // The first open imports the legacy localStorage archive into IndexedDB and
  // then consumes the legacy key, leaving the durable migration marker behind.
  const migrated = await history.loadStore();
  assert.deepEqual(migrated.threads, fixture.threads);
  assert.deepEqual(migrated.messages, fixture.messages);
  assert.deepEqual(migrated.meta, fixture.meta);
  assert.equal(storage.get("alpha.chat-history.migrated.v1"), "alpha-chat-history");
  assert.deepEqual([...storage.keys()], ["alpha.chat-history.migrated.v1"]);

  await history.clearLocalStore();
  assert.deepEqual((await history.loadStore()).threads, []);

  assert.deepEqual(await history.importStoreJson(JSON.stringify(fixture)), { threads: 1, messages: 1 });

  const exported = JSON.parse(await history.exportStoreJson());
  assert.equal(exported.app, fixture.app);
  assert.equal(exported.version, fixture.version);
  // Appending messages stamps the thread with `updated_at`; every identity
  // field must still round-trip through the archive untouched.
  const [{ updated_at: archiveStamp, ...importedThread }] = exported.threads;
  assert.ok(archiveStamp, "the imported thread carries its archive stamp");
  assert.deepEqual([importedThread], fixture.threads);
  assert.deepEqual(exported.messages, fixture.messages);
  assert.deepEqual(exported.meta, fixture.meta);
  assert.deepEqual(
    [...storage.keys()],
    ["alpha.chat-history.migrated.v1"],
    "an import never resurrects the legacy localStorage key",
  );
  assert.deepEqual(await history.importStoreJson(JSON.stringify(exported)), { threads: 0, messages: 0 });
});

test("invalid history reports a neutral error", async () => {
  await assert.rejects(history.importStoreJson("{}"), {
    message: "That file is not a valid chat history export.",
  });
});
