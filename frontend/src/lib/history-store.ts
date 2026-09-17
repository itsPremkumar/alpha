import { ChatMessage, Thread } from "@/types/chat";

/**
 * Offline-first chat history storage (localStorage).
 *
 * The server remains the source of truth when reachable: on startup local
 * threads load instantly, then server threads merge in (server wins metadata
 * conflicts). Every message is also cached locally, so history survives
 * reloads and offline gaps. Export/import moves history between browsers.
 */

const KEY = "deerflow.chatstore.v1";
const MAX_THREADS = 100;
const MAX_MSGS_PER_THREAD = 300;
const MAX_CONTENT_CHARS = 20000;

export interface ThreadMeta {
  botName: string | null;
  goal: string | null;
}

interface StoreShape {
  version: 1;
  threads: Thread[];
  messages: Record<string, ChatMessage[]>;
  meta: Record<string, ThreadMeta>;
}

function emptyStore(): StoreShape {
  return { version: 1, threads: [], messages: {}, meta: {} };
}

function trimMessage(m: ChatMessage): ChatMessage {
  return {
    ...m,
    content: typeof m.content === "string" ? m.content.slice(0, MAX_CONTENT_CHARS) : "",
    thinking: typeof m.thinking === "string" ? m.thinking.slice(0, 5000) : undefined,
  };
}

export function loadStore(): StoreShape {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return emptyStore();
    const parsed = JSON.parse(raw) as Partial<StoreShape>;
    if (!Array.isArray(parsed.threads)) return emptyStore();
    return {
      version: 1,
      threads: parsed.threads.filter((t) => t && typeof t.thread_id === "string").slice(0, MAX_THREADS),
      messages:
        parsed.messages && typeof parsed.messages === "object"
          ? Object.fromEntries(
              Object.entries(parsed.messages)
                .filter(([, v]) => Array.isArray(v))
                .map(([k, v]) => [k, (v as ChatMessage[]).slice(-MAX_MSGS_PER_THREAD).map(trimMessage)])
            )
          : {},
      meta: parsed.meta && typeof parsed.meta === "object" ? (parsed.meta as Record<string, ThreadMeta>) : {},
    };
  } catch {
    return emptyStore();
  }
}

function writeStore(s: StoreShape): boolean {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
    return true;
  } catch {
    // Quota exceeded: drop oldest threads and retry once.
    try {
      const trimmed: StoreShape = {
        ...s,
        threads: s.threads.slice(0, 30),
        messages: Object.fromEntries(Object.entries(s.messages).slice(-30)),
      };
      localStorage.setItem(KEY, JSON.stringify(trimmed));
      return true;
    } catch {
      return false;
    }
  }
}

function mutate(fn: (s: StoreShape) => void): void {
  const s = loadStore();
  fn(s);
  s.threads = s.threads.slice(0, MAX_THREADS);
  writeStore(s);
}

export function upsertLocalThread(t: Thread): void {
  mutate((s) => {
    const i = s.threads.findIndex((x) => x.thread_id === t.thread_id);
    const row = { ...t };
    if (i >= 0) s.threads[i] = { ...s.threads[i], ...row };
    else s.threads.unshift(row);
  });
}

/** Replace a temporary local id with the real server id everywhere. */
export function remapThreadId(oldId: string, next: Thread): void {
  mutate((s) => {
    s.threads = s.threads.map((t) => (t.thread_id === oldId ? { ...t, ...next } : t));
    if (s.messages[oldId]) {
      s.messages[next.thread_id] = [...(s.messages[next.thread_id] || []), ...s.messages[oldId]].slice(-MAX_MSGS_PER_THREAD);
      delete s.messages[oldId];
    }
    if (s.meta[oldId]) {
      s.meta[next.thread_id] = { ...(s.meta[next.thread_id] || { botName: null, goal: null }), ...s.meta[oldId] };
      delete s.meta[oldId];
    }
  });
}

export function appendLocalMessages(threadId: string, msgs: ChatMessage[]): void {
  if (msgs.length === 0) return;
  mutate((s) => {
    const list = [...(s.messages[threadId] || []), ...msgs.map(trimMessage)].slice(-MAX_MSGS_PER_THREAD);
    s.messages[threadId] = list;
    const t = s.threads.find((x) => x.thread_id === threadId);
    if (t) t.updated_at = new Date().toISOString();
  });
}

