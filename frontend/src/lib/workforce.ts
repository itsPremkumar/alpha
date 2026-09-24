import { get, send } from "./http";

export interface PresenceMember {
  project_id: string;
  bot_name: string;
  role_in_project: string;
  status: string;
  current_task_id: string | null;
  blocked_reason: string | null;
  joined_at: string;
  last_activity: string;
}

export interface ProjectStateSnapshot {
  project_id: string;
  goal: string;
  phase: string;
  active_tasks: number;
  blocked_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  active_agents: number;
  arch_version: string;
  latest_decision: string | null;
  open_conflicts: number;
  open_risks: string[];
  last_verified: string | null;
}

export interface DecisionRecord {
  decision_id: string;
  title: string;
  body: string;
  reason: string;
  made_by: string;
  approved_by: string | null;
  created_at: string;
}

export interface CandidatePick {
  bot_name: string;
  capability_match: number;
  available: boolean;
  load: number;
  reputation: number;
  score: number;
}

const enc = encodeURIComponent;

export async function joinProject(projectId: string, botName: string, role = "worker"): Promise<PresenceMember> {
  return send<PresenceMember>(`/projects/${enc(projectId)}/join`, "POST", { bot_name: botName, role_in_project: role });
}

export async function leaveProject(projectId: string, botName: string): Promise<{ left: boolean }> {
  return send(`/projects/${enc(projectId)}/leave`, "POST", { bot_name: botName });
}

export async function fetchPresence(projectId: string): Promise<PresenceMember[]> {
  const d = await get<{ members: PresenceMember[] }>(`/projects/${enc(projectId)}/presence`);
  return d.members || [];
}

export async function fetchProjectState(projectId: string, refresh = false): Promise<ProjectStateSnapshot> {
  return get<ProjectStateSnapshot>(`/projects/${enc(projectId)}/state${refresh ? "?refresh=true" : ""}`);
}

export async function setProjectPhase(projectId: string, phase: string): Promise<ProjectStateSnapshot> {
  return send(`/projects/${enc(projectId)}/phase`, "POST", { phase });
}

export async function searchDecisions(projectId: string, q?: string): Promise<DecisionRecord[]> {
  const d = await get<{ decisions: DecisionRecord[] }>(`/projects/${enc(projectId)}/decisions${q ? `?q=${enc(q)}` : ""}`);
  return d.decisions || [];
}

export async function recordDecision(projectId: string, input: { title: string; body: string; reason?: string; made_by?: string }): Promise<DecisionRecord> {
  return send(`/projects/${enc(projectId)}/decisions`, "POST", input);
}

export async function fetchProjectEvents(projectId: string, afterSeq = 0): Promise<Array<Record<string, unknown>>> {
  const d = await get<{ events: Array<Record<string, unknown>> }>(`/projects/${enc(projectId)}/events?after_seq=${afterSeq}`);
  return d.events || [];
}

export async function fetchProjectContext(projectId: string, botRole = "worker"): Promise<Record<string, unknown>> {
  return get(`/projects/${enc(projectId)}/context?bot_role=${enc(botRole)}`);
}

export async function selectAgent(requiredCapabilities: string[], projectId?: string): Promise<{ candidates: CandidatePick[]; selected: CandidatePick | null }> {
  return send("/bots/select", "POST", { required_capabilities: requiredCapabilities, project_id: projectId });
}

export async function routeTaskType(taskType: string): Promise<{ primary: string; chain: string[]; category: string }> {
  return send("/bots/route-task", "POST", { task_type: taskType });
}

export async function fetchOpsAdvice(currentWorkers = 1): Promise<{ recommendation: string; max_workers: number; model_class: string; reasons: string[] }> {
  return get(`/ops/advice?current_workers=${currentWorkers}`);
}

export async function evaluatePolicy(action: string, actor = "*", projectId = "*"): Promise<{ verdict: string; reason: string }> {
  return send("/policy/evaluate", "POST", { action, actor, project_id: projectId });
}

