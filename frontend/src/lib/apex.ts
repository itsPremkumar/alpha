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

/**
 * The rungs APEX may be *enabled* at — what the enable picker offers.
 *
 * `off` is a state, not a rung: `POST /apex/enable` refuses it by name
 * ("use disable() for the 'off' profile; enabling requires a real profile").
 * This list is the single answer to "what may the operator turn it on to", so
 * the picker's options and the value the toggle adopts cannot disagree.
 */
export const ENABLE_PROFILES: readonly ApexProfile[] = ["assist", "autonomous", "apex_max"];

/**
 * The profile the enable picker should adopt from a server mode read, or
 * `null` when the server's answer is not a rung it may be enabled at.
 *
 * This exists because adopting raw was a defect. A scope nobody has enabled
 * yet reads as `off`, and the picker — whose option list deliberately excludes
 * `off` — adopted it as its controlled value anyway: the `<select>` then
 * *displayed* `assist` (the first option, since no option matched) while the
 * state behind it held `off`, so a first-ever "Turn on" posted
 * `{"profile":"off"}` and failed 422. The most common path on a fresh install
 * was the broken one.
 *
 * Two rules follow, both about refusing to manufacture a claim:
 *
 * - **`off` is never adopted.** It is what the switch already reports; the
 *   picker keeps the rung it was showing (or its default), which is the rung
 *   the next enable will actually send.
 * - **An unknown profile is never snapped.** A record written by a newer build
 *   could name a rung this build does not offer; coercing it to a known one
 *   would enable a *different* authority than the record names, so it falls
 *   through to the default and the record's `load_note` is what surfaces it.
 */
export function profileToAdopt(modeProfile: string): ApexProfile | null {
  return (ENABLE_PROFILES as readonly string[]).includes(modeProfile) ? (modeProfile as ApexProfile) : null;
}

/** One row of the composer's APEX menu: the rung, its name, and what it costs. */
export interface ApexRung {
  value: ApexProfile;
  label: string;
  hint: string;
}

const APEX_RUNG_LABELS: Record<ApexProfile, string> = {
  off: "Off",
  assist: "Assist",
  autonomous: "Autonomous",
  apex_max: "Apex Max",
};

/**
 * One line per rung, each a claim `alpha.apex.contract` actually makes, so the
 * menu cannot promise an authority the contract does not grant:
 *
 * - `off` — every authority false, every budget zero.
 * - `assist` — everything except the seven capability-changing keys
 *   (`terminal`, `git`, `mcp`, `a2a`, `subagents`, `swarm`, `browser`).
 * - `autonomous` / `apex_max` — every authority true; they differ only in
 *   budgets and protected actions, never in what may be reached.
 *
 * `Record<ApexProfile, string>` is the drift guard: a profile added to
 * `ApexProfile` without a hint here is a type error, not an empty menu row.
 */
const APEX_RUNG_HINTS: Record<ApexProfile, string> = {
  off: "APEX is not in force; Alpha executes requests normally.",
  assist: "Everything except terminal, git, mcp, a2a, subagents, swarm and browser. Smallest budgets.",
  autonomous: "Every authority, mid-size budgets.",
  apex_max: "Every authority, widest budgets; protected actions still need a human.",
};

/**
 * The menu, derived from `APEX_PROFILES` rather than listed again, so a new
 * profile cannot ship with a rung the menu forgot — or with two menus that
 * disagree about how many rungs there are.
 */
export const APEX_RUNGS: readonly ApexRung[] = APEX_PROFILES.map((value) => ({
  value,
  label: APEX_RUNG_LABELS[value],
  hint: APEX_RUNG_HINTS[value],
}));

/**
 * What the composer chip discloses, in words, about what its menu does.
 *
 * The sentence exists because the control is reachable from the chat screen,
 * where "pick a profile and everything is automated" is the natural reading and
 * the false one: nothing in the chat or run path consults the APEX mode. It
 * sets the autonomy contract for the scope — the same switch the APEX panel
 * shows — and a run still starts when a message is sent.
 */
export const APEX_COMPOSER_DISCLOSURE =
  "Sets the APEX autonomy contract for this scope — the same switch the APEX panel shows. It does not start a run: send a message as usual. A higher profile raises budgets only, never the emergency stop or a protected action.";

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
  /** Absent is not zero: an unreadable count is `null`, never a measured `0`. */
  cycle_count: number | null;
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
      cycle_count: optNum(inner.cycle_count),
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

