import { ChatMessage, Thread } from "@/types/chat";

/**
 * Complete, browser-local chat history.
 *
 * The Gateway remains the source of truth for live server state, while this
 * IndexedDB repository keeps an uncapped local archive on this computer. The
 * previous localStorage implementation silently kept only 100 threads, 300
 * messages per thread, and truncated every message at 20,000 characters. This
 * store deliberately has no application-level history caps. Browser quota or
 * IndexedDB failures are surfaced to the caller instead of dropping the oldest
 * conversations.
 *
 * A legacy `alpha.chatstore.v1` localStorage value is imported once on first
 * open, then removed after the IndexedDB transaction commits.
 */

const DB_NAME = "alpha-chat-history";
const DB_VERSION = 2;
const THREADS_STORE = "threads";
const HISTORIES_STORE = "histories";
const META_STORE = "meta";
const SETTINGS_STORE = "settings";
const LEGACY_KEY = "alpha.chatstore.v1";
const LEGACY_MIGRATION_KEY = "alpha.chat-history.migrated.v1";

export interface ThreadMeta {
  botName: string | null;
  goal: string | null;
}

export interface StoreShape {
  version: 1;
  threads: Thread[];
  messages: Record<string, ChatMessage[]>;
  meta: Record<string, ThreadMeta>;
  warning?: string;
}

interface HistoryRow {
  thread_id: string;
  messages: ChatMessage[];
}

interface MetaRow {
  thread_id: string;
  value: ThreadMeta;
}

let databasePromise: Promise<IDBDatabase> | null = null;

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed"));
  });
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error ?? new Error("IndexedDB transaction failed"));
    transaction.onabort = () => reject(transaction.error ?? new Error("IndexedDB transaction was aborted"));
  });
}

/**
 * Like {@link transactionDone}, but prefers a real merge failure over the
 * generic abort error. A throw inside a request callback cannot propagate to
 * the awaiting caller directly — aborting the transaction is the only way to
 * stop it — so the original cause is carried here and reported instead.
 */
function transactionDoneWith(transaction: IDBTransaction, failure: { error: unknown }): Promise<void> {
  return transactionDone(transaction).catch((error) => {
    throw failure.error ?? error;
  });
}

/**
 * Callback-safe read-modify-write.
 *
 * The unsafe shape is `const current = await get(key); store.put(...)`. A
 * browser auto-commits a `readwrite` transaction as soon as the microtask queue
 * drains, so the `put()` issued after that `await` can land on an already
 * committed transaction and throw `TransactionInactiveError` — silently losing
 * the write. Issuing the write from inside the request's own success handler
 * keeps the transaction open, which also makes the merge atomic against a
 * concurrent writer.
 */
async function updateRecord<T>(
  database: IDBDatabase,
  storeName: string,
  key: IDBValidKey,
  update: (current: T | undefined) => T | undefined,
): Promise<void> {
  const transaction = database.transaction(storeName, "readwrite");
  const failure: { error: unknown } = { error: null };
  const done = transactionDoneWith(transaction, failure);
  const store = transaction.objectStore(storeName);
  const request = store.get(key) as IDBRequest<T | undefined>;
  request.onsuccess = () => {
    try {
      const next = update(request.result);
      if (next !== undefined) store.put(next);
    } catch (error) {
      failure.error = error;
      try {
        transaction.abort();
      } catch {
        /* Already finished; the recorded failure is what the caller needs. */
      }
    }
  };
  request.onerror = () => {
    failure.error = failure.error ?? request.error;
    try {
      transaction.abort();
    } catch {
      /* Already aborted. */
    }
  };
  await done;
}

/**
 * Multi-store variant of {@link updateRecord}. Every read/write happens inside
 * request callbacks, so the transaction stays open and the batch is atomic.
 */
async function updateRecords(
  database: IDBDatabase,
  storeNames: string[],
  operations: Array<{ storeName: string; key: IDBValidKey; update: (current: any) => any }>,
): Promise<void> {
  if (operations.length === 0) return;
  const transaction = database.transaction(storeNames, "readwrite");
  const failure: { error: unknown } = { error: null };
  const done = transactionDoneWith(transaction, failure);
  for (const operation of operations) {
    const store = transaction.objectStore(operation.storeName);
    const request = store.get(operation.key);
    request.onsuccess = () => {
      try {
        const next = operation.update(request.result);
        if (next !== undefined) store.put(next);
      } catch (error) {
        failure.error = failure.error ?? error;
        try {
          transaction.abort();
        } catch {
          /* Already finished; the recorded failure is what the caller needs. */
        }
      }
    };
    request.onerror = () => {
      failure.error = failure.error ?? request.error;
      try {
        transaction.abort();
      } catch {
        /* Already aborted. */
      }
    };
  }
  await done;
}