export async function openCouncilCase(artifactId: string, artifactRef: string): Promise<Record<string, unknown>> {
  return send("/council/cases", "POST", { artifact_id: artifactId, artifact_ref: artifactRef });
}

export async function submitCouncilReview(caseId: string, reviewer: string, verdict: string, evidence = ""): Promise<{ outcome: string; reason: string }> {
  return send(`/council/cases/${enc(caseId)}/reviews`, "POST", { reviewer, verdict, evidence });
}

export async function createMission(objective: string): Promise<Record<string, unknown>> {
  return send("/missions", "POST", { objective });
}

export async function listMissions(): Promise<Array<Record<string, unknown>>> {
  const d = await get<{ missions: Array<Record<string, unknown>> }>(`/missions`);
  return d.missions || [];
}

export async function runBenchmarkSuite(name: string): Promise<Record<string, unknown>> {
  return send(`/benchmarks/suites/${enc(name)}/run`, "POST", {});
}

export async function interviewQuestions(objective: string): Promise<{ plan: Record<string, unknown>; questions: Array<Record<string, unknown>> }> {
  return send("/plan-mode/interview/questions", "POST", { objective });
}

export async function checkCompletion(projectId: string, evidence: Array<Record<string, unknown>>, taskKind = "code"): Promise<{ passed: boolean; missing: string[] }> {
  return send(`/projects/${enc(projectId)}/completion-check`, "POST", { evidence, task_kind: taskKind });
}

export interface DMInboxMessage {
  delivery_id: string;
  sender: string;
  recipient: string;
  body: string;
  status: string;
  created_at: number;
}

export async function fetchInbox(botName: string, unreadOnly = false): Promise<{ messages: DMInboxMessage[]; unread_count: number }> {
  return get(`/bots/${enc(botName)}/inbox?unread_only=${unreadOnly ? "true" : "false"}`);
}

export async function sendDM(botName: string, target: string, message: string): Promise<Record<string, unknown>> {
  return send(`/bots/${enc(botName)}/dm`, "POST", { target, message });
}

export async function ackDM(botName: string, deliveryId: string): Promise<Record<string, unknown>> {
  return send(`/bots/${enc(botName)}/inbox/${enc(deliveryId)}/ack`, "POST", {});
}

export async function fetchBotChat(botName: string): Promise<{ canonical_thread_id: string; unread_count: number; recent: DMInboxMessage[] }> {
  return get(`/bots/${enc(botName)}/chat`);
}

export async function fetchConstitution(projectId: string): Promise<{ present: boolean; markdown?: string; sha16?: string }> {
  return get(`/projects/${enc(projectId)}/constitution`);
}

export async function fetchLocks(projectId: string): Promise<{ locks: Array<Record<string, unknown>>; pending_requests: Array<Record<string, unknown>> }> {
  return get(`/projects/${enc(projectId)}/locks`);
}

export async function acquireLock(projectId: string, input: { scope: string; path: string; owner_bot: string; reason?: string }): Promise<Record<string, unknown>> {
  return send(`/projects/${enc(projectId)}/locks`, "POST", input);
}

export async function releaseLock(projectId: string, lockId: string, requesterBot: string): Promise<Record<string, unknown>> {
  return send(`/projects/${enc(projectId)}/locks/${enc(lockId)}?requester_bot=${enc(requesterBot)}`, "DELETE");
}

export async function fetchHandoffs(projectId: string): Promise<Array<Record<string, unknown>>> {
  const d = await get<{ handoffs: Array<Record<string, unknown>> }>(`/projects/${enc(projectId)}/handoffs`);
  return d.handoffs || [];
}

export async function acceptHandoff(projectId: string, handoffId: string, toBot: string): Promise<Record<string, unknown>> {
  return send(`/projects/${enc(projectId)}/handoffs/${enc(handoffId)}/accept`, "POST", { to_bot: toBot });
}

export async function fetchSkillUsage(): Promise<Array<Record<string, unknown>>> {
  const d = await get<{ usage: Array<Record<string, unknown>> }>(`/skills/usage`);
  return d.usage || [];
}

