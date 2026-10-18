import { ApiClientError, apiUrl, createApiClient, GATEWAY_BASE } from "./api-client";

/**
 * The capability scoping client: `GET`/`PUT /api/bots/{name}/capabilities`.
 *
 * WHY THIS IS NOT A PLAIN `apiFetch` CALL. The route refuses an over-ceiling
 * grant with a structured 422 — `detail.message` plus `detail.violations` —
 * and the shared `ApiClientError` deliberately carries only STRING details
 * (an object detail is not copied out of it). The scoping UI exists to show
 * every violation, so this module captures the raw error body (keyed per
 * request, the same mechanism `bot-model-config.ts` uses) and rethrows it as
 * a `CapabilityRefusalError` when the body actually holds violations.
 *
 * The mapper follows the page's honesty contract: absent is `null`, never a
 * fabricated `[]`, `0` or `false`. A grant the server did not report reads
 * "not reported" on screen, not "no capabilities".
 */

/** One entry of the server's capability vocabulary. */
export interface CapabilityCatalogEntry {
  /** The wire name, verbatim. */
  name: string;
  /**
   * Its rank. `null` when the server did not report one — which must not read
   * as rank 0 (that would paint every entry as within any ceiling).
   */
  rank: number | null;
}

/** The server-owned ceiling the grant is intersected with. */
export interface CapabilityCeiling {
  schema_version: number | null;
  /** Grants with rank ≤ this are admissible; `null` when the server omitted it. */
  max_capability_rank: number | null;
  allowed_capabilities: string[] | null;
  max_live_profiles: number | null;
  max_total_profiles: number | null;
  source: string | null;
}

/** The recorded re-scope: who, why, and what the grant was before. */
export interface CapabilityRescope {
  by: string | null;
  reason: string | null;
  /** `null` when the server did not record the previous grant. */
  previous: string[] | null;
}

/** The declared surface shown beside the grant — NOT written by this route. */
export interface CapabilityDeclared {
  toolsets: string[] | null;
  skills: string[] | null;
  mcp_servers: string[] | null;
  memory_scope: string | null;
  model: string | null;
}

/** What a successful save changed, as the route itself answered it. */
export interface CapabilityChange {
  previous_capabilities: string[] | null;
  effective_capabilities: string[] | null;
  epoch_before: string | null;
  /**
   * Whether the fingerprint moved. `false` for a grant-only save is the
   * honest answer: `capabilities` is deliberately not an epoch input — the
   * fingerprint covers the run-shaping surface (toolsets/skills/model).
   */
  epoch_moved: boolean;
  version_before: number | null;
  /** The server's own explanation, rendered verbatim when present. */
  note: string | null;
}

/** The whole scoping read (`GET`) or save answer (`PUT`). */
export interface CapabilityScope {
  bot: string;
  /** What the profile stores. `null` when the server omitted it. */
  requested: string[] | null;
  /** What the live ceiling admitted. */
  effective: string[] | null;
  /** `requested − effective`, disclosed rather than folded away. */
  denied: string[] | null;
  demoted: string[] | null;
  last_rescope: CapabilityRescope | null;
  ceiling: CapabilityCeiling | null;
  /** The vocabulary the editor lists. `[]` only when the server sent none. */
  known: CapabilityCatalogEntry[];
  declared: CapabilityDeclared | null;
  epoch: string | null;
  version: number | null;
  /** Per-domain enforcement disclosure, rendered verbatim. */
  enforcement: Record<string, string>;
  /** Present only on a save's answer; `null` on a plain read. */
  change: CapabilityChange | null;
}

const asString = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const asNumber = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const asList = (v: unknown): string[] | null =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : null;
const asRow = (v: unknown): Record<string, unknown> | null =>
  v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;

function normalizeCeiling(raw: unknown): CapabilityCeiling | null {
  const row = asRow(raw);
  if (!row) return null;
  return {
    schema_version: asNumber(row.schema_version),
    max_capability_rank: asNumber(row.max_capability_rank),
    allowed_capabilities: asList(row.allowed_capabilities),
    max_live_profiles: asNumber(row.max_live_profiles),
    max_total_profiles: asNumber(row.max_total_profiles),
    source: asString(row.source),
  };
}

