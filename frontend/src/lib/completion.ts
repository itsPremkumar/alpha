import { errMsg, get, send } from "./http";

/**
 * The completion-engine client — `GET|POST /completion/*`.
 *
 * This is the browser half of the acceptance-contract ledger. The backend is
 * `app/gateway/routers/completion.py` over `alpha.completion`, and it is the
 * only route to `complete`. Nothing here changes that: the client maps what it
 * is given and refuses to invent the rest.
 *
 * The rules, each with a tempting wrong reading:
 *
 * 1. **Absent is `null`, never `0`, `""` or `false`.** A count the Gateway did
 *    not send reads *not reported*, not `0` — "the ledger recorded zero
 *    passed gates" and "the ledger could not be read" lead to opposite
 *    actions. `scopes: null` is `ScopeSummary[] | null` for the same reason: an
 *    omitted list is not an empty one.
 * 2. **Server enum strings are preserved verbatim.** `status`, `state`,
 *    `outcome`, `freshness`, `verdict` and `mode` are typed `string`, so a
 *    value from a newer Gateway renders as itself rather than being snapped to
 *    something this build knows. The exported `*STATUSES` arrays are the
 *    *filter* vocabularies the server accepts today — a filter a member of a
 *    newer Gateway's enum is refused locally with the allowed set named,
 *    because sending it would only earn a 422 with the same list in it.
 * 3. **Bounds are mirrored, not tightened.** `limit` is clamped onto the
 *    server's own window (scopes `ge=1, le=500`; events `ge=1, le=2000`).
 * 4. **A refusal keeps the server's words.** `errMsg` replaces a 401/403/404
 *    detail with a generic sentence, which is right for an incidental failure
 *    and wrong here: every mutation is admin-gated, and "completion mutations
 *    require an administrator" *is* the answer. `failureText` keeps an `Error`
 *    carrying a numeric status verbatim.
 * 5. **The vocabulary of this ledger is three-valued, and it stays that way.**
 *    `GateStatus.WAIVED`, `CheckOutcome.NOT_RUN` and `Freshness.STALE` are
 *    each a *decision*, not a soft pass. The `VERIFIED_TONES` / `PASSED_TONES`
 *    sets below are the only renderings allowed to look like success, so a
 *    waived gate or a check that never ran cannot be tinted green by accident.
 *
 * Coverage: `src/lib/completion.test.mjs`.
 */

// ---------------------------------------------------------------------------
// Server vocabularies (the filter sets a request may carry)
// ---------------------------------------------------------------------------

/** Every scope status the list filter accepts, in the server's enum order. */
export const SCOPE_STATUSES = [
  "planning",
  "running",
  "verifying",
  "complete",
  "partial",
  "blocked",
  "failed",
  "cancelled",
  "handoff_required",
] as const;

/** Every task state the engine can report. */
export const TASK_STATES = [
  "waiting",
  "ready",
  "in_flight",
  "returned",
  "verifying",
  "verified",
  "blocked",
  "abandoned",
  "failed",
] as const;

/** Every gate status the engine can report, and the list filter accepts. */
export const GATE_STATUSES = [
  "pending",
  "running",
  "passed",
  "failed",
  "stale",
  "blocked",
  "waived",
  "abandoned",
] as const;

/** Every event kind the journal filter accepts, in the server's enum order. */
export const EVENT_KINDS = [
  "contract.created",
  "contract.revised",
  "contract.validation_failed",
  "task.ready",
  "task.dispatched",
  "task.started",
  "task.returned",
  "task.verified",
  "task.blocked",
  "dispatch.launch_failed",
  "dispatch.abandoned",
  "gate.started",
  "gate.passed",
  "gate.failed",
  "gate.stale",
  "gate.blocked",
  "evidence.created",
  "evidence.invalidated",
  "ownership.claimed",
  "ownership.conflict",
  "ownership.released",
  "approval.granted",
  "approval.revoked",
  "scope.created",
  "scope.started",
  "scope.reconciliation_failed",
  "scope.finalized",
  "scope.handoff_required",
  "scope.cancelled",
  "budget.exhausted",
] as const;

/** The three verify modes `POST .../tasks/{tid}/verify` accepts. */
export const VERIFY_MODES = ["status", "verify", "reverify"] as const;

/** The only two verdicts a human review may record. */
export const REVIEW_VERDICTS = ["pass", "fail"] as const;

export type ScopeStatus = (typeof SCOPE_STATUSES)[number];
export type TaskState = (typeof TASK_STATES)[number];
export type GateStatus = (typeof GATE_STATUSES)[number];
export type EventKind = (typeof EVENT_KINDS)[number];
export type VerifyMode = (typeof VERIFY_MODES)[number];
export type ReviewVerdict = (typeof REVIEW_VERDICTS)[number];

/**
 * The only renderings that may look like a success.
 *
 * `passed` is the sole status that satisfies a gate. `current` is the sole
 * freshness that lets evidence speak for the present. Everything else — a
 * `stale` pass, a `waived` gate, a check that `not_run` — is a decision to be
 * *shown*, not a green tick to be softened.
 */
export const PASSED_TONES: readonly string[] = [
  "passed",
  "current",
  "met",
  "complete",
];
export const VERIFIED_TONES: readonly string[] = ["verified"];

/** The server's list bounds (`Query(ge=1, le=500)`, default 100). */
export const SCOPE_LIMIT_DEFAULT = 100;
export const SCOPE_LIMIT_MAX = 500;

/** The server's event bounds (`Query(ge=1, le=2000)`, default 200). */
export const EVENT_LIMIT_DEFAULT = 200;
export const EVENT_LIMIT_MAX = 2000;

/** The server's reason bound for cancel/block (`Field(min_length=1, max_length=2000)`). */
export const REASON_MIN_LENGTH = 1;
export const REASON_MAX_LENGTH = 2000;

// ---------------------------------------------------------------------------
// Wire shapes
// ---------------------------------------------------------------------------

/**
 * The engine's posture. `count` is `null` when the ledger could not be read —
 * never `0`, which would read as "nothing recorded".
 */
