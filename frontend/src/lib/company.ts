import { get, send, asList, pick } from "./http";

/**
 * Company OS client.
 *
 * Tenancy is server-owned: every path here carries an explicit `company_id` and
 * the Gateway resolves the owner from the authenticated principal. A client must
 * never send an owner, and must never assume a "default" company — an
 * ownerless read that silently fell back to the first company is exactly the bug
 * this client avoids by requiring the id on every call.
 *
 * Honesty rules applied throughout:
 *   - absent optional numbers map to `null`, never `0`;
 *   - an unreadable subsystem keeps its own `reachable: false` so a failed read
 *     never renders as an empty list;
 *   - every health/spend figure carries the `basis` the server measured it with.
 */

export interface CompanyArchetype {
  archetype: string;
  display_name: string;
  description: string;
}

export interface Charter {
  mission: string;
  vision: string;
  values: string[];
  constraints: string[];
  autonomy_tier: string;
  budget_daily_usd: number | null;
  budget_total_usd: number | null;
  currency: string;
}

export interface CompanySummary {
  company_id: string;
  name: string;
  archetype: string;
  description: string;
  state: string;
  charter: Charter;
  created_at: number;
  updated_at: number;
}

export interface KeyResult {
  key_result_id: string;
  title: string;
  unit: string;
  baseline: number | null;
  target: number | null;
  current: number | null;
  current_basis: string;
  source_kpi_id: string | null;
  source_board_id: string | null;
}

export interface Objective {
  objective_id: string;
  title: string;
  rationale: string;
  status: string;
  owner_unit_id: string | null;
  key_results: KeyResult[];
  project_ids: string[];
}

export interface OrgUnit {
  unit_id: string;
  name: string;
  kind: string;
  parent_unit_id: string | null;
  lead_agent_handle: string | null;
  purpose: string;
}

export interface Role {
  role_id: string;
  title: string;
  clearance: string;
  responsibilities: string[];
  decision_authority: string[];
  max_concurrent_assignments: number;
}

export interface Employment {
  employment_id: string;
  agent_handle: string;
  employment_type: string;
  status: string;
  role_id: string | null;
  unit_id: string | null;
  reports_to_handle: string | null;
  backup_agent_handle: string | null;
  performance_score: number | null;
  performance_basis: string;
  last_review_at: number | null;
}

export interface WorkItem {
  task_id: string;
  board_id: string;
  title: string;
  assignee_agent_handle: string | null;
  column: string;
  priority: string;
  definition_of_done: string[];
  cost_usd: number | null;
  cost_basis: string;
}

export interface ProjectLink {
  project_id: string;
  title: string;
  lead_agent_handle: string | null;
  status: string;
  status_basis: string;
  objective_ids: string[];
}

export interface GroupLink {
  room_id: string;
  name: string;
  purpose: string;
  member_agent_handles: string[];
}

export interface ScheduleLink {
  schedule_id: string;
  backend: string;
  backend_task_id: string;
  title: string;
  recurrence: string;
  enabled: boolean;
}

export interface Accountability {
  accountability_id: string;
  title: string;
  primary_agent_handle: string;
  backup_agent_handle: string | null;
  escalation_agent_handle: string | null;
  status: string;
  active_agent_handle: string | null;
}

export interface Approval {
  approval_id: string;
  kind: string;
  title: string;
  rationale: string;
  status: string;
  requested_at: number;
  decision_note: string;
}

export interface Metrics {
  headcount: number;
  active_headcount: number;
  project_count: number;
  active_project_count: number;
  open_work_items: number;
  blocked_work_items: number;
  in_review_work_items: number;
  completed_work_items: number;
  room_count: number;
  schedule_count: number;
  objective_count: number;
  pending_approvals: number;
  health_percent: number | null;
  health_basis: string;
  measured_at: number | null;
}

export interface Health {
  health_percent: number | null;
  basis: string;
  reason?: string | null;
  components?: Record<string, number>;
}

export interface WorkSummary {
  reachable: boolean;
  error?: string;
  measured?: boolean;
  total_items?: number;
  open_items?: number;
  ready_items?: number;
  blocked_items?: number;
  in_review_items?: number;
  done_items?: number;
  unassigned_items?: number;
  items_missing_dod?: number;
}