export async function fetchCuratorReport(): Promise<Record<string, unknown>> {
  return get(`/skills/curator`);
}

export async function runCurator(dryRun = true): Promise<Record<string, unknown>> {
  return send(`/skills/curator/run?dry_run=${dryRun ? "true" : "false"}&suggest_merges=true`, "POST", {});
}

export async function fetchSkillTiers(): Promise<Array<Record<string, unknown>>> {
  const d = await get<{ tiers: Array<Record<string, unknown>> }>(`/skills/tiers`);
  return d.tiers || [];
}

export async function fetchBlueprints(): Promise<Array<Record<string, unknown>>> {
  const d = await get<{ blueprints: Array<Record<string, unknown>> }>(`/scheduled-tasks/blueprints`);
  return d.blueprints || [];
}

export async function launchBlueprint(blueprintId: string, values: Record<string, string>): Promise<Record<string, unknown>> {
  return send(`/scheduled-tasks/blueprints/${enc(blueprintId)}/launch`, "POST", { values });
}

export async function fetchIncidents(taskId: string): Promise<Array<Record<string, unknown>>> {
  const d = await get<{ incidents: Array<Record<string, unknown>> }>(`/scheduled-tasks/${enc(taskId)}/incidents`);
  return d.incidents || [];
}

export async function fetchBenchmarkSuites(): Promise<Array<Record<string, unknown>>> {
  const d = await get<{ suites: Array<Record<string, unknown>> }>(`/benchmarks/suites`);
  return d.suites || [];
}

export async function fetchConsoleInsights(days = 7): Promise<{ digest: string; report: Record<string, unknown> }> {
  return get(`/console/insights?days=${days}`);
}

export async function listCouncilCases(status?: string): Promise<Array<Record<string, unknown>>> {
  const d = await get<{ cases: Array<Record<string, unknown>> }>(`/council/cases${status ? `?status=${enc(status)}` : ""}`);
  return d.cases || [];
}

export async function fetchPolicies(): Promise<Array<Record<string, unknown>>> {
  const d = await get<{ policies: Array<Record<string, unknown>> }>(`/policy/policies`);
  return d.policies || [];
}

export async function fetchPendingApprovals(): Promise<Array<Record<string, unknown>>> {
  const d = await get<{ approvals: Array<Record<string, unknown>> }>(`/policy/approvals?status=pending`);
  return d.approvals || [];
}

export async function decideApproval(requestId: string, approved: boolean): Promise<Record<string, unknown>> {
  return send(`/policy/approvals/${enc(requestId)}/decide`, "POST", { approved });
}

export async function localEndpointHealth(baseUrl: string): Promise<{ reachable: boolean; models: string[]; reason: string }> {
  return get(`/models/local/health?base_url=${enc(baseUrl)}`);
}

export interface WarRoomSnapshot {
  project_id: string;
  status: string;
  state: Record<string, unknown>;
  members: Array<{
    bot_name: string;
    role_in_project: string;
    status: string;
    current_task_id: string | null;
    joined_at: string;
    last_activity: string;
  }>;
  active_locks: Array<{
    lock_id: string;
    scope: string;
    path: string;
    owner_bot: string;
    reason: string;
    expires_at: number;
  }>;
  pending_lock_requests: Array<{
    request_id: string;
    requester_bot: string;
    scope: string;
    path: string;
    reason: string;
  }>;
  pending_approvals?: WarRoomApprovalItem[];
  contracts?: WarRoomContractItem[];
  living_spec?: WarRoomLivingSpec;
  cost_summary?: WarRoomCostSummary;
  standup?: WarRoomStandup;
  checkpoints?: WarRoomCheckpoint[];
  leaderboard?: WarRoomLeaderboardEntry[];
  canary_history?: WarRoomCanaryResult[];
  visual_qa?: Array<{ receipt_id: string; url: string; passed: boolean; visual_stability_score: number; verified_by: string; verified_at: string }>;
  avo_lineage?: WarRoomAVOLineage;
  epistemic_claims?: WarRoomEpistemicClaim[];
  rsi_status?: WarRoomRSIStatus;
  trajectories?: WarRoomTrajectoryTrace[];
  handoffs: Array<{
    handoff_id: string;
    task_id: string;
    from_bot: string;
    to_bot: string;
    objective: string;
    status: string;
  }>;
  decisions: Array<{
    decision_id: string;
    title: string;
    status: string;
    decided_by: string;
  }>;
  events: Array<{
    seq: number;
    event_id: string;
    type: string;
    actor: string;
    payload: Record<string, unknown>;
    created_at: number;
  }>;
  kill_switch: {
    active: boolean;
    reason: string;
    paused_bots: Record<string, unknown>;
  };
}

