/**
 * Client for `GET /api/models/discovery` — a provider's **live** catalog.
 *
 * This is the only route that reports context window, vision support, request
 * parameters, and per-model price. It is deliberately a separate read from
 * `GET /api/models` because the two answer different questions:
 *
 * - `/api/models` — what **this deployment** can run right now.
 * - `/api/models/discovery` — what each provider **publishes**, for providers
 *   whose catalog answered.
 *
 * A provider that did not answer is reported with `ok: false` and an error, and
 * that provider is absent from the model list. It is never flattened into "this
 * provider has no models", which would read as a confirmed empty catalog rather
 * than a failed probe.
 *
 * The refresh route (`POST /api/models/discovery/refresh`) is admin-only because
 * it makes an outbound request, so `forceRefresh` is opt-in and its refusal is
 * surfaced rather than swallowed.
 */

import { apiFetch } from "@/lib/api-client";
import type { DiscoveredModel } from "@/lib/model-capabilities";

/** One provider's catalog plus how that answer was obtained. */
export interface DiscoveredProvider {
  provider: string;
  /** Did this provider answer at all? `false` means the read failed. */
  ok: boolean;
  error: string | null;
  /** ISO timestamp of the fetch this data came from, or `null`. */
  fetched_at: string | null;
  age_seconds: number | null;
  /** True when the cache is older than its freshness window. */
  stale: boolean;
  source_url: string | null;
  model_count: number;
  free_count: number;
  models: DiscoveredModel[];
}

/** `GET /api/models/discovery` envelope. */
export interface ModelDiscovery {
  providers: DiscoveredProvider[];
  /** Discovery is best-effort: this names what it could not do. */
  supported: Record<string, string> | null;
}

function asDiscoveredModel(raw: any): DiscoveredModel | null {
  if (!raw || typeof raw.id !== "string" || !raw.id) return null;
  // Numeric fields are copied only when they are finite numbers. A provider
  // sending a string, null, or a sentinel yields `null`/absent rather than a
  // number the UI would then format as a real window or price.
  const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);
  const list = (value: unknown): string[] | null => (Array.isArray(value) ? value.filter((v) => typeof v === "string") : null);

  return {
    id: raw.id,
    name: typeof raw.name === "string" ? raw.name : undefined,
    context_length: num(raw.context_length),
    endpoint_context_length: num(raw.endpoint_context_length),
    endpoint_max_completion_tokens: num(raw.endpoint_max_completion_tokens),
    supports_vision: typeof raw.supports_vision === "boolean" ? raw.supports_vision : null,
    supports_thinking: typeof raw.supports_thinking === "boolean" ? raw.supports_thinking : null,
    reasoning_efforts: list(raw.reasoning_efforts),
    supported_parameters: list(raw.supported_parameters),
    input_price_per_million: num(raw.input_price_per_million),
    output_price_per_million: num(raw.output_price_per_million),
    is_free: Boolean(raw.is_free),
    configured: Boolean(raw.configured),
  };
}

function asProvider(raw: any): DiscoveredProvider {
  const models = Array.isArray(raw?.models)
    ? raw.models.map(asDiscoveredModel).filter((m: DiscoveredModel | null): m is DiscoveredModel => m !== null)
    : [];
  return {
    provider: typeof raw?.provider === "string" ? raw.provider : "unknown",
    // `ok` is read strictly: absent means the provider never answered, which is
    // a failed probe rather than a provider that reports zero models.
    ok: raw?.ok === true,
    error: typeof raw?.error === "string" && raw.error ? raw.error : null,
    fetched_at: typeof raw?.fetched_at === "string" ? raw.fetched_at : null,
    age_seconds: typeof raw?.age_seconds === "number" ? raw.age_seconds : null,
    stale: raw?.stale === true,
    source_url: typeof raw?.source_url === "string" ? raw.source_url : null,
    model_count: typeof raw?.model_count === "number" ? raw.model_count : models.length,
    free_count: typeof raw?.free_count === "number" ? raw.free_count : 0,
    models,
  };
}

export interface FetchDiscoveryOptions {
  /** Restrict to one provider; the server 400s an unknown id. */
  provider?: string;
}

/**
 * Read the cached discovery catalogs.
 *
 * Rejects on a non-2xx with the server's reason rather than resolving to an
 * empty list — an empty list would read as "no provider publishes anything",
 * which is a different claim from "the read failed".
 */
export async function fetchModelDiscovery(options: FetchDiscoveryOptions = {}): Promise<ModelDiscovery> {
  const query = options.provider ? `?provider=${encodeURIComponent(options.provider)}` : "";
  const res = await apiFetch(`/models/discovery${query}`);
  if (!res.ok) throw new Error(`Model discovery failed (HTTP ${res.status})`);
  const body = await res.json();
  const providers = Array.isArray(body?.providers) ? body.providers.map(asProvider) : [];
  return {
    providers,
    supported: body?.supported && typeof body.supported === "object" ? body.supported : null,
  };
}

/**
 * Force a refetch of one provider's catalog. **Admin-only** — it makes an
 * outbound request — so a 403 here is the server's refusal and is thrown, not
 * downgraded to a warning.
 */
export async function refreshModelDiscovery(provider: string): Promise<void> {
  const res = await apiFetch(`/models/discovery/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* keep the status-code form when the body is not JSON */
    }
    throw new Error(`Discovery refresh refused: ${detail}`);
  }
}

/** Every model across every provider that answered, flattened. */
export function allDiscoveredModels(discovery: ModelDiscovery | null | undefined): DiscoveredModel[] {
  if (!discovery) return [];
  return discovery.providers.filter((p) => p.ok).flatMap((p) => p.models);
}