export interface CompletionHealth {
  readable: boolean | null;
  /** The ledger's own reason when `readable` is false; `null` when unreported. */
  error: string | null;
  path: string | null;
  /** Scopes on disk; `null` when unreported. */
  count: number | null;
  /** Journal rows; `null` when unreported. */
  events: number | null;
  enabled: boolean | null;
  /** `"enforce"` | `"observe"`, or `null` when not reported. */
  mode: string | null;
  /** Enabled *and* enforcing — a disabled engine never reads as a live gate. */
  enforcing: boolean | null;
  guarding: boolean | null;
  policy_id: string | null;
  workspace_root: string | null;
  /** The registered check names, or `null` when the Gateway sent no list. */
  checks: string[] | null;
  limits: CompletionLimits | null;
  generated_at: string | null;
}

/** The engine's ceilings. Every field is `null` when the block did not arrive. */
export interface CompletionLimits {
  max_tasks: number | null;
  max_depth: number | null;
  max_concurrency: number | null;
  max_gate_runs: number | null;
  wall_clock_seconds: number | null;
  no_progress_limit: number | null;
  max_scopes: number | null;
}

/** `GET /checks` — the allowlist plus the vocabularies a planner writes against. */
export interface CompletionChecks {
  /** `null` means "the Gateway sent no check list", distinct from `[]`. */
  checks: string[] | null;
  reverify_modes: string[] | null;
  scope_statuses: string[] | null;
  task_states: string[] | null;
  gate_statuses: string[] | null;
  event_kinds: string[] | null;
  policy: Record<string, unknown> | null;
  limits: CompletionLimits | null;
}

/** One scope's flat projection (`scope_summary`). */
export interface ScopeSummary {
  scope_id: string;
  owner_id: string | null;
  thread_id: string | null;
  mode: string | null;
  status: string;
  status_reason: string | null;
  request_snapshot: string | null;
  request_revision: number | null;
  contract_valid: boolean | null;
  contract_digest: string | null;
  requirements_total: number | null;
  requirements_met: number | null;
  tasks_total: number | null;
  tasks_verified: number | null;
  tasks_in_flight: number | null;
  gates_total: number | null;
  gates_required: number | null;
  gates_passed: number | null;
  gates_stale: number | null;
  evidence_total: number | null;
  dispatches_total: number | null;
  finalize_attempts: number | null;
  no_progress_count: number | null;
  budget_exhausted: boolean | null;
  created_at: string | null;
  updated_at: string | null;
  finished_at: string | null;
  final_status: string | null;
}

/** `GET /scopes` — an explicit envelope, so an unreadable ledger is not a `[]`. */
export interface ScopeList {
  /** `false` means the Gateway did not report, not "there are none". */
  reported: boolean;
  /** `"all"` (admin) / `"owner"` (member), or `null` when not reported. */
  scope: string | null;
  count: number | null;
  /** `null` means "no list arrived"; `[]` means "the list arrived empty". */
  scopes: ScopeSummary[] | null;
}

/** One contract clause as the API projects it. */
export interface RequirementRow {
  requirement_id: string;
  intent: string | null;
  mandatory: boolean | null;
  observable: string | null;
  task_id: string | null;
  gate_ids: string[] | null;
  status: string | null;
  evidence_ids: string[] | null;
  request_revision: number | null;
  notes: string | null;
}

/** One deterministic validation finding. `severity: "error"` blocks dispatch. */
export interface ContractFinding {
  code: string;
  severity: string;
  message: string;
  subject_id: string | null;
}

/** The acceptance contract. */
export interface ContractView {
  contract_id: string | null;
  version: number | null;
  digest: string | null;
  valid: boolean | null;
  findings: ContractFinding[] | null;
  requirements: RequirementRow[] | null;
}

/** One task node plus its scheduling projection. */
export interface TaskView {
  task_id: string;
  title: string | null;
  objective: string | null;
  parent_id: string | null;
  depends_on: string[] | null;
  gate_ids: string[] | null;
  /** The engine's own state string, verbatim. */
  state: string;
  owner: string | null;
  owns: string[] | null;
  expected_outputs: string[] | null;
  integration: boolean | null;
  dispatch_ids: string[] | null;
  blocked_reason: string | null;
  attempts: number | null;
  max_attempts: number | null;
  depth: number | null;
  children: string[] | null;
  gate_statuses: Record<string, string> | null;
  dispatchable: boolean | null;
  readiness_reason: string | null;
  blocking: string[] | null;
}

/** One evidence row as `Evidence` serializes it. */
export interface EvidenceRow {
  evidence_id: string;
  gate_id: string | null;
  task_id: string | null;
  check_ref: string | null;
  check_version: string | null;
  /** `passed` is the only member that can satisfy a gate. */
  outcome: string;
  reason: string | null;
  summary: string | null;
  exit_code: number | null;
  output_preview: string | null;
  output_truncated: boolean | null;
  freshness: string | null;
  invalidation_reason: string | null;
  duration_seconds: number | null;
  worker: string | null;
  ended_at: string | null;
}

/** One gate plus its latest evidence and approval liveness. */
export interface GateView {
  gate_id: string;
  task_id: string | null;
  title: string | null;
  required: boolean | null;
  kind: string | null;
  check_ref: string | null;
  expectation: unknown;
  /** The engine's own status string, verbatim. */
  status: string;
  evidence_ids: string[] | null;
  risk_tier: string | null;
  approval_policy: string | null;
  definition_digest: string | null;
  attempts: number | null;
  last_failure: string | null;
  waiver_reason: string | null;
  latest_evidence: EvidenceRow | null;
  /** `null` means liveness was not reported, not "the approval is stale". */
  approval_live: boolean | null;
}

/** One launch attempt in a dispatch wave. */
export interface DispatchView {
  dispatch_id: string;
  wave_id: string | null;
  task_id: string | null;
  agent: string | null;
  attempt: number | null;
  /** `requested` is not `started`; `unknown` is a third state. */
  state: string;
  handle: string | null;
  requested_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  launch_error: string | null;
  terminal_reason: string | null;
  verified: boolean | null;
}

/** A declared write scope with an expiry. */
export interface OwnershipView {
  task_id: string | null;
  owner: string | null;
  paths: string[] | null;
  claimed_at: string | null;
  expires_at: string | null;
}