type LegacyParseResult =
  | { status: "absent" }
  | { status: "valid"; store: StoreShape }
  | { status: "invalid"; error: string };

function parseLegacyStore(): LegacyParseResult {
  let raw: string | null;
  try {
    raw = localStorage.getItem(LEGACY_KEY);
  } catch (error) {
    return {
      status: "invalid",
      error: error instanceof Error ? error.message : "Legacy local history could not be read.",
    };
  }
  if (!raw) return { status: "absent" };
  try {
    const parsed = JSON.parse(raw) as Partial<StoreShape>;
    if (!Array.isArray(parsed.threads)) {
      return { status: "invalid", error: "Legacy chat history has no valid thread list." };
    }
    return {
      status: "valid",
      store: {
        version: 1,
        threads: parsed.threads.filter(
          (thread): thread is Thread => Boolean(thread && typeof thread.thread_id === "string"),
        ),
        messages:
          parsed.messages && typeof parsed.messages === "object"
            ? Object.fromEntries(
                Object.entries(parsed.messages).filter((entry): entry is [string, ChatMessage[]] => {
                  const value = entry[1];
                  return Array.isArray(value) && value.every((message) => Boolean(message && typeof message.id === "string"));
                }),
              )
            : {},
        meta:
          parsed.meta && typeof parsed.meta === "object"
            ? Object.fromEntries(
                Object.entries(parsed.meta).map(([threadId, value]) => [
                  threadId,
                  {
                    botName:
                      value && typeof value === "object" && typeof (value as ThreadMeta).botName === "string"
                        ? (value as ThreadMeta).botName
                        : null,
                    goal:
                      value && typeof value === "object" && typeof (value as ThreadMeta).goal === "string"
                        ? (value as ThreadMeta).goal
                        : null,
                  },
                ]),
              )
            : {},
      },
    };
  } catch (error) {
    return {
      status: "invalid",
      error: error instanceof Error ? error.message : "Legacy chat history contains invalid JSON.",
    };
  }
}

interface SettingRow {
  key: string;
  value: string;
}

async function migrateLegacyStore(database: IDBDatabase): Promise<void> {
  const readTransaction = database.transaction(SETTINGS_STORE, "readonly");
  const readDone = transactionDone(readTransaction);
  const marker = await requestResult(
    readTransaction.objectStore(SETTINGS_STORE).get("legacy_migration") as IDBRequest<SettingRow | undefined>,
  );
  await readDone;

  if (marker?.value === DB_NAME) {
    try {
      localStorage.removeItem(LEGACY_KEY);
    } catch {
      /* The durable IndexedDB marker prevents a repeated overwrite. */
    }
    return;
  }

  const legacy = parseLegacyStore();
  if (legacy.status === "invalid") {
    const warning = `Legacy chat history was preserved but could not be migrated: ${legacy.error}`;
    const warningTransaction = database.transaction(SETTINGS_STORE, "readwrite");
    const warningDone = transactionDone(warningTransaction);
    warningTransaction.objectStore(SETTINGS_STORE).put({ key: "legacy_migration_error", value: warning });
    await warningDone;
    console.error(warning);
    return;
  }
  if (legacy.status === "absent") {
    const markerTransaction = database.transaction(SETTINGS_STORE, "readwrite");
    const markerDone = transactionDone(markerTransaction);
    markerTransaction.objectStore(SETTINGS_STORE).put({ key: "legacy_migration", value: DB_NAME });
    await markerDone;
    return;
  }

  const transaction = database.transaction(
    [THREADS_STORE, HISTORIES_STORE, META_STORE, SETTINGS_STORE],
    "readwrite",
  );
  const done = transactionDone(transaction);
  const threads = transaction.objectStore(THREADS_STORE);
  const histories = transaction.objectStore(HISTORIES_STORE);
  const meta = transaction.objectStore(META_STORE);
  const settings = transaction.objectStore(SETTINGS_STORE);
  settings.delete("legacy_migration_error");

  for (const thread of legacy.store.threads) threads.put(thread);
  for (const [threadId, messages] of Object.entries(legacy.store.messages)) {
    histories.put({ thread_id: threadId, messages });
  }
  for (const [threadId, value] of Object.entries(legacy.store.meta)) {
    meta.put({ thread_id: threadId, value });
  }
  settings.put({ key: "legacy_migration", value: DB_NAME });
  await done;

  try {
    localStorage.setItem(LEGACY_MIGRATION_KEY, DB_NAME);
    localStorage.removeItem(LEGACY_KEY);
  } catch {
    // The marker committed inside IndexedDB above prevents a stale legacy value
    // from overwriting newer history on the next page load.
  }
}

