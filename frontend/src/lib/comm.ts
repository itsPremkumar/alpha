import { get, send, asList, pick } from "./http";
import { fetchRoster, fetchInbox, registerRosterAgent } from "./inbox";
import type { Breadcrumb, MembershipRule, RoomRoster, RoomScope, RoomState, RulePreview, TreeNode } from "./groups-tree";

export type { Breadcrumb, MembershipRule, RoomRoster, RoomScope, RoomState, RulePreview, TreeNode };

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
  /** Server stamp, or `null` when the row carried none — never `""` as a time. */
  at: string | null;
  kind: string;
  read?: boolean;
  /** Id of the message this replies to, or `null` when it is not a reply. */
  replyTo?: string | null;
  /** `{room, sender}` when this message was forwarded here, else `null`. */
  forwardedFrom?: { room: string; sender: string } | null;
  /** Set once the body was edited in place. */
  editedAt?: string | null;
  /** A deleted row keeps its id so replies still resolve. */
  deleted?: boolean;
  /** emoji -> the actors who reacted. */
  reactions?: Record<string, string[]>;
}

function asTime(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const raw = typeof value === "string" ? value.trim() : String(value);
  return raw ? raw : null;
}

/** Absent stays `null` — an unknown reaction set is not an empty one. */
function asReactions(value: unknown): Record<string, string[]> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const out: Record<string, string[]> = {};
  for (const [emoji, actors] of Object.entries(value as Record<string, unknown>)) {
    if (Array.isArray(actors)) out[emoji] = actors.map((a) => String(a));
  }
  return out;
}

function asForwardedFrom(value: unknown): { room: string; sender: string } | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const rec = value as Record<string, unknown>;
  const room = rec.room == null ? "" : String(rec.room);
  const sender = rec.sender == null ? "" : String(rec.sender);
  if (!room && !sender) return null;
  return { room, sender };
}

function toMsg(m: Record<string, unknown>, i: number): ChatMsg {
  const edited = asTime(pick<unknown>(m, ["edited_at", "edited"], null));
  return {
    id: String(pick(m, ["id", "message_id"], `m-${i}-${Date.now()}`)),
    sender: String(pick(m, ["sender", "sender_name", "author", "bot", "role"], "?")),
    content: String(pick(m, ["content", "text", "message"], "")),
    at: asTime(pick<unknown>(m, ["created_at", "timestamp", "at", "time"], null)),
    kind: String(pick(m, ["intent", "kind", "message_type", "type"], "discussion")),
    read: pick<boolean | undefined>(m, ["read"], undefined),
    replyTo: pick<string | null>(m, ["reply_to"], null) ?? null,
    forwardedFrom: asForwardedFrom(pick<unknown>(m, ["forwarded_from"], null)),
    editedAt: edited,
    deleted: pick<boolean | undefined>(m, ["deleted"], false) === true,
    reactions: asReactions(pick<unknown>(m, ["reactions"], null)),
  };
}

/* ---------------- Group rooms (groups.py) ---------------- */

export interface Room {
  name: string;
  members: string[];
  status: string;
  messages: ChatMsg[];
}

// List helpers below propagate fetch failures (no catch → no fake `[]`):
// callers must render an "unavailable" state, never an empty success.

export async function listRooms(): Promise<Array<{ name: string; members: string[]; status: string }>> {
  const d = await get<unknown>("/groups");
  return asList(d, ["rooms", "groups", "data"]).map((g) => ({
    name: String(pick(g, ["name"], "")),
    members: Array.isArray(g.members) ? (g.members as string[]) : [],
    status: String(pick(g, ["status", "state"], "")),
  }));
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

export async function postToRoom(
  name: string,
  sender: string,
  content: string,
  intent = "discussion",
  replyTo?: string | null,
): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/messages`, "POST", {
    sender,
    content,
    intent,
    ...(replyTo ? { reply_to: replyTo } : {}),
  });
}

/* ---------------- Message features (edit / delete / react / forward) -------- */

export async function editRoomMessage(name: string, messageId: string, content: string): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/messages/${encodeURIComponent(messageId)}`, "PATCH", { content });
}

export async function deleteRoomMessage(name: string, messageId: string): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/messages/${encodeURIComponent(messageId)}`, "DELETE");
}

/** Toggle one actor's reaction. Resolves only once the server confirmed the map. */
export async function reactToRoomMessage(
  name: string,
  messageId: string,
  actor: string,
  emoji: string,
): Promise<Record<string, string[]>> {
  const d = await send<Record<string, unknown>>(
    `/groups/${encodeURIComponent(name)}/messages/${encodeURIComponent(messageId)}/reactions`,
    "POST",
    { actor, emoji },
  );
  return asReactions(d.reactions) ?? {};
}

export async function forwardRoomMessage(
  name: string,
  messageId: string,
  targetRoom: string,
  sender: string,
  content?: string | null,
): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/messages/${encodeURIComponent(messageId)}/forward`, "POST", {
    sender,
    target_room: targetRoom,
    ...(content != null ? { content } : {}),
  });
}