/** The scope-level hard budget. */
export interface BudgetView {
  max_tasks: number | null;
  max_depth: number | null;
  max_concurrency: number | null;
  max_gate_runs: number | null;
  wall_clock_seconds: number | null;
  gate_runs_used: number | null;
  exhausted: boolean | null;
  exhaustion_reason: string | null;
}

/** One reason finalization cannot claim completion. */
export interface Blocker {
  code: string;
  message: string;
  subject_id: string | null;
  kind: string | null;
}

/** The original-request-to-contract audit. */
export interface ReconciliationView {
  /** Named because it *is* a heuristic — `heuristic_clause_match`. */
  basis: string | null;
  clauses_seen: number | null;
  /** Request clauses with no requirement covering them. */
  unrepresented: string[] | null;
  /** Mandatory requirements without current passing evidence. */
  unmet: string[] | null;
  /** Requirements bound to no observable at all. */
  unverifiable: string[] | null;
  clean: boolean | null;
  notes: string | null;
}

/**
 * The finalization report. `enforced: false` means the engine computed the
 * decision and did **not** move the scope — observe mode never claims a
 * completion it did not apply.
 */
export interface FinalReport {
  status: string | null;
  enforced: boolean | null;
  complete: boolean | null;
  blockers: Blocker[] | null;
  checks_ran: number | null;
  checks_passed: number | null;
  checks_failed: number | null;
  checks_stale: number | null;
  checks_not_run: number | null;
  requirements_met: number | null;
  requirements_total: number | null;
  /** Required checks this attempt did not re-execute. */
  not_rerun: string[] | null;
  reconciliation: ReconciliationView | null;
  summary: string | null;
  generated_at: string | null;
  attempt: number | null;
  /** Only the `finalize` route carries this; the preview does not. */
  scope_status: string | null;
}

/** `GET /scopes/{id}` — the full live projection. */
export interface ScopeDetail {
  scope: ScopeSummary | null;
  contract: ContractView | null;
  tasks: TaskView[] | null;
  gates: GateView[] | null;
  dispatches: DispatchView[] | null;
  ownership: OwnershipView[] | null;
  budget: BudgetView | null;
  final_report: FinalReport | null;
  scheduling: Record<string, unknown> | null;
  mode: string | null;
  enforcing: boolean | null;
  policy_id: string | null;
}

/** `GET /scopes/{id}/events` — the bounded journal. */
export interface EventList {
  scope_id: string | null;
  count: number | null;
  /** `null` means the Gateway sent no rows; `[]` means the journal is empty. */
  events: CompletionEvent[] | null;
}

/** One journal entry. */
export interface CompletionEvent {
  event_id: string;
  kind: string;
  at: string | null;
  producer: string | null;
  task_id: string | null;
  gate_id: string | null;
  payload: Record<string, string> | null;
}

/** `GET /scopes/{id}/gates/{gid}/approval` — what a grant would cover. */
export interface ApprovalBrief {
  gate_id: string | null;
  definition_digest: string | null;
  reason: string | null;
  [key: string]: unknown;
}

/** `POST .../tasks/{tid}/verify` — `VerificationResult.to_dict()`. */
export interface VerifyResult {
  task_id: string | null;
  state: string | null;
  passed: boolean | null;
  ran: number | null;
  [key: string]: unknown;
}

export interface ScopeListQuery {
  thread_id?: string;
  status?: string;
  /** Admins only; a member is always scoped to themselves server-side. */
  owner_id?: string;
  include_terminal?: boolean;
  limit?: number;
}

export interface EventQuery {
  limit?: number;
  /** Repeatable server-side; one kind per call here. */
  kind?: string;
}

// ---------------------------------------------------------------------------
// Mapping — verbatim where the server spoke, null where it did not
// ---------------------------------------------------------------------------

type Rec = Record<string, unknown>;

function rec(v: unknown): Rec {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : {};
}

function optNum(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function optStr(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

function optBool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

/** A list, or `null` when the Gateway sent something else. */
function optList(v: unknown): string[] | null {
  return Array.isArray(v)
    ? v.filter((item): item is string => typeof item === "string")
    : null;
}

/** An object, or `null`. An empty record stays `{}` — that is a real answer. */
function optRec(v: unknown): Rec | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : null;
}

/**
 * A record of enum strings, or `null`.
 *
 * One non-string value makes the whole table unreadable: a partial verdict map
 * is not a partial answer to "which gates passed" — it is an answer you cannot
 * trust, so the view says so.
 */
function optStrMap(v: unknown): Record<string, string> | null {
  const source = optRec(v);
  if (!source) return null;
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(source)) {
    if (typeof value !== "string") return null;
    out[key] = value;
  }
  return out;
}

/** A string-keyed payload map, or `null`. Same unreadable-table rule. */
function optPayload(v: unknown): Record<string, string> | null {
  return optStrMap(v);
}

function numOr(v: unknown, fallback: number): number {
  const n = optNum(v);
  return n === null ? fallback : n;
}

/** Map the engine posture. Pure; exported for tests. */
export function mapHealth(raw: unknown): CompletionHealth {
  const h = rec(raw);
  return {
    readable: optBool(h.readable),
    error: optStr(h.error),
    path: optStr(h.path),
    count: optNum(h.count),
    events: optNum(h.events),
    enabled: optBool(h.enabled),
    mode: optStr(h.mode),
    enforcing: optBool(h.enforcing),
    guarding: optBool(h.guarding),
    policy_id: optStr(h.policy_id),
    workspace_root: optStr(h.workspace_root),
    checks: optList(h.checks),
    limits:
      h.limits === undefined || h.limits === null ? null : mapLimits(h.limits),
    generated_at: optStr(h.generated_at),
  };
}

/** Map the ceilings block. Pure; exported for tests. */
export function mapLimits(raw: unknown): CompletionLimits {
  const l = rec(raw);
  return {
    max_tasks: optNum(l.max_tasks),
    max_depth: optNum(l.max_depth),
    max_concurrency: optNum(l.max_concurrency),
    max_gate_runs: optNum(l.max_gate_runs),
    wall_clock_seconds: optNum(l.wall_clock_seconds),
    no_progress_limit: optNum(l.no_progress_limit),
    max_scopes: optNum(l.max_scopes),
  };
}

