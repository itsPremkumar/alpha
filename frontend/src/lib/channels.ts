import { get, send, asList, pick } from "./http";

export interface ChannelState {
  name: string;
  enabled: boolean;
  connected: boolean;
  status: string;
}

export async function channelStatus(): Promise<ChannelState[]> {
  // No try/catch on purpose: a failure must reject with the server's reason,
  // so `[]` can only ever mean "the server said there are no channels"
  // (frontend/src/AGENTS.md → Client honesty rules). Both callers already
  // handle rejection — ChannelsSection.tsx routes it to setError, and the
  // system.ts:68 probe turns it into ok:false with the real message.
  const d = await get<Record<string, unknown>>("/channels");
  const channels = d.channels;
  // The Gateway reports channel state as a map keyed by channel id
  // (`{channels: {slack: {enabled, running}}}`), not as an array, so the
  // array-only envelope reader would silently yield nothing here. Fold the
  // map into the entry shape the mapping below already understands, with the
  // key winning as the channel name; array/envelope payloads keep their path.
  const list =
    channels && typeof channels === "object" && !Array.isArray(channels)
      ? Object.entries(channels as Record<string, unknown>).map(([name, value]) => ({
          ...(value && typeof value === "object" ? (value as Record<string, unknown>) : {}),
          name,
        }))
      : asList(channels ?? d, ["channels", "data"]);
  if (list.length === 0) return [];
  return list.map((c) => ({
    name: String(pick(c, ["name", "channel"], "")),
    enabled: Boolean(pick(c, ["enabled"], false)),
    connected: Boolean(pick(c, ["connected", "running"], false)),
    status: String(pick(c, ["status", "state"], "")),
  }));
}

export async function restartChannel(name: string): Promise<string> {
  const d = await send<Record<string, unknown>>(`/channels/${encodeURIComponent(name)}/restart`, "POST", {});
  return String(pick(d, ["message", "status"], "Restart requested."));
}

export interface ChannelProvider {
  id: string;
  name: string;
  description: string;
  configured: boolean;
}

export async function listProviders(): Promise<ChannelProvider[]> {
  // Backend: GET /api/channels/providers -> {enabled, providers}
  // Rejects on failure: `[]` must mean the server listed no providers, not
  // that the call failed (frontend/src/AGENTS.md → Client honesty rules).
  const d = await get<unknown>("/channels/providers");
  return asList(d, ["providers", "data"]).map((p) => ({
    id: String(pick(p, ["id", "provider"], "")),
    name: String(pick(p, ["name", "display_name"], "")),
    description: String(pick(p, ["description"], "")),
    configured: Boolean(pick(p, ["configured", "connected"], false)),
  }));
}

export interface ChannelConnection {
  id: string;
  provider: string;
  label: string;
  status: string;
}

export async function listConnections(): Promise<ChannelConnection[]> {
  // Backend: GET /api/channels/connections -> {connections}
  // Rejects on failure: `[]` must mean the server listed no connections, not
  // that the call failed (frontend/src/AGENTS.md → Client honesty rules).
  const d = await get<unknown>("/channels/connections");
  return asList(d, ["connections", "data"]).map((c, i) => ({
    id: String(pick(c, ["id", "connection_id"], `conn-${i}`)),
    provider: String(pick(c, ["provider"], "")),
    label: String(pick(c, ["label", "name"], "")),
    status: String(pick(c, ["status", "state"], "")),
  }));
}

export async function connectProvider(provider: string): Promise<Record<string, unknown>> {
  // Backend: POST /api/channels/{provider}/connect -> {mode, url?, code, instruction, ...}
  return send<Record<string, unknown>>(`/channels/${encodeURIComponent(provider)}/connect`, "POST", {});
}

export async function disconnectConnection(connectionId: string): Promise<void> {
  // Backend: DELETE /api/channels/connections/{connection_id} (204)
  await send(`/channels/connections/${encodeURIComponent(connectionId)}`, "DELETE");
}

export async function larkStatus(): Promise<Record<string, unknown> | null> {
  // Rejects on failure so a dead Gateway cannot masquerade as "Lark not
  // configured"; `null` remains only for a real empty response body.
  return await get<Record<string, unknown>>("/integrations/lark/status");
}