export async function deleteRoom(name: string): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}`, "DELETE");
}

export async function startRoomRun(name: string, objective: string): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/runs`, "POST", { objective });
}

export async function listRoomRuns(name: string): Promise<Array<Record<string, unknown>>> {
  const d = await get<unknown>(`/groups/${encodeURIComponent(name)}/runs`);
  return asList(d, ["runs", "data"]);
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
  // Roster failure propagates: without it we cannot tell "no direct threads"
  // apart from "the gateway never answered".
  const roster = await fetchRoster(threadId);
  const names = [...new Set([me, ...roster.map((r) => r.name)])].filter(Boolean).slice(0, 11);
  const all: Array<ChatMsg & { to: string }> = [];
  await Promise.all(
    names.map(async (n) => {
      try {
        const inbox = await fetchInbox(threadId, n);
        for (const m of inbox) {
          // Carry the row's own stamp through — this was hardcoded to "",
          // which is why DM bubbles never showed a time.
          all.push({ id: m.id, sender: m.from, content: m.content, at: m.createdAt, kind: "message", read: m.read, to: m.to });
        }
      } catch {
        /* per-agent tolerance: a single inbox may be inaccessible by design */
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
  // Failure propagates: an empty attendance would read as "nobody is here".
  const d = await get<unknown>("/company/attendance/status");
  return asList(d, ["heartbeats", "attendance", "agents", "data"]).map((a) => ({
    name: String(pick(a, ["name", "bot_name", "agent"], "")),
    status: String(pick(a, ["status", "state", "presence"], "unknown")),
    detail: String(pick(a, ["current_task", "active_task_id", "detail", "last_activity"], "")),
  }));
}

/* ---------------- Room roster (measured presence, per member) ------------- */

/**
 * The resolved presence states a room member can report.
 *
 * `unknown` is a real state and is not a failure: it means no owning system has
 * a record of that member. It is deliberately distinct from `offline`, which is
 * an observation.
 */
export type PresenceState = "online" | "busy" | "idle" | "offline" | "unknown";

export interface MemberPresence {
  name: string;
  /** `null` when the server reported no state word, never a defaulted "online". */
  state: PresenceState | null;
  /** Which system answered — lets the UI say why, instead of guessing. */
  source: string;
  activityAt: string | null;
  detail: string;
  role: string | null;
  displayName: string | null;
}

const PRESENCE_STATES: PresenceState[] = ["online", "busy", "idle", "offline", "unknown"];

function asPresenceState(value: unknown): PresenceState | null {
  const v = typeof value === "string" ? value.trim().toLowerCase() : "";
  // An unrecognised word is preserved as null, not snapped to a state the
  // server never claimed.
  return (PRESENCE_STATES as string[]).includes(v) ? (v as PresenceState) : null;
}

/**
 * Every member of a room with its measured presence state.
 *
 * This is the roster the group details panel renders. It reads the room's own
 * membership rather than the thread-scoped agent roster, which is empty for any
 * room the operator never opened as a thread — that mismatch is why every
 * participant previously rendered as `unknown`.
 */
export async function listRoomMembers(name: string): Promise<MemberPresence[]> {
  const d = await get<unknown>(`/groups/${encodeURIComponent(name)}/members`);
  return asList(d, ["members", "data"]).map((m) => ({
    name: String(pick(m, ["name", "bot_name"], "")),
    state: asPresenceState(pick(m, ["state", "status"], null)),
    source: String(pick(m, ["source"], "unresolved")),
    activityAt: asTime(pick<unknown>(m, ["activity_at", "last_active"], null)),
    detail: String(pick(m, ["detail"], "")),
    role: pick<string | null>(m, ["role"], null) ?? null,
    displayName: pick<string | null>(m, ["display_name"], null) ?? null,
  }));
}

/* ---------------- Nested groups: forest, roster, rules, lifecycle ------------- */

export async function deleteRoomCascade(name: string, cascade: boolean): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}?cascade=${cascade ? "true" : "false"}`, "DELETE");
}

/** Every room in the forest. Declared server-side before the `/{name}` catch-all. */
export async function groupTree(): Promise<TreeNode[]> {
  const d = await get<unknown>("/groups/tree");
  return asList(d, ["nodes", "data"]).map(toTreeNode);
}

function toTreeNode(n: Record<string, unknown>): TreeNode {
  const rawScope = (n.scope ?? {}) as Record<string, unknown>;
  return {
    room_id: String(pick(n, ["room_id"], "")),
    name: String(pick(n, ["name"], "")),
    topic: String(pick(n, ["topic"], "")),
    summary: String(pick(n, ["summary"], "")),
    project_id: pick<string | null>(n, ["project_id"], null) ?? null,
    mode: String(pick(n, ["mode"], "")),
    moderator: pick<string | null>(n, ["moderator"], null) ?? null,
    message_count: Number(pick(n, ["message_count"], 0)) || 0,
    created_at: asTime(pick<unknown>(n, ["created_at"], null)),
    updated_at: asTime(pick<unknown>(n, ["updated_at"], null)),
    scope: toScope(rawScope),
    child_count: Number(pick(n, ["child_count"], 0)) || 0,
    // Absent stays null: a tree node without both counts must not render as 0.
    direct_count: pick<number | null>(n, ["direct_count"], null),
    effective_count: pick<number | null>(n, ["effective_count"], null),
  };
}

function toScope(raw: Record<string, unknown>): RoomScope {
  return {
    room_id: String(pick(raw, ["room_id"], "")),
    parents: Array.isArray(raw.parents) ? (raw.parents as string[]) : [],
    authority_parent: pick<string | null>(raw, ["authority_parent"], null) ?? null,
    // Server-derived. Displayed verbatim, never recomputed here.
    path: String(pick(raw, ["path"], "")),
    depth: Number(pick(raw, ["depth"], 0)) || 0,
    state: String(pick(raw, ["state"], "active")) as RoomState,
    inbound: String(pick(raw, ["inbound"], "parent")) as RoomScope["inbound"],
    outbound: String(pick(raw, ["outbound"], "none")) as RoomScope["outbound"],
    max_hop: Number(pick(raw, ["max_hop"], 3)) || 3,
    mark_relayed: pick(raw, ["mark_relayed"], true) === true,
  };
}

function asNumberList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((v) => String(v)) : [];
}

/** The room's membership split by origin. Both counts always travel together. */
export async function roomRoster(name: string): Promise<RoomRoster> {
  const d = await get<Record<string, unknown>>(`/groups/${encodeURIComponent(name)}/roster`);
  const asStrings = (v: unknown) => asNumberList(v);
  return {
    room_id: String(pick(d, ["room_id"], "")),
    direct: asStrings(d.direct),
    rule_matched: asStrings(d.rule_matched),
    inherited: asStrings(d.inherited),
    inherited_from: Object.fromEntries(
      Object.entries((d.inherited_from as Record<string, unknown>) ?? {}).map(([k, v]) => [k, asStrings(v)]),
    ),
    rule_sources: Object.fromEntries(
      Object.entries((d.rule_sources as Record<string, unknown>) ?? {}).map(([k, v]) => [k, asStrings(v)]),
    ),
    excluded: asStrings(d.excluded),
    expired: asStrings(d.expired),
    effective: asStrings(d.effective),
    effective_count: Number(pick(d, ["effective_count"], 0)) || 0,
    direct_count: Number(pick(d, ["direct_count"], 0)) || 0,
    scope: toScope((d.scope ?? {}) as Record<string, unknown>),
    rules: asList({ rules: d.rules }, ["rules"]).map(
      (r): MembershipRule => ({
        id: String(pick(r, ["id"], "")),
        field: String(pick(r, ["field"], "role")) as MembershipRule["field"],
        op: String(pick(r, ["op"], "eq")) as MembershipRule["op"],
        value: String(pick(r, ["value"], "")),
        enabled: pick(r, ["enabled"], true) !== false,
        label: String(pick(r, ["label"], "")),
      }),
    ),
  };
}

export async function createSubgroup(
  parent: string,
  body: { name: string; topic?: string; summary?: string; members?: string[]; inherit?: boolean },
): Promise<void> {
  await send(`/groups/${encodeURIComponent(parent)}/subgroups`, "POST", {
    name: body.name,
    topic: body.topic ?? "",
    summary: body.summary ?? "",
    ...(body.members?.length ? { members: body.members } : {}),
    inherit: body.inherit ?? true,
  });
}

export async function listChildren(name: string): Promise<string[]> {
  const d = await get<unknown>(`/groups/${encodeURIComponent(name)}/children`);
  return asList(d, ["children", "data"]).map((c) => String(pick(c, ["name"], "")));
}

export async function roomBreadcrumbs(name: string): Promise<Breadcrumb[]> {
  const d = await get<unknown>(`/groups/${encodeURIComponent(name)}/ancestors`);
  return asList(d, ["breadcrumbs"]).map((b) => ({
    room_id: String(pick(b, ["room_id"], "")),
    name: String(pick(b, ["name"], "")),
    path: String(pick(b, ["path"], "")),
    depth: Number(pick(b, ["depth"], 0)) || 0,
    inherited_count: Number(pick(b, ["inherited_count"], 0)) || 0,
  }));
}

export async function setRoomLifecycle(name: string, state: string): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/lifecycle`, "PATCH", { state });
}

export async function setRoomPolicy(
  name: string,
  body: { inbound?: string; outbound?: string; max_hop?: number; authority_parent?: string | null; clear_authority?: boolean },
): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/policy`, "PATCH", body);
}

export async function moveGroup(name: string, parents: string[], authorityParent?: string | null): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/move`, "POST", {
    parents,
    ...(authorityParent ? { authority_parent: authorityParent } : {}),
  });
}