/** Map the check/vocabulary catalogue. Pure; exported for tests. */
export function mapChecks(raw: unknown): CompletionChecks {
  const c = rec(raw);
  return {
    checks: optList(c.checks),
    reverify_modes: optList(c.reverify_modes),
    scope_statuses: optList(c.scope_statuses),
    task_states: optList(c.task_states),
    gate_statuses: optList(c.gate_statuses),
    event_kinds: optList(c.event_kinds),
    policy: optRec(c.policy),
    limits:
      c.limits === undefined || c.limits === null ? null : mapLimits(c.limits),
  };
}

/** Map one flat scope projection. Pure; exported for tests. */
export function mapScopeSummary(raw: unknown): ScopeSummary {
  const s = rec(raw);
  return {
    scope_id: optStr(s.scope_id) ?? "",
    owner_id: optStr(s.owner_id),
    thread_id: optStr(s.thread_id),
    mode: optStr(s.mode),
    status: optStr(s.status) ?? "",
    status_reason: optStr(s.status_reason),
    request_snapshot: optStr(s.request_snapshot),
    request_revision: optNum(s.request_revision),
    contract_valid: optBool(s.contract_valid),
    contract_digest: optStr(s.contract_digest),
    requirements_total: optNum(s.requirements_total),
    requirements_met: optNum(s.requirements_met),
    tasks_total: optNum(s.tasks_total),
    tasks_verified: optNum(s.tasks_verified),
    tasks_in_flight: optNum(s.tasks_in_flight),
    gates_total: optNum(s.gates_total),
    gates_required: optNum(s.gates_required),
    gates_passed: optNum(s.gates_passed),
    gates_stale: optNum(s.gates_stale),
    evidence_total: optNum(s.evidence_total),
    dispatches_total: optNum(s.dispatches_total),
    finalize_attempts: optNum(s.finalize_attempts),
    no_progress_count: optNum(s.no_progress_count),
    budget_exhausted: optBool(s.budget_exhausted),
    created_at: optStr(s.created_at),
    updated_at: optStr(s.updated_at),
    finished_at: optStr(s.finished_at),
    final_status: optStr(s.final_status),
  };
}

/** Map the list envelope. Pure; exported for tests. */
export function mapScopeList(raw: unknown): ScopeList {
  const l = rec(raw);
  const rows = Array.isArray(l.scopes) ? l.scopes : null;
  return {
    reported: l.reported === true,
    scope: optStr(l.scope),
    count: optNum(l.count),
    scopes: rows ? rows.map(mapScopeSummary) : null,
  };
}

/** Map one contract finding. Pure; exported for tests. */
export function mapFinding(raw: unknown): ContractFinding {
  const f = rec(raw);
  return {
    code: optStr(f.code) ?? "",
    severity: optStr(f.severity) ?? "",
    message: optStr(f.message) ?? "",
    subject_id: optStr(f.subject_id),
  };
}

/** Map one requirement clause. Pure; exported for tests. */
export function mapRequirement(raw: unknown): RequirementRow {
  const r = rec(raw);
  return {
    requirement_id: optStr(r.requirement_id) ?? "",
    intent: optStr(r.intent),
    mandatory: optBool(r.mandatory),
    observable: optStr(r.observable),
    task_id: optStr(r.task_id),
    gate_ids: optList(r.gate_ids),
    status: optStr(r.status),
    evidence_ids: optList(r.evidence_ids),
    request_revision: optNum(r.request_revision),
    notes: optStr(r.notes),
  };
}

/** Map the contract block. Pure; exported for tests. */
export function mapContract(raw: unknown): ContractView {
  const c = rec(raw);
  const findings = Array.isArray(c.findings) ? c.findings : null;
  const requirements = Array.isArray(c.requirements) ? c.requirements : null;
  return {
    contract_id: optStr(c.contract_id),
    version: optNum(c.version),
    digest: optStr(c.digest),
    valid: optBool(c.valid),
    findings: findings ? findings.map(mapFinding) : null,
    requirements: requirements ? requirements.map(mapRequirement) : null,
  };
}

/** Map one evidence row. Pure; exported for tests. */
export function mapEvidence(raw: unknown): EvidenceRow {
  const e = rec(raw);
  return {
    evidence_id: optStr(e.evidence_id) ?? "",
    gate_id: optStr(e.gate_id),
    task_id: optStr(e.task_id),
    check_ref: optStr(e.check_ref),
    check_version: optStr(e.check_version),
    outcome: optStr(e.outcome) ?? "",
    reason: optStr(e.reason),
    summary: optStr(e.summary),
    exit_code: optNum(e.exit_code),
    output_preview: optStr(e.output_preview),
    output_truncated: optBool(e.output_truncated),
    freshness: optStr(e.freshness),
    invalidation_reason: optStr(e.invalidation_reason),
    duration_seconds: optNum(e.duration_seconds),
    worker: optStr(e.worker),
    ended_at: optStr(e.ended_at),
  };
}

/** Map one task plus its readiness projection. Pure; exported for tests. */
export function mapTask(raw: unknown): TaskView {
  const t = rec(raw);
  return {
    task_id: optStr(t.task_id) ?? "",
    title: optStr(t.title),
    objective: optStr(t.objective),
    parent_id: optStr(t.parent_id),
    depends_on: optList(t.depends_on),
    gate_ids: optList(t.gate_ids),
    state: optStr(t.state) ?? "",
    owner: optStr(t.owner),
    owns: optList(t.owns),
    expected_outputs: optList(t.expected_outputs),
    integration: optBool(t.integration),
    dispatch_ids: optList(t.dispatch_ids),
    blocked_reason: optStr(t.blocked_reason),
    attempts: optNum(t.attempts),
    max_attempts: optNum(t.max_attempts),
    depth: optNum(t.depth),
    children: optList(t.children),
    gate_statuses: optStrMap(t.gate_statuses),
    dispatchable: optBool(t.dispatchable),
    readiness_reason: optStr(t.readiness_reason),
    blocking: optList(t.blocking),
  };
}

