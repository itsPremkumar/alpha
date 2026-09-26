import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("local history uses durable IndexedDB without application-level truncation caps", () => {
  const source = read("./history-store.ts");
  assert.match(source, /indexedDB\.open/);
  assert.match(source, /alpha-chat-history/);
  assert.match(source, /LEGACY_KEY = "alpha\.chatstore\.v1"/);
  assert.match(source, /migrateLegacyStore/);
  assert.match(source, /legacy_migration/);
  assert.match(source, /prevent.*overwrite|prevents a repeated overwrite/i);
  assert.doesNotMatch(source, /MAX_THREADS|MAX_MSGS_PER_THREAD|MAX_CONTENT_CHARS/);
  assert.doesNotMatch(source, /\.slice\(-MAX_/);
  assert.match(source, /if \(typeof message\.content === "string"\) content = message\.content/);
});

test("opening New Chat is ephemeral and only the first real action creates a thread", () => {
  const source = read("../components/ChatView.tsx");
  const newChat = source.slice(
    source.indexOf("const handleNewChat"),
    source.indexOf("/** Owner of a thread"),
  );
  assert.match(newChat, /setActiveThreadId\(null\)/);
  assert.doesNotMatch(newChat, /createThread\(/);
  assert.doesNotMatch(newChat, /upsertLocalThread\(/);

  const send = source.slice(source.indexOf("const sendMessage"), source.indexOf("const handleVoiceTranscript"));
  assert.match(send, /if \(!threadId\)/);
  assert.match(send, /createThread\(content\.slice/);

  const attach = source.slice(source.indexOf("const handleAttach"), source.indexOf("const handleSaveGoal"));
  assert.match(attach, /deleteThread\(draftServerId\)/);
  assert.match(attach, /empty draft was removed/);
});

test("thread and message APIs walk every server page", () => {
  const source = read("./api.ts");
  const threadList = source.slice(
    source.indexOf("export async function fetchThreadsResult"),
    source.indexOf("/**\n * LEGACY-COMPAT thread list"),
  );
  assert.match(threadList, /while \(true\)/);
  assert.match(threadList, /offset \+= list\.length/);
  assert.doesNotMatch(threadList, /limit = 100/);

  const history = source.slice(
    source.indexOf("export async function fetchThreadHistoryResult"),
    source.indexOf("/**\n * LEGACY-COMPAT thread history"),
  );
  assert.match(history, /messages\/page/);
  assert.match(history, /next_before_seq/);
  assert.match(history, /while \(true\)/);

  const titleSearch = read("./threads-ext.ts");
  assert.match(titleSearch, /pageSize = 500/);
  assert.match(titleSearch, /offset \+= page\.length/);
  // Every page walk needs a non-advancing guard, or a server that ignores
  // `offset` loops forever.
  assert.match(titleSearch, /did not advance/);

  // …and the same rule for the project conversation list.
  const projects = read("./projects.ts");
  assert.match(projects, /did not advance/);
});

test("bot profiles expose project creation and Projects manages multi-bot membership", () => {
  const detail = read("../components/bots/BotDetailPanel.tsx");
  assert.match(detail, /createProject\(/);
  assert.match(detail, /\[\{ name: bot\.name, role: "lead" \}\]/);

  const card = read("../components/bots/BotProfileCard.tsx");
  assert.match(card, /Building2/);
  assert.match(card, /onSelect\(bot\)/);

  const projects = read("../components/sections/ProjectsSection.tsx");
  assert.match(projects, /attachProjectAgents/);
  assert.match(projects, /detachProjectAgent/);
  assert.match(projects, /type="checkbox"/);
  assert.match(projects, /Add .*selected bot/);
});

test("the local archive is never destructively swept for 'empty' threads on startup", () => {
  const source = read("./history-store.ts");
  // Inferring emptiness from an absent local message list destroys real
  // server-side history, so the sweep must not exist at all.
  assert.doesNotMatch(source, /removeEmptyLocalThreads/);
  const chatView = read("../components/ChatView.tsx");
  assert.doesNotMatch(chatView, /removeEmptyLocalThreads/);

  // A thread is hidden only behind a complete empty server page AND a
  // confirmed empty upload list; a partial page or failed read never hides it.
  const hide = chatView.slice(
    chatView.indexOf("async function hideConfirmedEmptyDrafts"),
    chatView.indexOf("async function archiveServerHistory"),
  );
  assert.match(hide, /if \(history\.incomplete\) \{/);
  assert.match(hide, /A partial page cannot prove the thread is empty/);
  assert.match(hide, /uploads unavailable/);
});

test("local history read-modify-write runs inside the transaction, not after an await", () => {
  const source = read("./history-store.ts");
  // Awaiting a get() and then putting lets IndexedDB auto-commit the
  // readwrite transaction first, losing the write. Every merge must therefore
  // issue its put() from inside the request callback.
  assert.match(source, /request\.onsuccess = \(\) => \{/);
  assert.match(source, /const next = update\(request\.result\);/);
  assert.doesNotMatch(
    source,
    /const current = await requestResult\([^)]*\)[\s\S]{0,200}?\.put\(/,
  );
  // The mutations actually route through the callback-safe helpers.
  for (const fn of [
    "export async function upsertLocalThread",
    "export async function appendLocalMessages",
    "export async function updateLocalMessage",
    "export async function setThreadMeta",
  ]) {
    const start = source.indexOf(fn);
    assert.ok(start > 0, `missing ${fn}`);
    const body = source.slice(start, source.indexOf("\nexport ", start + 10));
    assert.match(body, /updateRecord|updateRecords/, `${fn} must use a callback-safe transaction`);
    assert.doesNotMatch(body, /await requestResult\(/, `${fn} must not await before writing`);
  }
});

test("a schema upgrade in another tab drops the cached connection instead of reusing it", () => {
  const source = read("./history-store.ts");
  assert.match(source, /database\.onversionchange = \(\) => \{/);
  assert.match(source, /databasePromise === cached/);
  assert.match(source, /databasePromise = null/);
});

test("thread metadata merges field-wise, distinguishing an omitted field from an explicit null", () => {
  const source = read("./history-store.ts");
  const merge = source.slice(
    source.indexOf("function mergeThreadFields"),
    source.indexOf("function mergeMessageFields"),
  );
  // An older Gateway that omits bot_name / project_id must not drop the badge…
  assert.match(merge, /if \(value === undefined \|\| value === ""\) continue;/);
  assert.match(merge, /merged\.thread_id = incoming\.thread_id/);
  // …but an explicit null is the server saying "unassigned" and must clear it,
  // otherwise a chat moved out of a project keeps that scope forever.
  assert.doesNotMatch(merge, /value === null/);

  // The chat view applies the identical rule when merging the server list.
  const chatView = read("../components/ChatView.tsx");
  const view = chatView.slice(
    chatView.indexOf("const mergeThreads"),
    chatView.indexOf("const reloadThreads"),
  );
  assert.match(view, /if \(value === undefined \|\| value === ""\) continue/);
  assert.doesNotMatch(view, /value === null/);
  assert.doesNotMatch(view, /\{ \.\.\.local, \.\.\.serverThread \}/);

  // api.ts is the producer of that contract: the key is only written when the
  // server actually sent the field, and "" is the documented omission sentinel.
  const api = read("./api.ts");
  assert.match(api, /Empty optional strings let mergeThreads preserve a known local/);
  assert.match(api, /Object\.hasOwn\(metadata, "agent_workspace_project_id"\) \|\| Object\.hasOwn\(thread, "project_id"\)/);
});

test("the whole server history is archived on this computer in bounded background batches", () => {
  const chatView = read("../components/ChatView.tsx");
  const archive = chatView.slice(
    chatView.indexOf("async function archiveServerHistory"),
    chatView.indexOf("export default function ChatView"),
  );
  assert.match(archive, /for \(const thread of threads\)|for \(let offset = 0; offset < threads\.length/);
  assert.match(archive, /ARCHIVE_BATCH_SIZE/);
  // Partial pages are real history and are archived, with the truncation stated.
  assert.match(archive, /archived partially/);
  assert.match(archive, /await setLocalMessages\(thread\.thread_id, history\.value\)/);
  // The run is fire-and-forget so first paint is not blocked.
  assert.match(chatView, /void archiveServerHistory\(/);
  assert.match(chatView, /archiveInFlightRef\.current/);
  // A partial page is surfaced in the opened conversation, not hidden.
  const load = chatView.slice(
    chatView.indexOf("async function loadMessages"),
    chatView.indexOf("Restore this conversation's specialist bot"),
  );
  assert.match(load, /history\.incomplete/);
  assert.match(load, /only partially loaded/);
});

test("an unavailable server conversation list is stated, not rendered as empty history", () => {
  const chatView = read("../components/ChatView.tsx");
  assert.match(chatView, /serverHistoryError/);
  assert.match(chatView, /Server conversation list unavailable/);
  assert.match(chatView, /not a confirmed empty history/);
  // A failed refresh keeps the already-listed conversations on screen.
  const reload = chatView.slice(
    chatView.indexOf("const reloadThreads"),
    chatView.indexOf("// Fetch the complete message feed"),
  );
  assert.match(reload, /setServerHistoryError\(result\.error\)/);
  assert.match(reload, /setServerHistoryError\(null\)/);
});

test("a stream that outlives its conversation and rapid duplicate uploads are both fenced", () => {
  const chatView = read("../components/ChatView.tsx");
  // Run generation: navigation invalidates the run's React state writes.
  assert.match(chatView, /const runIsCurrent = \(\) => runGenerationRef\.current === runGeneration;/);
  assert.match(chatView, /const stopVoiceForNavigation = \(\) => \{\s*\n\s*\/\/[^]*?runGenerationRef\.current \+= 1;/);
  // The workspace-wide in-flight flag always clears, or the composer wedges.
  assert.match(chatView, /finally \{\s*\n[^}]*setIsLoading\(false\);/);
  // Attachment lock: two rapid drops must not each create a draft thread.
  assert.match(chatView, /if \(attachmentLockRef\.current\) \{/);
  assert.match(chatView, /attachmentLockRef\.current = true;/);
  assert.match(chatView, /attachmentLockRef\.current = false;/);
});

test("sidebar search drops stale responses so a slow query cannot overwrite a newer one", () => {
  const sidebar = read("../components/ThreadSidebar.tsx");
  assert.match(sidebar, /searchGenerationRef/);
  assert.match(sidebar, /if \(searchGenerationRef\.current !== generation\) return;/);
});