function openHistoryDatabase(): Promise<IDBDatabase> {
  if (databasePromise) return databasePromise;
  if (typeof window === "undefined" || typeof window.indexedDB === "undefined") {
    return Promise.reject(new Error("This browser does not provide IndexedDB for local chat history."));
  }

  const pending = new Promise<IDBDatabase>((resolve, reject) => {
    const request = window.indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(THREADS_STORE)) {
        database.createObjectStore(THREADS_STORE, { keyPath: "thread_id" });
      }
      if (!database.objectStoreNames.contains(HISTORIES_STORE)) {
        database.createObjectStore(HISTORIES_STORE, { keyPath: "thread_id" });
      }
      if (!database.objectStoreNames.contains(META_STORE)) {
        database.createObjectStore(META_STORE, { keyPath: "thread_id" });
      }
      if (!database.objectStoreNames.contains(SETTINGS_STORE)) {
        database.createObjectStore(SETTINGS_STORE, { keyPath: "key" });
      }
    };
    request.onerror = () => reject(request.error ?? new Error("Could not open local chat history storage."));
    request.onblocked = () => reject(new Error("Local chat history storage is blocked by another open Alpha tab."));
    request.onsuccess = () => {
      const database = request.result;
      // Another tab is upgrading the schema. Close this handle and drop the
      // cached promise so the next caller reopens at the new version instead
      // of writing through a connection that is about to become unusable.
      database.onversionchange = () => {
        database.close();
        if (databasePromise === cached) databasePromise = null;
      };
      database.onclose = () => {
        if (databasePromise === cached) databasePromise = null;
      };
      void migrateLegacyStore(database).then(
        () => resolve(database),
        (error) => {
          database.close();
          reject(error instanceof Error ? error : new Error("Could not migrate local chat history."));
        },
      );
    };
  });

  const cached = pending.catch((error) => {
    databasePromise = null;
    throw error;
  });
  databasePromise = cached;
  return cached;
}

function normalizeMessage(message: ChatMessage): ChatMessage {
  let content = "";
  if (typeof message.content === "string") content = message.content;
  else if (message.content !== undefined && message.content !== null) {
    try {
      content = JSON.stringify(message.content) ?? String(message.content);
    } catch {
      content = String(message.content);
    }
  }
  return { ...message, content };
}

function messageTime(message: ChatMessage): number {
  const value = Date.parse(message.createdAt || "");
  return Number.isFinite(value) ? value : 0;
}

/**
 * Field-wise thread merge.
 *
 * An incoming (server) value replaces the local one, with two deliberate
 * exceptions, because the two states mean different things:
 *
 * - `undefined` / absent key — the server did not mention the field. An older
 *   Gateway omits `bot_name` / `project_id` entirely, so the locally known bot
 *   link or project scope must survive.
 * - `""` — `api.ts -> threadFromResponse` writes an empty string as the
 *   "omitted" sentinel for `title`, so it is treated the same way.
 *
 * An explicit `null` is NOT skipped: it is how the server says "this thread has
 * no bot" / "this thread is not in a project", and skipping it would leave a
 * chat that the user just removed from a project permanently scoped to it.
 */
function mergeThreadFields(local: Thread, incoming: Thread): Thread {
  const merged: Thread = { ...local };
  for (const [key, value] of Object.entries(incoming)) {
    if (value === undefined || value === "") continue;
    (merged as unknown as Record<string, unknown>)[key] = value;
  }
  // The identity and existence of the record itself must always be adopted.
  merged.thread_id = incoming.thread_id;
  return merged;
}

/**
 * Merge a server page into the local archive without creating duplicate turns.
 * Older local optimistic ids differ from durable server ids, so exact-id
 * matching alone is insufficient. A same-role/same-content pair is reconciled
 * only when it represents the same run or timestamps are close; repeated user
 * prompts with the same text remain separate.
 */