export interface RosterDrift {
  employment_count: number;
  active_employment_count: number;
  missing_profile_count: number;
  missing_profiles: string[];
  unassigned_bot_count: number;
  unassigned_bots: string[];
  measured?: boolean;
}

export interface CostStatus {
  spent_today_usd: number | null;
  budget_daily_usd: number | null;
  budget_total_usd: number | null;
  utilization_percent: number | null;
  basis: string;
  measured_run_count: number;
  last_measured_at: number | null;
}

export interface LoopState {
  enabled: boolean;
  state: string;
  consecutive_no_progress_ticks: number;
  recent_outcomes: string[];
  last_ritual_at: Record<string, number>;
}

export interface Rituals {
  due: string[];
  ran: string[];
  last_ritual_at: Record<string, number>;
  cadence: Record<string, string>;
}

export interface Overview {
  company: CompanySummary & {
    roles: Role[];
    units: OrgUnit[];
    employments: Employment[];
    accountabilities: Accountability[];
    objectives: Objective[];
    projects: ProjectLink[];
    work_items: WorkItem[];
    groups: GroupLink[];
    schedules: ScheduleLink[];
    approvals: Approval[];
    loop_policy: Record<string, unknown>;
    cost: Record<string, unknown>;
  };
  metrics: Metrics;
  health: Health;
  work: WorkSummary;
  workforce: RosterDrift;
  cost: CostStatus;
  loop: LoopState;
  rituals: Rituals;
  approvals: Approval[];
  invariants_ok: boolean;
  invariant_problems: string[];
}

export interface TickRecord {
  tick_id: string;
  outcome: string;
  reason: string;
  started_at: number;
  duration_ms: number | null;
  dispatched: Array<Record<string, unknown>>;
  deferred: Array<Record<string, unknown>>;
  failures: Array<Record<string, unknown>>;
  cost_usd: number | null;
  cost_basis: string;
  observed: Record<string, number>;
}

export interface OrgNode {
  handle: string;
  employment_type: string;
  status: string;
  reports_to: string | null;
  unit_id: string | null;
  unit_name: string | null;
  role_title: string | null;
  clearance: string | null;
  decision_authority: string[];
}

export interface PlannedEmployee {
  handle: string;
  display_name: string;
  role: string;
  unit: string;
  reports_to: string | null;
  employment_type: string;
  clearance: string;
  bot_template: string | null;
  requested_capabilities: string[];
  decision_authority: string[];
}

/** Rejects on failure — an unreachable catalogue must not read as "no archetypes". */
export async function listArchetypes(): Promise<CompanyArchetype[]> {
  const d = await get<Record<string, unknown>>("/companies/archetypes");
  return asList(d, ["archetypes", "data"]).map((a) => ({
    archetype: String(pick(a, ["archetype"], "")),
    display_name: String(pick(a, ["display_name"], "")),
    description: String(pick(a, ["description"], "")),
  }));
}

/** Preview a blueprint without creating anything. */
export async function previewBlueprint(payload: {
  prompt: string;
  name?: string;
  archetype?: string | null;
  autonomy_tier?: string;
  budget_daily_usd?: number | null;
}): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>("/companies/preview", "POST", {
    prompt: payload.prompt,
    name: payload.name ?? "",
    archetype: payload.archetype ?? null,
    autonomy_tier: payload.autonomy_tier ?? "T1_advise",
    budget_daily_usd: payload.budget_daily_usd ?? null,
  });
}

/**
 * Every company owned by the caller.
 *
 * `asList` returns `[]` for an unrecognised envelope, which would look like
 * "you have no companies". This throws instead so the section can distinguish a
 * failed read from an empty portfolio.
 */