/** Map one gate plus its latest evidence. Pure; exported for tests. */
export function mapGate(raw: unknown): GateView {
  const g = rec(raw);
  return {
    gate_id: optStr(g.gate_id) ?? "",
    task_id: optStr(g.task_id),
    title: optStr(g.title),
    required: optBool(g.required),
    kind: optStr(g.kind),
    check_ref: optStr(g.check_ref),
    expectation: g.expectation,
    status: optStr(g.status) ?? "",
    evidence_ids: optList(g.evidence_ids),
    risk_tier: optStr(g.risk_tier),
    approval_policy: optStr(g.approval_policy),
    definition_digest: optStr(g.definition_digest),
    attempts: optNum(g.attempts),
    last_failure: optStr(g.last_failure),
    waiver_reason: optStr(g.waiver_reason),
    latest_evidence:
      g.latest_evidence === undefined || g.latest_evidence === null
        ? null
        : mapEvidence(g.latest_evidence),
    approval_live: optBool(g.approval_live),
  };
}

/** Map one dispatch record. Pure; exported for tests. */
export function mapDispatch(raw: unknown): DispatchView {
  const d = rec(raw);
  return {
    dispatch_id: optStr(d.dispatch_id) ?? "",
    wave_id: optStr(d.wave_id),
    task_id: optStr(d.task_id),
    agent: optStr(d.agent),
    attempt: optNum(d.attempt),
    state: optStr(d.state) ?? "",
    handle: optStr(d.handle),
    requested_at: optStr(d.requested_at),
    started_at: optStr(d.started_at),
    finished_at: optStr(d.finished_at),
    launch_error: optStr(d.launch_error),
    terminal_reason: optStr(d.terminal_reason),
    verified: optBool(d.verified),
  };
}

/** Map one ownership claim. Pure; exported for tests. */
export function mapOwnership(raw: unknown): OwnershipView {
  const o = rec(raw);
  return {
    task_id: optStr(o.task_id),
    owner: optStr(o.owner),
    paths: optList(o.paths),
    claimed_at: optStr(o.claimed_at),
    expires_at: optStr(o.expires_at),
  };
}

/** Map the budget block. Pure; exported for tests. */
export function mapBudget(raw: unknown): BudgetView {
  const b = rec(raw);
  return {
    max_tasks: optNum(b.max_tasks),
    max_depth: optNum(b.max_depth),
    max_concurrency: optNum(b.max_concurrency),
    max_gate_runs: optNum(b.max_gate_runs),
    wall_clock_seconds: optNum(b.wall_clock_seconds),
    gate_runs_used: optNum(b.gate_runs_used),
    exhausted: optBool(b.exhausted),
    exhaustion_reason: optStr(b.exhaustion_reason),
  };
}

/** Map one finalization blocker. Pure; exported for tests. */
export function mapBlocker(raw: unknown): Blocker {
  const b = rec(raw);
  return {
    code: optStr(b.code) ?? "",
    message: optStr(b.message) ?? "",
    subject_id: optStr(b.subject_id),
    kind: optStr(b.kind),
  };
}

/** Map the reconciliation audit. Pure; exported for tests. */
export function mapReconciliation(raw: unknown): ReconciliationView {
  const r = rec(raw);
  return {
    basis: optStr(r.basis),
    clauses_seen: optNum(r.clauses_seen),
    unrepresented: optList(r.unrepresented),
    unmet: optList(r.unmet),
    unverifiable: optList(r.unverifiable),
    clean: optBool(r.clean),
    notes: optStr(r.notes),
  };
}

/**
 * Map a finalization report.
 *
 * `complete` is read with `=== true`: an absent flag is *not reported*, and
 * reading it as false would say the engine decided against completion when the
 * truth is that no decision arrived.
 */
export function mapFinalReport(raw: unknown): FinalReport {
  const r = rec(raw);
  const blockers = Array.isArray(r.blockers) ? r.blockers : null;
  return {
    status: optStr(r.status),
    enforced: optBool(r.enforced),
    complete: optBool(r.complete),
    blockers: blockers ? blockers.map(mapBlocker) : null,
    checks_ran: optNum(r.checks_ran),
    checks_passed: optNum(r.checks_passed),
    checks_failed: optNum(r.checks_failed),
    checks_stale: optNum(r.checks_stale),
    checks_not_run: optNum(r.checks_not_run),
    requirements_met: optNum(r.requirements_met),
    requirements_total: optNum(r.requirements_total),
    not_rerun: optList(r.not_rerun),
    reconciliation:
      r.reconciliation === undefined || r.reconciliation === null
        ? null
        : mapReconciliation(r.reconciliation),
    summary: optStr(r.summary),
    generated_at: optStr(r.generated_at),
    attempt: optNum(r.attempt),
    scope_status: optStr(r.scope_status),
  };
}

/** Map the full scope projection. Pure; exported for tests. */
export function mapScopeDetail(raw: unknown): ScopeDetail {
  const d = rec(raw);
  const tasks = Array.isArray(d.tasks) ? d.tasks : null;
  const gates = Array.isArray(d.gates) ? d.gates : null;
  const dispatches = Array.isArray(d.dispatches) ? d.dispatches : null;
  const ownership = Array.isArray(d.ownership) ? d.ownership : null;
  return {
    scope:
      d.scope === undefined || d.scope === null
        ? null
        : mapScopeSummary(d.scope),
    contract:
      d.contract === undefined || d.contract === null
        ? null
        : mapContract(d.contract),
    tasks: tasks ? tasks.map(mapTask) : null,
    gates: gates ? gates.map(mapGate) : null,
    dispatches: dispatches ? dispatches.map(mapDispatch) : null,
    ownership: ownership ? ownership.map(mapOwnership) : null,
    budget:
      d.budget === undefined || d.budget === null ? null : mapBudget(d.budget),
    final_report:
      d.final_report === undefined || d.final_report === null
        ? null
        : mapFinalReport(d.final_report),
    scheduling: optRec(d.scheduling),
    mode: optStr(d.mode),
    enforcing: optBool(d.enforcing),
    policy_id: optStr(d.policy_id),
  };
}