/**
 * `POST /apex/sessions/{id}/cycle`. Admin-gated; executes no domain work.
 *
 * The body is `{}` rather than absent: the route declares `payload: CycleRequest`
 * with no default, so FastAPI answers 422 `loc=["body"]` "Field required" to a
 * request with no body at all — every click of the panel's button was refused
 * before `run_cycle` was reached. `{}` is what the backend's own tests post; the
 * route reads only `all_sessions` from it.
 */
export async function runApexCycle(sessionId: string): Promise<ApexCycleResult> {
  return mapCycle(await send<Rec>(`/apex/sessions/${encodeURIComponent(sessionId)}/cycle`, "POST", {}));
}

/** `POST /apex/sessions/{id}/steer`. A mission constraint, never a prompt rewrite. */
export async function steerApexSession(sessionId: string, instruction: string): Promise<void> {
  await send<Rec>(`/apex/sessions/${encodeURIComponent(sessionId)}/steer`, "POST", { instruction });
}

// --------------------------------------------------------------------------- //
// Session control — pause / resume / stop (spec §3, §33)
// --------------------------------------------------------------------------- //
//
// The three verbs move *this conversation's* session. They are a control
// plane, not a second lifecycle owner: a pause parks the executive's own
// decision loop and reaches nothing about a run already admitted to
// `RunManager`, and the `stop` route says so in the server's own note rather
// than implying work was interrupted.

/** One APEX session record, as `session.to_dict()` sends it. */
export interface ApexSessionRecord {
  session_id: string;
  owner: string;
  objective: string;
  state: string;
  profile: string;
  contract_digest: string;
  mission_id: string;
  thread_id: string;
  blocked_reason: string;
  cycle_count: number | null;
  acceptance_criteria: string[];
  created_at: number | null;
  updated_at: number | null;
}

function mapSessionRecord(v: unknown): ApexSessionRecord {
  const r = rec(v);
  return {
    session_id: str(r.session_id),
    owner: str(r.owner),
    objective: str(r.objective),
    state: str(r.state),
    profile: str(r.profile),
    contract_digest: str(r.contract_digest),
    mission_id: str(r.mission_id),
    thread_id: str(r.thread_id),
    blocked_reason: str(r.blocked_reason),
    cycle_count: optNum(r.cycle_count),
    acceptance_criteria: Array.isArray(r.acceptance_criteria) ? (r.acceptance_criteria as unknown[]).map(String) : [],
    created_at: optNum(r.created_at),
    updated_at: optNum(r.updated_at),
  };
}

export type ApexControlAction = "pause" | "resume" | "stop";

export interface ApexControlOutcome {
  /** Whether *this* call changed anything. A second pause is `false`. */
  applied: boolean;
  /** Why nothing changed, when `applied` is false (e.g. "already paused"). */
  reason: string;
  /** The session the server reported after the verb — never a local guess. */
  session: ApexSessionRecord;
  /** The stop route's boundary note; null when the route carried none. */
  note: string | null;
}

function mapControlOutcome(v: unknown): ApexControlOutcome {
  const r = rec(v);
  return {
    applied: bool(r.applied),
    reason: optStr(r.reason) ?? "",
    session: mapSessionRecord(r.session),
    note: optStr(r.note),
  };
}

/**
 * `POST /apex/{pause,resume,stop}`. Admin-gated server-side; a refusal here
 * rejects with the gateway's reason (403 for a non-admin, 404 when no
 * session is bound to the scope, 409 for a terminal one).
 *
 * The result carries the *server's* session record: the caller renders what
 * the transition produced, never what the click implied.
 */
export async function setApexControl(
  action: ApexControlAction,
  opts?: { scopeKey?: string },
): Promise<ApexControlOutcome> {
  const body: Record<string, string> = {};
  if (opts?.scopeKey) body.scope_key = opts.scopeKey;
  return mapControlOutcome(await send<Rec>(`/apex/${action}`, "POST", body));
}

// --------------------------------------------------------------------------- //
// The approval gate — the operator's verdict on parked work
// --------------------------------------------------------------------------- //
//
// A blocked cycle parks the session and creates one pending approval naming
// the blocker. Only an operator verdict moves it: `approved` un-parks the
// session, `rejected` keeps it parked with the refusal on record. The client
// keeps that asymmetry visible — a rejection is not a "close" that makes the
// row disappear into success.

export interface ApexApprovalRecord {
  approval_id: string;
  session_id: string;
  /** `pending` until an operator decides; then `approved` / `rejected`. */
  status: string;
  /** The blocker, in the requester's words. */
  note: string;
  requester: string;
  /** The deciding operator; empty while the ask is pending. */
  operator: string;
  requested_at: number | null;
  decided_at: number | null;
}