function mergeMessageFields(existing: ChatMessage, incoming: ChatMessage): ChatMessage {
  const merged: ChatMessage = { ...existing };
  for (const [key, value] of Object.entries(incoming)) {
    if (value !== undefined) (merged as unknown as Record<string, unknown>)[key] = value;
  }
  return normalizeMessage(merged);
}

function mergeMessages(existing: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const merged = existing.map(normalizeMessage);
  const used = new Set<number>();

  for (const raw of incoming) {
    const message = normalizeMessage(raw);
    const exactIndex = merged.findIndex(
      (candidate) =>
        candidate.id === message.id ||
        (typeof candidate.sequence === "number" &&
          typeof message.sequence === "number" &&
          candidate.sequence === message.sequence),
    );
    if (exactIndex >= 0) {
      merged[exactIndex] = mergeMessageFields(merged[exactIndex], message);
      continue;
    }

    let matchingIndex = -1;
    for (let index = merged.length - 1; index >= 0; index -= 1) {
      if (used.has(index)) continue;
      const candidate = merged[index];
      if (typeof candidate.sequence === "number" && typeof message.sequence === "number") continue;
      if (candidate.role !== message.role || candidate.content !== message.content) continue;
      const sameRun = Boolean(message.runId && candidate.runId === message.runId);
      const candidateTime = messageTime(candidate);
      const incomingTime = messageTime(message);
      const closeInTime =
        candidateTime > 0 && incomingTime > 0 && Math.abs(candidateTime - incomingTime) <= 10 * 60 * 1000;
      if (sameRun || closeInTime) {
        matchingIndex = index;
        break;
      }
    }

    if (matchingIndex >= 0) {
      used.add(matchingIndex);
      merged[matchingIndex] = mergeMessageFields(merged[matchingIndex], message);
    } else {
      merged.push(message);
    }
  }

  return merged
    .map((message, index) => ({ message, index }))
    .sort((left, right) => {
      const leftSequence = left.message.sequence;
      const rightSequence = right.message.sequence;
      if (typeof leftSequence === "number" && typeof rightSequence === "number" && leftSequence !== rightSequence) {
        return leftSequence - rightSequence;
      }
      const leftTime = messageTime(left.message);
      const rightTime = messageTime(right.message);
      if (leftTime > 0 && rightTime > 0 && leftTime !== rightTime) return leftTime - rightTime;
      return left.index - right.index;
    })
    .map(({ message }) => message);
}

export async function loadStore(): Promise<StoreShape> {
  const database = await openHistoryDatabase();
  const transaction = database.transaction([THREADS_STORE, HISTORIES_STORE, META_STORE, SETTINGS_STORE], "readonly");
  const done = transactionDone(transaction);
  const threadRequest = transaction.objectStore(THREADS_STORE).getAll() as IDBRequest<Thread[]>;
  const historyRequest = transaction.objectStore(HISTORIES_STORE).getAll() as IDBRequest<HistoryRow[]>;
  const metaRequest = transaction.objectStore(META_STORE).getAll() as IDBRequest<MetaRow[]>;
  const warningRequest = transaction.objectStore(SETTINGS_STORE).get("legacy_migration_error") as IDBRequest<SettingRow | undefined>;
  const [threads, histories, metas, warning] = await Promise.all([
    requestResult(threadRequest),
    requestResult(historyRequest),
    requestResult(metaRequest),
    requestResult(warningRequest),
  ]);
  await done;

  return {
    version: 1,
    threads: threads.filter((thread) => Boolean(thread && typeof thread.thread_id === "string")),
    messages: Object.fromEntries(
      histories
        .filter((row) => Boolean(row && typeof row.thread_id === "string" && Array.isArray(row.messages)))
        .map((row) => [row.thread_id, row.messages.map(normalizeMessage)]),
    ),
    meta: Object.fromEntries(
      metas
        .filter((row) => Boolean(row && typeof row.thread_id === "string" && row.value))
        .map((row) => [
          row.thread_id,
          {
            botName: typeof row.value.botName === "string" ? row.value.botName : null,
            goal: typeof row.value.goal === "string" ? row.value.goal : null,
          },
        ]),
    ),
    ...(warning?.value ? { warning: warning.value } : {}),
  };
}

export async function upsertLocalThread(thread: Thread): Promise<void> {
  const database = await openHistoryDatabase();
  // Field-wise merge inside the transaction: a server refresh must not drop a
  // locally known bot/project link just because the page omitted it.
  await updateRecord<Thread>(database, THREADS_STORE, thread.thread_id, (current) =>
    mergeThreadFields(current ?? ({} as Thread), thread),
  );
}