export async function listCompanies(): Promise<CompanySummary[]> {
  const body = await get<unknown>("/companies");
  let rows: unknown = body;
  if (!Array.isArray(rows)) {
    if (!rows || typeof rows !== "object") {
      throw new Error("The server returned an unreadable company list.");
    }
    const rec = rows as Record<string, unknown>;
    const nested = [rec.companies, rec.data].find((v) => Array.isArray(v));
    if (!nested) throw new Error("The server returned an unreadable company list.");
    rows = nested;
  }
  return (rows as unknown[]).map((c) => {
    const rec = (c ?? {}) as Record<string, unknown>;
    return {
      company_id: String(pick(rec, ["company_id"], "")),
      name: String(pick(rec, ["name"], "")),
      archetype: String(pick(rec, ["archetype"], "unknown")),
      description: String(pick(rec, ["description"], "")),
      state: String(pick(rec, ["state"], "unknown")),
      charter: (rec.charter ?? {}) as Charter,
      created_at: Number(pick(rec, ["created_at"], 0)),
      updated_at: Number(pick(rec, ["updated_at"], 0)),
    };
  });
}

export async function getCompany(companyId: string): Promise<Overview> {
  const d = await get<Record<string, unknown>>(`/companies/${encodeURIComponent(companyId)}/overview`);
  const rawHealth = (d.health ?? {}) as Record<string, unknown>;
  const rawCost = (d.cost ?? {}) as Record<string, unknown>;
  const rawMetrics = (d.metrics ?? {}) as Record<string, unknown>;
  return {
    company: (d.company ?? {}) as Overview["company"],
    metrics: {
      ...(rawMetrics as unknown as Metrics),
      health_percent: numOrNull(rawMetrics.health_percent),
    },
    // Every optional number is normalised so an absent figure can never reach the
    // UI as `undefined` and be rendered as zero.
    health: {
      health_percent: numOrNull(rawHealth.health_percent),
      basis: String(pick(rawHealth, ["basis"], "unmeasured")),
      reason: pick(rawHealth, ["reason"], null) as string | null,
    },
    work: (d.work ?? { reachable: false }) as WorkSummary,
    workforce: (d.workforce ?? {}) as RosterDrift,
    cost: {
      ...(rawCost as unknown as CostStatus),
      spent_today_usd: numOrNull(rawCost.spent_today_usd),
      budget_daily_usd: numOrNull(rawCost.budget_daily_usd),
      budget_total_usd: numOrNull(rawCost.budget_total_usd),
      utilization_percent: numOrNull(rawCost.utilization_percent),
      basis: String(pick(rawCost, ["basis"], "unmeasured")),
    },
    loop: (d.loop ?? {}) as LoopState,
    rituals: (d.rituals ?? { due: [], ran: [], last_ritual_at: {}, cadence: {} }) as Rituals,
    approvals: asList({ approvals: d.approvals }, ["approvals"]).map((a) => ({
      approval_id: String(pick(a, ["approval_id"], "")),
      kind: String(pick(a, ["kind"], "")),
      title: String(pick(a, ["title"], "")),
      rationale: String(pick(a, ["rationale"], "")),
      status: String(pick(a, ["status"], "")),
      requested_at: Number(pick(a, ["requested_at"], 0)),
      decision_note: String(pick(a, ["decision_note"], "")),
    })) as Approval[],
    invariants_ok: rec_bool(d.invariants_ok),
    invariant_problems: Array.isArray(d.invariant_problems) ? (d.invariant_problems as string[]) : [],
  };
}

export { boolOrNull, numOrNull };

/** Normalises an absent numeric to `null`, never to `0` and never to `undefined`. */
function numOrNull(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return null;
}