/** Map one journal entry. Pure; exported for tests. */
export function mapEvent(raw: unknown): CompletionEvent {
  const e = rec(raw);
  return {
    event_id: optStr(e.event_id) ?? "",
    kind: optStr(e.kind) ?? "",
    at: optStr(e.at),
    producer: optStr(e.producer),
    task_id: optStr(e.task_id),
    gate_id: optStr(e.gate_id),
    payload: optPayload(e.payload),
  };
}

/** Map the event envelope. Pure; exported for tests. */
export function mapEventList(raw: unknown): EventList {
  const l = rec(raw);
  const rows = Array.isArray(l.events) ? l.events : null;
  return {
    scope_id: optStr(l.scope_id),
    count: optNum(l.count),
    events: rows ? rows.map(mapEvent) : null,
  };
}

/** Map the verify response. Pure; exported for tests. */
export function mapVerifyResult(raw: unknown): VerifyResult {
  const r = rec(raw);
  return {
    task_id: optStr(r.task_id),
    state: optStr(r.state),
    passed: optBool(r.passed),
    ran: optNum(r.ran),
    ...r,
  };
}

/** Map the approval brief. Pure; exported for tests. */
export function mapApprovalBrief(raw: unknown): ApprovalBrief {
  const r = rec(raw);
  return {
    gate_id: optStr(r.gate_id),
    definition_digest: optStr(r.definition_digest),
    reason: optStr(r.reason),
    ...r,
  };
}

// ---------------------------------------------------------------------------
// Request shaping
// ---------------------------------------------------------------------------

/** Clamp a caller's limit onto the server's own `ge=1, le=500` window. */
export function clampScopeLimit(limit: number | null | undefined): number {
  if (typeof limit !== "number" || !Number.isFinite(limit))
    return SCOPE_LIMIT_DEFAULT;
  return Math.min(SCOPE_LIMIT_MAX, Math.max(1, Math.round(limit)));
}

/** Clamp a caller's limit onto the server's own `ge=1, le=2000` window. */
export function clampEventLimit(limit: number | null | undefined): number {
  if (typeof limit !== "number" || !Number.isFinite(limit))
    return EVENT_LIMIT_DEFAULT;
  return Math.min(EVENT_LIMIT_MAX, Math.max(1, Math.round(limit)));
}

/**
 * Refuse a filter the server would reject, naming the allowed set.
 *
 * Mirrors the router's own `invalid_filter` check exactly — same set, same
 * refusal — so a typo is explained before a round trip rather than after one.
 * An empty string is *omitted* instead: the server reads `status=` as a value,
 * not as "no filter".
 */
function assertFilter(
  name: string,
  value: string | undefined,
  allowed: readonly string[],
): void {
  if (value === undefined || value === "") return;
  if (!allowed.includes(value))
    throw new Error(
      `unknown ${name} ${JSON.stringify(value)}; expected one of ${JSON.stringify([...allowed])}`,
    );
}

/** Refuse a reason the server would 422, naming the bound. */
export function assertReason(reason: string): void {
  const length = reason.length;
  if (length < REASON_MIN_LENGTH || length > REASON_MAX_LENGTH) {
    throw new Error(
      `reason must be ${REASON_MIN_LENGTH}–${REASON_MAX_LENGTH} characters (got ${length})`,
    );
  }
}

/** Build `GET /completion/scopes?…`, omitting empty filters and clamping `limit`. */
export function buildScopeListPath(query: ScopeListQuery = {}): string {
  assertFilter("status", query.status, SCOPE_STATUSES);
  const params = new URLSearchParams();
  const put = (key: string, value: string | undefined): void => {
    if (value !== undefined && value !== "") params.set(key, value);
  };
  put("thread_id", query.thread_id);
  put("status", query.status);
  put("owner_id", query.owner_id);
  if (query.include_terminal !== undefined)
    params.set("include_terminal", String(query.include_terminal));
  params.set("limit", String(clampScopeLimit(query.limit)));
  return `/completion/scopes?${params.toString()}`;
}

/** Build `GET /completion/scopes/{id}/events?…`. */
export function buildEventPath(
  scopeId: string,
  query: EventQuery = {},
): string {
  assertFilter("kind", query.kind, EVENT_KINDS);
  const params = new URLSearchParams();
  params.set("limit", String(clampEventLimit(query.limit)));
  if (query.kind) params.set("kind", query.kind);
  return `/completion/scopes/${encodeURIComponent(scopeId)}/events?${params.toString()}`;
}

/** Refuse a verify mode the server does not accept, naming the allowed set. */
function assertVerifyMode(mode: string): void {
  if (!(VERIFY_MODES as readonly string[]).includes(mode)) {
    throw new Error(
      `unknown mode ${JSON.stringify(mode)}; expected one of ${JSON.stringify([...VERIFY_MODES])}`,
    );
  }
}

/** Refuse a review verdict the server does not accept, naming the allowed set. */
function assertReviewVerdict(verdict: string): void {
  if (!(REVIEW_VERDICTS as readonly string[]).includes(verdict)) {
    throw new Error(
      `unknown verdict ${JSON.stringify(verdict)}; expected one of ${JSON.stringify([...REVIEW_VERDICTS])}`,
    );
  }
}

/**
 * The sentence a failure renders.
 *
 * `errMsg` swaps a 401/403/404 detail for a generic sentence — correct for an
 * incidental call, wrong here: every mutation is admin-gated and the refusal
 * ("completion mutations require an administrator") *is* the answer. So an
 * `Error` carrying a numeric status keeps its message verbatim.
 */
export function failureText(err: unknown): string {
  if (
    err instanceof Error &&
    err.message &&
    typeof (err as { status?: unknown }).status === "number"
  )
    return err.message;
  return errMsg(err);
}

/** A refusal that carries the engine's own machine-readable code, if any. */
export interface CompletionRefusal {
  detail: string | null;
  code: string | null;
  subject: string | null;
  findings: ContractFinding[] | null;
  conflicts: string[] | null;
}

/**
 * Parse the router's error body into its machine-readable parts.
 *
 * The router's one error shape is `{detail, code}` top-level, with `findings`
 * on a `contract_invalid` and `conflicts` on an `ownership_conflict`. A body
 * that is not that shape (a plain-text detail, a Starlette route-level 404)
 * still yields `detail`, so a caller always has the server's sentence.
 */