export function setLocalMessages(threadId: string, msgs: ChatMessage[]): void {
  mutate((s) => {
    s.messages[threadId] = msgs.map(trimMessage).slice(-MAX_MSGS_PER_THREAD);
  });
}

export function updateLocalMessage(threadId: string, messageId: string, patch: Partial<ChatMessage>): void {
  mutate((s) => {
    const list = s.messages[threadId];
    if (!list) return;
    s.messages[threadId] = list.map((m) => (m.id === messageId ? trimMessage({ ...m, ...patch }) : m));
  });
}

export function removeLocalThread(threadId: string): void {
  mutate((s) => {
    s.threads = s.threads.filter((t) => t.thread_id !== threadId);
    delete s.messages[threadId];
    delete s.meta[threadId];
  });
}

export function setThreadMeta(threadId: string, patch: Partial<ThreadMeta>): void {
  mutate((s) => {
    const cur = s.meta[threadId] || {};
    s.meta[threadId] = {
      botName: patch.botName !== undefined ? patch.botName : (cur.botName ?? null),
      goal: patch.goal !== undefined ? patch.goal : (cur.goal ?? null),
    };
  });
}

export function clearLocalStore(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

export interface SearchHit {
  thread_id: string;
  title: string;
  snippet: string;
  messageId: string;
}

/** Full-text search across all locally stored messages. */
export function searchLocalMessages(query: string, limit = 15): SearchHit[] {
  const q = query.trim().toLowerCase();
  if (q.length < 2) return [];
  const s = loadStore();
  const hits: SearchHit[] = [];
  for (const t of s.threads) {
    const list = s.messages[t.thread_id] || [];
    for (let i = list.length - 1; i >= 0; i--) {
      const m = list[i];
      const idx = m.content.toLowerCase().indexOf(q);
      if (idx >= 0) {
        const start = Math.max(0, idx - 40);
        hits.push({
          thread_id: t.thread_id,
          title: t.title,
          snippet: (start > 0 ? "…" : "") + m.content.slice(start, idx + 80).replace(/\n/g, " "),
          messageId: m.id,
        });
        if (hits.length >= limit) return hits;
        break;
      }
    }
  }
  return hits;
}

export function storageInfo(): { threads: number; messages: number; kb: number } {
  const s = loadStore();
  let messages = 0;
  for (const list of Object.values(s.messages)) messages += list.length;
  let kb = 0;
  try {
    kb = Math.round(((localStorage.getItem(KEY) || "").length * 2) / 1024);
  } catch {
    kb = 0;
  }
  return { threads: s.threads.length, messages, kb };
}

export function exportStoreJson(): string {
  return JSON.stringify({ app: "deerflow-chat-history", ...loadStore(), exportedAt: new Date().toISOString() }, null, 2);
}

/** Merge an exported file into the store. Returns counts for user feedback. */
export function importStoreJson(text: string): { threads: number; messages: number } {
  const parsed = JSON.parse(text) as Partial<StoreShape>;
  if (!parsed || !Array.isArray(parsed.threads)) throw new Error("That file is not a valid chat history export.");
  let threads = 0;
  let messages = 0;
  mutate((s) => {
    const knownThreads = new Set(s.threads.map((t) => t.thread_id));
    for (const t of parsed.threads || []) {
      if (!t || typeof t.thread_id !== "string" || knownThreads.has(t.thread_id)) continue;
      s.threads.push({ ...t });
      knownThreads.add(t.thread_id);
      threads++;
    }
    const incoming = (parsed.messages || {}) as Record<string, ChatMessage[]>;
    for (const [tid, list] of Object.entries(incoming)) {
      if (!Array.isArray(list)) continue;
      const known = new Set((s.messages[tid] || []).map((m) => m.id));
      const fresh = list.filter((m) => m && typeof m.id === "string" && !known.has(m.id)).map(trimMessage);
      if (fresh.length > 0) {
        s.messages[tid] = [...(s.messages[tid] || []), ...fresh].slice(-MAX_MSGS_PER_THREAD);
        messages += fresh.length;
      }
    }
    if (parsed.meta && typeof parsed.meta === "object") {
      for (const [tid, m] of Object.entries(parsed.meta)) {
        if (!s.meta[tid] && m && typeof m === "object") {
          const rec = m as Partial<ThreadMeta>;
          s.meta[tid] = { botName: rec.botName ?? null, goal: rec.goal ?? null };
        }
      }
    }
  });
  return { threads, messages };
}