/** Normalises an absent boolean to `null` so the UI can say "not reported". */
function boolOrNull(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function rec_bool(v: unknown): boolean {
  return typeof v === "boolean" ? v : false;
}

export async function createCompany(payload: {
  prompt: string;
  name: string;
  archetype?: string | null;
  autonomy_tier?: string;
  budget_daily_usd?: number | null;
  hire?: boolean;
}): Promise<{ company: CompanySummary; hire: Record<string, unknown> }> {
  const d = await send<Record<string, unknown>>("/companies", "POST", {
    prompt: payload.prompt,
    name: payload.name,
    archetype: payload.archetype ?? null,
    autonomy_tier: payload.autonomy_tier ?? "T1_advise",
    budget_daily_usd: payload.budget_daily_usd ?? null,
    hire: payload.hire ?? true,
  });
  return {
    company: (d.company ?? {}) as CompanySummary,
    hire: (d.hire ?? {}) as Record<string, unknown>,
  };
}

export async function getOrgChart(companyId: string): Promise<{
  nodes: OrgNode[];
  units: OrgUnit[];
  accountabilities: Accountability[];
}> {
  const d = await get<Record<string, unknown>>(`/companies/${encodeURIComponent(companyId)}/org`);
  return {
    nodes: asList(d, ["nodes"]).map((n) => ({
      handle: String(pick(n, ["handle"], "")),
      employment_type: String(pick(n, ["employment_type"], "employee")),
      status: String(pick(n, ["status"], "unknown")),
      reports_to: pick(n, ["reports_to"], null) as string | null,
      unit_id: pick(n, ["unit_id"], null) as string | null,
      unit_name: pick(n, ["unit_name"], null) as string | null,
      role_title: pick(n, ["role_title"], null) as string | null,
      clearance: pick(n, ["clearance"], null) as string | null,
      decision_authority: Array.isArray(n.decision_authority) ? (n.decision_authority as string[]) : [],
    })) as OrgNode[],
    units: asList(d, ["units"]) as unknown as OrgUnit[],
    accountabilities: asList(d, ["accountabilities"]) as unknown as Accountability[],
  };
}

export async function hireEmployee(
  companyId: string,
  payload: {
    handle: string;
    role_title: string;
    unit_name: string;
    reports_to_handle?: string | null;
    bot_template?: string | null;
  },
): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(`/companies/${encodeURIComponent(companyId)}/employees`, "POST", payload);
}

export async function cloneEmployee(
  companyId: string,
  payload: { source: string; handle: string; role?: string | null; display_name?: string | null },
): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(`/companies/${encodeURIComponent(companyId)}/employees/clone`, "POST", payload);
}

export async function terminateEmployee(companyId: string, handle: string): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(`/companies/${encodeURIComponent(companyId)}/employees/terminate`, "POST", { handle });
}

export async function promoteEmployee(companyId: string, handle: string): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(`/companies/${encodeURIComponent(companyId)}/employees/promote`, "POST", { handle });
}

export async function reviewEmployee(
  companyId: string,
  handle: string,
  payload: { score: number | null; note?: string },
): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(`/companies/${encodeURIComponent(companyId)}/employees/review`, "POST", {
    handle,
    score: payload.score,
    note: payload.note ?? "",
  });
}

export async function createWorkItem(
  companyId: string,
  payload: {
    title: string;
    description?: string;
    priority?: string;
    assignee_agent_handle?: string | null;
    definition_of_done?: string[];
    objective_id?: string | null;
  },
): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(`/companies/${encodeURIComponent(companyId)}/work`, "POST", payload);
}

export async function moveWorkItem(companyId: string, taskId: string, column: string): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(
    `/companies/${encodeURIComponent(companyId)}/work/${encodeURIComponent(taskId)}/move`,
    "POST",
    { column },
  );
}

export async function createGroup(
  companyId: string,
  payload: { name: string; topic?: string; members?: string[] },
): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(`/companies/${encodeURIComponent(companyId)}/groups`, "POST", payload);
}

export async function announce(
  companyId: string,
  payload: { room_id: string; sender?: string; content: string },
): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(`/companies/${encodeURIComponent(companyId)}/announce`, "POST", {
    room_id: payload.room_id,
    sender: payload.sender ?? "company",
    content: payload.content,
  });
}

export async function attachProject(
  companyId: string,
  payload: { project_id: string; title?: string; objective_ids?: string[] },
): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(`/companies/${encodeURIComponent(companyId)}/projects`, "POST", payload);
}

/** `limit` is clamped to the router's 1..200 window. */
export async function listTicks(companyId: string, limit = 25): Promise<TickRecord[]> {
  const bounded = Math.max(1, Math.min(Math.floor(limit), 200));
  const d = await get<Record<string, unknown>>(`/companies/${encodeURIComponent(companyId)}/loop?limit=${bounded}`);
  return asList(d, ["ticks"]).map((t) => ({
    tick_id: String(pick(t, ["tick_id"], "")),
    outcome: String(pick(t, ["outcome"], "unknown")),
    reason: String(pick(t, ["reason"], "")),
    started_at: Number(pick(t, ["started_at"], 0)),
    duration_ms: pick(t, ["duration_ms"], null) as number | null,
    dispatched: Array.isArray(t.dispatched) ? (t.dispatched as Array<Record<string, unknown>>) : [],
    deferred: Array.isArray(t.deferred) ? (t.deferred as Array<Record<string, unknown>>) : [],
    failures: Array.isArray(t.failures) ? (t.failures as Array<Record<string, unknown>>) : [],
    cost_usd: pick(t, ["cost_usd"], null) as number | null,
    cost_basis: String(pick(t, ["cost_basis"], "unmeasured")),
    observed: (t.observed ?? {}) as Record<string, number>,
  }));
}