export function mapRefusal(raw: unknown): CompletionRefusal {
  const body = typeof raw === "string" ? { detail: raw } : rec(raw);
  const findings = Array.isArray(body.findings) ? body.findings : null;
  const conflicts = Array.isArray(body.conflicts)
    ? body.conflicts.map((c) => (typeof c === "string" ? c : JSON.stringify(c)))
    : null;
  return {
    detail: optStr(body.detail),
    code: optStr(body.code),
    subject: optStr(body.subject),
    findings: findings ? findings.map(mapFinding) : null,
    conflicts,
  };
}

/**
 * Is this refusal a refusal this panel knows how to explain?
 *
 * `true` when the body carried the router's own `code`, so the caller can show
 * the engine's vocabulary (`illegal_transition`, `approval_required`, …)
 * instead of a generic sentence.
 */
export function isEngineRefusal(body: unknown): boolean {
  return mapRefusal(body).code !== null;
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

/** `GET /completion/health` — the engine's posture. Rejects with the server's reason. */
export async function fetchHealth(): Promise<CompletionHealth> {
  return mapHealth(await get<unknown>("/completion/health"));
}

/** `GET /completion/checks` — the check allowlist plus the planner's vocabularies. */
export async function fetchChecks(): Promise<CompletionChecks> {
  return mapChecks(await get<unknown>("/completion/checks"));
}

/** `GET /completion/scopes` — a newest-first page of summaries. */
export async function fetchScopes(
  query: ScopeListQuery = {},
): Promise<ScopeList> {
  return mapScopeList(await get<unknown>(buildScopeListPath(query)));
}

/** `GET /completion/scopes/{id}` — the full live projection. */
export async function fetchScope(scopeId: string): Promise<ScopeDetail> {
  return mapScopeDetail(
    await get<unknown>(`/completion/scopes/${encodeURIComponent(scopeId)}`),
  );
}

/** `GET /completion/scopes/{id}/contract` — obligations and their findings. */
export async function fetchContract(scopeId: string): Promise<ContractView> {
  return mapContract(
    await get<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/contract`,
    ),
  );
}

/** `GET /completion/scopes/{id}/events` — the bounded journal. */
export async function fetchEvents(
  scopeId: string,
  query: EventQuery = {},
): Promise<EventList> {
  return mapEventList(await get<unknown>(buildEventPath(scopeId, query)));
}

/** `GET /completion/scopes/{id}/reconciliation` — the read-only request audit. */
export async function fetchReconciliation(
  scopeId: string,
): Promise<ReconciliationView> {
  return mapReconciliation(
    await get<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/reconciliation`,
    ),
  );
}

/**
 * `GET /completion/scopes/{id}/finalization/preview` — the decision, unapplied.
 *
 * Deliberately a read: this route never moves a scope, and the report it
 * returns is labelled `enforced: false`.
 */
export async function fetchFinalizationPreview(
  scopeId: string,
): Promise<FinalReport> {
  return mapFinalReport(
    await get<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/finalization/preview`,
    ),
  );
}

/** `GET /completion/scopes/{id}/gates/{gid}/approval` — what a grant covers. */
export async function fetchApprovalBrief(
  scopeId: string,
  gateId: string,
): Promise<ApprovalBrief> {
  return mapApprovalBrief(
    await get<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/gates/${encodeURIComponent(gateId)}/approval`,
    ),
  );
}

// ---------------------------------------------------------------------------
// Mutations — every one is admin-gated server-side
// ---------------------------------------------------------------------------

/**
 * `POST /completion/scopes` — open one.
 *
 * Admin-only server-side; this client does not decide that, it sends the
 * request and surfaces the server's 403. A contract with blocking findings is
 * a 422 carrying every finding — and the refused scope is still readable,
 * because a rejected plan is a record the caller can repair.
 */
export async function createScope(
  body: Record<string, unknown>,
): Promise<ScopeSummary> {
  return mapScopeSummary(
    await send<unknown>("/completion/scopes", "POST", body),
  );
}

/** `POST /completion/scopes/{id}/start` — or resume a parked scope with its reason. */
export async function startScope(
  scopeId: string,
): Promise<Record<string, unknown>> {
  return rec(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/start`,
      "POST",
    ),
  );
}

/** `POST /completion/scopes/{id}/cancel` — with a recorded reason. */
export async function cancelScope(
  scopeId: string,
  reason: string,
): Promise<Record<string, unknown>> {
  assertReason(reason);
  return rec(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/cancel`,
      "POST",
      { reason },
    ),
  );
}

/**
 * `POST /completion/scopes/{id}/contract/revise` — amend the obligations.
 *
 * Gates that previously passed are demoted to `stale`: the obligations moved,
 * so what a gate proved no longer answers the question now being asked.
 */
export async function reviseContract(
  scopeId: string,
  requirements: Array<Record<string, unknown>>,
): Promise<Record<string, unknown>> {
  return rec(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/contract/revise`,
      "POST",
      { requirements },
    ),
  );
}

/** `POST /completion/scopes/{id}/tasks` — extend the plan under the ceilings. */
export async function addTasks(
  scopeId: string,
  tasks: Array<Record<string, unknown>>,
  gates: Array<Record<string, unknown>> = [],
): Promise<Record<string, unknown>> {
  return rec(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/tasks`,
      "POST",
      { tasks, gates },
    ),
  );
}

/** `POST /completion/scopes/{id}/schedule` — recompute readiness, report what may start. */
export async function scheduleScope(
  scopeId: string,
): Promise<Record<string, unknown>> {
  return rec(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/schedule`,
      "POST",
    ),
  );
}

/** `POST /completion/scopes/{id}/dispatch` — request one task's launch. */
export async function dispatchTask(
  scopeId: string,
  taskId: string,
  agent = "",
): Promise<DispatchView> {
  return mapDispatch(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/dispatch`,
      "POST",
      { task_id: taskId, agent },
    ),
  );
}