export interface WarRoomApprovalItem {
  request_id: string;
  project_id: string;
  bot_name: string;
  action_type: string;
  risk_level: "low" | "medium" | "high" | "critical";
  details: Record<string, unknown>;
  diff_preview?: string;
  status: "pending" | "approved" | "rejected" | "timed_out";
  created_at: string;
}

export interface WarRoomContractItem {
  task_id: string;
  project_id: string;
  title: string;
  assignee_bot: string;
  verifier_bot?: string;
  status: string;
  evidence_receipts: Array<{ kind: string; reference: string; verified_by: string; detail?: string }>;
  created_at: string;
}

export interface WarRoomLivingSpec {
  project_id: string;
  title: string;
  updated_at: string;
  sections: Record<string, {
    section_key: string;
    title: string;
    content: string;
    last_author_bot: string;
    version: number;
    updated_at: string;
  }>;
}

export interface WarRoomCostSummary {
  project_id: string;
  daily_budget_usd: number;
  current_spend_24h: number;
  budget_utilized_ratio: number;
  bot_breakdown: Record<string, {
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
  }>;
}

export interface WarRoomStandup {
  project_id: string;
  timestamp: string;
  executive_summary: string;
  blockers: string[];
  stagnant_alerts: Array<{
    task_id: string;
    assignee_bot: string;
    minutes_inactive: number;
    recommendation: string;
  }>;
}

export async function fetchWarRoomData(projectId: string): Promise<WarRoomSnapshot> {
  return get(`/projects/${enc(projectId)}/war-room`);
}

export async function resolveApprovalRequest(
  projectId: string,
  requestId: string,
  approved: boolean,
  comment = "",
  resolvedBy = "human_operator"
): Promise<WarRoomApprovalItem> {
  return send(`/projects/${enc(projectId)}/approvals/${enc(requestId)}/resolve`, "POST", {
    approved,
    comment,
    resolved_by: resolvedBy,
  });
}

export interface WarRoomCheckpoint {
  checkpoint_id: string;
  project_id: string;
  tag: string;
  timestamp_iso: string;
  active_locks: Array<Record<string, unknown>>;
  contracts: Array<Record<string, unknown>>;
}

export interface WarRoomLeaderboardEntry {
  bot_name: string;
  challenges_attempted: number;
  challenges_passed: number;
  // Null while the bot has no measured attempts — never an invented default.
  pass_rate: number | null;
  avg_duration_seconds: number | null;
  reputation_score: number | null;
  rank: number;
}

export interface WarRoomCanaryResult {
  probe_id: string;
  target_port: number;
  target_url: string;
  status: "healthy" | "degraded" | "failed";
  http_status: number | null;
  latency_ms: number;
  recommendation: string;
  tested_at: string;
}

export async function createCheckpoint(projectId: string, tag = "manual"): Promise<WarRoomCheckpoint> {
  return send(`/projects/${enc(projectId)}/checkpoints`, "POST", { tag });
}

export async function restoreCheckpoint(projectId: string, checkpointId: string): Promise<Record<string, unknown>> {
  return send(`/projects/${enc(projectId)}/checkpoints/${enc(checkpointId)}/restore`, "POST", {});
}

export async function probeCanary(projectId: string, port = 3000, mockSuccess = false): Promise<WarRoomCanaryResult> {
  return send(`/projects/${enc(projectId)}/canary/probe`, "POST", { port, mock_success: mockSuccess });
}