export async function promoteGroup(name: string): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/promote`, "POST", {});
}

/**
 * Merge every direct child into this room. Resolves with the server's own
 * report, because a partial merge says "merged 3 of 4" — never a bare success.
 */
export async function mergeGroups(name: string): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(`/groups/${encodeURIComponent(name)}/merge`, "POST", {});
}

export async function addRoomMember(
  name: string,
  botName: string,
  body: { from_room?: string | null; expires_at?: string | null } = {},
): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/members`, "POST", {
    bot_name: botName,
    ...(body.from_room ? { from_room: body.from_room } : {}),
    ...(body.expires_at ? { expires_at: body.expires_at } : {}),
  });
}

export async function removeRoomMember(name: string, botName: string): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/members/${encodeURIComponent(botName)}`, "DELETE");
}

export async function setMemberExcluded(name: string, botName: string, excluded: boolean): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/members/${encodeURIComponent(botName)}/exclude`, "POST", { excluded });
}

/** Declare a rule and learn what it matches, in one round trip. */
export async function addRoomRule(
  name: string,
  body: { field: string; op: string; value: string; enabled?: boolean; label?: string },
): Promise<RulePreview> {
  const d = await send<Record<string, unknown>>(`/groups/${encodeURIComponent(name)}/rules`, "POST", {
    ...body,
    enabled: body.enabled ?? true,
    label: body.label ?? "",
  });
  return {
    id: String(pick(d, ["id"], "")),
    field: String(pick(d, ["field"], "role")) as MembershipRule["field"],
    op: String(pick(d, ["op"], "eq")) as MembershipRule["op"],
    value: String(pick(d, ["value"], "")),
    enabled: pick(d, ["enabled"], true) !== false,
    label: String(pick(d, ["label"], "")),
    matches: Number(pick(d, ["matches"], 0)) || 0,
    matched_names: asNumberList(d.matched_names),
  };
}