/** `POST .../dispatches/{did}/confirm` — the handle is what makes a task in-flight. */
export async function confirmLaunch(
  scopeId: string,
  dispatchId: string,
  handle: string,
): Promise<DispatchView> {
  return mapDispatch(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/dispatches/${encodeURIComponent(dispatchId)}/confirm`,
      "POST",
      { handle },
    ),
  );
}

/** `POST .../dispatches/{did}/launch-failed` — recorded, never retried into a loop. */
export async function launchFailed(
  scopeId: string,
  dispatchId: string,
  error: string,
): Promise<DispatchView> {
  return mapDispatch(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/dispatches/${encodeURIComponent(dispatchId)}/launch-failed`,
      "POST",
      { error },
    ),
  );
}

/**
 * `POST .../dispatches/{did}/return` — record a worker's result as RETURNED.
 *
 * **Never VERIFIED.** This route cannot hand a task `verified`, which is the
 * whole point of the state machine; only the task's own gates do that.
 */
export async function workerReturned(
  scopeId: string,
  dispatchId: string,
  reason = "",
  artifacts: string[] = [],
): Promise<Record<string, unknown>> {
  return rec(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/dispatches/${encodeURIComponent(dispatchId)}/return`,
      "POST",
      {
        reason,
        artifacts,
      },
    ),
  );
}

/** `POST .../tasks/{tid}/block` — park everything that depends on it. */
export async function blockTask(
  scopeId: string,
  taskId: string,
  reason: string,
): Promise<Record<string, unknown>> {
  assertReason(reason);
  return rec(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/tasks/${encodeURIComponent(taskId)}/block`,
      "POST",
      { reason },
    ),
  );
}

/**
 * `POST .../tasks/{tid}/verify` — run a task's gates and record the evidence.
 *
 * `status` computes without executing; `verify` runs only what is not already
 * proven current; `reverify` re-runs required gates even when they previously
 * passed. Retry exhaustion moves the task to FAILED with the reason — never to
 * VERIFIED.
 */
export async function verifyTask(
  scopeId: string,
  taskId: string,
  mode: VerifyMode = "verify",
  worker = "",
): Promise<VerifyResult> {
  assertVerifyMode(mode);
  return mapVerifyResult(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/tasks/${encodeURIComponent(taskId)}/verify`,
      "POST",
      { mode, worker },
    ),
  );
}

/** `POST .../gates/{gid}/run` — execute one gate and record its evidence. */
export async function runGate(
  scopeId: string,
  gateId: string,
  worker = "",
): Promise<EvidenceRow> {
  return mapEvidence(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/gates/${encodeURIComponent(gateId)}/run`,
      "POST",
      { worker },
    ),
  );
}

/** `POST .../gates/{gid}/approve` — the grant is bound to the gate's digest. */
export async function approveGate(
  scopeId: string,
  gateId: string,
  approver: string,
  reason = "",
): Promise<Record<string, unknown>> {
  return rec(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/gates/${encodeURIComponent(gateId)}/approve`,
      "POST",
      { approver, reason },
    ),
  );
}

/** `POST .../gates/{gid}/revoke` — a later run needs a fresh grant. */
export async function revokeGate(
  scopeId: string,
  gateId: string,
  reason = "",
): Promise<Record<string, unknown>> {
  return rec(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/gates/${encodeURIComponent(gateId)}/revoke`,
      "POST",
      { reason },
    ),
  );
}

/**
 * `POST .../gates/{gid}/review` — a human verdict on a `manual.review` gate.
 *
 * The verdict lands as evidence attributed to a named reviewer — never
 * disguised as an automated pass. A failed review is FAILED evidence, not
 * NOT_RUN.
 */
export async function reviewGate(
  scopeId: string,
  gateId: string,
  verdict: ReviewVerdict,
  reviewer: string,
  notes = "",
): Promise<Record<string, unknown>> {
  assertReviewVerdict(verdict);
  return rec(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/gates/${encodeURIComponent(gateId)}/review`,
      "POST",
      { verdict, reviewer, notes },
    ),
  );
}

/** `POST /completion/scopes/{id}/recover` — fold what the host reports back in. */
export async function recoverScope(
  scopeId: string,
  hostStates: Record<string, string>,
): Promise<Record<string, unknown>> {
  return rec(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/recover`,
      "POST",
      { host_states: hostStates },
    ),
  );
}

/**
 * `POST /completion/scopes/{id}/finalize` — the completion guard.
 *
 * `rerun_required` (default true) re-executes required gates at this boundary
 * so a stale pass cannot answer for work done since. A non-complete outcome is
 * a 200 with `complete: false` and its blockers — the refusal *is* the answer,
 * not a transport error. In observe mode (or on a disabled engine) the decision
 * is recorded with `enforced: false` and the status is left alone.
 */
export async function finalizeScope(
  scopeId: string,
  rerunRequired = true,
  rerunGateIds: string[] | null = null,
): Promise<FinalReport> {
  const payload: { rerun_required: boolean; rerun_gate_ids?: string[] } = {
    rerun_required: rerunRequired,
  };
  if (rerunGateIds !== null) payload.rerun_gate_ids = rerunGateIds;
  return mapFinalReport(
    await send<unknown>(
      `/completion/scopes/${encodeURIComponent(scopeId)}/finalize`,
      "POST",
      payload,
    ),
  );
}

/**
 * The gates count a panel may quote as "passed", or `null`.
 *
 * `null` when the server sent no number: "zero gates passed" and "the count
 * was not reported" are opposite claims, and only the first is a fact about
 * the work.
 */
export function gatesPassedText(summary: ScopeSummary): string {
  return summary.gates_passed === null
    ? "not reported"
    : String(summary.gates_passed);
}

/**
 * The freshness sentence for one gate's latest evidence.
 *
 * Two different absences, kept apart: a gate with **no** evidence has never
 * been checked at all, while a gate whose evidence is `stale` was checked and
 * no longer speaks for the current world. Neither is a pass.
 */
export function freshnessText(gate: GateView): string {
  if (!gate.latest_evidence)
    return "no evidence recorded — this gate has never been checked";
  const freshness = gate.latest_evidence.freshness;
  if (freshness === null || freshness === "") return "freshness not reported";
  if (freshness === "stale")
    return "stale — a pass from before the last edit cannot answer for work done since";
  if (freshness === "unknown")
    return "freshness unknown — the inputs this check observed could not be re-read";
  return `freshness: ${freshness}`;
}