// ---------------------------------------------------------------------------
// Phase 7 ASI Core Models & API helpers
// ---------------------------------------------------------------------------

export interface WarRoomAVOVersion {
  version_id: string;
  parent_id: string | null;
  hypothesis: string;
  modification: string;
  correctness: boolean;
  performance_score: number;
  quality_score: number;
  composite_score: number;
  trajectory_depth: number;
  rejection_reason: string | null;
  created_at: number;
}

export interface WarRoomAVOLineage {
  head_id: string | null;
  versions: WarRoomAVOVersion[];
  pareto_frontier: WarRoomAVOVersion[];
  supervisor_status: string;
}

export interface WarRoomEpistemicClaim {
  claim_id: string;
  text: string;
  status: "fact" | "observation" | "inference" | "hypothesis" | "assumption" | "contradiction" | "speculation" | "obsolete";
  confidence: number;
  bayesian_prior: number;
  bayesian_posterior: number;
  falsification_test: string;
  verification_method: string;
  supporting_evidence: string[];
  contradicting_evidence: string[];
  is_verified?: boolean;
}

export interface WarRoomRSIStatus {
  stage: "idle" | "bottleneck_detected" | "hypothesis_generated" | "candidate_created" | "ab_test_running" | "holdout_evaluation" | "promoted" | "rolled_back";
  active_configurations: Record<string, Record<string, unknown>>;
  last_cycle_summary?: string;
}

export interface WarRoomTrajectoryStep {
  step_id: string;
  goal_id: string;
  step_index: number;
  thought: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
  tool_output: string;
  status: string;
  error: string;
  created_at: string;
}

export interface WarRoomTrajectoryTrace {
  goal_id: string;
  steps: WarRoomTrajectoryStep[];
  total_steps: number;
  created_at: string;
}

export async function triggerAVOIteration(
  projectId: string,
  payload: { hypothesis: string; modification: string; performance_score?: number; quality_score?: number; correctness?: boolean }
): Promise<Record<string, unknown>> {
  return send(`/projects/${enc(projectId)}/avo/iterate`, "POST", payload);
}

export async function registerEpistemicClaim(
  projectId: string,
  payload: { text: string; status?: string; prior_confidence?: number; falsification_test?: string }
): Promise<WarRoomEpistemicClaim> {
  return send(`/projects/${enc(projectId)}/epistemics/claims`, "POST", payload);
}

export async function addEpistemicEvidence(
  projectId: string,
  claimId: string,
  payload: { evidence: string; is_supporting?: boolean; likelihood_ratio?: number }
): Promise<WarRoomEpistemicClaim> {
  return send(`/projects/${enc(projectId)}/epistemics/claims/${enc(claimId)}/evidence`, "POST", payload);
}

export async function triggerRSICycle(
  projectId: string,
  payload: { bottleneck: string; target_component?: string }
): Promise<Record<string, unknown>> {
  return send(`/projects/${enc(projectId)}/rsi/cycle`, "POST", { bottleneck: payload.bottleneck, target_component: payload.target_component, force_promote: false });
}

export async function replayTrajectory(
  projectId: string,
  goalId: string,
  fromStepIndex = 0
): Promise<Record<string, unknown>> {
  return send(`/projects/${enc(projectId)}/trajectories/${enc(goalId)}/replay`, "POST", { from_step_index: fromStepIndex });
}

// ==============================================================================
// Autonomous Self-Configuration Interfaces & APIs
// ==============================================================================

export interface GoalAnalysisResult {
  raw_goal: string;
  intent: string;
  domain: string;
  complexity: "trivial" | "simple" | "moderate" | "complex" | "research_frontier";
  risk_score: number;
  estimated_turns: number;
  suggested_mode: "direct" | "plan" | "coding" | "research" | "swarm" | "autonomous" | "recover" | "company";
  recommended_model_tier: "fast_local" | "standard" | "reasoning_frontier" | "multi_model_ensemble";
  recommended_topology: "solo" | "hierarchical" | "debate_council" | "parallel_mesh";
  required_capabilities: string[];
  tool_whitelist: string[];
  reasoning_budget_tokens: number;
  reflection_frequency: number;
  context_compaction_threshold: number;
  ambiguities: string[];
  mitigation_strategies: string[];
}

