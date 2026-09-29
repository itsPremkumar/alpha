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

/**
 * One background loop's measured runtime state, from
 * `GET /ops/integration-health -> autonomy.loops.<id>`.
 *
 * Every counter is `number | null`. The Gateway sends real zeroes for a loop
 * that has simply never run, and omits a field entirely on a build that does not
 * report it; those are different claims and collapsing them is how "0 runs"
 * starts meaning "we did not measure".
 *
 * `enabled` is the one that matters most. `coverage.loops` is a *manifest* fact
 * (is the loop declared and wired), and all eight loops reading `8 / 8 wired`
 * with a green "complete" badge said nothing at all about whether any of them
 * is switched on. On the measured deployment all eight are `enabled: false`.
 */
export interface AutonomyLoopStatus {
  id: string;
  description: string;
  enabled: boolean;
  running: boolean | null;
  parked: boolean | null;
  park_reason: string;
  runs: number | null;
  failures: number | null;
  last_run_at: number | null;
  last_duration_seconds: number | null;
  last_error: string;
  last_summary: string;
  task_alive: boolean | null;
}

/**
 * `reported` is the distinction the UI needs and a bare object cannot carry:
 * false means the payload had no `autonomy` key (nothing was measured), true
 * means the server measured and answered — possibly with an empty loop set.
 */
export interface AutonomyStatus {
  reported: boolean;
  enabled: boolean | null;
  started_at: number | null;
  loops: AutonomyLoopStatus[];
}

export interface EventBusStatus {
  reported: boolean;
  enabled: boolean | null;
  published: number | null;
  dropped_total: number | null;
  subscribers: number | null;
}

export interface PeerNetworkSummary {
  reported: boolean;
  enabled: boolean | null;
}

export interface IntegrationHealth {
  manifest_found: boolean;
  manifest_version: string;
  coverage: Record<string, ClassCoverage>;
  autonomy: AutonomyStatus;
  event_bus: EventBusStatus;
  peer_network: PeerNetworkSummary;
  capabilities: CapabilityStatus[];
  unwired: string[];
  generated_at: string;
}

/** A finite number, or `null` when the server sent something that is not one. */
function optNum(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function optBool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

function optStr(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/**
 * Parse `autonomy` into per-loop rows.
 *
 * The previous mapper kept the whole block as an opaque `Record<string, unknown>`
 * that no view ever read, so ~110 measured fields — including whether every loop
 * is switched off — reached the client and were then dropped on the floor.
 */
function toAutonomy(raw: unknown): AutonomyStatus {
  if (raw === undefined || raw === null || typeof raw !== "object") {
    return { reported: false, enabled: null, started_at: null, loops: [] };
  }
  const autonomy = rec(raw);
  const loops = Object.entries(rec(autonomy.loops)).map(([id, value]) => {
    const loop = rec(value);
    return {
      id,
      description: optStr(loop.description),
      enabled: Boolean(loop.enabled),
      running: optBool(loop.running),
      parked: optBool(loop.parked),
      park_reason: optStr(loop.park_reason),
      runs: optNum(loop.runs),
      failures: optNum(loop.failures),
      last_run_at: optNum(loop.last_run_at),
      last_duration_seconds: optNum(loop.last_duration_seconds),
      last_error: optStr(loop.last_error),
      last_summary: optStr(loop.last_summary),
      task_alive: optBool(loop.task_alive),
    };
  });
  return {
    reported: true,
    enabled: optBool(autonomy.enabled),
    started_at: optNum(autonomy.started_at),
    loops: loops.sort((a, b) => a.id.localeCompare(b.id)),
  };
}

function toEventBus(raw: unknown): EventBusStatus {
  if (raw === undefined || raw === null || typeof raw !== "object") {
    return { reported: false, enabled: null, published: null, dropped_total: null, subscribers: null };
  }
  const bus = rec(raw);
  return {
    reported: true,
    enabled: optBool(bus.enabled),
    published: optNum(bus.published),
    dropped_total: optNum(bus.dropped_total),
    // `subscribers` is a list; its length is the only thing the header can say.
    subscribers: Array.isArray(bus.subscribers) ? bus.subscribers.length : optNum(bus.subscribers),
  };
}

/**
 * Only the on/off flag. The rest of the block is the peer network's identity
 * card, pairing secret and filesystem paths — the `peer_network` view owns
 * those, and a credentials surface must not be duplicated into a wiring panel.
 */
function toPeerNetwork(raw: unknown): PeerNetworkSummary {
  if (raw === undefined || raw === null || typeof raw !== "object") {
    return { reported: false, enabled: null };
  }
  return { reported: true, enabled: optBool(rec(raw).enabled) };
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
    autonomy: toAutonomy(d.autonomy),
    event_bus: toEventBus(d.event_bus),
    peer_network: toPeerNetwork(d.peer_network),
    capabilities,
    unwired: Array.isArray(d.unwired) ? (d.unwired as unknown[]).map(String) : [],
    generated_at: String(d.generated_at ?? ""),
  };
}
