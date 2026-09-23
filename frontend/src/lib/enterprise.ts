/** Enterprise Client API for the Autonomous AI Software Enterprise War Room. */

import { req } from "@/lib/http";

export interface CapabilityContract {
  contract_id: string;
  role_id: string;
  title: string;
  department: string;
  clearance: string;
  capabilities: string[];
  max_concurrency: number;
  decision_authority: string[];
  active: boolean;
}

export interface OrgNode {
  node_id: string;
  bot_name: string;
  title: string;
  role_type: string;
  department: string;
  reports_to: string | null;
  subordinates: string[];
  contract: CapabilityContract;
  status: string;
}

export interface DepartmentHierarchy {
  dept_id: string;
  name: string;
  department_type: string;
  lead_bot_name: string;
  lead_title: string;
  workers: OrgNode[];
  capabilities: string[];
  token_budget: number;
  token_spent: number;
  burn_rate_tpm: number;
  circuit_breaker_active: boolean;
}

export interface EnterpriseHierarchyChart {
  enterprise_name: string;
  csuite: OrgNode[];
  departments: DepartmentHierarchy[];
  total_headcount: number;
  roles: OrgNode[];
}

export interface RFCReview {
  review_id: string;
  reviewer_bot: string;
  department: string;
  verdict: "approve" | "reject" | "amend";
  epistemic_confidence: number;
  argument: string;
  created_at: number;
}

export interface DebateArgument {
  argument_id: string;
  speaker_bot: string;
  department: string;
  stance: "pro" | "con" | "amend" | "synthesis";
  claim: string;
  evidence: string;
  counter_to_id: string | null;
  epistemic_weight: number;
  created_at: number;
}

export interface EnterpriseRFC {
  rfc_id: string;
  title: string;
  author_bot: string;
  department: string;
  status: "draft" | "under_review" | "debating" | "approved" | "rejected" | "implemented";
  summary: string;
  proposal_content: string;
  affected_departments: string[];
  reviews: RFCReview[];
  debate_thread: DebateArgument[];
  consensus_score: number;
  gating_passed: boolean;
  gating_reason: string;
  created_at: number;
  updated_at: number;
}

export interface TreasuryAllocation {
  dept_id: string;
  department_name: string;
  allocated_tokens: number;
  spent_tokens: number;
  balance_tokens: number;
  burn_rate_tpm: number;
  roi_velocity: number;
  circuit_breaker_active: boolean;
  circuit_breaker_threshold_tpm: number;
}

export interface TreasuryTelemetry {
  total_allocated_tokens: number;
  total_spent_tokens: number;
  total_balance_tokens: number;
  overall_burn_rate_tpm: number;
  active_circuit_breakers_count: number;
  average_roi_velocity: number;
  departments: TreasuryAllocation[];
}

export interface CryptographicSignature {
  signature_id: string;
  signatory_role: string;
  signatory_bot: string;
  signature_hash: string;
  payload_digest: string;
  timestamp: number;
  verified: boolean;
}

export interface ReleaseCandidate {
  release_id: string;
  version: string;
  component: string;
  description: string;
  diff_hash: string;
  holdout_benchmark_score: number;
  holdout_passed: boolean;
  security_scan_passed: boolean;
  architecture_approved: boolean;
  signatures: CryptographicSignature[];
  status: "staged" | "multi_sig_verified" | "promoted_active" | "rejected" | "archived";
  promoted_at: number | null;
  created_at: number;
}

export interface DAGTask {
  task_id: string;
  title: string;
  assigned_bot: string;
  department: string;
  dependencies: string[];
  status: "pending" | "ready" | "in_progress" | "completed" | "failed";
  definition_of_done: string[];
  dod_verified: boolean;
  execution_output: string;
  estimated_tokens: number;
}

export interface DAGSprint {
  sprint_id: string;
  spec_id: string;
  title: string;
  tasks: DAGTask[];
  topology_layers: string[][];
  status: "active" | "completed" | "blocked";
  progress_percent: number;
}

export interface EnterpriseTelemetry {
  heartbeat_cycle: number;
  uptime_seconds: number;
  csuite_status: Record<string, string>;
  departments_count: number;
  active_workers_count: number;
  active_rfcs_count: number;
  approved_rfcs_count: number;
  active_sprints_count: number;
  tasks_completed_count: number;
  treasury_overall_burn_rate_tpm: number;
  treasury_circuit_breakers_tripped: number;
  system_latency_p95_ms: number;
  security_posture_score: number | null;
  holdout_pass_rate_percent: number | null;
  latest_release_version: string | null;
  stagnation_recovery_status: string;
  last_heartbeat_timestamp: string;
}