function mapApprovalRecord(v: unknown): ApexApprovalRecord {
  const r = rec(v);
  return {
    approval_id: str(r.approval_id),
    session_id: str(r.session_id),
    status: str(r.status),
    note: str(r.note),
    requester: str(r.requester),
    operator: str(r.operator),
    requested_at: optNum(r.requested_at),
    decided_at: optNum(r.decided_at),
  };
}

export interface ApexApprovals {
  available: boolean;
  /** The server's reason when the store could not be read. */
  reason: string;
  /** The whole matching backlog — not the length of `approvals`. */
  count: number | null;
  /** Pending verdicts across the whole backlog, not just the rows shown. */
  pending: number | null;
  /** How many rows the server actually sent. Absent → `null`, never `count`. */
  returned: number | null;
  /** True when `approvals` is a bounded slice of a larger backlog. */
  truncated: boolean;
  approvals: ApexApprovalRecord[];
}

/**
 * `GET /apex/approvals`.
 *
 * `count` and `pending` are `null` — never `0` — when the store could not be
 * read: "we could not look" and "nothing is pending" lead to opposite
 * decisions, so the difference survives the mapping.
 *
 * The route bounds the returned list while counting the *whole* backlog, so
 * `returned`/`truncated` travel with it. Without them a panel showing 200 rows
 * beside `count: 500` would look internally inconsistent, and one that derived
 * the total from `approvals.length` would understate the gate by exactly the
 * rows it hid.
 */
export async function fetchApexApprovals(): Promise<ApexApprovals> {
  const r = rec(await get<Rec>("/apex/approvals"));
  const count = optNum(r.count);
  const returned = optNum(r.returned);
  return {
    available: bool(r.available),
    reason: optStr(r.reason) ?? "",
    count,
    pending: optNum(r.pending),
    returned,
    // Derived as well as declared: a Gateway that bounds the list but omits
    // the flag would otherwise render a 200-row panel with no mention of the
    // 300 it hid. Absent bounds on both sides mean "not bounded" → false.
    truncated: r.truncated === true || (returned !== null && count !== null && returned < count),
    approvals: (Array.isArray(r.approvals) ? r.approvals : []).map((row) => mapApprovalRecord(row)),
  };
}

export interface ApexApprovalDecision {
  approval: ApexApprovalRecord;
  /** True only when this verdict returned the session to ACTIVE. */
  resumed: boolean;
  session: ApexSessionRecord | null;
}

/**
 * `POST /apex/approvals/{id}/approve` / `.../reject`.
 *
 * The two verdicts are asymmetric on purpose: approve un-parks, reject leaves
 * the session blocked. The response says which happened (`resumed`) rather
 * than the UI inferring it from which button was pressed. A second verdict on
 * the same id is a 409 and rejects here with the server's reason.
 */
export async function decideApexApproval(
  approvalId: string,
  verdict: "approve" | "reject",
  opts?: { note?: string },
): Promise<ApexApprovalDecision> {
  const path = `/apex/approvals/${encodeURIComponent(approvalId)}/${verdict === "approve" ? "approve" : "reject"}`;
  const r = rec(await send<Rec>(path, "POST", { note: opts?.note ?? "" }));
  return {
    approval: mapApprovalRecord(r.approval),
    resumed: bool(r.resumed),
    session: r.session === undefined || r.session === null ? null : mapSessionRecord(r.session),
  };
}

// --------------------------------------------------------------------------- //
// Goals — the Goal Operating System's HTTP surface (spec §7, §33)
// --------------------------------------------------------------------------- //

export interface ApexGoalRecord {
  goal_id: string;
  objective: string;
  parent_goal_id: string;
  state: string;
  owner: string;
  priority: number | null;
  risk: string;
  plan_version: number | null;
  success_criteria: string[];
  constraints: string[];
  session_id: string;
  mission_id: string;
  current_strategy: string;
  blocked_reason: string;
  created_at: number | null;
  updated_at: number | null;
}

