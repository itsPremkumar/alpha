// lib/workflows.ts — real-API client for the Dynamic Workflow Engine
// (backend/app/gateway/routers/workflows.py, 17 routes) plus the checkpoints
// (checkpoints.py) and jobs (jobs.py) routers that share the Workflows
// section's execution surface. Every field name mirrors the router response
// models exactly (WorkflowRun/WorkflowDefinition/WorkflowEvent in
// alpha/workflow/models.py + events.py, HydrationReport in schemas.py,
// CodeCheckpoint in code_agentic_core.py, JobResult in alpha/jobs/models.py).
// Nothing is synthesized when the server omits a value: absent booleans stay
// null/false as sent, absent lists stay empty, and failures reject instead of
// returning fabricated data.
import { get, send, asList, pick } from "./http";

/* ── Workflow definitions ────────────────────────────────────────────── */

export interface WorkflowListItem {
  id: string;
  name: string;
  version: string;
  description: string;
  node_count: number;
}

function toWorkflowListItem(w: Record<string, unknown>): WorkflowListItem {
  const count = Number(pick(w, ["node_count"], 0));
  return {
    id: String(pick(w, ["id"], "")),
    name: String(pick(w, ["name"], "")),
    version: String(pick(w, ["version"], "")),
    description: String(pick(w, ["description"], "")),
    node_count: Number.isFinite(count) ? count : 0,
  };
}

/** GET /workflows → { workflows: [{id,name,version,description,node_count}], count } (workflows.py:119) */
export async function listWorkflows(): Promise<WorkflowListItem[]> {
  const d = await get<unknown>("/workflows");
  return asList(d, ["workflows", "data"]).map(toWorkflowListItem);
}

export interface WorkflowGraphNode {
  id: string;
  type: string;
  executor?: string;
  requires_approval?: boolean;
  status?: string;
  prompt?: string | null;
}

export interface WorkflowDefinition {
  id: string;
  name: string;
  version: string;
  description: string;
  graph: { version: number; nodes: Record<string, WorkflowGraphNode>; edges: unknown[]; metadata: Record<string, unknown> };
  variables: Record<string, unknown>;
  policies: Record<string, unknown>;
}

function toDefinition(d: Record<string, unknown>): WorkflowDefinition {
  const graph = (d.graph && typeof d.graph === "object" ? d.graph : {}) as Record<string, unknown>;
  const rawNodes = graph.nodes && typeof graph.nodes === "object" ? (graph.nodes as Record<string, unknown>) : {};
  const nodes: Record<string, WorkflowGraphNode> = {};
  for (const [key, value] of Object.entries(rawNodes)) {
    const n = (value && typeof value === "object" ? value : {}) as Record<string, unknown>;
    nodes[key] = {
      id: String(pick(n, ["id"], key)),
      type: String(pick(n, ["type"], "unknown")),
      executor: typeof n.executor === "string" ? n.executor : undefined,
      requires_approval: typeof n.requires_approval === "boolean" ? n.requires_approval : undefined,
      status: typeof n.status === "string" ? n.status : undefined,
      prompt: typeof n.prompt === "string" ? n.prompt : null,
    };
  }
  const graphVersion = Number(pick(graph, ["version"], 1));
  return {
    id: String(pick(d, ["id"], "")),
    name: String(pick(d, ["name"], "")),
    version: String(pick(d, ["version"], "")),
    description: String(pick(d, ["description"], "")),
    graph: {
      version: Number.isFinite(graphVersion) ? graphVersion : 1,
      nodes,
      edges: Array.isArray(graph.edges) ? graph.edges : [],
      metadata: graph.metadata && typeof graph.metadata === "object" ? (graph.metadata as Record<string, unknown>) : {},
    },
    variables: d.variables && typeof d.variables === "object" ? (d.variables as Record<string, unknown>) : {},
    policies: d.policies && typeof d.policies === "object" ? (d.policies as Record<string, unknown>) : {},
  };
}