export async function removeRoomRule(name: string, ruleId: string): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/rules/${encodeURIComponent(ruleId)}`, "DELETE");
}

/** Who each rule matches right now — before it is trusted to populate a room. */
export async function previewRoomRules(name: string): Promise<{ rules: RulePreview[]; valid_fields: string[]; valid_ops: string[] }> {
  const d = await get<Record<string, unknown>>(`/groups/${encodeURIComponent(name)}/rules/preview`);
  return {
    rules: asList(d, ["rules"]).map(
      (r): RulePreview => ({
        id: String(pick(r, ["id"], "")),
        field: String(pick(r, ["field"], "role")) as MembershipRule["field"],
        op: String(pick(r, ["op"], "eq")) as MembershipRule["op"],
        value: String(pick(r, ["value"], "")),
        enabled: pick(r, ["enabled"], true) !== false,
        label: String(pick(r, ["label"], "")),
        matches: Number(pick(r, ["matches"], 0)) || 0,
        matched_names: asNumberList(r.matched_names),
      }),
    ),
    valid_fields: asNumberList(d.valid_fields),
    valid_ops: asNumberList(d.valid_ops),
  };
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

/**
 * The room's intent vocabulary.
 *
 * Mirrors `alpha.groups.room.MessageIntent` on the server, which is the single
 * source of truth. This list previously declared its own thirteen kinds while
 * the router validated against a separate six-value tuple, so seven of the
 * options in the composer — including `decision`, sent by "Post as group
 * decision" — were rejected with a 422. The server now accepts the union, and
 * the client asks it rather than assuming.
 */
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

export type MessageKind = (typeof MESSAGE_KINDS)[number];

/** Emoji the room accepts for a reaction; mirrors `alpha.groups.room.REACTION_EMOJI`. */
export const REACTION_EMOJI = ["👍", "👎", "🎉", "🚀", "👀", "❤️", "🔥", "🤔"] as const;

export function kindTone(kind: string): "blue" | "amber" | "green" | "gray" {
  if (["blocker", "warning", "escalation"].includes(kind)) return "amber";
  if (["decision", "task_completion", "answer"].includes(kind)) return "green";
  if (kind !== "discussion") return "blue";
  return "gray";
}