export async function runTick(companyId: string): Promise<TickRecord> {
  const d = await send<Record<string, unknown>>(`/companies/${encodeURIComponent(companyId)}/loop/tick`, "POST", {});
  const t = (d.tick ?? {}) as Record<string, unknown>;
  return {
    tick_id: String(pick(t, ["tick_id"], "")),
    outcome: String(pick(t, ["outcome"], "unknown")),
    reason: String(pick(t, ["reason"], "")),
    started_at: Number(pick(t, ["started_at"], 0)),
    duration_ms: pick(t, ["duration_ms"], null) as number | null,
    dispatched: Array.isArray(t.dispatched) ? (t.dispatched as Array<Record<string, unknown>>) : [],
    deferred: Array.isArray(t.deferred) ? (t.deferred as Array<Record<string, unknown>>) : [],
    failures: Array.isArray(t.failures) ? (t.failures as Array<Record<string, unknown>>) : [],
    cost_usd: pick(t, ["cost_usd"], null) as number | null,
    cost_basis: String(pick(t, ["cost_basis"], "unmeasured")),
    observed: (t.observed ?? {}) as Record<string, number>,
  };
}

export async function setLoopPolicy(
  companyId: string,
  patch: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(`/companies/${encodeURIComponent(companyId)}/loop`, "PATCH", patch);
}

export async function setCompanyState(
  companyId: string,
  state: string,
): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(`/companies/${encodeURIComponent(companyId)}/state`, "POST", { state });
}

export async function listApprovals(companyId: string, status?: string): Promise<Approval[]> {
  const q = status ? `?status=${encodeURIComponent(status)}` : "";
  const d = await get<Record<string, unknown>>(`/companies/${encodeURIComponent(companyId)}/approvals${q}`);
  return asList(d, ["approvals"]).map((a) => ({
    approval_id: String(pick(a, ["approval_id"], "")),
    kind: String(pick(a, ["kind"], "")),
    title: String(pick(a, ["title"], "")),
    rationale: String(pick(a, ["rationale"], "")),
    status: String(pick(a, ["status"], "")),
    requested_at: Number(pick(a, ["requested_at"], 0)),
    decision_note: String(pick(a, ["decision_note"], "")),
  })) as Approval[];
}

export async function decideApproval(
  companyId: string,
  approvalId: string,
  decision: "approve" | "reject",
  note = "",
): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(
    `/companies/${encodeURIComponent(companyId)}/approvals/${encodeURIComponent(approvalId)}/${decision}`,
    "POST",
    { note },
  );
}

/** Renders a measured health figure honestly: no number, no claim. */
export function healthLabel(health: Health | null | undefined): string {
  if (!health || health.health_percent === null || health.health_percent === undefined) {
    return health?.reason ? `Not measured — ${health.reason}` : "Not measured";
  }
  return `${health.health_percent}%`;
}

export function healthTone(health: Health | null | undefined): "green" | "amber" | "gray" {
  if (!health || health.health_percent === null || health.health_percent === undefined) return "gray";
  if (health.health_percent >= 70) return "green";
  if (health.health_percent >= 40) return "amber";
  return "gray";
}

/** Formats a nullable USD figure without ever rendering a fabricated 0. */
export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "not measured";
  return `$${value.toFixed(4)}`;
}

export function tickTone(outcome: string): "green" | "blue" | "amber" | "red" | "gray" {
  switch (outcome) {
    case "dispatched":
      return "green";
    case "idle":
      return "blue";
    case "deferred":
    case "paused":
    case "budget_exhausted":
      return "amber";
    case "error":
      return "red";
    case "no_progress":
      return "amber";
    default:
      return "gray";
  }
}