/** GET /workflows/{workflow_id} → WorkflowDefinition.model_dump() (workflows.py:138) */
export async function getWorkflow(workflowId: string): Promise<WorkflowDefinition> {
  const d = await get<Record<string, unknown>>(`/workflows/${encodeURIComponent(workflowId)}`);
  return toDefinition(d);
}

export interface WorkflowDraft {
  id: string;
  name: string;
  description?: string;
  graph?: Record<string, unknown>;
  variables?: Record<string, unknown>;
  policies?: Record<string, unknown>;
}

/** POST /workflows → { id, name, version } (workflows.py:102) */
export async function createWorkflow(draft: WorkflowDraft): Promise<{ id: string; name: string; version: string }> {
  const body: Record<string, unknown> = { id: draft.id, name: draft.name };
  if (draft.description !== undefined) body.description = draft.description;
  if (draft.graph !== undefined) body.graph = draft.graph;
  if (draft.variables !== undefined) body.variables = draft.variables;
  if (draft.policies !== undefined) body.policies = draft.policies;
  const d = await send<Record<string, unknown>>("/workflows", "POST", body);
  return {
    id: String(pick(d, ["id"], draft.id)),
    name: String(pick(d, ["name"], draft.name)),
    version: String(pick(d, ["version"], "")),
  };
}

/* ── Runs ────────────────────────────────────────────────────────────── */

export interface WorkflowRun {
  run_id: string;
  workflow_id: string;
  graph_version: number;
  status: string;
  state: Record<string, unknown>;
  node_states: Record<string, string>;
  active_nodes: string[];
  completed_nodes: string[];
  failed_nodes: string[];
  waiting_nodes: string[];
  iteration_counts: Record<string, number>;
  metrics: Record<string, unknown>;
  history: Array<Record<string, unknown>>;
  waiting_reason: string | null;
  approval_request_id: string | null;
  created_at: string;
  updated_at: string;
}

function toStringArray(v: unknown): string[] {
  return Array.isArray(v) ? v.map((x) => String(x)) : [];
}

function toStringRecord(v: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (v && typeof v === "object") {
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) out[k] = String(val);
  }
  return out;
}

function toNumberRecord(v: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (v && typeof v === "object") {
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
      const n = Number(val);
      if (Number.isFinite(n)) out[k] = n;
    }
  }
  return out;
}

function toRun(r: Record<string, unknown>): WorkflowRun {
  const graphVersion = Number(pick(r, ["graph_version"], 1));
  return {
    run_id: String(pick(r, ["run_id"], "")),
    workflow_id: String(pick(r, ["workflow_id"], "")),
    graph_version: Number.isFinite(graphVersion) ? graphVersion : 1,
    status: String(pick(r, ["status"], "unknown")),
    state: r.state && typeof r.state === "object" ? (r.state as Record<string, unknown>) : {},
    node_states: toStringRecord(r.node_states),
    active_nodes: toStringArray(r.active_nodes),
    completed_nodes: toStringArray(r.completed_nodes),
    failed_nodes: toStringArray(r.failed_nodes),
    waiting_nodes: toStringArray(r.waiting_nodes),
    iteration_counts: toNumberRecord(r.iteration_counts),
    metrics: r.metrics && typeof r.metrics === "object" ? (r.metrics as Record<string, unknown>) : {},
    history: Array.isArray(r.history) ? (r.history as Array<Record<string, unknown>>) : [],
    waiting_reason: typeof r.waiting_reason === "string" ? r.waiting_reason : null,
    approval_request_id: typeof r.approval_request_id === "string" ? r.approval_request_id : null,
    created_at: String(pick(r, ["created_at"], "")),
    updated_at: String(pick(r, ["updated_at"], "")),
  };
}

/** POST /workflows/{workflow_id}/runs → WorkflowRun.model_dump() (workflows.py:148) */
export async function startWorkflowRun(
  workflowId: string,
  opts: { initial_state?: Record<string, unknown>; mode?: string } = {},
): Promise<WorkflowRun> {
  const d = await send<Record<string, unknown>>(`/workflows/${encodeURIComponent(workflowId)}/runs`, "POST", {
    initial_state: opts.initial_state ?? {},
    mode: opts.mode ?? "normal",
  });
  return toRun(d);
}

