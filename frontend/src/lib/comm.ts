import { get, send, asList, pick } from "./http";
import { fetchRoster, fetchInbox, registerRosterAgent } from "./inbox";

export const OPERATOR = "operator";

/** Sender colors, WhatsApp-style (stable per name). */
const SENDER_COLORS = [
  "#35cd71", "#00a0f4", "#e542a3", "#ff8c00", "#7c5cff",
  "#00b8a9", "#e5c100", "#ef4b4b", "#4b9bef", "#9b59b6",
];

export function senderColor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return SENDER_COLORS[h % SENDER_COLORS.length];
}

export interface ChatMsg {
  id: string;
  sender: string;
  content: string;
  at: string;
  kind: string;
  read?: boolean;
}

function toMsg(m: Record<string, unknown>, i: number): ChatMsg {
  return {
    id: String(pick(m, ["id", "message_id"], `m-${i}-${Date.now()}`)),
    sender: String(pick(m, ["sender", "sender_name", "author", "bot", "role"], "?")),
    content: String(pick(m, ["content", "text", "message"], "")),
    at: String(pick(m, ["created_at", "timestamp", "at", "time"], "")),
    kind: String(pick(m, ["intent", "kind", "message_type", "type"], "discussion")),
    read: pick<boolean | undefined>(m, ["read"], undefined),
  };
}

/* ---------------- Group rooms (groups.py) ---------------- */

export interface Room {
  name: string;
  members: string[];
  status: string;
  messages: ChatMsg[];
}

export async function listRooms(): Promise<Array<{ name: string; members: string[]; status: string }>> {
  try {
    const d = await get<unknown>("/groups");
    return asList(d, ["rooms", "groups", "data"]).map((g) => ({
      name: String(pick(g, ["name"], "")),
      members: Array.isArray(g.members) ? (g.members as string[]) : [],
      status: String(pick(g, ["status", "state"], "")),
    }));
  } catch {
    return [];
  }
}

export async function getRoom(name: string): Promise<Room> {
  const d = await get<Record<string, unknown>>(`/groups/${encodeURIComponent(name)}`);
  const rec = (d.room ?? d) as Record<string, unknown>;
  return {
    name: String(pick(rec, ["name"], name)),
    members: Array.isArray(rec.members) ? (rec.members as string[]) : [],
    status: String(pick(rec, ["status", "state"], "")),
    messages: asList(rec.messages ?? rec.recent_messages ?? d.messages ?? [], ["messages"]).map((m, i) => toMsg(m, i)),
  };
}

export async function createRoom(name: string, members: string[]): Promise<void> {
  await send("/groups", "POST", { name, members });
}

export async function postToRoom(name: string, sender: string, content: string, intent = "discussion"): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/messages`, "POST", { sender, content, intent });
}

export async function deleteRoom(name: string): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}`, "DELETE");
}

export async function startRoomRun(name: string, objective: string): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/runs`, "POST", { objective });
}

export async function listRoomRuns(name: string): Promise<Array<Record<string, unknown>>> {
  try {
    const d = await get<unknown>(`/groups/${encodeURIComponent(name)}/runs`);
    return asList(d, ["runs", "data"]);
  } catch {
    return [];
  }
}

export async function cancelRoomRun(name: string, runId: string): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/runs/${encodeURIComponent(runId)}/cancel`, "POST", {});
}

/* ---------------- Direct agent↔agent threads (agent_messages, thread-scoped) ---------------- */

export interface DmThread {
  id: string;
  peer: string;
  messages: ChatMsg[];
}

export async function listDmThreads(threadId: string, me = OPERATOR): Promise<DmThread[]> {
  const roster = await fetchRoster(threadId).catch(() => []);
  const names = [...new Set([me, ...roster.map((r) => r.name)])].filter(Boolean).slice(0, 11);
  const all: Array<ChatMsg & { to: string }> = [];
  await Promise.all(
    names.map(async (n) => {
      try {
        const inbox = await fetchInbox(threadId, n);
        for (const m of inbox) {
          all.push({ id: m.id, sender: m.from, content: m.content, at: "", kind: "message", read: m.read, to: m.to });
        }
      } catch {
        /* agent without inbox access */
      }
    })
  );
  const seen = new Set<string>();
  const byPeer = new Map<string, ChatMsg[]>();
  for (const m of all) {
    const key = `${m.id}::${m.sender}::${m.to}`;
    if (seen.has(key)) continue;
    seen.add(key);
    // Pair by the other participant; skip broadcasts with no clear peer.
    const peer = m.sender === me ? m.to : m.sender;
    if (!peer || peer === "?" || peer === "all") continue;
    if (!byPeer.has(peer)) byPeer.set(peer, []);
    byPeer.get(peer)!.push({ id: m.id, sender: m.sender, content: m.content, at: m.at, kind: m.kind, read: m.read });
  }
  return Array.from(byPeer.entries()).map(([peer, messages]) => ({
    id: `dm:${peer}`,
    peer,
    messages: messages.slice(-100),
  }));
}

export async function ensureRosterAgent(threadId: string, name: string): Promise<void> {
  try {
    await registerRosterAgent(threadId, name, "worker");
  } catch {
    /* already registered */
  }
}

/* ---------------- Presence (roster + company roll-call) ---------------- */

export interface PresenceEntry {
  name: string;
  status: string;
  detail: string;
}

export async function rollCall(): Promise<PresenceEntry[]> {
  try {
    const d = await get<unknown>("/company/attendance/roll-call");
    return asList(d, ["agents", "attendance", "data"]).map((a) => ({
      name: String(pick(a, ["name", "bot_name", "agent"], "")),
      status: String(pick(a, ["status", "state", "presence"], "unknown")),
      detail: String(pick(a, ["current_task", "detail", "last_activity"], "")),
    }));
  } catch {
    return [];
  }
}

/* ---------------- Read tracking (local; backend marks read on fetch) ---------------- */

const SEEN_KEY = "alpha.msgseen.v1";

function loadSeen(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(SEEN_KEY) || "{}") as Record<string, string>;
  } catch {
    return {};
  }
}

export function unreadCount(convId: string, messages: ChatMsg[]): number {
  if (messages.length === 0) return 0;
  const seen = loadSeen()[convId];
  if (!seen) return Math.min(messages.length, 99);
  const idx = messages.findIndex((m) => m.id === seen);
  return idx < 0 ? 0 : messages.length - idx - 1;
}

export function markSeen(convId: string, messages: ChatMsg[]): void {
  if (messages.length === 0) return;
  try {
    const seen = loadSeen();
    seen[convId] = messages[messages.length - 1].id;
    localStorage.setItem(SEEN_KEY, JSON.stringify(seen));
  } catch {
    /* ignore */
  }
}

/* ---------------- Message kinds (structured A2A layer) ---------------- */

export const MESSAGE_KINDS = [
  "discussion",
  "question",
  "answer",
  "request",
  "status",
  "handoff",
  "decision",
  "blocker",
  "warning",
  "approval_request",
  "escalation",
  "task_assignment",
  "task_completion",
] as const;

export function kindTone(kind: string): "blue" | "amber" | "green" | "gray" {
  if (["blocker", "warning", "escalation"].includes(kind)) return "amber";
  if (["decision", "task_completion", "answer"].includes(kind)) return "green";
  if (kind !== "discussion") return "blue";
  return "gray";
}
