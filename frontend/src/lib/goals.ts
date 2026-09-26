// lib/goals.ts — real-API client for the goals domain:
//  * /goals/contracts  (goal_contracts.py, 7 routes — strict pydantic models
//    in alpha/goals/models.py: GoalContract, PlanVersion, TaskAttempt)
//  * /goal-integrity   (goal_integrity.py — GoalIntegrityReport from
//    alpha/planning/integrity.py)
//  * /missions         (missions.py, 5 routes — Mission.to_dict from
//    alpha/missions/store.py)
// Field names mirror the router responses exactly. Statuses and scores pass
// through as the server sent them; failures reject instead of returning
// fabricated records.
import { get, send, asList, pick } from "./http";

/* ── Goal contracts (goal_contracts.py) ──────────────────────────────── */

export type GoalContractStatus = "active" | "achieved" | "abandoned";

export interface GoalContract {
  id: string;
  owner_id: string;
  created_at: number;
  updated_at: number;
  objective: string;
  status: GoalContractStatus | string;
}

function toContract(c: Record<string, unknown>): GoalContract {
  const created = Number(pick(c, ["created_at"], 0));
  const updated = Number(pick(c, ["updated_at"], 0));
  return {
    id: String(pick(c, ["id"], "")),
    owner_id: String(pick(c, ["owner_id"], "")),
    created_at: Number.isFinite(created) ? created : 0,
    updated_at: Number.isFinite(updated) ? updated : 0,
    objective: String(pick(c, ["objective"], "")),
    status: String(pick(c, ["status"], "unknown")),
  };
}

/** GET /goals/contracts → GoalContract[] (goal_contracts.py:74) */
export async function listGoalContracts(): Promise<GoalContract[]> {
  const d = await get<unknown>("/goals/contracts");
  return asList(d, ["contracts", "data", "items"]).map(toContract);
}

/** GET /goals/contracts/{contract_id} → GoalContract (goal_contracts.py:86) */
export async function getGoalContract(contractId: string): Promise<GoalContract> {
  const d = await get<Record<string, unknown>>(`/goals/contracts/${encodeURIComponent(contractId)}`);
  return toContract(d);
}

/** POST /goals/contracts → GoalContract (goal_contracts.py:80) */
export async function createGoalContract(objective: string): Promise<GoalContract> {
  const d = await send<Record<string, unknown>>("/goals/contracts", "POST", { objective });
  return toContract(d);
}

export type PlanStatus = "draft" | "approved" | "superseded";

export interface PlanVersion {
  id: string;
  owner_id: string;
  created_at: number;
  updated_at: number;
  contract_id: string;
  version: number;
  content: Record<string, unknown>;
  status: PlanStatus | string;
}

function toPlan(p: Record<string, unknown>): PlanVersion {
  const created = Number(pick(p, ["created_at"], 0));
  const updated = Number(pick(p, ["updated_at"], 0));
  const version = Number(pick(p, ["version"], 0));
  return {
    id: String(pick(p, ["id"], "")),
    owner_id: String(pick(p, ["owner_id"], "")),
    created_at: Number.isFinite(created) ? created : 0,
    updated_at: Number.isFinite(updated) ? updated : 0,
    contract_id: String(pick(p, ["contract_id"], "")),
    version: Number.isFinite(version) ? version : 0,
    content: p.content && typeof p.content === "object" ? (p.content as Record<string, unknown>) : {},
    status: String(pick(p, ["status"], "unknown")),
  };
}

/** POST /goals/contracts/{contract_id}/plans → PlanVersion (goal_contracts.py:92) */
export async function createGoalPlan(contractId: string, content: Record<string, unknown>): Promise<PlanVersion> {
  const d = await send<Record<string, unknown>>(
    `/goals/contracts/${encodeURIComponent(contractId)}/plans`,
    "POST",
    { content },
  );
  return toPlan(d);
}