function mapGoalRecord(v: unknown): ApexGoalRecord {
  const r = rec(v);
  return {
    goal_id: str(r.goal_id),
    objective: str(r.objective),
    parent_goal_id: str(r.parent_goal_id),
    state: str(r.state),
    owner: str(r.owner),
    priority: optNum(r.priority),
    risk: str(r.risk),
    plan_version: optNum(r.plan_version),
    success_criteria: Array.isArray(r.success_criteria) ? (r.success_criteria as unknown[]).map(String) : [],
    constraints: Array.isArray(r.constraints) ? (r.constraints as unknown[]).map(String) : [],
    session_id: str(r.session_id),
    mission_id: str(r.mission_id),
    current_strategy: str(r.current_strategy),
    blocked_reason: str(r.blocked_reason),
    created_at: optNum(r.created_at),
    updated_at: optNum(r.updated_at),
  };
}

export interface ApexGoals {
  available: boolean;
  reason: string;
  /** `null` when the store could not be read — never 0. */
  count: number | null;
  goals: ApexGoalRecord[];
}

/** `GET /apex/goals`. Owner-scoped unless the caller is an admin. */
export async function fetchApexGoals(): Promise<ApexGoals> {
  const r = rec(await get<Rec>("/apex/goals"));
  return {
    available: bool(r.available),
    reason: optStr(r.reason) ?? "",
    count: optNum(r.count),
    goals: (Array.isArray(r.goals) ? r.goals : []).map((row) => mapGoalRecord(row)),
  };
}

export interface CreateApexGoal {
  objective: string;
  description?: string;
  /** Create as a child of this goal. A child may not outrank any ancestor. */
  parent_goal_id?: string;
  session_id?: string;
  mission_id?: string;
  success_criteria?: string[];
  constraints?: string[];
  priority?: number;
  risk?: string;
}

/** `POST /apex/goals`. Authenticated, not admin: the owner is the caller. */
export async function createApexGoal(input: CreateApexGoal): Promise<ApexGoalRecord> {
  const body = await send<Rec>("/apex/goals", "POST", input);
  return mapGoalRecord(rec(body.goal));
}

/** `GET /apex/goals/{id}` — the stored record. */
export async function fetchApexGoal(goalId: string): Promise<ApexGoalRecord> {
  const body = rec(await get<Rec>(`/apex/goals/${encodeURIComponent(goalId)}`));
  return mapGoalRecord(body.goal);
}

// --------------------------------------------------------------------------- //
// The mode toggle
// --------------------------------------------------------------------------- //
//
// `enabled` and `contract_enabled` are **two different claims** and the client
// keeps them apart.
//
// The backend reports `enabled` as the recorded intent and `contract_enabled` as
// what the frozen contract actually grants. They disagree in exactly the cases
// that matter: a corrupt mode store answers `enabled: false` for every scope
// (fail-closed), and a record persisted by a newer build under a profile this
// one does not know degrades to `assist` — so `enabled` can be true while the
// authority is not what was asked for. A toggle that rendered only `enabled`
// would paint the second case green.

export interface ApexMode {
  /** The recorded intent: did anything switch this scope on? */
  enabled: boolean;
  /** What the contract actually grants. This is what gates work. */
  contract_enabled: boolean;
  profile: ApexProfile | string;
  scope_key: string;
  /** Whether *this* call changed anything. A second enable is `false`. */
  changed: boolean;
  contract_digest: string;
  /** Whether the write reached disk. `false` means the toggle did not persist. */
  durable: boolean;
  /** Why nothing changed, when `changed` is false. */
  reason: string;
  enabled_at: number | null;
  updated_at: number | null;
  /** Set when the stored profile is not one this build knows. */
  load_note: string | null;
  /** Set when the mode store could not be read at all. */
  load_error: string | null;
  /**
   * The live session bound to this scope, or `null` when none exists.
   *
   * The control verbs and the approval gate act on it, so a client rendering
   * the switch can render the controls beside it — and can tell "off" from
   * "off because no session was ever created".
   */
  active_session: ApexSessionRecord | null;
}

function mapMode(v: unknown): ApexMode {
  const r = rec(v);
  return {
    enabled: bool(r.enabled),
    contract_enabled: bool(r.contract_enabled),
    profile: str(r.profile) || "off",
    scope_key: str(r.scope_key),
    changed: bool(r.changed),
    contract_digest: str(r.contract_digest),
    durable: bool(r.durable),
    reason: str(r.reason),
    enabled_at: optNum(r.enabled_at),
    updated_at: optNum(r.updated_at),
    load_note: optStr(r.load_note),
    load_error: optStr(r.load_error),
    active_session: r.active_session === undefined || r.active_session === null ? null : mapSessionRecord(r.active_session),
  };
}

export type ApexChipTone = "loading" | "unknown" | "off" | "on" | "degraded";