export interface SelfConfigProfile {
  profile_id: string;
  project_id: string;
  goal: string;
  operating_mode: string;
  model_tier: string;
  primary_model: string;
  fallback_model: string;
  active_tools: string[];
  reasoning_budget_tokens: number;
  thought_depth: string;
  max_turns: number;
  context_compaction_threshold: number;
  loop_detection_limit: number;
  topology: string;
  swarm_roles: string[];
  autonomous_pivots_enabled: boolean;
  guardrails: Record<string, unknown>;
  updated_at: string;
}

export interface RuntimeTuningInput {
  reasoning_budget_tokens?: number;
  context_compaction_threshold?: number;
  loop_detection_limit?: number;
  primary_model?: string;
  operating_mode?: string;
  thought_depth?: string;
  extra_tools?: string[];
  disabled_tools?: string[];
}

export async function fetchSelfConfigStatus(projectId: string): Promise<{ project_id: string; active_profile: SelfConfigProfile; analyses_performed: number; last_analysis: GoalAnalysisResult | null }> {
  return get(`/projects/${enc(projectId)}/self-config/status`);
}

export async function inferSelfConfig(
  projectId: string,
  goal: string,
  context?: Record<string, unknown>
): Promise<{ analysis: GoalAnalysisResult; recommended_profile: SelfConfigProfile }> {
  return send(`/projects/${enc(projectId)}/self-config/infer`, "POST", { goal, context: context || {} });
}

export async function tuneSelfConfig(
  projectId: string,
  tuning: RuntimeTuningInput
): Promise<SelfConfigProfile> {
  return send(`/projects/${enc(projectId)}/self-config/tune`, "POST", tuning);
}

// ==============================================================================
// Agent Meta-Compiler & Self-Replication Interfaces & APIs
// ==============================================================================

export interface AgentBlueprint {
  blueprint_id: string;
  generation: number;
  parent_id: string | null;
  name: string;
  architecture_tag: string;
  system_prompt_template: string;
  reasoning_strategy: string;
  memory_layout: string;
  tool_bindings: string[];
  reflection_frequency: number;
  stagnation_recovery_policy: string;
  model_tier: string;
  hyperparameters: Record<string, unknown>;
  specialization: string;
  mutation_notes: string;
  created_at: string;
}

export interface BenchmarkScorecard {
  benchmark_id: string;
  blueprint_id: string;
  generation: number;
  coding_score: number;
  reasoning_score: number;
  tool_accuracy_score: number;
  token_efficiency_score: number;
  robustness_score: number;
  composite_score: number;
  passed_regression_suite: boolean;
  details: Array<{ suite: string; score: number; status: string }>;
  evaluated_at: string;
}

export interface HotSwapOutcome {
  success: boolean;
  previous_head_id: string;
  new_head_id: string;
  generation: number;
  migrated_tasks: number;
  telemetry: Record<string, unknown>;
  promoted_at: string;
}

export interface MetaLineageData {
  project_id: string;
  active_head: AgentBlueprint;
  active_scorecard: BenchmarkScorecard;
  total_generations: number;
  blueprints_count: number;
  pareto_frontier: AgentBlueprint[];
  history: Array<Record<string, unknown>>;
}

export async function fetchMetaLineage(projectId: string): Promise<MetaLineageData> {
  return get(`/projects/${enc(projectId)}/meta-compiler/lineage`);
}

export async function compileNextGenBlueprint(
  projectId: string,
  payload: { parent_id?: string; optimization_target?: string; mutation_notes?: string; specialist_domain?: string }
): Promise<AgentBlueprint> {
  return send(`/projects/${enc(projectId)}/meta-compiler/compile`, "POST", payload);
}