// --- API Calls ---

export async function fetchEnterpriseHierarchy(): Promise<EnterpriseHierarchyChart> {
  return req<EnterpriseHierarchyChart>("/enterprise/hierarchy");
}

export async function fetchEnterpriseRFCs(): Promise<EnterpriseRFC[]> {
  return req<EnterpriseRFC[]>("/enterprise/rfcs");
}

export async function createEnterpriseRFC(payload: {
  title: string;
  author_bot: string;
  department: string;
  summary: string;
  proposal_content: string;
  affected_departments: string[];
}): Promise<EnterpriseRFC> {
  return req<EnterpriseRFC>("/enterprise/rfcs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function submitRFCReview(
  rfcId: string,
  payload: {
    reviewer_bot: string;
    department: string;
    verdict: string;
    argument: string;
    epistemic_confidence: number;
  }
): Promise<RFCReview> {
  return req<RFCReview>(`/enterprise/rfcs/${rfcId}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function submitDebateArgument(
  rfcId: string,
  payload: {
    speaker_bot: string;
    department: string;
    stance: string;
    claim: string;
    evidence: string;
  }
): Promise<DebateArgument> {
  return req<DebateArgument>(`/enterprise/rfcs/${rfcId}/debate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function evaluateRFCGating(rfcId: string): Promise<{ gating_passed: boolean; consensus_score: number; reason: string }> {
  return req<{ gating_passed: boolean; consensus_score: number; reason: string }>(`/enterprise/rfcs/${rfcId}/gate`, {
    method: "POST",
  });
}

export async function fetchEnterpriseTreasury(): Promise<TreasuryTelemetry> {
  return req<TreasuryTelemetry>("/enterprise/treasury");
}

export async function resetTreasuryCircuitBreaker(deptId: string): Promise<TreasuryAllocation> {
  return req<TreasuryAllocation>("/enterprise/treasury/reset-breaker", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dept_id: deptId }),
  });
}

export async function fetchMissionPipeline(): Promise<{ epics: Array<{ epic_id: string; title: string; target_department: string; status: string }>; sprints: DAGSprint[] }> {
  return req<{ epics: Array<{ epic_id: string; title: string; target_department: string; status: string }>; sprints: DAGSprint[] }>("/enterprise/missions/pipeline");
}

export async function stepSprintDAG(sprintId: string): Promise<{ status: string; progress_percent: number; advanced_tasks: string[] }> {
  return req<{ status: string; progress_percent: number; advanced_tasks: string[] }>(`/enterprise/sprints/${sprintId}/step`, {
    method: "POST",
  });
}

export async function fetchCouncilReleases(): Promise<ReleaseCandidate[]> {
  return req<ReleaseCandidate[]>("/enterprise/council/releases");
}

export async function runHoldoutBenchmark(releaseId: string): Promise<{ holdout_benchmark_score: number; holdout_passed: boolean }> {
  return req<{ holdout_benchmark_score: number; holdout_passed: boolean }>(`/enterprise/council/releases/${releaseId}/benchmark`, {
    method: "POST",
  });
}

export async function signCouncilRelease(releaseId: string, role: string, botName: string): Promise<{ signature: CryptographicSignature; release_status: string }> {
  return req<{ signature: CryptographicSignature; release_status: string }>(`/enterprise/council/releases/${releaseId}/sign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, bot_name: botName }),
  });
}

export async function promoteCouncilRelease(releaseId: string): Promise<ReleaseCandidate> {
  return req<ReleaseCandidate>(`/enterprise/council/releases/${releaseId}/promote`, {
    method: "POST",
  });
}

export async function triggerEnterpriseHeartbeat(): Promise<{ cycle: number; system_latency_p95_ms: number; telemetry: EnterpriseTelemetry }> {
  return req<{ cycle: number; system_latency_p95_ms: number; telemetry: EnterpriseTelemetry }>("/enterprise/heartbeat", {
    method: "POST",
  });
}

export async function fetchEnterpriseTelemetry(): Promise<EnterpriseTelemetry> {
  return req<EnterpriseTelemetry>("/enterprise/telemetry");
}
