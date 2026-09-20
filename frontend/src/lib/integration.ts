import { get } from "./http";

type Rec = Record<string, unknown>;

function rec(v: unknown): Rec {
  return v && typeof v === "object" ? (v as Rec) : {};
}

function num(v: unknown, fallback = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

/** Wired/excluded counts for one manifest section (tools, routers, ...). */
export interface ClassCoverage {
  total: number;
  wired: number;
  intentionally_unwired: number;
  excluded: number;
}

/** One opt-in capability from backend alpha.capabilities.catalog. */
export interface CapabilityStatus {
  id: string;
  enabled: boolean;
  loadable: boolean;
  module: string;
  target: string;
  kind: string;
  description: string;
  error?: string;
}

export interface IntegrationHealth {
  manifest_found: boolean;
  manifest_version: string;
  coverage: Record<string, ClassCoverage>;
  autonomy: Rec;
  event_bus: Rec;
  capabilities: CapabilityStatus[];
  unwired: string[];
  generated_at: string;
}

/** Full wiring picture: manifest coverage, autonomy loops, event bus, capabilities. */
export async function fetchIntegrationHealth(): Promise<IntegrationHealth> {
  const d = await get<Rec>("/ops/integration-health");

  const coverage: Record<string, ClassCoverage> = {};
  const rawCoverage = rec(d.coverage);
  for (const [section, value] of Object.entries(rawCoverage)) {
    const c = rec(value);
    coverage[section] = {
      total: num(c.total),
      wired: num(c.wired),
      intentionally_unwired: num(c.intentionally_unwired),
      excluded: num(c.excluded),
    };
  }

  const rawCaps = rec(d.capabilities);
  const capabilities: CapabilityStatus[] = Object.entries(rawCaps)
    .map(([id, value]) => {
      const c = rec(value);
      return {
        id,
        enabled: Boolean(c.enabled),
        loadable: Boolean(c.loadable),
        module: String(c.module ?? ""),
        target: String(c.target ?? ""),
        kind: String(c.kind ?? "engine"),
        description: String(c.description ?? ""),
        error: c.error === undefined ? undefined : String(c.error),
      };
    })
    .sort((a, b) => a.id.localeCompare(b.id));

  return {
    manifest_found: Boolean(d.manifest_found),
    manifest_version: String(d.manifest_version ?? ""),
    coverage,
    autonomy: rec(d.autonomy),
    event_bus: rec(d.event_bus),
    capabilities,
    unwired: Array.isArray(d.unwired) ? (d.unwired as unknown[]).map(String) : [],
    generated_at: String(d.generated_at ?? ""),
  };
}