/** POST /goals/contracts/{contract_id}/plans/{version}/approve → PlanVersion (goal_contracts.py:98) */
export async function approveGoalPlan(contractId: string, version: number): Promise<PlanVersion> {
  const d = await send<Record<string, unknown>>(
    `/goals/contracts/${encodeURIComponent(contractId)}/plans/${version}/approve`,
    "POST",
  );
  return toPlan(d);
}

export type AttemptStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";

/** The AttemptStatus literal the server model accepts (alpha/goals/models.py:11). */
export const ATTEMPT_STATUSES: readonly AttemptStatus[] = [
  "pending",
  "running",
  "succeeded",
  "failed",
  "cancelled",
];

export interface TaskAttempt {
  id: string;
  owner_id: string;
  created_at: number;
  updated_at: number;
  plan_id: string;
  intent: string;
  status: AttemptStatus | string;
}

function toAttempt(a: Record<string, unknown>): TaskAttempt {
  const created = Number(pick(a, ["created_at"], 0));
  const updated = Number(pick(a, ["updated_at"], 0));
  return {
    id: String(pick(a, ["id"], "")),
    owner_id: String(pick(a, ["owner_id"], "")),
    created_at: Number.isFinite(created) ? created : 0,
    updated_at: Number.isFinite(updated) ? updated : 0,
    plan_id: String(pick(a, ["plan_id"], "")),
    intent: String(pick(a, ["intent"], "")),
    status: String(pick(a, ["status"], "unknown")),
  };
}

/** POST /goals/contracts/{contract_id}/attempts → TaskAttempt (goal_contracts.py:110) */
export async function createGoalAttempt(
  contractId: string,
  planId: string,
  intent: string,
): Promise<TaskAttempt> {
  const d = await send<Record<string, unknown>>(
    `/goals/contracts/${encodeURIComponent(contractId)}/attempts`,
    "POST",
    { plan_id: planId, intent },
  );
  return toAttempt(d);
}

/** POST /goals/contracts/{contract_id}/attempts/{attempt_id}/transition → TaskAttempt (goal_contracts.py:123) */
export async function transitionGoalAttempt(
  contractId: string,
  attemptId: string,
  status: AttemptStatus,
): Promise<TaskAttempt> {
  const d = await send<Record<string, unknown>>(
    `/goals/contracts/${encodeURIComponent(contractId)}/attempts/${encodeURIComponent(attemptId)}/transition`,
    "POST",
    { status },
  );
  return toAttempt(d);
}

/* ── Goal integrity audit (goal_integrity.py) ────────────────────────── */

export interface IntegrityFlaggedTask {
  task_id: string;
  description: string;
  reason: string;
}

export interface IntegrityReport {
  mission_goal: string;
  audited_subtasks_count: number;
  drift_score: number;
  is_aligned: boolean;
  scope_creep_detected: boolean;
  overengineering_detected: boolean;
  flagged_tasks: IntegrityFlaggedTask[];
  findings: string[];
  recommendations: string[];
  timestamp: number;
}

/** POST /goal-integrity/audit → GoalIntegrityReport.model_dump() (goal_integrity.py:22) */
export async function auditGoalIntegrity(missionGoal: string, subtasks: string[]): Promise<IntegrityReport> {
  const d = await send<Record<string, unknown>>("/goal-integrity/audit", "POST", {
    mission_goal: missionGoal,
    subtasks,
  });
  const count = Number(pick(d, ["audited_subtasks_count"], 0));
  const drift = Number(pick(d, ["drift_score"], 0));
  const timestamp = Number(pick(d, ["timestamp"], 0));
  return {
    mission_goal: String(pick(d, ["mission_goal"], missionGoal)),
    audited_subtasks_count: Number.isFinite(count) ? count : 0,
    drift_score: Number.isFinite(drift) ? drift : 0,
    is_aligned: d.is_aligned === true,
    scope_creep_detected: d.scope_creep_detected === true,
    overengineering_detected: d.overengineering_detected === true,
    flagged_tasks: Array.isArray(d.flagged_tasks)
      ? (d.flagged_tasks as Array<Record<string, unknown>>).map((t) => ({
          task_id: String(pick(t, ["task_id"], "")),
          description: String(pick(t, ["description"], "")),
          reason: String(pick(t, ["reason"], "")),
        }))
      : [],
    findings: toStringArray(d.findings),
    recommendations: toStringArray(d.recommendations),
    timestamp: Number.isFinite(timestamp) ? timestamp : 0,
  };
}

