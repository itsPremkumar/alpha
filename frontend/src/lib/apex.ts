import { get, send } from "./http";

/**
 * APEX — the executive control plane, as the UI sees it.
 *
 * ## The one rule this file exists to keep
 *
 * Every field is `number | string | boolean | null` and **`null` means "not
 * measured"**. It is never collapsed to `0`, `""` or `false`.
 *
 * The backend goes to real trouble to preserve that distinction: an unreadable
 * APEX store reports `count: null` rather than an empty list, and an
 * unattached subsystem reports `available: false` with a `reason`. A mapper here
 * that did `num(v, 0)` would turn "we could not read the store" into "there are
 * zero sessions" — and an operator would read the second as a working system.
 *
 * That is the same rule `integration.ts` applies to the loop counters, for the
 * same reason.
 */

type Rec = Record<string, unknown>;

function rec(v: unknown): Rec {
  return v && typeof v === "object" ? (v as Rec) : {};
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/** Optional string: `null` when absent, so "unmeasured" survives the mapping. */
function optStr(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

/** Optional number. `null` for absent/NaN — never 0. */
function optNum(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  return null;
}

function bool(v: unknown): boolean {
  return v === true;
}

/** A block the backend could not read, with the reason it could not. */
export interface ApexUnavailable {
  available: false;
  reason: string;
}

export type ApexBlock<T> = ({ available: true } & T) | ApexUnavailable;

export type ApexProfile = "off" | "assist" | "autonomous" | "apex_max";

export const APEX_PROFILES: readonly ApexProfile[] = ["off", "assist", "autonomous", "apex_max"];

/** Budget ceilings for the active contract. Every field is required by the schema. */
export interface ApexBudget {
  max_active_agents: number;
  max_parallel_tasks: number;
  max_delegation_depth: number;
  max_replans: number;
  max_retries_per_failure_class: number;
  max_runtime_minutes: number;
  max_tool_calls: number;
}

/** One delegated policy kernel. APEX records where each decision actually goes. */
export interface ApexPolicySite {
  name: string;
  module: string;
}

/**
 * The contract projection.
 *
 * `policy_sites_missing` is the field that matters most on this surface. APEX is
 * not a policy kernel — it delegates every verdict — so a delegated kernel that
 * is absent is an *unenforced boundary*, and the operator needs to see that
 * rather than infer coverage from the panel rendering at all.
 */
export interface ApexContract {
  profile: ApexProfile;
  enabled: boolean;
  digest: string;
  budget: ApexBudget;
  emergency_stop: boolean;
  authority_granted: string[];
  protected_actions: Record<string, string>;
  policy_sites: ApexPolicySite[];
  policy_sites_live: string[];
  policy_sites_missing: string[];
  note: string;
}

/** Fleet control, read through the emergency stop's only home. */
export interface ApexFleet {
  mode: string;
  generation: number | null;
  reason: string;
  estop_sentinel: boolean;
  admits_work: boolean;
}

/** Session counts. `total` is `null` when the store could not be read. */
export interface ApexSessions {
  total: number | null;
  by_state: Record<string, number>;
  active: number | null;
  terminal: number | null;
}

/**
 * One invariant row.
 *
 * `live` reflects whether the named enforcement module imports *and* exposes the
 * named symbol. A missing site reports `live: false` with its reason; it is
 * never shown as a pass.
 */
export interface ApexInvariant {
  id: string;
  statement: string;
  live: boolean;
  module: string;
  symbol: string;
  reason: string;
}

export interface ApexInvariantReport {
  declared: number;
  live: number;
  all_live: boolean;
  invariants: ApexInvariant[];
}

export interface ApexSessionSummary {
  session_id: string;
  objective: string;
  state: string;
  profile: string;
  contract_digest: string;
  mission_id: string;
  contract_drift: boolean;
  blocked_reason: string;
  cycle_count: number;
  acceptance_criteria: string[];
  updated_at: number | null;
}

export interface ApexStatus {
  schema: string;
  contract: ApexBlock<ApexContract>;
  fleet: ApexBlock<ApexFleet>;
  sessions: ApexBlock<ApexSessions>;
  /** Present only when requested; a UI poll passes `include_invariants=false`. */
  invariants?: ApexInvariantReport;
  session?: ApexBlock<ApexSessionSummary>;
}

/** One step of a cycle — why the decision came out the way it did. */
export interface ApexCycleStep {
  name: string;
  outcome: string;
  detail: string;
}

export interface ApexDecision {
  action: string;
  reason: string;
  confidence: number;
  blocked: boolean;
}

export interface ApexCycleResult {
  session_id: string;
  decision: ApexDecision;
  steps: ApexCycleStep[];
  state_before: string;
  state_after: string;
  changed: boolean;
}

function mapContract(v: unknown): ApexContract {
  const r = rec(v);
  const budget = rec(r.budget);
  const controls = rec(r.controls);
  const protectedActions = rec(r.protected_actions);
  const sites = rec(r.policy_sites);
  const policySites: ApexPolicySite[] = Object.entries(sites).map(([name, dotted]) => ({
    name,
    module: str(dotted),
  }));
  return {
    profile: (optStr(r.profile) ?? "off") as ApexProfile,
    enabled: bool(r.enabled),
    digest: str(r.digest),
    budget: {
      max_active_agents: optNum(budget.max_active_agents) ?? 0,
      max_parallel_tasks: optNum(budget.max_parallel_tasks) ?? 0,
      max_delegation_depth: optNum(budget.max_delegation_depth) ?? 0,
      max_replans: optNum(budget.max_replans) ?? 0,
      max_retries_per_failure_class: optNum(budget.max_retries_per_failure_class) ?? 0,
      max_runtime_minutes: optNum(budget.max_runtime_minutes) ?? 0,
      max_tool_calls: optNum(budget.max_tool_calls) ?? 0,
    },
    emergency_stop: bool(controls.emergency_stop),
    authority_granted: Array.isArray(r.authority_granted) ? (r.authority_granted as unknown[]).map(String) : [],
    protected_actions: Object.fromEntries(
      Object.entries(protectedActions).map(([k, v]) => [k, String(v)]),
    ),
    policy_sites: policySites,
    policy_sites_live: Array.isArray(r.policy_sites_live) ? (r.policy_sites_live as unknown[]).map(String) : [],
    policy_sites_missing: Array.isArray(r.policy_sites_missing)
      ? (r.policy_sites_missing as unknown[]).map(String)
      : [],
    note: str(r.note),
  };
}

function mapFleet(v: unknown): ApexFleet {
  const r = rec(v);
  return {
    mode: str(r.mode),
    generation: optNum(r.generation),
    reason: str(r.reason),
    estop_sentinel: bool(r.estop_sentinel),
    admits_work: bool(r.admits_work),
  };
}

function mapSessions(v: unknown): ApexSessions {
  const r = rec(v);
  const byState = rec(r.by_state);
  return {
    total: optNum(r.total),
    by_state: Object.fromEntries(Object.entries(byState).map(([k, n]) => [k, optNum(n) ?? 0])),
    active: optNum(r.active),
    terminal: optNum(r.terminal),
  };
}

/**
 * Split an `ApexBlock`. A block the backend could not read becomes the
 * unavailable arm with its real reason — it never becomes a zeroed success.
 */
function block<T>(v: unknown, map: (inner: Rec) => T): ({ available: true } & T) | ApexUnavailable {
  const r = rec(v);
  if (r.available !== true) {
    return { available: false, reason: optStr(r.reason) ?? "the Gateway did not say why" };
  }
  return { available: true, ...map(r) };
}

export function mapStatus(v: unknown): ApexStatus {
  const r = rec(v);
  const out: ApexStatus = {
    schema: str(r.schema),
    contract: block(r.contract, (c) => mapContract(c)),
    fleet: block(r.fleet, (f) => mapFleet(f)),
    sessions: block(r.sessions, (s) => mapSessions(s)),
  };
  if (r.invariants !== undefined && r.invariants !== null) {
    const inv = rec(r.invariants);
    out.invariants = {
      declared: optNum(inv.declared) ?? 0,
      live: optNum(inv.live) ?? 0,
      all_live: bool(inv.all_live),
      invariants: (Array.isArray(inv.invariants) ? inv.invariants : []).map((row) => {
        const item = rec(row);
        return {
          id: str(item.id),
          statement: str(item.statement),
          live: bool(item.live),
          module: str(item.module),
          symbol: str(item.symbol),
          reason: str(item.reason),
        };
      }),
    };
  }
  if (r.session !== undefined && r.session !== null) {
    const s = rec(r.session);
    out.session = block(s, (inner) => ({
      session_id: str(inner.session_id),
      objective: str(inner.objective),
      state: str(inner.state),
      profile: str(inner.profile),
      contract_digest: str(inner.contract_digest),
      mission_id: str(inner.mission_id),
      contract_drift: bool(inner.contract_drift),
      blocked_reason: str(inner.blocked_reason),
      cycle_count: optNum(inner.cycle_count) ?? 0,
      acceptance_criteria: Array.isArray(inner.acceptance_criteria)
        ? (inner.acceptance_criteria as unknown[]).map(String)
        : [],
      updated_at: optNum(inner.updated_at),
    }));
  }
  return out;
}

function mapCycle(v: unknown): ApexCycleResult {
  const r = rec(v);
  const d = rec(r.decision);
  return {
    session_id: str(r.session_id),
    decision: {
      action: str(d.action),
      reason: str(d.reason),
      confidence: optNum(d.confidence) ?? 0,
      blocked: bool(d.blocked),
    },
    steps: (Array.isArray(r.steps) ? r.steps : []).map((row) => {
      const item = rec(row);
      return { name: str(item.name), outcome: str(item.outcome), detail: str(item.detail) };
    }),
    state_before: str(r.state_before),
    state_after: str(r.state_after),
    changed: bool(r.changed),
  };
}

/** `GET /apex/status`. `include_invariants=false` keeps a poll loop cheap. */
export async function fetchApexStatus(opts?: {
  sessionId?: string;
  includeInvariants?: boolean;
}): Promise<ApexStatus> {
  const params = new URLSearchParams();
  if (opts?.sessionId) params.set("session_id", opts.sessionId);
  if (opts?.includeInvariants === false) params.set("include_invariants", "false");
  const query = params.toString();
  return mapStatus(await get<Rec>(`/apex/status${query ? `?${query}` : ""}`));
}

/** `GET /apex/policy`. The panel that explains what a profile actually grants. */
export async function fetchApexPolicy(profile: ApexProfile): Promise<ApexContract> {
  return mapContract(await get<Rec>(`/apex/policy?profile=${encodeURIComponent(profile)}`));
}

/** `GET /apex/invariants`. Declared and live travel together, never collapsed. */
export async function fetchApexInvariants(): Promise<ApexInvariantReport> {
  const raw = rec(await get<Rec>("/apex/invariants"));
  return mapStatus({ invariants: raw }).invariants as ApexInvariantReport;
}

export interface CreateApexSession {
  objective: string;
  profile?: ApexProfile;
  acceptance_criteria?: string[];
  mission_id?: string;
  thread_id?: string;
}

/** `POST /apex/sessions`. Admin-gated; a widening request answers 422 named. */
export async function createApexSession(input: CreateApexSession): Promise<{ session_id: string }> {
  const body = await send<Rec>("/apex/sessions", "POST", { profile: "autonomous", ...input });
  return { session_id: str(rec(body.session).session_id) };
}

/** `POST /apex/sessions/{id}/cycle`. Admin-gated; executes no domain work. */
export async function runApexCycle(sessionId: string): Promise<ApexCycleResult> {
  return mapCycle(await send<Rec>(`/apex/sessions/${encodeURIComponent(sessionId)}/cycle`, "POST"));
}

/** `POST /apex/sessions/{id}/steer`. A mission constraint, never a prompt rewrite. */
export async function steerApexSession(sessionId: string, instruction: string): Promise<void> {
  await send<Rec>(`/apex/sessions/${encodeURIComponent(sessionId)}/steer`, "POST", { instruction });
}