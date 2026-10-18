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

export interface ChannelCredentialField {
  /** The config key the server stores this value under. */
  name: string;
  /** The server's own label for the field. */
  label: string;
  /** `password` values are shown masked and reused when posted back masked. */
  type: string;
  required: boolean;
}

export interface ChannelProvider {
  id: string;
  name: string;
  description: string;
  configured: boolean;
  /**
   * The fields the server will accept for this provider
   * (`POST /api/channels/{provider}/runtime-config` values), taken from
   * `GET /api/channels/providers`. Empty when the server sent none — the
   * configure form then offers nothing rather than inventing fields.
   */
  credentialFields: ChannelCredentialField[];
  /**
   * What the server currently holds, with secret fields masked
   * (`********`). Posting a masked value back reuses the stored one, so an
   * unchanged secret is not a "missing required configuration" error.
   */
  credentialValues: Record<string, string>;
}

/**
 * The provider catalog plus the subsystem's own master switch.
 *
 * `GET /api/channels/providers` answers `{enabled, providers}`, and
 * `enabled: false` is the *cause* of an empty `providers` array
 * (backend/app/gateway/routers/channel_connections.py:647 filters the catalog
 * down to the providers the config has enabled). Collapsing that to `[]` made a
 * subsystem that is switched off by configuration render exactly like a
 * subsystem with nothing to connect — the reader is sent to debug the wrong
 * thing. `enabled` is therefore carried through as a nullable flag: null only
 * when the Gateway did not report it.
 */
export interface ChannelProviderCatalog {
  /** The channel-connections master switch; null when the Gateway omitted it. */
  enabled: boolean | null;
  providers: ChannelProvider[];
}

export async function listProviders(): Promise<ChannelProviderCatalog> {
  // Backend: GET /api/channels/providers -> {enabled, providers}
  // Rejects on failure: an empty catalog must mean the server listed no
  // providers, not that the call failed (frontend/src/AGENTS.md → Client
  // honesty rules).
  const d = await get<Record<string, unknown>>("/channels/providers");
  return {
    enabled: typeof d.enabled === "boolean" ? d.enabled : null,
    providers: asList(d, ["providers", "data"]).map((p) => ({
      id: String(pick(p, ["id", "provider"], "")),
      name: String(pick(p, ["name", "display_name"], "")),
      description: String(pick(p, ["description"], "")),
      configured: Boolean(pick(p, ["configured", "connected"], false)),
      credentialFields: asList(p.credential_fields, ["fields"])
        .map((f) => ({
          name: String(pick(f, ["name"], "")),
          label: String(pick(f, ["label", "name"], "")),
          type: String(pick(f, ["type"], "text")),
          required: Boolean(pick(f, ["required"], false)),
        }))
        .filter((f) => f.name !== ""),
      credentialValues: stringRecord(p.credential_values),
    })),
  };
}

/** A plain string→string record; non-string entries are dropped, not stringified. */
function stringRecord(value: unknown): Record<string, string> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    if (typeof v === "string") out[k] = v;
  }
  return out;
}

export interface ChannelRuntimeConfigResult {
  provider: string;
  /** The provider row's state after the write, as the server re-read it. */
  configured: boolean;
}

/**
 * Save one provider's runtime credentials
 * (`POST /api/channels/{provider}/runtime-config`).
 *
 * Admin-only on the Gateway; a non-admin caller receives the server's own
 * refusal as the rejection, which the form shows instead of a silent no-op.
 * The response is the provider row re-read *after* the write, so the caller
 * renders the server's `configured` verdict rather than assuming the save
 * took. A secret field posted back while still masked (`********`) reuses the
 * stored value (backend `_required_runtime_values`), so "leave it as shown,
 * change one field" works.
 */
export async function saveChannelRuntimeConfig(provider: string, values: Record<string, string>): Promise<ChannelRuntimeConfigResult> {
  const d = await send<Record<string, unknown>>(`/channels/${encodeURIComponent(provider)}/runtime-config`, "POST", { values });
  return {
    provider: String(pick(d, ["provider"], provider)),
    configured: Boolean(pick(d, ["configured"], false)),
  };
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