function toStringArray(v: unknown): string[] {
  return Array.isArray(v) ? v.map((x) => String(x)) : [];
}

/* ── Missions (missions.py) ──────────────────────────────────────────── */

export type MissionStatus = "draft" | "active" | "paused" | "completed" | "cancelled";

/** Values the transition route itself accepts (missions.py:91); the store enforces legality. */
export const MISSION_TRANSITION_TARGETS = ["active", "paused", "completed", "cancelled"] as const;

export interface Mission {
  mission_id: string;
  owner: string;
  objective: string;
  constraints: Record<string, unknown>;
  budget: Record<string, unknown>;
  status: MissionStatus | string;
  thread_ids: string[];
  artifacts: string[];
  created_at: number;
  updated_at: number;
}

function toMission(m: Record<string, unknown>): Mission {
  const created = Number(pick(m, ["created_at"], 0));
  const updated = Number(pick(m, ["updated_at"], 0));
  return {
    mission_id: String(pick(m, ["mission_id"], "")),
    owner: String(pick(m, ["owner"], "")),
    objective: String(pick(m, ["objective"], "")),
    constraints: m.constraints && typeof m.constraints === "object" ? (m.constraints as Record<string, unknown>) : {},
    budget: m.budget && typeof m.budget === "object" ? (m.budget as Record<string, unknown>) : {},
    status: String(pick(m, ["status"], "unknown")),
    thread_ids: Array.isArray(m.thread_ids) ? m.thread_ids.map((x) => String(x)) : [],
    artifacts: Array.isArray(m.artifacts) ? m.artifacts.map((x) => String(x)) : [],
    created_at: Number.isFinite(created) ? created : 0,
    updated_at: Number.isFinite(updated) ? updated : 0,
  };
}

/** GET /missions → { missions: Mission[], count } (missions.py:44) */
export async function listMissions(status?: string): Promise<Mission[]> {
  const path = status ? `/missions?status=${encodeURIComponent(status)}` : "/missions";
  const d = await get<unknown>(path);
  return asList(d, ["missions", "data"]).map(toMission);
}

/** POST /missions → Mission.to_dict() (missions.py:30) */
export async function createMission(
  objective: string,
  opts: { constraints?: Record<string, unknown>; budget?: Record<string, unknown> } = {},
): Promise<Mission> {
  const d = await send<Record<string, unknown>>("/missions", "POST", {
    objective,
    constraints: opts.constraints ?? {},
    budget: opts.budget ?? {},
  });
  return toMission(d);
}

/** GET /missions/{mission_id} → Mission.to_dict() (missions.py:58) */
export async function getMission(missionId: string): Promise<Mission> {
  const d = await get<Record<string, unknown>>(`/missions/${encodeURIComponent(missionId)}`);
  return toMission(d);
}

/** POST /missions/{mission_id}/transition?to=X → Mission.to_dict() (missions.py:77; `to` is a query param) */
export async function transitionMission(missionId: string, to: string): Promise<Mission> {
  const d = await send<Record<string, unknown>>(
    `/missions/${encodeURIComponent(missionId)}/transition?to=${encodeURIComponent(to)}`,
    "POST",
  );
  return toMission(d);
}

/** POST /missions/{mission_id}/threads → Mission.to_dict() with the thread attached (missions.py:103) */
export async function attachMissionThread(missionId: string, threadId: string): Promise<Mission> {
  const d = await send<Record<string, unknown>>(`/missions/${encodeURIComponent(missionId)}/threads`, "POST", {
    thread_id: threadId,
  });
  return toMission(d);
}