function normalizeRescope(raw: unknown): CapabilityRescope | null {
  const row = asRow(raw);
  if (!row) return null;
  return { by: asString(row.by), reason: asString(row.reason), previous: asList(row.previous) };
}

function normalizeDeclared(raw: unknown): CapabilityDeclared | null {
  const row = asRow(raw);
  if (!row) return null;
  return {
    toolsets: asList(row.toolsets),
    skills: asList(row.skills),
    mcp_servers: asList(row.mcp_servers),
    memory_scope: asString(row.memory_scope),
    model: asString(row.model),
  };
}

function normalizeChange(raw: unknown): CapabilityChange | null {
  const row = asRow(raw);
  if (!row) return null;
  return {
    previous_capabilities: asList(row.previous_capabilities),
    effective_capabilities: asList(row.effective_capabilities),
    epoch_before: asString(row.epoch_before),
    epoch_moved: row.epoch_moved === true,
    version_before: asNumber(row.version_before),
    note: asString(row.note),
  };
}

/**
 * Map the wire body onto a `CapabilityScope` (exported for tests).
 *
 * Throws when the payload names no bot: an unreadable scope must fail the
 * section's read (which the page renders as "could not load this section")
 * rather than render an empty grant that reads as "this bot can do nothing".
 */
export function normalizeCapabilityScope(raw: unknown): CapabilityScope {
  const row = asRow(raw);
  const bot = row ? asString(row.bot) : null;
  if (!row || !bot) throw new Error("The Gateway returned an unreadable capability scope.");
  const known = Array.isArray(row.known_capabilities)
    ? row.known_capabilities
        .map((entry) => asRow(entry))
        .filter((entry): entry is Record<string, unknown> => entry !== null)
        .map((entry) => ({ name: typeof entry.name === "string" ? entry.name : "", rank: asNumber(entry.rank) }))
        .filter((entry) => entry.name !== "")
    : [];
  const enforcement: Record<string, string> = {};
  const rawEnforcement = asRow(row.enforcement);
  if (rawEnforcement) {
    for (const [key, value] of Object.entries(rawEnforcement)) {
      if (typeof value === "string") enforcement[key] = value;
    }
  }
  return {
    bot,
    requested: asList(row.requested_capabilities),
    effective: asList(row.effective_capabilities),
    denied: asList(row.denied_capabilities),
    demoted: asList(row.demoted_capabilities),
    last_rescope: normalizeRescope(row.last_rescope),
    ceiling: normalizeCeiling(row.ceiling),
    known,
    declared: normalizeDeclared(row.declared),
    epoch: asString(row.epoch),
    version: asNumber(row.version),
    enforcement,
    change: normalizeChange(row.changed),
  };
}

/**
 * A structured refusal from the scoping route: the ceiling (or the creator's
 * grant) would not admit the request and NOTHING was written.
 *
 * Separate from `ApiClientError` for the same reason
 * `ModelConfigValidationError` is: the shared client keeps only string
 * details, and without this the panel could show only
 * "Request failed (HTTP 422)." — dropping exactly the per-rule violations the
 * operator needs to fix the request.
 */
export class CapabilityRefusalError extends Error {
  readonly violations: string[];
  readonly status: number;

  constructor(message: string, violations: string[], status: number) {
    super(message || "The capability grant was refused.");
    this.name = "CapabilityRefusalError";
    this.violations = violations;
    this.status = status;
  }
}

/** Pull `detail.violations` out of a captured 422 body (exported for tests). */
export function violationsFromBody(body: unknown): string[] {
  const detail = asRow(body)?.detail;
  const row = asRow(detail);
  if (!row) return [];
  return asList(row.violations) ?? [];
}

/** Pull `detail.message` (or a string detail) out of a captured body (exported for tests). */
export function messageFromBody(body: unknown): string | null {
  const detail = asRow(body)?.detail;
  if (typeof detail === "string" && detail) return detail;
  const row = asRow(detail);
  return row ? asString(row.message) : null;
}

export interface CapabilityClientOptions {
  baseUrl?: string;
  fetch?: typeof fetch;
  getCookie?: () => string;
}