export async function benchmarkBlueprint(
  projectId: string,
  blueprintId: string,
  baselineScore = 0.80
): Promise<BenchmarkScorecard> {
  return send(`/projects/${enc(projectId)}/meta-compiler/benchmark`, "POST", { blueprint_id: blueprintId, baseline_score: baselineScore });
}

export async function hotswapBlueprint(
  projectId: string,
  blueprintId: string
): Promise<HotSwapOutcome> {
  return send(`/projects/${enc(projectId)}/meta-compiler/hotswap`, "POST", { blueprint_id: blueprintId, force: false });
}

export async function rollbackBlueprint(
  projectId: string,
  targetBlueprintId: string
): Promise<{ success: boolean; active_head_id: string; generation: number }> {
  return send(`/projects/${enc(projectId)}/meta-compiler/rollback`, "POST", { target_blueprint_id: targetBlueprintId });
}

// ==============================================================================
// Perpetual Never-Ending Autonomous Daemon Interfaces & APIs
// ==============================================================================

export interface PerpetualGoalItem {
  goal_id: string;
  title: string;
  description: string;
  priority: number;
  status: string;
  progress_percent: number;
  subtasks: string[];
  created_at: string;
  updated_at: string;
}

export interface AutonomousTaskItem {
  task_id: string;
  goal_id: string;
  title: string;
  source: string;
  status: string;
  priority: number;
  details: Record<string, unknown>;
  created_at: string;
}

export interface StagnationIncidentItem {
  incident_id: string;
  detected_at: string;
  signature: string;
  repeated_action: string;
  consecutive_failures: number;
  recovery_action_taken: string;
  resolved: boolean;
}

export interface MemoryConsolidationReportItem {
  report_id: string;
  timestamp: string;
  traces_analyzed: number;
  facts_extracted: number;
  skills_indexed: number;
  pruned_tokens: number;
  summary: string;
}

export interface PerpetualDaemonTelemetry {
  state: "stopped" | "running" | "paused" | "stagnation_recovery" | "consolidating_memory" | "discovering_tasks";
  heartbeat_count: number;
  uptime_seconds: number;
  active_goals_count: number;
  total_tasks_discovered: number;
  tasks_completed_count: number;
  stagnation_incidents_recovered: number;
  consolidation_cycles_completed: number;
  last_heartbeat_at: string;
}

export interface PerpetualStatusData {
  project_id: string;
  telemetry: PerpetualDaemonTelemetry;
  active_goal: PerpetualGoalItem;
  all_goals: PerpetualGoalItem[];
  tasks: AutonomousTaskItem[];
  stagnation_incidents: StagnationIncidentItem[];
  latest_consolidation: MemoryConsolidationReportItem | null;
}

export async function fetchPerpetualStatus(projectId: string): Promise<PerpetualStatusData> {
  return get(`/projects/${enc(projectId)}/perpetual/status`);
}

export async function startPerpetualDaemon(projectId: string): Promise<{ project_id: string; state: string }> {
  return send(`/projects/${enc(projectId)}/perpetual/start`, "POST", {});
}

export async function stopPerpetualDaemon(projectId: string): Promise<{ project_id: string; state: string }> {
  return send(`/projects/${enc(projectId)}/perpetual/stop`, "POST", {});
}

export async function triggerPerpetualHeartbeat(projectId: string): Promise<Record<string, unknown>> {
  return send(`/projects/${enc(projectId)}/perpetual/heartbeat`, "POST", {});
}

export async function triggerPerpetualDiscovery(projectId: string): Promise<{ discovered_count: number; tasks: AutonomousTaskItem[] }> {
  return send(`/projects/${enc(projectId)}/perpetual/discover`, "POST", {});
}

export async function triggerPerpetualConsolidation(projectId: string): Promise<Record<string, unknown>> {
  return send(`/projects/${enc(projectId)}/perpetual/consolidate`, "POST", {});
}

export async function createPerpetualGoal(
  projectId: string,
  title: string,
  description = "",
  priority = 1
): Promise<PerpetualGoalItem> {
  return send(`/projects/${enc(projectId)}/perpetual/goals`, "POST", { title, description, priority });
}