/** GET /workflows/runs/{run_id} → WorkflowRun.model_dump() (workflows.py:161) */
export async function getWorkflowRun(runId: string): Promise<WorkflowRun> {
  const d = await get<Record<string, unknown>>(`/workflows/runs/${encodeURIComponent(runId)}`);
  return toRun(d);
}

/** POST /workflows/runs/{run_id}/step → WorkflowRun.model_dump() (workflows.py:171) */
export async function stepWorkflowRun(runId: string): Promise<WorkflowRun> {
  const d = await send<Record<string, unknown>>(`/workflows/runs/${encodeURIComponent(runId)}/step`, "POST");
  return toRun(d);
}

/** POST /workflows/runs/{run_id}/approvals/{node_id} → WorkflowRun.model_dump() (workflows.py:205) */
export async function resolveRunApproval(
  runId: string,
  nodeId: string,
  approved: boolean,
  feedback = "",
): Promise<WorkflowRun> {
  const d = await send<Record<string, unknown>>(
    `/workflows/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(nodeId)}`,
    "POST",
    { approved, feedback },
  );
  return toRun(d);
}

export interface TurnOutcome {
  run_id: string;
  workflow_id: string;
  mode: string;
  paradigm: string;
  status: string;
  waves: number;
  failed_nodes: string[];
  reason: string | null;
  handoff: Record<string, unknown> | null;
}

export interface TurnRequest {
  prompt: string;
  mode?: string;
  paradigm?: string;
  initial_state?: Record<string, unknown>;
  max_waves?: number;
  handoff_to?: string;
}

/** POST /workflows/turns → kernel TurnOutcome verbatim (workflows.py:223) */
export async function runWorkflowTurn(req: TurnRequest): Promise<TurnOutcome> {
  const body: Record<string, unknown> = { prompt: req.prompt };
  if (req.mode !== undefined) body.mode = req.mode;
  if (req.paradigm !== undefined) body.paradigm = req.paradigm;
  if (req.initial_state !== undefined) body.initial_state = req.initial_state;
  if (req.max_waves !== undefined) body.max_waves = req.max_waves;
  if (req.handoff_to !== undefined && req.handoff_to !== "") body.handoff_to = req.handoff_to;
  const d = await send<Record<string, unknown>>("/workflows/turns", "POST", body);
  const waves = Number(pick(d, ["waves"], 0));
  return {
    run_id: String(pick(d, ["run_id"], "")),
    workflow_id: String(pick(d, ["workflow_id"], "")),
    mode: String(pick(d, ["mode"], "")),
    paradigm: String(pick(d, ["paradigm"], "")),
    status: String(pick(d, ["status"], "unknown")),
    waves: Number.isFinite(waves) ? waves : 0,
    failed_nodes: toStringArray(d.failed_nodes),
    reason: typeof d.reason === "string" ? d.reason : null,
    handoff: d.handoff && typeof d.handoff === "object" ? (d.handoff as Record<string, unknown>) : null,
  };
}

/* ── Events, replay, durability ──────────────────────────────────────── */

