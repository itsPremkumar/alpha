import { apiFetch } from "./api-client";

export interface FreeProviderHealth {
  name: string;
  healthy: boolean | null;
  /**
   * Server-reported eligibility only — never inferred from health.
   * Sources: an explicit per-provider `eligible` flag, else membership in the
   * catalog's top-level `eligible_candidates`; otherwise false.
   */
  eligible: boolean;
  /** False when the server disclosed no eligibility data at all (unknown ≠ eligible). */
  eligibilityKnown: boolean;
  lastChecked?: string | null;
  failure?: string | null;
  /**
   * Model IDs this gateway currently serves, as disclosed by the server's
   * `models[]` entries. This is the only per-model data the endpoint carries:
   * health is measured **per provider**, never per model, so a model listed
   * here inherits nothing — it is "served by a provider in this state", not
   * "this model answered a call".
   */
  modelIds: string[];
  /**
   * How many models the provider has in total. `null` when the server did not
   * report a count, which is different from a reported `0`.
   */
  modelCount: number | null;
  /**
   * True when `modelIds` is a bounded prefix of the provider's catalog. The
   * server sends at most 25 per provider, so a truncated list must say so
   * rather than presenting itself as the whole catalog.
   */
  modelsTruncated: boolean;
  /** Measured round-trip, or `null` when nothing answered. Never coerced to `0`. */
  latencyMs: number | null;
  /** Tri-state catalog fetch: true=listed, false=failed, null=never attempted. */
  discoveryOk: boolean | null;
  discoveryError?: string | null;
  consecutiveFailures: number | null;
  /** ISO instant while the provider is in backoff, else `null`. */
  cooldownUntil: string | null;
  lastSuccess?: string | null;
  sourceLabels: string[];
}

export interface FreeCatalogView {
  providers: FreeProviderHealth[];
  updatedAt?: string | null;
  /** The server's own `refreshed_at`; `null` until it has refreshed once. */
  refreshedAt?: string | null;
  /** Verbatim server prose. Never replaced with a client-written summary. */
  selectionMethod?: string | null;
  disclaimer?: string | null;
  raw: unknown;
}

/** Honest view of GET /models/free/catalog. Never fabricates providers. */
export async function fetchFreeCatalog(opts?: { refresh?: boolean; probe?: boolean }): Promise<FreeCatalogView> {
  const params = new URLSearchParams();
  if (opts?.refresh) params.set("refresh", "true");
  if (opts?.probe) params.set("probe", "true");
  const qs = params.toString();
  const res = await apiFetch(`/models/free/catalog${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(`Free catalog unavailable (HTTP ${res.status})`);
  const data = await res.json();
  const body = data as Record<string, unknown>;
  const list: unknown = body.providers ?? body.catalog ?? [];
  const rawCandidates = body.eligible_candidates;
  const candidates: string[] | null = Array.isArray(rawCandidates)
    ? rawCandidates.filter((c): c is string => typeof c === "string")
    : null;
  const providers: FreeProviderHealth[] = Array.isArray(list)
    ? list.map((p) => {
        const r = p as Record<string, unknown>;
        const health = typeof r.healthy === "boolean" ? r.healthy : null;
        const name = String(r.name ?? r.provider ?? "unknown");
        let eligible: boolean;
        let eligibilityKnown: boolean;
        if (typeof r.eligible === "boolean") {
          eligible = r.eligible;
          eligibilityKnown = true;
        } else if (candidates !== null) {
          // catalog_dict() discloses eligibility as "provider:model" strings.
          eligible = candidates.some((c) => c.startsWith(`${name}:`));
          eligibilityKnown = true;
        } else {
          // No server eligibility signal: never infer it from health.
          eligible = false;
          eligibilityKnown = false;
        }
        return {
          name,
          healthy: health,
          eligible,
          eligibilityKnown,
          lastChecked: isoOrNull(r.last_checked) ?? isoOrNull(r.lastChecked),
          failure: strOrNull(r.failure) ?? strOrNull(r.last_error),
          ...mapModelDisclosure(r),
          // A measured round-trip, or `null` when nothing answered. Never `0`:
          // zero is the fastest possible link and nobody measured this one.
          latencyMs: typeof r.latency_ms === "number" && Number.isFinite(r.latency_ms) ? r.latency_ms : null,
          discoveryOk: typeof r.discovery_ok === "boolean" ? r.discovery_ok : null,
          discoveryError: strOrNull(r.discovery_error),
          consecutiveFailures: typeof r.consecutive_failures === "number" ? r.consecutive_failures : null,
          cooldownUntil: isoOrNull(r.cooldown_until),
          lastSuccess: isoOrNull(r.last_success),
          sourceLabels: Array.isArray(r.source_labels)
            ? r.source_labels.filter((s): s is string => typeof s === "string")
            : [],
        };
      })
    : [];
  // The endpoint publishes `refreshed_at`; `updated_at` is the cache-file key.
  // Reading only the latter is why the header never showed a refresh time.
  const refreshedAt = typeof body.refreshed_at === "string" ? body.refreshed_at : null;
  const updatedAt = typeof body.updated_at === "string" ? body.updated_at : refreshedAt;
  return {
    providers,
    updatedAt,
    refreshedAt,
    selectionMethod: strOrNull(body.selection_method),
    disclaimer: strOrNull(body.disclaimer),
    raw: data,
  };
}

/**
 * Per-provider model disclosure, mapped without embellishment.
 *
 * `models[]` entries are objects carrying an `id`; the endpoint sends a bounded
 * prefix (25) and flags the cut with `models_truncated`. An entry that is not an
 * object with a string `id` is skipped rather than rendered as `[object Object]`,
 * and a missing `model_count` stays `null` instead of becoming the length of the
 * list that was itself already truncated.
 */
function mapModelDisclosure(r: Record<string, unknown>): Pick<
  FreeProviderHealth,
  "modelIds" | "modelCount" | "modelsTruncated"
> {
  const raw = r.models;
  const modelIds = Array.isArray(raw)
    ? raw
        .map((m) => (m !== null && typeof m === "object" ? (m as Record<string, unknown>).id : m))
        .filter((id): id is string => typeof id === "string" && id.length > 0)
    : [];
  return {
    modelIds,
    modelCount: typeof r.model_count === "number" ? r.model_count : null,
    modelsTruncated: r.models_truncated === true,
  };
}

function strOrNull(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/** Coerce an epoch-seconds field to an ISO instant; absent/invalid stays `null`. */
function isoOrNull(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value) && value > 0) {
    return new Date(value * 1000).toISOString();
  }
  return null;
}
