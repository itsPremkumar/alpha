/**
 * Group profile, links, goals, pinning, threads, search, receipts and typing —
 * the network layer only.
 *
 * The derivations live in `./groups-profile-model`, which imports nothing, so
 * its tests run without a bundler. This file adds the calls and the response
 * shapes.
 *
 * **A failed read throws.** It is never turned into an empty profile or an
 * empty link list, because "this group has no description" and "the profile
 * read failed" are opposite claims and a form that silently renders blank
 * invites the operator to save it — at which point the blank becomes real.
 *
 * Every route here is declared above the `/{name}` catch-all server-side; the
 * paths below mirror them one-for-one so a rename on either side is a
 * TypeScript error rather than a runtime `Room 'profile' not found`.
 */

import { apiUrl } from "@/lib/api-client";
import { get, send } from "@/lib/http";
import type { GroupGoal, GroupLink, GroupProfile } from "./groups-profile-model";

export * from "./groups-profile-model";

const enc = encodeURIComponent;

// ── Profile ────────────────────────────────────────────────────────────────

export async function fetchGroupProfile(room: string): Promise<GroupProfile> {
  return (await get<Record<string, unknown>>(`/groups/${enc(room)}/profile`)) as GroupProfile;
}

export interface ProfilePatch {
  description?: string | null;
  purpose?: string | null;
  goals?: string[];
  tags?: string[];
  category?: string | null;
  avatar_url?: string | null;
  banner_url?: string | null;
  avatar_color?: string | null;
  created_by?: string | null;
}

/**
 * Patch the profile.
 *
 * Only the keys present in `patch` are sent, so an untouched field is *absent*
 * from the body rather than present-and-null — the server distinguishes the two
 * through `model_fields_set`, and sending explicit nulls for fields the form
 * did not change would clear them.
 */
export async function updateGroupProfile(room: string, patch: ProfilePatch): Promise<GroupProfile> {
  const body: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(patch)) {
    if (value !== undefined) body[key] = value;
  }
  return (await send(`/groups/${enc(room)}/profile`, "PATCH", body)) as GroupProfile;
}

// ── Links ──────────────────────────────────────────────────────────────────

export interface GroupLinkList {
  room: string;
  links: GroupLink[];
  count: number;
  valid_types: string[];
}

export async function fetchGroupLinks(room: string): Promise<GroupLinkList> {
  return (await get<Record<string, unknown>>(`/groups/${enc(room)}/links`)) as unknown as GroupLinkList;
}

export async function addGroupLink(
  room: string,
  link: { label: string; url: string; link_type?: string; icon?: string | null },
): Promise<GroupLink> {
  return (await send(`/groups/${enc(room)}/links`, "POST", link)) as GroupLink;
}

/** 204 with no body — the caller refetches rather than trusting a null. */
export async function removeGroupLink(room: string, linkId: string): Promise<void> {
  await send(`/groups/${enc(room)}/links/${enc(linkId)}`, "DELETE");
}

export async function reorderGroupLinks(room: string, linkIds: string[]): Promise<GroupLink[]> {
  const d = (await send(`/groups/${enc(room)}/links/reorder`, "PATCH", { link_ids: linkIds })) as Record<
    string,
    unknown
  >;
  return (d.links as GroupLink[]) ?? [];
}

// ── Goals ──────────────────────────────────────────────────────────────────

export async function fetchGroupGoals(room: string): Promise<GroupGoal[]> {
  const d = await get<Record<string, unknown>>(`/groups/${enc(room)}/goals`);
  return (d.goals as GroupGoal[]) ?? [];
}

export async function addGroupGoal(room: string, title: string, description = ""): Promise<GroupGoal> {
  return (await send(`/groups/${enc(room)}/goals`, "POST", { title, description })) as GroupGoal;
}

export async function updateGroupGoal(room: string, goalId: string, updates: Partial<GroupGoal>): Promise<GroupGoal> {
  return (await send(`/groups/${enc(room)}/goals/${enc(goalId)}`, "PATCH", updates)) as GroupGoal;
}

// ── Project binding ────────────────────────────────────────────────────────

export interface ProjectLink {
  project_id: string;
  project_name?: string;
  project_type?: string;
}

export async function fetchGroupProjectLink(room: string): Promise<ProjectLink | null> {
  const d = await get<Record<string, unknown>>(`/groups/${enc(room)}/project-link`);
  return (d.project_link as ProjectLink | null) ?? null;
}

export async function linkGroupProject(room: string, link: ProjectLink): Promise<ProjectLink> {
  return (await send(`/groups/${enc(room)}/project-link`, "POST", link)) as ProjectLink;
}

export async function unlinkGroupProject(room: string): Promise<void> {
  await send(`/groups/${enc(room)}/project-link`, "DELETE");
}

// ── Clone ──────────────────────────────────────────────────────────────────

export interface CloneOptions {
  new_name: string;
  include_members?: boolean;
  include_rules?: boolean;
  include_links?: boolean;
  include_profile?: boolean;
}

/**
 * Clone a group's charter into a new room.
 *
 * The transcript is never copied — the endpoint documents that, and the
 * client has no option that would ask for it.
 */