export interface CapabilityClient {
  get(name: string): Promise<CapabilityScope>;
  /** The one scoping write: the requested grant, plus the recorded reason. */
  put(name: string, capabilities: string[], reason?: string): Promise<CapabilityScope>;
}

/**
 * Build a client. Each instance owns its own error-body capture so two panels
 * cannot read each other's 422 payloads.
 */
export function createCapabilityClient(options: CapabilityClientOptions = {}): CapabilityClient {
  const netFetch = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> =>
    options.fetch ? options.fetch(input, init) : globalThis.fetch(input, init);

  // Error bodies keyed by `METHOD url`; the shared client consumes its own
  // copy of the body before throwing, so this is the only place the raw
  // `{message, violations}` payload survives.
  const errorBodies = new Map<string, unknown>();

  const capturingFetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const response = await netFetch(input, init);
    const key = `${(init?.method || "GET").toUpperCase()} ${String(input)}`;
    if (response.ok) {
      errorBodies.delete(key);
      return response;
    }
    try {
      errorBodies.set(key, await response.clone().json());
    } catch {
      errorBodies.set(key, null);
    }
    return response;
  };

  const request = createApiClient({
    fetch: capturingFetch,
    getCookie: options.getCookie,
    baseUrl: options.baseUrl,
  });

  const base = options.baseUrl ?? GATEWAY_BASE;
  const path = (name: string) => `/bots/${encodeURIComponent(name)}/capabilities`;

  /** Run one call, converting a captured refusal into a typed error. */
  async function call(method: string, relative: string, init: RequestInit = {}): Promise<CapabilityScope> {
    const key = `${method} ${apiUrl(relative, base)}`;
    try {
      const response = await request(relative, { ...init, method });
      return normalizeCapabilityScope(await response.json());
    } catch (err) {
      const body = errorBodies.get(key);
      errorBodies.delete(key);
      const violations = violationsFromBody(body);
      if (violations.length) {
        throw new CapabilityRefusalError(
          messageFromBody(body) || "",
          violations,
          err instanceof ApiClientError ? err.status : 422,
        );
      }
      throw err;
    }
  }

  return {
    get: (name) => call("GET", path(name)),
    put: (name, capabilities, reason) =>
      call("PUT", path(name), {
        headers: { "Content-Type": "application/json" },
        // The reason is optional; the server's own default names this panel
        // when the operator states none.
        body: JSON.stringify(reason && reason.trim() ? { capabilities, reason: reason.trim() } : { capabilities }),
      }),
  };
}

/** The default client components import. */
export const capabilityClient = createCapabilityClient();

export function fetchBotCapabilities(name: string): Promise<CapabilityScope> {
  return capabilityClient.get(name);
}

export function rescopeBotCapabilities(name: string, capabilities: string[], reason?: string): Promise<CapabilityScope> {
  return capabilityClient.put(name, capabilities, reason);
}

/**
 * Where one catalog entry sits relative to the live ceiling.
 *
 * `unknown` is a real answer: with no ceiling on the wire (or no rank for the
 * entry), the editor must not claim a limit it cannot see. The server still
 * refuses an over-ceiling save — this only marks the picks the editor can
 * already tell will be refused.
 */
export function capabilityStanding(
  entry: CapabilityCatalogEntry,
  ceiling: CapabilityCeiling | null,
): "within" | "above" | "unknown" {
  const max = ceiling?.max_capability_rank ?? null;
  if (max === null || entry.rank === null) return "unknown";
  return entry.rank > max ? "above" : "within";
}

/**
 * The one-line change record rendered after a save, or `null` on a plain read.
 *
 * The epoch half is deliberately honest: `capabilities` is not an epoch input
 * (a recorded design decision — the fingerprint covers the run-shaping
 * surface), so a grant-only save says "epoch unchanged" instead of implying a
 * new epoch. The server's own `note` is rendered verbatim beside this line.
 */
export function capabilityChangeLine(scope: CapabilityScope): string | null {
  const change = scope.change;
  if (!change) return null;
  const parts = [`v${change.version_before ?? "?"} → v${scope.version ?? "?"}`];
  parts.push(
    change.epoch_moved ? `epoch ${change.epoch_before ?? "?"} → ${scope.epoch ?? "?"}` : "epoch unchanged",
  );
  return parts.join(" · ");
}
