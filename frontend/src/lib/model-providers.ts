/**
 * Provider bring-your-own-key surface: the client for adding a provider's
 * credential and a custom endpoint from the Settings UI.
 *
 * Two contracts this module has to get right:
 *
 * 1. **The key never round-trips.** The server stores the secret in an encrypted
 *    envelope and returns a `masked_key`; it never sends the plaintext back. So
 *    there is no "read my key" call here, and `mask_secret` output is the only
 *    echo available. `key_env` exists so an operator can keep the key in
 *    `.env` and store only the variable *name*.
 * 2. **A refusal carries the server's reason.** Egress screening rejects
 *    loopback/RFC1918 hosts outside the declared local tier, URL-embedded
 *    credentials, and cloud metadata endpoints. That refusal names what it
 *    blocked, so it is surfaced verbatim rather than collapsed into
 *    "configuration failed".
 */

import { apiFetch } from "@/lib/api-client";

/** How a custom endpoint is reached, which decides the egress tier applied. */
export type EndpointKind = "custom" | "local";

export interface ConfigureProviderPayload {
  /** Provider id from `GET /api/models/providers`. */
  provider: string;
  /**
   * The secret. Omit to keep the stored key; pass `null` to remove it. Never
   * stored client-side and never read back.
   */
  api_key?: string | null;
  /** Store only this env var's name instead of the key itself. */
  api_key_env?: string | null;
  /** Extra headers (gateway auth, vanity domain). */
  headers?: Record<string, string> | null;
  /** Register a custom model on this provider in the same call. */
  model_id?: string | null;
  display_name?: string | null;
  /** Base URL for a custom/self-hosted endpoint. */
  base_url?: string | null;
  kind?: EndpointKind;
}

export interface ConfigureProviderResult {
  success: boolean;
  provider: string;
  configured: boolean;
  /** Server-side redaction of the stored key. Never the plaintext. */
  masked_key: string | null;
  message: string;
}

/** Register or remove a provider credential and (optionally) a custom model. */
export async function configureProvider(payload: ConfigureProviderPayload): Promise<ConfigureProviderResult> {
  const res = await apiFetch(`/models/providers/configure`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    // The egress policy and the value validators both report *why*; keeping
    // that text is the whole value of this call.
    const detail = typeof body?.detail === "string" ? body.detail : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return {
    success: body?.success === true,
    provider: typeof body?.provider === "string" ? body.provider : payload.provider,
    configured: body?.configured === true,
    masked_key: typeof body?.masked_key === "string" ? body.masked_key : null,
    message: typeof body?.message === "string" ? body.message : "",
  };
}

/** At-rest protection for stored provider credentials. */
export interface CredentialStorageStatus {
  /**
   * `dpapi-user` | `fernet-file` | `none` | `plaintext`.
   *
   * `plaintext` is a real, reportable state and the Settings surface shows it
   * rather than implying encryption that is not there.
   */
  storage: string;
  /** True when a legacy plaintext file was migrated on first read. */
  migrated: boolean;
  detail?: string | null;
}

export async function fetchCredentialStorageStatus(): Promise<CredentialStorageStatus> {
  const res = await apiFetch(`/models/providers/credentials-storage`);
  if (!res.ok) throw new Error(`Credential storage status failed (HTTP ${res.status})`);
  const body = await res.json();
  return {
    storage: typeof body?.storage === "string" ? body.storage : "unknown",
    migrated: body?.migrated === true,
    detail: typeof body?.detail === "string" ? body.detail : null,
  };
}