export async function remapThreadId(oldId: string, next: Thread): Promise<ThreadMeta | null> {
  const database = await openHistoryDatabase();
  const transaction = database.transaction([THREADS_STORE, HISTORIES_STORE, META_STORE], "readwrite");
  const done = transactionDone(transaction);
  const threads = transaction.objectStore(THREADS_STORE);
  const histories = transaction.objectStore(HISTORIES_STORE);
  const meta = transaction.objectStore(META_STORE);
  const currentThreadPromise = requestResult(threads.get(oldId) as IDBRequest<Thread | undefined>);
  const currentHistoryPromise = requestResult(histories.get(oldId) as IDBRequest<HistoryRow | undefined>);
  const targetHistoryPromise = requestResult(histories.get(next.thread_id) as IDBRequest<HistoryRow | undefined>);
  const currentMetaPromise = requestResult(meta.get(oldId) as IDBRequest<MetaRow | undefined>);
  const targetMetaPromise = requestResult(meta.get(next.thread_id) as IDBRequest<MetaRow | undefined>);

  // The reads were all queued before this function yields, so they keep the
  // write transaction alive. Promise continuations are browser microtasks and
  // issue their writes before IndexedDB can auto-commit the transaction.
  const [currentThread, currentHistory, targetHistory, currentMeta, targetMeta] = await Promise.all([
    currentThreadPromise,
    currentHistoryPromise,
    targetHistoryPromise,
    currentMetaPromise,
    targetMetaPromise,
  ]);
  threads.put({ ...(currentThread ?? {}), ...next });
  threads.delete(oldId);
  if (currentHistory || targetHistory) {
    histories.put({
      thread_id: next.thread_id,
      messages: mergeMessages(targetHistory?.messages ?? [], currentHistory?.messages ?? []),
    });
    histories.delete(oldId);
  }
  const nextMeta = currentMeta?.value ?? targetMeta?.value;
  if (nextMeta) {
    meta.put({ thread_id: next.thread_id, value: nextMeta });
    meta.delete(oldId);
  }
  await done;
  return currentMeta?.value ?? null;
}

export async function appendLocalMessages(threadId: string, messages: ChatMessage[]): Promise<void> {
  if (messages.length === 0) return;
  const database = await openHistoryDatabase();
  const now = new Date().toISOString();
  await updateRecords(
    database,
    [THREADS_STORE, HISTORIES_STORE],
    [
      {
        storeName: HISTORIES_STORE,
        key: threadId,
        update: (current: HistoryRow | undefined) => ({
          thread_id: threadId,
          messages: mergeMessages(current?.messages ?? [], messages),
        }),
      },
      {
        storeName: THREADS_STORE,
        key: threadId,
        // An unknown thread stays unknown: a message write must not invent a
        // thread row that the list views would then render as a conversation.
        update: (current: Thread | undefined) =>
          current ? { ...current, updated_at: now } : undefined,
      },
    ],
  );
}

/**
 * Cache the complete server page in the local archive. This is intentionally a
 * merge, not a replacement: compaction/regeneration may remove a message from
 * the active Gateway view, but the user's complete on-device archive keeps it.
 */
export async function setLocalMessages(threadId: string, messages: ChatMessage[]): Promise<void> {
  await appendLocalMessages(threadId, messages);
}

export async function updateLocalMessage(threadId: string, messageId: string, patch: Partial<ChatMessage>): Promise<void> {
  const database = await openHistoryDatabase();
  await updateRecord<HistoryRow>(database, HISTORIES_STORE, threadId, (current) => {
    if (!current || !Array.isArray(current.messages)) return undefined;
    let changed = false;
    const messages = current.messages.map((message) => {
      if (message.id !== messageId) return message;
      changed = true;
      return normalizeMessage({ ...message, ...patch });
    });
    return changed ? { ...current, messages } : undefined;
  });
}

export async function removeLocalThread(threadId: string): Promise<void> {
  const database = await openHistoryDatabase();
  const transaction = database.transaction([THREADS_STORE, HISTORIES_STORE, META_STORE], "readwrite");
  const done = transactionDone(transaction);
  transaction.objectStore(THREADS_STORE).delete(threadId);
  transaction.objectStore(HISTORIES_STORE).delete(threadId);
  transaction.objectStore(META_STORE).delete(threadId);
  await done;
}