export interface WorkflowEvent {
  event_id: string;
  workflow_run_id: string;
  event_type: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

function toEvent(e: Record<string, unknown>): WorkflowEvent {
  return {
    event_id: String(pick(e, ["event_id"], "")),
    workflow_run_id: String(pick(e, ["workflow_run_id"], "")),
    event_type: String(pick(e, ["event_type"], "unknown")),
    timestamp: String(pick(e, ["timestamp"], "")),
    payload: e.payload && typeof e.payload === "object" ? (e.payload as Record<string, unknown>) : {},
  };
}

export interface RunEvents {
  run_id: string;
  events: WorkflowEvent[];
  count: number;
}

/** GET /workflows/runs/{run_id}/events → { run_id, events, count } (workflows.py:215) */
export async function getRunEvents(runId: string): Promise<RunEvents> {
  const d = await get<Record<string, unknown>>(`/workflows/runs/${encodeURIComponent(runId)}/events`);
  const events = Array.isArray(d.events) ? (d.events as Array<Record<string, unknown>>).map(toEvent) : [];
  const count = Number(pick(d, ["count"], events.length));
  return {
    run_id: String(pick(d, ["run_id"], runId)),
    events,
    count: Number.isFinite(count) ? count : events.length,
  };
}

export interface DurableRunEvents {
  run_id: string;
  count: number;
  events: Array<Record<string, unknown>>;
  corrupt_tail: Array<Record<string, unknown>>;
}

/** GET /workflows/runs/{run_id}/events/durable → append-only JSONL records + corrupt-tail disclosures (workflows.py:354) */
export async function getDurableRunEvents(runId: string): Promise<DurableRunEvents> {
  const d = await get<Record<string, unknown>>(`/workflows/runs/${encodeURIComponent(runId)}/events/durable`);
  const events = Array.isArray(d.events) ? (d.events as Array<Record<string, unknown>>) : [];
  const count = Number(pick(d, ["count"], events.length));
  return {
    run_id: String(pick(d, ["run_id"], runId)),
    count: Number.isFinite(count) ? count : events.length,
    events,
    corrupt_tail: Array.isArray(d.corrupt_tail) ? (d.corrupt_tail as Array<Record<string, unknown>>) : [],
  };
}

export interface ReplayMismatch {
  field: string;
  live: unknown;
  replayed: unknown;
}

export interface ReplayReport {
  run_id: string;
  events_folded: number;
  events_emitted_during_replay: number;
  covered_fields: string[];
  matches_live: boolean;
  mismatches: ReplayMismatch[];
  replayed: Record<string, unknown>;
}

/** POST /workflows/runs/{run_id}/replay → fold-vs-live comparison (workflows.py:274) */
export async function replayWorkflowRun(runId: string): Promise<ReplayReport> {
  const d = await send<Record<string, unknown>>(`/workflows/runs/${encodeURIComponent(runId)}/replay`, "POST");
  const folded = Number(pick(d, ["events_folded"], 0));
  const emitted = Number(pick(d, ["events_emitted_during_replay"], 0));
  return {
    run_id: String(pick(d, ["run_id"], runId)),
    events_folded: Number.isFinite(folded) ? folded : 0,
    // 0 must survive as 0 — replay reading the shared log must never be shown
    // as "something was emitted".
    events_emitted_during_replay: Number.isFinite(emitted) ? emitted : 0,
    covered_fields: toStringArray(d.covered_fields),
    matches_live: d.matches_live === true,
    mismatches: Array.isArray(d.mismatches)
      ? (d.mismatches as Array<Record<string, unknown>>).map((m) => ({
          field: String(pick(m, ["field"], "")),
          live: m.live,
          replayed: m.replayed,
        }))
      : [],
    replayed: d.replayed && typeof d.replayed === "object" ? (d.replayed as Record<string, unknown>) : {},
  };
}

export interface DurabilityStatus {
  dispatcher: { attached: boolean; write_failures: number; last_error: string | null };
  store: {
    store_dir: string;
    writable: boolean;
    writable_detail: string | null;
    persisted_runs: string[];
    persisted_run_count: number;
  };
}

/** GET /workflows/system/durability → honest journal status (workflows.py:331) */
export async function getDurabilityStatus(): Promise<DurabilityStatus> {
  const d = await get<Record<string, unknown>>("/workflows/system/durability");
  const dispatcher = (d.dispatcher && typeof d.dispatcher === "object" ? d.dispatcher : {}) as Record<string, unknown>;
  const store = (d.store && typeof d.store === "object" ? d.store : {}) as Record<string, unknown>;
  const failures = Number(pick(dispatcher, ["write_failures"], 0));
  const count = Number(pick(store, ["persisted_run_count"], 0));
  return {
    dispatcher: {
      // Booleans are passed through as observed: writable:false stays false.
      attached: dispatcher.attached === true,
      write_failures: Number.isFinite(failures) ? failures : 0,
      last_error: typeof dispatcher.last_error === "string" ? dispatcher.last_error : null,
    },
    store: {
      store_dir: String(pick(store, ["store_dir"], "")),
      writable: store.writable === true,
      writable_detail: typeof store.writable_detail === "string" ? store.writable_detail : null,
      persisted_runs: toStringArray(store.persisted_runs),
      persisted_run_count: Number.isFinite(count) ? count : 0,
    },
  };
}

export interface HydrationReport {
  status: string;
  store_dir: string;
  hydrated_runs: string[];
  skipped_existing: string[];
  corrupt_runs: Array<{ run_id: string; error: string }>;
  stale_projections: Array<{ run_id: string; detail: string }>;
  missing_graphs: string[];
  missing_definitions: string[];
  disclosures: string[];
}

/** POST /workflows/hydrate → honest HydrationReport (workflows.py:410, schemas.py:96) */
export async function hydrateWorkflowEngine(): Promise<HydrationReport> {
  const d = await send<Record<string, unknown>>("/workflows/hydrate", "POST");
  return {
    status: String(pick(d, ["status"], "unknown")),
    store_dir: String(pick(d, ["store_dir"], "")),
    hydrated_runs: toStringArray(d.hydrated_runs),
    skipped_existing: toStringArray(d.skipped_existing),
    corrupt_runs: Array.isArray(d.corrupt_runs)
      ? (d.corrupt_runs as Array<Record<string, unknown>>).map((c) => ({
          run_id: String(pick(c, ["run_id"], "")),
          error: String(pick(c, ["error"], "")),
        }))
      : [],
    stale_projections: Array.isArray(d.stale_projections)
      ? (d.stale_projections as Array<Record<string, unknown>>).map((c) => ({
          run_id: String(pick(c, ["run_id"], "")),
          detail: String(pick(c, ["detail"], "")),
        }))
      : [],
    missing_graphs: toStringArray(d.missing_graphs),
    missing_definitions: toStringArray(d.missing_definitions),
    disclosures: toStringArray(d.disclosures),
  };
}

export interface ProjectReport {
  run_id: string;
  status: string;
  last_seq: number;
  event_count: number;
  graph_versions: string[];
  definition_recorded: boolean;
}

/** POST /workflows/runs/{run_id}/project → materialize live projection (workflows.py:367) */
export async function projectWorkflowRun(runId: string): Promise<ProjectReport> {
  const d = await send<Record<string, unknown>>(`/workflows/runs/${encodeURIComponent(runId)}/project`, "POST");
  const lastSeq = Number(pick(d, ["last_seq"], 0));
  const eventCount = Number(pick(d, ["event_count"], 0));
  return {
    run_id: String(pick(d, ["run_id"], runId)),
    status: String(pick(d, ["status"], "unknown")),
    last_seq: Number.isFinite(lastSeq) ? lastSeq : 0,
    event_count: Number.isFinite(eventCount) ? eventCount : 0,
    graph_versions: toStringArray(d.graph_versions),
    definition_recorded: d.definition_recorded === true,
  };
}

/* ── Plan-graph revisions ────────────────────────────────────────────── */

export interface PlanHistory {
  workflow_id: string;
  versions: number[];
  count: number;
  latest_source: string | null;
}

/** GET /workflows/{workflow_id}/plans → durable graph-revision history (workflows.py:427) */
export async function listWorkflowPlans(workflowId: string): Promise<PlanHistory> {
  const d = await get<Record<string, unknown>>(`/workflows/${encodeURIComponent(workflowId)}/plans`);
  const versions = Array.isArray(d.versions) ? (d.versions as unknown[]).map((v) => Number(v)).filter((v) => Number.isFinite(v)) : [];
  const count = Number(pick(d, ["count"], versions.length));
  return {
    workflow_id: String(pick(d, ["workflow_id"], workflowId)),
    versions,
    count: Number.isFinite(count) ? count : versions.length,
    latest_source: typeof d.latest_source === "string" ? d.latest_source : null,
  };
}

export interface PlanRecord {
  workflow_id: string;
  version: number;
  source: string;
  created_at: string;
}

/** POST /workflows/{workflow_id}/plans → append a revision (workflows.py:448) */
export async function recordWorkflowPlan(
  workflowId: string,
  opts: { source?: string; note?: string } = {},
): Promise<PlanRecord> {
  const d = await send<Record<string, unknown>>(`/workflows/${encodeURIComponent(workflowId)}/plans`, "POST", {
    source: opts.source ?? "manual",
    note: opts.note ?? "",
  });
  const version = Number(pick(d, ["version"], 0));
  return {
    workflow_id: String(pick(d, ["workflow_id"], workflowId)),
    version: Number.isFinite(version) ? version : 0,
    source: String(pick(d, ["source"], "manual")),
    created_at: String(pick(d, ["created_at"], "")),
  };
}

/* ── Run patching ────────────────────────────────────────────────────── */

export interface PatchResult {
  status: string;
  new_graph_version: number;
}

/**
 * POST /workflows/runs/{run_id}/patch → { status: "committed", new_graph_version }
 * (workflows.py:186). The body is a WorkflowPatch exactly as the server model
 * defines it (workflow_run_id, base_graph_version, reason, operations…); the
 * server validates and rejects bad patches with its real reason.
 */
export async function patchWorkflowRun(
  runId: string,
  patch: Record<string, unknown>,
): Promise<PatchResult> {
  const d = await send<Record<string, unknown>>(`/workflows/runs/${encodeURIComponent(runId)}/patch`, "POST", patch);
  const v = Number(pick(d, ["new_graph_version"], 0));
  return { status: String(pick(d, ["status"], "unknown")), new_graph_version: Number.isFinite(v) ? v : 0 };
}

/* ── Checkpoints (checkpoints.py) ────────────────────────────────────── */

export interface Checkpoint {
  checkpoint_id: string;
  label: string;
  created_at: number;
  files_count: number;
  test_passed: boolean | null;
  failure_count: number;
  git_ref: string | null;
}

function toCheckpoint(c: Record<string, unknown>): Checkpoint {
  const created = Number(pick(c, ["created_at"], 0));
  const filesCount = Number(pick(c, ["files_count"], 0));
  const failures = Number(pick(c, ["failure_count"], 0));
  return {
    checkpoint_id: String(pick(c, ["checkpoint_id"], "")),
    label: String(pick(c, ["label"], "")),
    created_at: Number.isFinite(created) ? created : 0,
    files_count: Number.isFinite(filesCount) ? filesCount : 0,
    // null (tests never ran) survives as null — never coerced to false/true.
    test_passed: typeof c.test_passed === "boolean" ? c.test_passed : null,
    failure_count: Number.isFinite(failures) ? failures : 0,
    git_ref: typeof c.git_ref === "string" ? c.git_ref : null,
  };
}

/** GET /checkpoints → [{checkpoint_id,label,created_at,files_count,test_passed,failure_count,git_ref}] (checkpoints.py:42, code_agentic_core.py:375) */
export async function listCheckpoints(): Promise<Checkpoint[]> {
  const d = await get<unknown>("/checkpoints");
  return asList(d, ["checkpoints", "data"]).map(toCheckpoint);
}

export interface CheckpointCreateResult {
  status: string;
  checkpoint_id: string;
  label: string;
  created_at: number;
  files_count: number;
  git_ref: string | null;
}

/** POST /checkpoints → {status:"created", checkpoint_id, label, created_at, files_count, git_ref} (checkpoints.py:48) */
export async function createCheckpoint(label: string, rootPath = ".", targetFiles?: string[]): Promise<CheckpointCreateResult> {
  const body: Record<string, unknown> = { label, root_path: rootPath };
  if (targetFiles && targetFiles.length > 0) body.target_files = targetFiles;
  const d = await send<Record<string, unknown>>("/checkpoints", "POST", body);
  const created = Number(pick(d, ["created_at"], 0));
  const filesCount = Number(pick(d, ["files_count"], 0));
  return {
    status: String(pick(d, ["status"], "unknown")),
    checkpoint_id: String(pick(d, ["checkpoint_id"], "")),
    label: String(pick(d, ["label"], "")),
    created_at: Number.isFinite(created) ? created : 0,
    files_count: Number.isFinite(filesCount) ? filesCount : 0,
    git_ref: typeof d.git_ref === "string" ? d.git_ref : null,
  };
}

export interface RollbackResult {
  status: string;
  checkpoint_id: string;
  label: string;
  restored_files_count: number;
  restored_files: string[];
  git_ref: string | null;
}

/** POST /checkpoints/{id}/rollback → {status:"rolled_back", restored_files…} (checkpoints.py:70, code_agentic_core.py:391) */
export async function rollbackCheckpoint(checkpointId: string, rootPath = "."): Promise<RollbackResult> {
  const d = await send<Record<string, unknown>>(
    `/checkpoints/${encodeURIComponent(checkpointId)}/rollback?root_path=${encodeURIComponent(rootPath)}`,
    "POST",
  );
  const restored = Number(pick(d, ["restored_files_count"], 0));
  return {
    status: String(pick(d, ["status"], "unknown")),
    checkpoint_id: String(pick(d, ["checkpoint_id"], checkpointId)),
    label: String(pick(d, ["label"], "")),
    restored_files_count: Number.isFinite(restored) ? restored : 0,
    restored_files: toStringArray(d.restored_files),
    git_ref: typeof d.git_ref === "string" ? d.git_ref : null,
  };
}

export interface CheckpointDiff {
  checkpoint_id: string;
  label: string;
  diff_count: number;
  diffs: Record<string, string>;
}

/** GET /checkpoints/{id}/diff → {checkpoint_id, label, diff_count, diffs} (checkpoints.py:79, code_agentic_core.py:418) */
export async function getCheckpointDiff(checkpointId: string, rootPath = "."): Promise<CheckpointDiff> {
  const d = await get<Record<string, unknown>>(
    `/checkpoints/${encodeURIComponent(checkpointId)}/diff?root_path=${encodeURIComponent(rootPath)}`,
  );
  const diffCount = Number(pick(d, ["diff_count"], 0));
  return {
    checkpoint_id: String(pick(d, ["checkpoint_id"], checkpointId)),
    label: String(pick(d, ["label"], "")),
    diff_count: Number.isFinite(diffCount) ? diffCount : 0,
    diffs: d.diffs && typeof d.diffs === "object" ? (d.diffs as Record<string, string>) : {},
  };
}

/* ── Jobs (jobs.py) ──────────────────────────────────────────────────── */

export interface JobRecord {
  job_id: string;
  status: string;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  execution_seconds: number | null;
  artifacts: string[];
  error: string | null;
  started_at: number | null;
  completed_at: number | null;
  metadata: Record<string, unknown>;
  /** From JobResult.metadata.title (queue.py:73) when the server sent one. */
  title?: string;
}

function toJob(j: Record<string, unknown>): JobRecord {
  const metadata = (j.metadata && typeof j.metadata === "object" ? j.metadata : {}) as Record<string, unknown>;
  return {
    job_id: String(pick(j, ["job_id"], "")),
    status: String(pick(j, ["status"], "unknown")),
    // Genuine nulls from JobResult (not-yet-started timestamps, absent exit
    // codes) stay null — Number(null) === 0 must never turn them into epoch 0.
    exit_code: typeof j.exit_code === "number" ? j.exit_code : null,
    stdout: String(pick(j, ["stdout"], "")),
    stderr: String(pick(j, ["stderr"], "")),
    execution_seconds: typeof j.execution_seconds === "number" ? j.execution_seconds : null,
    artifacts: toStringArray(j.artifacts),
    error: typeof j.error === "string" ? j.error : null,
    started_at: typeof j.started_at === "number" ? j.started_at : null,
    completed_at: typeof j.completed_at === "number" ? j.completed_at : null,
    metadata,
    title: typeof metadata.title === "string" ? metadata.title : undefined,
  };
}

/** GET /jobs → JobResult[] most-recent-first (jobs.py:85) */
export async function listJobs(opts: { status?: string; tag?: string; limit?: number } = {}): Promise<JobRecord[]> {
  const params: string[] = [];
  if (opts.status) params.push(`status=${encodeURIComponent(opts.status)}`);
  if (opts.tag) params.push(`tag=${encodeURIComponent(opts.tag)}`);
  params.push(`limit=${Math.min(100, Math.max(1, opts.limit ?? 50))}`);
  const d = await get<unknown>(`/jobs?${params.join("&")}`);
  return asList(d, ["jobs", "data"]).map(toJob);
}

/** GET /jobs/{job_id} → JobResult.model_dump() (jobs.py:99) */
export async function getJob(jobId: string): Promise<JobRecord> {
  const d = await get<Record<string, unknown>>(`/jobs/${encodeURIComponent(jobId)}`);
  return toJob(d);
}

export interface JobSubmitRequest {
  command: string;
  title?: string;
  working_dir?: string;
  priority?: string;
  timeout_seconds?: number;
  tags?: string[];
}

export interface JobSubmitResult {
  status: string;
  job_id: string;
  title: string;
  priority: string;
  timeout_seconds: number;
}

/** POST /jobs → {status:"queued", job_id, title, priority, timeout_seconds} (jobs.py:48). Requires an authenticated operator session (jobs.py:52). */
export async function submitJob(req: JobSubmitRequest): Promise<JobSubmitResult> {
  const body: Record<string, unknown> = { command: req.command };
  if (req.title !== undefined) body.title = req.title;
  if (req.working_dir !== undefined) body.working_dir = req.working_dir;
  if (req.priority !== undefined) body.priority = req.priority;
  if (req.timeout_seconds !== undefined) body.timeout_seconds = req.timeout_seconds;
  if (req.tags !== undefined) body.tags = req.tags;
  const d = await send<Record<string, unknown>>("/jobs", "POST", body);
  const timeout = Number(pick(d, ["timeout_seconds"], 0));
  return {
    status: String(pick(d, ["status"], "unknown")),
    job_id: String(pick(d, ["job_id"], "")),
    title: String(pick(d, ["title"], "")),
    priority: String(pick(d, ["priority"], "")),
    timeout_seconds: Number.isFinite(timeout) ? timeout : 0,
  };
}

export interface JobLogs {
  job_id: string;
  status: string;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  execution_seconds: number | null;
}

/** GET /jobs/{job_id}/logs → {job_id,status,exit_code,stdout,stderr,execution_seconds} (jobs.py:109) */
export async function getJobLogs(jobId: string): Promise<JobLogs> {
  const d = await get<Record<string, unknown>>(`/jobs/${encodeURIComponent(jobId)}/logs`);
  return {
    job_id: String(pick(d, ["job_id"], jobId)),
    status: String(pick(d, ["status"], "unknown")),
    exit_code: typeof d.exit_code === "number" ? d.exit_code : null,
    stdout: String(pick(d, ["stdout"], "")),
    stderr: String(pick(d, ["stderr"], "")),
    execution_seconds: typeof d.execution_seconds === "number" ? d.execution_seconds : null,
  };
}

/** POST /jobs/{job_id}/cancel → {status:"cancelled", job_id} (jobs.py:126) */
export async function cancelJob(jobId: string): Promise<{ status: string; job_id: string }> {
  const d = await send<Record<string, unknown>>(`/jobs/${encodeURIComponent(jobId)}/cancel`, "POST");
  return { status: String(pick(d, ["status"], "unknown")), job_id: String(pick(d, ["job_id"], jobId)) };
}
