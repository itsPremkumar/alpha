// Typed client for the local-first Alpha peer network.
// The UI never invents peer state: every list, status, and delivery receipt is
// read from the Gateway, and failed requests reject with its detail.
import { get, send, asList, pick } from "./http";

export type PeerConversationMode =
  | "direct"
  | "one_to_many"
  | "many_to_one"
  | "many_to_many"
  | "broadcast"
  | "inbox";

export interface PeerNetworkStatus {
  enabled: boolean;
  identity: Record<string, unknown> & { agent_id?: string; name?: string };
  discovery: Record<string, unknown>;
  transports: Record<string, unknown>;
  persistence: Record<string, unknown>;
  pairing_code: string | null;
  limits: Record<string, number>;
}

export interface Peer {
  agent_id: string;
  name: string;
  description: string;
  version: string;
  url: string;
  websocket_url: string | null;
  preferred_transport: string;
  capabilities: string[];
  skills: unknown[];
  source: string;
  trust: string;
  first_seen: string;
  last_seen: string;
  paired_at: string | null;
  card: Record<string, unknown>;
}

export interface PeerConversation {
  conversation_id: string;
  mode: PeerConversationMode | string;
  title: string;
  status: string;
  participants: string[];
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface PeerDelivery {
  recipient_id: string;
  status: string;
  transport: string | null;
  error: string | null;
  delivered_at: string | null;
  read_at: string | null;
}

export interface PeerMessage {
  message_id: string;
  conversation_id: string;
  sender_id: string;
  recipients: string[];
  kind: string;
  text: string;
  payload: Record<string, unknown>;
  status: string;
  direction: string;
  created_at: string;
  delivered_at: string | null;
  read_at: string | null;
  delivery_error: string | null;
  deliveries: PeerDelivery[];
}

function str(record: Record<string, unknown>, key: string, fallback = ""): string {
  const value = record[key];
  return typeof value === "string" ? value : fallback;
}

function nullableStr(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === "string" ? value : null;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function toPeer(raw: Record<string, unknown>): Peer {
  return {
    agent_id: str(raw, "agent_id"),
    name: str(raw, "name"),
    description: str(raw, "description"),
    version: str(raw, "version", "unknown"),
    url: str(raw, "url"),
    websocket_url: nullableStr(raw, "websocket_url"),
    preferred_transport: str(raw, "preferred_transport", "unknown"),
    capabilities: asList(raw.capabilities, ["capabilities"]).map((item) => String(item)),
    skills: Array.isArray(raw.skills) ? raw.skills : [],
    source: str(raw, "source", "unknown"),
    trust: str(raw, "trust", "unknown"),
    first_seen: str(raw, "first_seen"),
    last_seen: str(raw, "last_seen"),
    paired_at: nullableStr(raw, "paired_at"),
    card: record(raw.card),
  };
}

function toConversation(raw: Record<string, unknown>): PeerConversation {
  return {
    conversation_id: str(raw, "conversation_id"),
    mode: str(raw, "mode", "unknown"),
    title: str(raw, "title", "Untitled peer conversation"),
    status: str(raw, "status", "unknown"),
    participants: asList(raw.participants, ["participants"]).map((item) => String(item)),
    metadata: record(raw.metadata),
    created_at: str(raw, "created_at"),
    updated_at: str(raw, "updated_at"),
  };
}

function toDelivery(raw: Record<string, unknown>): PeerDelivery {
  return {
    recipient_id: str(raw, "recipient_id"),
    status: str(raw, "status", "unknown"),
    transport: nullableStr(raw, "transport"),
    error: nullableStr(raw, "error"),
    delivered_at: nullableStr(raw, "delivered_at"),
    read_at: nullableStr(raw, "read_at"),
  };
}

function toMessage(raw: Record<string, unknown>): PeerMessage {
  return {
    message_id: str(raw, "message_id"),
    conversation_id: str(raw, "conversation_id"),
    sender_id: str(raw, "sender_id"),
    recipients: asList(raw.recipients, ["recipients"]).map((item) => String(item)),
    kind: str(raw, "kind", "message"),
    text: str(raw, "text"),
    payload: record(raw.payload),
    status: str(raw, "status", "unknown"),
    direction: str(raw, "direction", "unknown"),
    created_at: str(raw, "created_at"),
    delivered_at: nullableStr(raw, "delivered_at"),
    read_at: nullableStr(raw, "read_at"),
    delivery_error: nullableStr(raw, "delivery_error"),
    deliveries: asList(raw.deliveries, ["deliveries"]).map((item) => toDelivery(item as Record<string, unknown>)),
  };
}

function statusFrom(raw: Record<string, unknown>): PeerNetworkStatus {
  return {
    enabled: raw.enabled === true,
    identity: record(raw.identity),
    discovery: record(raw.discovery),
    transports: record(raw.transports),
    persistence: record(raw.persistence),
    pairing_code: nullableStr(raw, "pairing_code"),
    limits: Object.fromEntries(
      Object.entries(record(raw.limits)).flatMap(([key, value]) =>
        typeof value === "number" && Number.isFinite(value) ? [[key, value] as [string, number]] : [],
      ),
    ),
  };
}

export async function fetchPeerNetworkStatus(): Promise<PeerNetworkStatus> {
  return statusFrom(await get<Record<string, unknown>>("/peer-network/status"));
}

export async function listPeers(options: { skill?: string; trust?: string } = {}): Promise<Peer[]> {
  const query = new URLSearchParams();
  if (options.skill) query.set("skill", options.skill);
  if (options.trust) query.set("trust", options.trust);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const body = await get<Record<string, unknown>>(`/peer-network/peers${suffix}`);
  return asList(body, ["peers"]).map((raw) => toPeer(raw as Record<string, unknown>));
}

export async function discoverPeers(): Promise<Peer[]> {
  const body = await send<Record<string, unknown>>("/peer-network/discover", "POST", {});
  return asList(body, ["peers"]).map((raw) => toPeer(raw as Record<string, unknown>));
}

export async function publishPeerCardToGitHub(): Promise<void> {
  await send<Record<string, unknown>>("/peer-network/github/publish", "POST", {});
}

export async function pairPeer(body: { endpoint: string; pairing_code: string; expected_agent_id?: string | null }): Promise<Peer> {
  const response = await send<Record<string, unknown>>("/peer-network/pair", "POST", {
    endpoint: body.endpoint,
    pairing_code: body.pairing_code,
    expected_agent_id: body.expected_agent_id ?? null,
  });
  return toPeer(record(response.peer));
}

export async function rotatePairingCode(): Promise<string> {
  const response = await send<Record<string, unknown>>("/peer-network/pair/rotate", "POST", {});
  const code = pick(response, ["pairing_code"], null);
  if (typeof code !== "string" || !code) throw new Error("Gateway did not return a pairing code");
  return code;
}

export async function setPeerTrust(agentId: string, trust: "discovered" | "blocked"): Promise<Peer> {
  const response = await send<Record<string, unknown>>(`/peer-network/peers/${encodeURIComponent(agentId)}/trust`, "PATCH", { trust });
  return toPeer(record(response.peer));
}

export async function listPeerConversations(): Promise<PeerConversation[]> {
  const body = await get<Record<string, unknown>>("/peer-network/conversations");
  return asList(body, ["conversations"]).map((raw) => toConversation(raw as Record<string, unknown>));
}

export async function createPeerConversation(body: {
  title: string;
  mode: PeerConversationMode;
  participants: string[];
  metadata?: Record<string, unknown>;
}): Promise<PeerConversation> {
  const response = await send<Record<string, unknown>>("/peer-network/conversations", "POST", {
    title: body.title,
    mode: body.mode,
    participants: body.participants,
    metadata: body.metadata ?? {},
  });
  return toConversation(response);
}

export async function listPeerMessages(conversationId: string, limit = 200): Promise<PeerMessage[]> {
  const bounded = Math.max(1, Math.min(limit, 1000));
  const body = await get<Record<string, unknown>>(`/peer-network/conversations/${encodeURIComponent(conversationId)}/messages?limit=${bounded}`);
  return asList(body, ["messages"]).map((raw) => toMessage(raw as Record<string, unknown>));
}

export async function sendPeerMessage(body: {
  conversation_id?: string | null;
  sender_id?: string | null;
  recipients?: string[];
  kind?: string;
  text: string;
  payload?: Record<string, unknown>;
  mode?: PeerConversationMode | null;
  title?: string | null;
  idempotency_key?: string | null;
}): Promise<PeerMessage> {
  const response = await send<Record<string, unknown>>("/peer-network/messages", "POST", {
    conversation_id: body.conversation_id ?? null,
    sender_id: body.sender_id ?? null,
    recipients: body.recipients ?? [],
    kind: body.kind ?? "chat",
    text: body.text,
    payload: body.payload ?? {},
    mode: body.mode ?? null,
    title: body.title ?? null,
    idempotency_key: body.idempotency_key ?? null,
  });
  return toMessage(response);
}

export async function markPeerMessageRead(messageId: string): Promise<PeerMessage> {
  const response = await send<Record<string, unknown>>(`/peer-network/messages/${encodeURIComponent(messageId)}/read`, "POST", {});
  return toMessage(record(response.message));
}
