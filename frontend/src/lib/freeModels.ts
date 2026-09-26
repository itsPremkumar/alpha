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
}

/** Honest view of GET /models/free/catalog. Never fabricates providers. */
export async function fetchFreeCatalog(opts?: { refresh?: boolean; probe?: boolean }): Promise<{ providers: FreeProviderHealth[]; updatedAt?: string | null; raw: unknown }> {
  const params = new URLSearchParams();
  if (opts?.refresh) params.set("refresh", "true");
  if (opts?.probe) params.set("probe", "true");
  const qs = params.toString();
  const res = await apiFetch(`/models/free/catalog${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(`Free catalog unavailable (HTTP ${res.status})`);
  const data = await res.json();
  const list: unknown = (data as Record<string, unknown>).providers ?? (data as Record<string, unknown>).catalog ?? [];
  const rawCandidates = (data as Record<string, unknown>).eligible_candidates;
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
          lastChecked: typeof r.last_checked === "string" ? r.last_checked : typeof r.lastChecked === "string" ? (r.lastChecked as string) : null,
          failure: typeof r.failure === "string" ? r.failure : typeof r.last_error === "string" ? (r.last_error as string) : null,
        };
      })
    : [];
  const updatedAt = typeof (data as Record<string, unknown>).updated_at === "string"
    ? ((data as Record<string, unknown>).updated_at as string)
    : null;
  return { providers, updatedAt, raw: data };
}