export async function cloneGroup(room: string, options: CloneOptions): Promise<GroupProfile> {
  return (await send(`/groups/${enc(room)}/clone`, "POST", options)) as unknown as GroupProfile;
}

// ── Pinning ────────────────────────────────────────────────────────────────

export interface PinnedMessages {
  room: string;
  pinned: Array<Record<string, unknown>>;
  count: number;
}

export async function fetchPinnedMessages(room: string): Promise<PinnedMessages> {
  return (await get<Record<string, unknown>>(`/groups/${enc(room)}/pinned`)) as unknown as PinnedMessages;
}

export async function pinMessage(room: string, messageId: string, actor: string): Promise<void> {
  await send(`/groups/${enc(room)}/messages/${enc(messageId)}/pin`, "POST", { actor });
}

export async function unpinMessage(room: string, messageId: string): Promise<void> {
  await send(`/groups/${enc(room)}/messages/${enc(messageId)}/pin`, "DELETE");
}

// ── Threading ──────────────────────────────────────────────────────────────

export interface MessageThread {
  room: string;
  root: Record<string, unknown> | null;
  replies: Array<Record<string, unknown>>;
}

export async function fetchMessageThread(room: string, messageId: string): Promise<MessageThread> {
  return (await get<Record<string, unknown>>(
    `/groups/${enc(room)}/messages/${enc(messageId)}/thread`,
  )) as unknown as MessageThread;
}

export interface ThreadRoot {
  id: string;
  content?: string;
  sender?: string;
  reply_count?: number;
  created_at?: string;
}

export async function fetchThreadRoots(room: string): Promise<ThreadRoot[]> {
  const d = await get<Record<string, unknown>>(`/groups/${enc(room)}/threads`);
  return (d.threads as ThreadRoot[]) ?? [];
}

// ── Search ─────────────────────────────────────────────────────────────────

export interface SearchResults {
  room: string;
  query: string;
  results: Array<Record<string, unknown>>;
  count: number;
}

export async function searchGroupMessages(
  room: string,
  query: string,
  opts: { sender?: string; intent?: string; limit?: number } = {},
): Promise<SearchResults> {
  const params = new URLSearchParams({ q: query });
  if (opts.sender) params.set("sender", opts.sender);
  if (opts.intent) params.set("intent", opts.intent);
  if (opts.limit) params.set("limit", String(opts.limit));
  return (await get<Record<string, unknown>>(
    `/groups/${enc(room)}/search?${params.toString()}`,
  )) as unknown as SearchResults;
}

// ── Read receipts ──────────────────────────────────────────────────────────

export interface UnreadMessages {
  room: string;
  reader: string;
  unread: Array<Record<string, unknown>>;
  count: number;
}

export async function fetchUnreadMessages(room: string, reader: string): Promise<UnreadMessages> {
  return (await get<Record<string, unknown>>(
    `/groups/${enc(room)}/unread?reader=${enc(reader)}`,
  )) as unknown as UnreadMessages;
}

export async function markMessageRead(room: string, messageId: string, actor: string): Promise<void> {
  await send(`/groups/${enc(room)}/messages/${enc(messageId)}/read`, "POST", { actor });
}

export async function fetchMessageReaders(room: string, messageId: string): Promise<string[]> {
  const d = await get<Record<string, unknown>>(`/groups/${enc(room)}/messages/${enc(messageId)}/readers`);
  return (d.readers as string[]) ?? [];
}

// ── Typing ─────────────────────────────────────────────────────────────────

export async function setTyping(room: string, botName: string, isTyping: boolean): Promise<void> {
  await send(`/groups/${enc(room)}/typing`, "POST", { bot_name: botName, is_typing: isTyping });
}

export async function fetchTyping(room: string): Promise<string[]> {
  const d = await get<Record<string, unknown>>(`/groups/${enc(room)}/typing`);
  return ((d.typing as Array<{ bot_name?: string }>) ?? []).map((entry) => entry.bot_name ?? "");
}

// ── Real-time events (SSE) ─────────────────────────────────────────────────

export interface RoomEvent {
  event: string;
  room_id?: string;
  data: Record<string, unknown>;
  seq?: number;
  at?: string;
}

/**
 * Subscribe to a room's event stream.
 *
 * `EventSource` cannot send headers, so the caller only ever sees events the
 * Gateway already authorized; a failure surfaces through `onError` rather than
 * being retried here, because a reconnect loop with no backoff would pin a CPU
 * core the moment the Gateway restarts.
 *
 * Returns a disposer. Calling it twice is safe — `close()` on a closed source
 * is a no-op — because a React effect can run its cleanup more than once under
 * StrictMode.
 */
export function subscribeRoomEvents(
  room: string,
  onEvent: (event: RoomEvent) => void,
  onError?: (event: MessageEvent | Event) => void,
): () => void {
  const source = new EventSource(apiUrl(`/groups/${enc(room)}/events`));
  source.onmessage = (event: MessageEvent) => {
    try {
      onEvent(JSON.parse(event.data) as RoomEvent);
    } catch {
      // A frame we cannot parse is not an event we can act on; dropping it
      // keeps the stream alive instead of throwing out every later frame.
    }
  };
  source.onerror = (event: Event) => onError?.(event);
  return () => source.close();
}
