import { get, send } from "./http";

/**
 * Delegation scenarios and the advisory plan they resolve to.
 *
 * These two routes are **previews**. `alpha.subagents.plan` imports no engine,
 * so a plan returned here routes nothing - it is what the planner *would* take.
 * Every mapping below therefore keeps `shadow` and `isExecutable` as real
 * values rather than assuming them, so a future non-shadow build cannot be
 * rendered by this client as though it were advisory.
 */

/** One delegation shape. `requirements` are what must be *measured* true first. */
export type Scenario = {
  id: string;
  title: string;
  summary: string;
  /** The mechanism this shape would use: none | single | fan_out | arena | council | pipeline | async. */
  dispatch: string;
  requirements: string[];
  estimatedChildren: number;
  /** The engine that would carry it out, or `""` for a shape that needs none. */
  engine: string;
};

/**
 * A tri-state capability reading.
 *
 * `null` is **unknown**, not absent - a failed read must not be rendered as
 * "not available". This is the same rule the Arena picker follows for its row
 * gating.
 */
export type Measured = boolean | null;

export type ExecutionPlan = {
  scenario: string;
  dispatch: string;
  reason: string;
  estimatedChildren: number;
  admittedChildren: number;
  /** Higher-priority scenarios that were passed over, each with the reason. */
  considered: Array<{ scenario: string; why: string }>;
  /** Anything that had to be lowered, naming its bound. */
  degradations: string[];
  shadow: boolean;
  isExecutable: boolean;
  perceivedTier: string;
  complexity: number;
};

export type ScenarioCatalogue = {
  scenarios: Scenario[];
  /** Whether an unresolved plan is advisory by default. Read it; do not assume. */
  shadowDefault: boolean;
};

const _str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);
const _num = (v: unknown, fallback = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : fallback);

/**
 * A measured flag, kept tri-state.
 *
 * A missing field is `null` (unknown). Coercing it to `false` would let a
 * degraded read make a working capability look absent.
 */
const _measured = (v: unknown): Measured => (typeof v === "boolean" ? v : null);

function toScenario(row: unknown): Scenario | null {
  if (!row || typeof row !== "object") return null;
  const r = row as Record<string, unknown>;
  if (typeof r.id !== "string" || !r.id) return null;
  return {
    id: r.id,
    title: _str(r.title, r.id),
    summary: _str(r.summary),
    dispatch: _str(r.dispatch, "none"),
    requirements: Array.isArray(r.requirements) ? r.requirements.filter((x): x is string => typeof x === "string") : [],
    estimatedChildren: _num(r.estimated_children),
    engine: _str(r.engine),
  };
}

function toPlan(row: unknown): ExecutionPlan {
  const r = (row && typeof row === "object" ? row : {}) as Record<string, unknown>;
  const considered = Array.isArray(r.considered)
    ? r.considered
        .filter((pair): pair is unknown[] => Array.isArray(pair) && pair.length >= 2)
        .map((pair) => ({ scenario: _str(pair[0]), why: _str(pair[1]) }))
    : [];
  return {
    scenario: _str(r.scenario, "direct"),
    dispatch: _str(r.dispatch, "none"),
    reason: _str(r.reason),
    estimatedChildren: _num(r.estimated_children),
    admittedChildren: _num(r.admitted_children),
    considered,
    degradations: Array.isArray(r.degradations) ? r.degradations.filter((x): x is string => typeof x === "string") : [],
    // Read, never assumed: a future non-shadow build must not be mislabelled.
    shadow: r.shadow === true,
    isExecutable: r.is_executable === true,
    perceivedTier: _str(r.perceived_tier),
    complexity: _num(r.complexity),
  };
}

export async function getScenarioCatalogue(): Promise<ScenarioCatalogue> {
  const value = (await get<unknown>("/subagents/scenarios")) as Record<string, unknown> | null;
  const rows = value && Array.isArray(value.scenarios) ? value.scenarios : [];
  return {
    scenarios: rows.map(toScenario).filter((s): s is Scenario => s !== null),
    shadowDefault: value?.shadow_default === true,
  };
}

export type PlanInputs = {
  prompt: string;
  delegationEnabled: boolean;
  arena?: Measured;
  council?: Measured;
  workflow?: Measured;
  childBudget?: number | null;
  scenario?: string;
};

/**
 * Resolve an advisory plan for a draft prompt.
 *
 * Omitted capability fields are simply not sent, so the Gateway reads them as
 * *unknown* and reports that wording - rather than this client inventing a
 * `false` the operator never measured.
 */
export async function previewExecutionPlan(inputs: PlanInputs): Promise<ExecutionPlan> {
  const payload: Record<string, unknown> = {
    prompt: inputs.prompt,
    delegation_enabled: inputs.delegationEnabled,
  };
  if (inputs.arena !== undefined) payload.arena_configured = inputs.arena;
  if (inputs.council !== undefined) payload.council_configured = inputs.council;
  if (inputs.workflow !== undefined) payload.workflow_configured = inputs.workflow;
  if (inputs.childBudget !== undefined && inputs.childBudget !== null) payload.child_budget = inputs.childBudget;
  if (inputs.scenario) payload.scenario = inputs.scenario;
  return toPlan(await send<unknown>("/subagents/plan", "POST", payload));
}

/** One-line operator copy for a resolved plan. */
export function planHeadline(plan: ExecutionPlan): string {
  return plan.scenario === "direct" ? "No delegation planned" : `Planned: ${plan.scenario.replace(/_/g, " ")}`;
}

/**
 * Whether a resolved plan is worth showing the operator.
 *
 * Lives here rather than in the component because `pnpm test` only globs
 * `src/lib/*.test.mjs` - a predicate written in a `.tsx` would ship untested.
 *
 * The subtlety this encodes: a plan that resolved to `direct` is **not**
 * automatically silent. When a higher-priority scenario was passed over, that
 * skip is the most useful sentence on screen ("you asked for a tournament, Arena
 * could not be read, so this will run directly"). Gating on `degradations`
 * alone - which is what this did first - hid exactly that case, because a skip
 * lands in `considered` and only an explicit request or a budget cut populates
 * `degradations`.
 */
export function planIsNoteworthy(plan: ExecutionPlan): boolean {
  return plan.scenario !== "direct" || plan.degradations.length > 0 || plan.considered.length > 0;
}