export async function setThreadMeta(threadId: string, patch: Partial<ThreadMeta>): Promise<void> {
  const database = await openHistoryDatabase();
  await updateRecord<MetaRow>(database, META_STORE, threadId, (current) => ({
    thread_id: threadId,
    value: {
      botName: patch.botName !== undefined ? patch.botName : current?.value.botName ?? null,
      goal: patch.goal !== undefined ? patch.goal : current?.value.goal ?? null,
    },
  }));
}

export async function clearLocalStore(): Promise<void> {
  const database = await openHistoryDatabase();
  const transaction = database.transaction([THREADS_STORE, HISTORIES_STORE, META_STORE], "readwrite");
  const done = transactionDone(transaction);
  transaction.objectStore(THREADS_STORE).clear();
  transaction.objectStore(HISTORIES_STORE).clear();
  transaction.objectStore(META_STORE).clear();
  await done;
}

export interface SearchHit {
  thread_id: string;
  title: string;
  snippet: string;
  messageId: string;
}

/** Full-text search across every locally archived message. */
export async function searchLocalMessages(query: string, limit = 15): Promise<SearchHit[]> {
  const q = query.trim().toLowerCase();
  if (q.length < 2) return [];
  const store = await loadStore();
  const hits: SearchHit[] = [];
  for (const thread of store.threads) {
    const messages = store.messages[thread.thread_id] ?? [];
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      const content = typeof message.content === "string" ? message.content : "";
      const matchIndex = content.toLowerCase().indexOf(q);
      if (matchIndex < 0) continue;
      const start = Math.max(0, matchIndex - 40);
      hits.push({
        thread_id: thread.thread_id,
        title: thread.title,
        snippet: (start > 0 ? "…" : "") + content.slice(start, matchIndex + 80).replace(/\n/g, " "),
        messageId: message.id,
      });
      if (hits.length >= limit) return hits;
      break;
    }
  }
  return hits;
}

export async function storageInfo(): Promise<{ threads: number; messages: number; kb: number }> {
  const store = await loadStore();
  const messages = Object.values(store.messages).reduce((count, list) => count + list.length, 0);
  let bytes = 0;
  try {
    bytes = new TextEncoder().encode(JSON.stringify(store)).byteLength;
  } catch {
    bytes = 0;
  }
  return { threads: store.threads.length, messages, kb: Math.ceil(bytes / 1024) };
}

export async function exportStoreJson(): Promise<string> {
  const store = await loadStore();
  return JSON.stringify(
    { app: "agent-workspace-chat-history", ...store, exportedAt: new Date().toISOString() },
    null,
    2,
  );
}

/** Merge an exported file into the on-device archive. Returns real import counts. */
export async function importStoreJson(text: string): Promise<{ threads: number; messages: number }> {
  const parsed = JSON.parse(text) as Partial<StoreShape>;
  if (!parsed || !Array.isArray(parsed.threads)) {
    throw new Error("That file is not a valid chat history export.");
  }

  let threadCount = 0;
  let messageCount = 0;
  const current = await loadStore();
  const incomingThreads = parsed.threads.filter(
    (thread): thread is Thread => Boolean(thread && typeof thread.thread_id === "string"),
  );
  for (const thread of incomingThreads) {
    if (current.threads.some((known) => known.thread_id === thread.thread_id)) continue;
    await upsertLocalThread(thread);
    threadCount += 1;
  }

  for (const [threadId, messages] of Object.entries((parsed.messages ?? {}) as Record<string, ChatMessage[]>)) {
    if (!Array.isArray(messages)) continue;
    const knownIds = new Set((current.messages[threadId] ?? []).map((message) => message.id));
    const fresh = messages.filter(
      (message): message is ChatMessage => Boolean(message && typeof message.id === "string" && !knownIds.has(message.id)),
    );
    if (fresh.length === 0) continue;
    await setLocalMessages(threadId, fresh);
    messageCount += fresh.length;
  }

  for (const [threadId, value] of Object.entries((parsed.meta ?? {}) as Record<string, ThreadMeta>)) {
    if (current.meta[threadId] || !value || typeof value !== "object") continue;
    await setThreadMeta(threadId, {
      botName: typeof value.botName === "string" ? value.botName : null,
      goal: typeof value.goal === "string" ? value.goal : null,
    });
  }
  return { threads: threadCount, messages: messageCount };
}