export interface ApexChipView {
  tone: ApexChipTone;
  /** What the chip prints as its state: `ON`, `OFF`, `unknown`, or `…` while reading. */
  state: string;
  /**
   * The profile *in force*, or `""` when none is.
   *
   * An `off` scope still reports the profile its next enable would use, and
   * printing that beside `OFF` would read as "apex_max, but off" — a profile
   * that is retained is not a profile that is in force.
   */
  profile: string;
  /** The whole reading, for the chip's tooltip. */
  title: string;
}

/**
 * Derive the composer chip from a mode read and (separately) its failure.
 *
 * Two failure shapes reach here and neither may render as `off`: an HTTP read
 * that rejected, and a `200` whose payload carries `load_error` because the
 * mode store itself could not be read (the server then reports `enabled: false`
 * fail-closed). "Could not tell" and "it is off" lead to opposite actions, so
 * `load_error` is checked before the enabled flag is ever read.
 *
 * `enabled && !contract_enabled` is the third non-binary case: the record says
 * on while the contract in force grants nothing — an unknown profile degrading,
 * or a scope whose contract no longer matches. It renders as `degraded` rather
 * than a green `ON`, because the green claim would be about a grant the server
 * says was not made.
 */
export function apexChipView(mode: ApexMode | null, readError: string | null): ApexChipView {
  if (readError) {
    return {
      tone: "unknown",
      state: "unknown",
      profile: "",
      title: `APEX state is not known — the mode read failed: ${readError}`,
    };
  }
  if (!mode) {
    return { tone: "loading", state: "…", profile: "", title: "Reading the APEX mode…" };
  }
  if (mode.load_error) {
    return {
      tone: "unknown",
      state: "unknown",
      profile: "",
      title: `APEX state is not known — the mode store could not be read: ${mode.load_error}`,
    };
  }
  if (mode.enabled && !mode.contract_enabled) {
    return {
      tone: "degraded",
      state: "ON",
      profile: String(mode.profile),
      title: `Recorded as on with profile ${mode.profile}, but the contract in force does not grant it${
        mode.reason ? `: ${mode.reason}` : "."
      }`,
    };
  }
  if (mode.enabled) {
    return {
      tone: "on",
      state: "ON",
      profile: String(mode.profile),
      title: `APEX autopilot is on with profile ${mode.profile} for scope ${mode.scope_key}. ${APEX_COMPOSER_DISCLOSURE}`,
    };
  }
  return {
    tone: "off",
    state: "OFF",
    profile: "",
    title: `APEX autopilot is off for scope ${mode.scope_key}. ${APEX_COMPOSER_DISCLOSURE}`,
  };
}

/**
 * Which menu row the checkmark marks: what is in force right now, not what the
 * record remembers.
 *
 * `null` marks nothing — a store that could not be read has no rung to show,
 * and a profile from a newer build is deliberately not snapped onto a rung this
 * build offers, or the menu would claim a profile the server is not running.
 */
export function liveRung(mode: ApexMode | null): ApexProfile | null {
  if (!mode || mode.load_error) return null;
  if (!mode.enabled) return "off";
  return profileToAdopt(mode.profile);
}

/**
 * `GET /apex/mode`. The read the toggle renders from.
 *
 * Never resolves to a guessed state: a rejected read throws, so the panel shows
 * the failure rather than a switch that looks like a working one pointed at
 * "off".
 */
export async function fetchApexMode(scopeKey?: string): Promise<ApexMode> {
  const query = scopeKey ? `?scope_key=${encodeURIComponent(scopeKey)}` : "";
  return mapMode(await get<Rec>(`/apex/mode${query}`));
}

/**
 * `POST /apex/enable` / `POST /apex/disable`.
 *
 * `profile` is sent only when enabling. `/apex/disable` takes no profile — the
 * server keeps the previous one so re-enabling restores the authority the user
 * had rather than resetting them to a default, and sending a profile there would
 * imply the field is meaningful on that route.
 *
 * A 422 names the valid profiles and rejects on it, so a typo cannot silently
 * grant a different authority than the operator asked for.
 */
export async function setApexMode(
  enabled: boolean,
  opts?: { profile?: ApexProfile; scopeKey?: string },
): Promise<ApexMode> {
  const path = enabled ? "/apex/enable" : "/apex/disable";
  const body: Record<string, string> = {};
  if (enabled && opts?.profile) body.profile = opts.profile;
  if (opts?.scopeKey) body.scope_key = opts.scopeKey;
  return mapMode(await send<Rec>(path, "POST", body));
}