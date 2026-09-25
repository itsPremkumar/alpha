"use client";

import React, { useEffect, useState } from "react";
import {
  listWorkflows, createWorkflow, getWorkflow, listWorkflowRuns, getWorkflowRun, startWorkflowRun, stepWorkflowRun,
  resolveRunApproval, replanWorkflowRun, compensateWorkflowRun, perceiveDynamicWorkflow, executeDynamicWorkflow,
  runWorkflowTurn, getRunEvents, getDurableRunEvents, replayWorkflowRun,
  projectWorkflowRun, patchWorkflowRun, getDurabilityStatus, hydrateWorkflowEngine,
  listWorkflowPlans, recordWorkflowPlan, listCheckpoints, createCheckpoint, getCheckpointDiff,
  rollbackCheckpoint, listJobs, submitJob, getJobLogs, cancelJob,
} from "@/lib/workflows";
import type {
  WorkflowListItem, WorkflowDefinition, WorkflowRunListItem, WorkflowRun, DynamicPerceiveResult, DynamicExecuteResult,
  TurnOutcome, RunEvents, DurableRunEvents,
  ReplayReport, ProjectReport, DurabilityStatus, HydrationReport, PlanHistory, Checkpoint,
  CheckpointDiff, JobRecord, JobLogs,
} from "@/lib/workflows";
import {
  listGoalContracts, createGoalContract, createGoalPlan, approveGoalPlan, createGoalAttempt,
  transitionGoalAttempt, auditGoalIntegrity, listMissions, createMission, transitionMission,
  attachMissionThread, ATTEMPT_STATUSES, MISSION_TRANSITION_TARGETS,
} from "@/lib/goals";
import type { GoalContract, PlanVersion, TaskAttempt, IntegrityReport, Mission, AttemptStatus } from "@/lib/goals";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, Field, SkeletonList, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { RefreshCw, Plus, Check, X, Zap, Search, ScrollText, RotateCcw, GitCompare, Sparkles, Rocket, Eye, Wrench } from "lucide-react";

type Tone = "green" | "amber" | "gray" | "blue" | "red" | "purple" | "cyan" | "indigo";

/** Badge tone per server-observed status; unknown values render neutral gray. */
const STATUS_TONES: Record<string, Tone> = {
  // Workflow run + node statuses (alpha/workflow/models.py)
  completed: "green", running: "blue", waiting_approval: "amber", waiting_event: "amber",
  suspended: "amber", failed: "red", cancelled: "gray", budget_exhausted: "red", pending: "gray",
  waiting: "amber", ready: "blue", succeeded: "green", retrying: "amber", skipped: "gray",
  compensating: "purple",
  // Job statuses (alpha/jobs/models.py)
  queued: "gray", timed_out: "red",
  // Goal contract / plan / attempt statuses (alpha/goals/models.py)
  active: "blue", achieved: "green", abandoned: "gray",
  draft: "gray", approved: "green", superseded: "amber",
  // Mission statuses (alpha/missions/store.py)
  paused: "amber",
  // Hydration report status (alpha/workflow/schemas.py)
  ok: "green", empty: "gray", degraded: "amber",
};

function statusTone(status: string): Tone {
  return STATUS_TONES[status] ?? "gray";
}

function fmtEpoch(sec: number | null | undefined): string {
  if (!sec || !Number.isFinite(sec) || sec <= 0) return "—";
  return new Date(sec * 1000).toLocaleString();
}

function fmtIso(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** Parse user-entered JSON honestly; throws with the real parse error. */
function parseJson(text: string, what: string): unknown {
  try {
    return JSON.parse(text);
  } catch (e) {
    throw new Error(`${what} must be valid JSON — ${e instanceof Error ? e.message : String(e)}`);
  }
}

function SubTabs(props: { value: string; onChange: (v: string) => void; tabs: Array<{ id: string; label: string }> }) {
  return (
    <div className="flex gap-1 rounded-xl bg-muted/60 p-1 w-fit flex-wrap">
      {props.tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => props.onChange(t.id)}
          className={`px-3 py-1.5 rounded-lg text-[11px] font-semibold ${props.value === t.id ? "bg-card shadow" : "text-muted-foreground hover:text-foreground"}`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

type PanelId = "workflows" | "goals" | "ops";

export function WorkflowsSection() {
  const [panel, setPanel] = useState<PanelId>("workflows");
  const [notice, setNotice] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const flash = (m: string) => {
    setNotice(m);
    window.setTimeout(() => setNotice(null), 6000);
  };

  return (
    <Section
      title="Workflows"
      hint="Dynamic workflow engine, goal contracts, missions, checkpoints & background jobs — every value below comes straight from the gateway APIs."
      actions={
        <Btn variant="ghost" onClick={() => setRefreshKey((k) => k + 1)}>
          <RefreshCw className="size-3.5" /> Refresh
        </Btn>
      }
    >
      <SubTabs
        value={panel}
        onChange={(v) => setPanel(v as PanelId)}
        tabs={[
          { id: "workflows", label: "Workflows" },
          { id: "goals", label: "Goals & Missions" },
          { id: "ops", label: "Checkpoints & Jobs" },
        ]}
      />
      {notice && <Notice message={notice} />}
      {panel === "workflows" && <WorkflowsPanel refreshKey={refreshKey} onNotice={flash} />}
      {panel === "goals" && <GoalsPanel refreshKey={refreshKey} onNotice={flash} />}
      {panel === "ops" && <OpsPanel refreshKey={refreshKey} onNotice={flash} />}
    </Section>
  );
}

/* ══ Panel 1: Workflows ═════════════════════════════════════════════ */

function WorkflowsPanel(props: { refreshKey: number; onNotice: (m: string) => void }) {
  const [workflows, setWorkflows] = useState<WorkflowListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [durability, setDurability] = useState<DurabilityStatus | null>(null);
  const [durabilityError, setDurabilityError] = useState<string | null>(null);
  const [hydrateReport, setHydrateReport] = useState<HydrationReport | null>(null);
  const [hydrating, setHydrating] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [draft, setDraft] = useState({ id: "", name: "", description: "", graphJson: "" });
  const [plans, setPlans] = useState<Record<string, PlanHistory>>({});
  const [definition, setDefinition] = useState<WorkflowDefinition | null>(null);
  const [runs, setRuns] = useState<Record<string, WorkflowRun>>({});
  const [runErrors, setRunErrors] = useState<Record<string, string>>({});
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [events, setEvents] = useState<RunEvents | null>(null);
  const [durableEvents, setDurableEvents] = useState<DurableRunEvents | null>(null);
  const [replay, setReplay] = useState<ReplayReport | null>(null);
  const [project, setProject] = useState<ProjectReport | null>(null);
  const [approvalFeedback, setApprovalFeedback] = useState("");
  const [patchText, setPatchText] = useState("");
  const [turn, setTurn] = useState({ prompt: "", mode: "normal", paradigm: "direct_agent", maxWaves: 50, handoffTo: "" });
  const [turnOutcome, setTurnOutcome] = useState<TurnOutcome | null>(null);
  const [dynamicPrompt, setDynamicPrompt] = useState("");
  const [dynamicPreview, setDynamicPreview] = useState<DynamicPerceiveResult | null>(null);
  const [dynamicExecuting, setDynamicExecuting] = useState(false);
  const [dynamicResult, setDynamicResult] = useState<DynamicExecuteResult | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setWorkflows(await listWorkflows());
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
    try {
      const rl = await listWorkflowRuns();
      setRuns((prev) => {
        const next = { ...prev };
        for (const r of rl) {
          if (!next[r.run_id]) {
            next[r.run_id] = {
              run_id: r.run_id,
              workflow_id: r.workflow_id,
              graph_version: r.graph_version,
              status: r.status,
              state: {},
              node_states: {},
              active_nodes: r.active_nodes,
              completed_nodes: r.completed_nodes,
              failed_nodes: r.failed_nodes,
              waiting_nodes: r.waiting_nodes,
              iteration_counts: {},
              metrics: {},
              history: [],
              waiting_reason: r.waiting_reason,
              approval_request_id: null,
              created_at: r.created_at,
              updated_at: r.updated_at,
            };
          }
        }
        return next;
      });
      if (activeRunId) await loadRun(activeRunId);
    } catch {
      // Optional run list hydration
    }
    try {
      setDurability(await getDurabilityStatus());
      setDurabilityError(null);
    } catch (e) {
      setDurability(null);
      setDurabilityError(errMsg(e));
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.refreshKey]);

  const loadRun = async (runId: string) => {
    setActiveRunId(runId);
    setDetailError(null);
    setEvents(null);
    setDurableEvents(null);
    setReplay(null);
    setProject(null);
    try {
      const run = await getWorkflowRun(runId);
      setRuns((prev) => ({ ...prev, [runId]: run }));
      setRunErrors((prev) => {
        const next = { ...prev };
        delete next[runId];
        return next;
      });
    } catch (e) {
      setRunErrors((prev) => ({ ...prev, [runId]: errMsg(e) }));
    }
  };

  const onHydrate = async () => {
    setHydrating(true);
    setDetailError(null);
    try {
      const report = await hydrateWorkflowEngine();
      setHydrateReport(report);
      props.onNotice(`Hydration: "${report.status}" — ${report.hydrated_runs.length} run(s) installed, ${report.skipped_existing.length} already present.`);
      const ids = Array.from(new Set([...report.hydrated_runs, ...(durability?.store.persisted_runs ?? [])]));
      for (const id of ids) await loadRun(id);
    } catch (e) {
      setDetailError(errMsg(e));
    } finally {
      setHydrating(false);
      try {
        setDurability(await getDurabilityStatus());
        setDurabilityError(null);
      } catch (e) {
        setDurability(null);
        setDurabilityError(errMsg(e));
      }
    }
  };

  const onRegister = async () => {
    setDetailError(null);
    try {
      if (!draft.id.trim() || !draft.name.trim()) throw new Error("Workflow id and name are required.");
      const graph = draft.graphJson.trim() ? (parseJson(draft.graphJson, "Graph") as Record<string, unknown>) : undefined;
      const created = await createWorkflow({
        id: draft.id.trim(),
        name: draft.name.trim(),
        description: draft.description,
        graph,
      });
      props.onNotice(`Registered "${created.name}" (${created.id}, version ${created.version}).`);
      setDraft({ id: "", name: "", description: "", graphJson: "" });
      setShowCreate(false);
      await load();
    } catch (e) {
      setDetailError(errMsg(e));
    }
  };

  const onStartRun = async (workflowId: string) => {
    setDetailError(null);
    try {
      const run = await startWorkflowRun(workflowId);
      setRuns((prev) => ({ ...prev, [run.run_id]: run }));
      setActiveRunId(run.run_id);
      props.onNotice(`Run ${run.run_id} started — status "${run.status}".`);
    } catch (e) {
      setDetailError(errMsg(e));
    }
  };

  const runAct = async (fn: () => Promise<void>) => {
    setDetailError(null);
    try {
      await fn();
    } catch (e) {
      setDetailError(errMsg(e));
    }
  };

  const applyRun = (run: WorkflowRun) => setRuns((prev) => ({ ...prev, [run.run_id]: run }));

  const onStep = () =>
    runAct(async () => {
      if (!activeRunId) return;
      const run = await stepWorkflowRun(activeRunId);
      applyRun(run);
      props.onNotice(`Stepped one wave — status "${run.status}".`);
    });

  const onReplay = () =>
    runAct(async () => {
      if (!activeRunId) return;
      setReplay(await replayWorkflowRun(activeRunId));
    });

  const onEvents = () =>
    runAct(async () => {
      if (!activeRunId) return;
      setEvents(await getRunEvents(activeRunId));
    });

  const onDurableEvents = () =>
    runAct(async () => {
      if (!activeRunId) return;
      setDurableEvents(await getDurableRunEvents(activeRunId));
    });

  const onProject = () =>
    runAct(async () => {
      if (!activeRunId) return;
      const report = await projectWorkflowRun(activeRunId);
      setProject(report);
      props.onNotice(`Projection written — ${report.event_count} event(s), last seq ${report.last_seq}.`);
    });

  const onPatch = () =>
    runAct(async () => {
      if (!activeRunId) return;
      const run = runs[activeRunId];
      if (!run) return;
      const parsed = parseJson(patchText, "Patch") as { reason?: string; operations?: unknown[] };
      if (!Array.isArray(parsed.operations)) throw new Error('Patch JSON needs an "operations" array (WorkflowPatch shape).');
      const result = await patchWorkflowRun(activeRunId, {
        workflow_run_id: run.run_id,
        base_graph_version: run.graph_version,
        reason: typeof parsed.reason === "string" && parsed.reason ? parsed.reason : "manual UI patch",
        proposed_by: "ui",
        operations: parsed.operations,
      });
      props.onNotice(`Patch ${result.status} — graph v${result.new_graph_version}.`);
      await loadRun(activeRunId);
    });

  const onApproval = (nodeId: string, approved: boolean) =>
    runAct(async () => {
      if (!activeRunId) return;
      const run = await resolveRunApproval(
        activeRunId,
        nodeId,
        approved,
        approvalFeedback,
        runs[activeRunId]?.approval_request_id ?? undefined,
      );
      applyRun(run);
      setApprovalFeedback("");
      props.onNotice(`Node "${nodeId}" ${approved ? "approved" : "denied"} — run "${run.status}".`);
    });

  const onTurn = () =>
    runAct(async () => {
      if (!turn.prompt.trim()) throw new Error("Write the prompt for the turn.");
      const outcome = await runWorkflowTurn({
        prompt: turn.prompt.trim(),
        mode: turn.mode,
        paradigm: turn.paradigm,
        max_waves: turn.maxWaves,
        handoff_to: turn.handoffTo.trim() || undefined,
      });
      setTurnOutcome(outcome);
      if (outcome.run_id) await loadRun(outcome.run_id);
    });

  const onPerceive = async (promptOverride?: string) => {
    const prompt = (promptOverride ?? dynamicPrompt).trim();
    if (!prompt) {
      setDetailError("Please enter a prompt to perceive.");
      return;
    }
    setDetailError(null);
    try {
      const res = await perceiveDynamicWorkflow(prompt);
      setDynamicPreview(res);
      props.onNotice(`Perceived intent "${res.perception.intent_type}" (${res.perception.execution_tier}) with ${res.goal.tasks.length} task(s).`);
    } catch (e) {
      setDetailError(errMsg(e));
    }
  };

  const onDynamicExecute = async (promptOverride?: string) => {
    const prompt = (promptOverride ?? dynamicPrompt).trim();
    if (!prompt) {
      setDetailError("Please enter a prompt to execute.");
      return;
    }
    setDynamicExecuting(true);
    setDetailError(null);
    try {
      const res = await executeDynamicWorkflow({ prompt, auto_execute: true, max_steps: 40 });
      setDynamicResult(res);
      const acceptance = res.metadata.acceptance_passed === true
        ? "domain acceptance verified"
        : "graph projection only — domain acceptance not claimed";
      props.onNotice(`Dynamic workflow "${res.workflow_id}" executed — status "${res.status}" (${res.completed_nodes.length} completed; ${acceptance}).`);
      await load();
      if (res.run_id) await loadRun(res.run_id);
    } catch (e) {
      setDetailError(errMsg(e));
    } finally {
      setDynamicExecuting(false);
    }
  };

  const onReplan = async (runId: string) => {
    setDetailError(null);
    try {
      const res = await replanWorkflowRun(runId, { resume: true });
      if (res.status === "no_op") {
         props.onNotice(`No replan needed for ${runId}: ${res.reason || "no failed nodes"}.`);
       } else if (res.status === "committed_resume_failed") {
         props.onNotice(`Replan committed, but resume failed for ${runId}: ${res.resume_error || "unknown error"}.`);
       } else {
         props.onNotice(`Replanned run ${runId} — graph v${res.new_graph_version} (ops: ${res.patch_operations.join(", ")}).`);
       }
      await loadRun(runId);
    } catch (e) {
      setDetailError(errMsg(e));
    }
  };

  const onCompensate = async (runId: string) => {
    setDetailError(null);
    try {
      const res = await compensateWorkflowRun(runId);
      props.onNotice(
         res.executed
           ? `Compensated run ${runId} — ${res.count} task(s) rolled back.`
           : `Compensation not executed for ${runId}: ${res.reason || res.status}.`,
       );
      await loadRun(runId);
    } catch (e) {
      setDetailError(errMsg(e));
    }
  };

  const onLoadPlans = (workflowId: string) =>
    runAct(async () => {
      const history = await listWorkflowPlans(workflowId);
      setPlans((prev) => ({ ...prev, [workflowId]: history }));
    });

  const onRecordPlan = (workflowId: string) =>
    runAct(async () => {
      const record = await recordWorkflowPlan(workflowId, { source: "manual" });
      props.onNotice(`Recorded ${record.workflow_id} v${record.version} (source: ${record.source}).`);
      await onLoadPlans(workflowId);
    });

  const onInspect = (workflowId: string) =>
    runAct(async () => {
      setDefinition(await getWorkflow(workflowId));
    });

  const activeRun = activeRunId ? runs[activeRunId] ?? null : null;
  const runIds = Array.from(new Set([...Object.keys(runs), ...Object.keys(runErrors)]));

  return (
    <div className="space-y-4">
      {detailError && <ErrorBox message={detailError} onRetry={() => setDetailError(null)} />}

      {/* Dynamic workflow graph — POST /workflows/dynamic/perceive & /execute */}
      <div className="rounded-xl border border-primary/30 bg-card p-4 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Sparkles className="size-4 text-primary" />
          <p className="text-sm font-semibold flex-1">
            Dynamic Workflow Graph (boost intent)
          </p>
          <Badge tone="blue">Graph execution</Badge>
          <Badge tone="purple">Perception · DAG · Saga · Self-healing</Badge>
        </div>
        <p className="text-xs text-muted-foreground">
          Enter a natural-language goal. The <span className="font-mono">boost</span>{" "}
          intent keyword (and other catalog-valid commands) automatically
          perceives intent &amp; risk, decomposes dependencies into parallel
          execution waves, assembles specialists &amp; tools, compiles a live
          Workflow DAG. Execution uses only bound executors; the default digest is a graph projection,
          never presented as domain-task completion.
        </p>
        <div className="flex gap-2 flex-wrap">
          <input
            value={dynamicPrompt}
            onChange={(e) => {
               setDynamicPrompt(e.target.value);
               setDynamicPreview(null);
               setDynamicResult(null);
             }}
            placeholder="boost intent: implement, test, and verify a production feature..."
            className={inputCls + " flex-1 min-w-[260px]"}
          />
          <Btn
            variant="ghost"
            disabled={dynamicExecuting}
            onClick={() => onPerceive()}
            title="Perceive intent, decompose DAG, and assemble resources without starting a run"
          >
            <Eye className="size-3.5" /> Perceive &amp; Preview DAG
          </Btn>
          <Btn
            disabled={dynamicExecuting}
            onClick={() => onDynamicExecute()}
            title="Compile and execute the dynamic workflow graph through its bound executors"
          >
            <Rocket className="size-3.5" />{" "}
            {dynamicExecuting ? "Executing…" : "Execute dynamic graph"}
          </Btn>
        </div>
        <div className="flex gap-1.5 flex-wrap text-[11px]">
          <span className="text-muted-foreground py-0.5">Quick presets:</span>
          {[
            "boost Build and verify end-to-end workflow automation",
            "boost Audit security, run full test suite, and fix regressions",
            "boost Refactor module architecture and validate contracts",
          ].map((preset) => (
            <button
              key={preset}
              type="button"
              onClick={() => {
                setDynamicPrompt(preset);
                void onPerceive(preset);
              }}
              className="rounded-md border border-border/60 bg-muted/40 hover:bg-muted px-2 py-0.5 font-mono text-[10px] transition"
            >
              {preset}
            </button>
          ))}
        </div>

        {dynamicPreview && (
          <div className="rounded-lg border border-border/60 bg-muted/20 p-3 space-y-2 text-xs">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-semibold">Perceived Goal:</span>
              <span className="font-mono">{dynamicPreview.goal.title || dynamicPreview.perception.raw_prompt}</span>
              <Badge tone="blue">{dynamicPreview.perception.primary_domain}</Badge>
              <Badge tone={statusTone(dynamicPreview.perception.execution_tier)}>
                tier: {dynamicPreview.perception.execution_tier}
              </Badge>
              <Badge tone="gray">
                complexity: {Math.round(dynamicPreview.perception.complexity_score * 100)}%
              </Badge>
              <Badge tone="green">
                {dynamicPreview.goal.tasks.length} tasks ·{" "}
                {dynamicPreview.goal.execution_waves.length} wave(s)
              </Badge>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              <div className="rounded-md border border-border/40 bg-background/60 p-2 space-y-1">
                <p className="text-[11px] font-semibold">
                  Execution Waves &amp; Saga Compensations
                </p>
                {dynamicPreview.goal.execution_waves.map((wave, idx) => (
                  <div key={idx} className="text-[11px] flex items-center gap-1.5 flex-wrap">
                    <Badge tone="purple">Wave {idx + 1}</Badge>
                    <span className="font-mono">{wave.join(" → ")}</span>
                  </div>
                ))}
                {dynamicPreview.goal.saga_compensations.length > 0 && (
                  <p className="text-[10px] text-muted-foreground pt-1">
                    Compensations: {dynamicPreview.goal.saga_compensations.map((c) => c.action_id).join(", ")}
                  </p>
                )}
              </div>
              <div className="rounded-md border border-border/40 bg-background/60 p-2 space-y-1">
                <p className="text-[11px] font-semibold">
                  Assembled Bot Specialists &amp; Tools
                </p>
                <p className="text-[11px] text-muted-foreground">
                  Specialists:{" "}
                  <span className="font-mono text-foreground">
                    {Object.keys(dynamicPreview.resources.bots).join(", ") || "default"}
                  </span>
                </p>
                <p className="text-[11px] text-muted-foreground">
                  Tools:{" "}
                  <span className="font-mono text-foreground">
                    {dynamicPreview.resources.tools.join(", ") || "alpha.tools"}
                  </span>
                </p>
                {dynamicPreview.resources.mcp_servers.length > 0 && (
                  <p className="text-[11px] text-muted-foreground">
                    MCP:{" "}
                    <span className="font-mono text-foreground">
                      {dynamicPreview.resources.mcp_servers.join(", ")}
                    </span>
                  </p>
                )}
              </div>
            </div>
          </div>
        )}

        {dynamicResult && (
          <div className={`rounded-lg border p-3 space-y-1.5 text-xs ${
             dynamicResult.status === "completed" && dynamicResult.metadata.acceptance_passed === true
               ? "border-emerald-500/30 bg-emerald-500/5"
               : dynamicResult.status === "failed"
                 ? "border-red-500/30 bg-red-500/5"
                 : "border-amber-500/30 bg-amber-500/5"
           }`}>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-semibold">Dynamic Execution Result:</span>
              <Badge tone={statusTone(dynamicResult.status)}>
                {dynamicResult.status}
              </Badge>
              <span className="font-mono">
                workflow: {dynamicResult.workflow_id}
              </span>
              {dynamicResult.run_id && (
                <span className="font-mono">run: {dynamicResult.run_id}</span>
              )}
              <span>
                ({dynamicResult.completed_count ?? dynamicResult.completed_nodes.length}/{dynamicResult.task_count ?? dynamicResult.total_steps}{" "}
                tasks completed{dynamicResult.waves ? ` across ${dynamicResult.waves.length} waves` : ""})
              </span>
            </div>
            <p className={dynamicResult.metadata.acceptance_passed === true ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400"}>
              {dynamicResult.metadata.acceptance_passed === true
                ? "Domain acceptance verified by the bound executor."
                : "Graph mechanics completed only; the default digest executor does not claim domain-task acceptance."}
            </p>
          </div>
        )}
      </div>

      {/* Durability status — honest journal health (GET /workflows/system/durability) */}
      <div className="rounded-xl border border-border/60 bg-card p-4 space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <p className="text-sm font-semibold flex-1">Event-journal durability</p>
          {durability && (
            <>
              <Badge tone={durability.store.writable ? "green" : "amber"}>
                store {durability.store.writable ? "writable" : "NOT writable"}
              </Badge>
              <Badge tone={durability.dispatcher.attached ? "green" : "amber"}>
                sink {durability.dispatcher.attached ? "attached" : "detached"}
              </Badge>
              <Badge tone={durability.dispatcher.write_failures > 0 ? "red" : "gray"}>
                {durability.dispatcher.write_failures} write failure(s)
              </Badge>
            </>
          )}
        </div>
        {durabilityError && <ErrorBox message={`Durability status unavailable: ${durabilityError}`} onRetry={load} />}
        {durability && (
          <div className="space-y-1 text-[11px] text-muted-foreground">
            <p className="font-mono break-all">store: {durability.store.store_dir || "—"}</p>
            {durability.store.writable_detail && <p>detail: {durability.store.writable_detail}</p>}
            {durability.dispatcher.last_error && <p className="text-destructive">last sink error: {durability.dispatcher.last_error}</p>}
            <p>
              persisted runs on disk: {durability.store.persisted_run_count}
              {durability.store.persisted_runs.length > 0 && ` (${durability.store.persisted_runs.slice(0, 8).join(", ")}${durability.store.persisted_runs.length > 8 ? ", …" : ""})`}
            </p>
          </div>
        )}
        <div className="flex gap-2 flex-wrap">
          <Btn variant="ghost" disabled={hydrating} onClick={onHydrate} title="Install persisted runs into this process (POST /workflows/hydrate)">
            {hydrating ? "Hydrating…" : "Hydrate persisted runs"}
          </Btn>
        </div>
        {hydrateReport && (
          <div className="rounded-lg border border-border/50 bg-muted/30 p-2.5 space-y-1 text-[11px]">
            <p className="flex items-center gap-2 flex-wrap">
              <span className="font-semibold">Hydration report:</span>
              <Badge tone={statusTone(hydrateReport.status)}>{hydrateReport.status}</Badge>
              <span>
                {hydrateReport.hydrated_runs.length} installed · {hydrateReport.skipped_existing.length} already present · {hydrateReport.corrupt_runs.length} corrupt · {hydrateReport.stale_projections.length} stale
              </span>
            </p>
            {[...hydrateReport.corrupt_runs.map((c) => `${c.run_id}: ${c.error}`), ...hydrateReport.stale_projections.map((s) => `${s.run_id}: ${s.detail}`), ...hydrateReport.disclosures].map((line, i) => (
              <p key={i} className="text-amber-600 dark:text-amber-400 break-all">{line}</p>
            ))}
          </div>
        )}
      </div>

      {/* Register a workflow definition */}
      <div className="flex gap-2 flex-wrap">
        <Btn onClick={() => setShowCreate((v) => !v)}>
          <Plus className="size-3.5" /> Register workflow
        </Btn>
      </div>
      {showCreate && (
        <div className="rounded-2xl border border-primary/30 bg-card p-4 space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Id" hint="Unique id, e.g. nightly-report.">
              <input value={draft.id} onChange={(e) => setDraft({ ...draft, id: e.target.value })} placeholder="nightly-report" className={inputCls} />
            </Field>
            <Field label="Name">
              <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Nightly report" className={inputCls} />
            </Field>
          </div>
          <Field label="Description">
            <input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} placeholder="What this workflow does" className={inputCls} />
          </Field>
          <Field label="Graph (JSON, required)" hint='WorkflowGraph shape: {"version":1,"nodes":{...},"edges":[...]}. The server requires at least one node and validates every reference.'>
            <textarea value={draft.graphJson} onChange={(e) => setDraft({ ...draft, graphJson: e.target.value })} rows={4} placeholder='{"nodes": {"n1": {"id": "n1", "type": "tool"}}, "edges": []}' className={`${inputCls} font-mono`} />
          </Field>
          <div className="flex gap-2">
            <Btn onClick={onRegister}>Save workflow</Btn>
            <Btn variant="ghost" onClick={() => setShowCreate(false)}>Cancel</Btn>
          </div>
        </div>
      )}

      {/* Workflow definitions list */}
      {loading ? (
        <SkeletonList rows={3} />
      ) : error ? (
        <ErrorBox message={error} onRetry={load} />
      ) : workflows.length === 0 ? (
        <EmptyState
          title="No workflows registered"
          hint="The dynamic-workflow engine has no definitions yet. Register one above, or run a turn below — the meta-planner expresses prompts as workflows when it can."
          action={<Btn onClick={() => setShowCreate(true)}><Plus className="size-3.5" /> Register workflow</Btn>}
        />
      ) : (
        <div className="space-y-2">
          {workflows.map((w) => (
            <div key={w.id} className="rounded-xl border border-border/60 bg-card p-4">
              <div className="flex items-center gap-2 flex-wrap">
                <p className="text-sm font-semibold flex-1 min-w-40">{w.name}</p>
                <Badge tone="blue">v{w.version}</Badge>
                <Badge tone="gray">{w.node_count} node(s)</Badge>
              </div>
              <p className="text-[11px] font-mono text-muted-foreground mt-0.5">{w.id}</p>
              {w.description && <p className="text-[11px] text-muted-foreground mt-1">{w.description}</p>}
              <div className="flex gap-2 mt-2.5 flex-wrap">
                <Btn onClick={() => onStartRun(w.id)}><Zap className="size-3.5" /> Start run</Btn>
                <Btn variant="ghost" onClick={() => onLoadPlans(w.id)}>Version history</Btn>
                <Btn variant="ghost" onClick={() => onRecordPlan(w.id)}>Record revision</Btn>
                <Btn variant="ghost" onClick={() => onInspect(w.id)}><Search className="size-3.5" /> Inspect</Btn>
              </div>
              {plans[w.id] && (
                <p className="text-[11px] text-muted-foreground mt-2">
                  revisions: {plans[w.id].versions.length > 0 ? plans[w.id].versions.join(", ") : "none"} · latest source: {plans[w.id].latest_source ?? "—"}
                </p>
              )}
              {definition && definition.id === w.id && (
                <div className="mt-2 rounded-lg border border-border/50 bg-muted/30 p-2.5 space-y-1">
                  <p className="text-[11px] font-semibold">Graph v{definition.graph.version} — {Object.keys(definition.graph.nodes).length} node(s), {definition.graph.edges.length} edge(s)</p>
                  {Object.values(definition.graph.nodes).map((n) => (
                    <p key={n.id} className="text-[11px] font-mono text-muted-foreground">
                      {n.id} · {n.type}{n.requires_approval ? " · requires approval" : ""}{n.status ? ` · ${n.status}` : ""}
                    </p>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Turn runner — POST /workflows/turns (kernel TurnOutcome shown verbatim) */}
      <div className="rounded-xl border border-border/60 bg-card p-4 space-y-3">
        <p className="text-sm font-semibold">Run a turn</p>
        <Field label="Prompt" hint="One orchestrated turn — the meta-planner expresses it as a workflow when it can; otherwise the server answers 400 with its exact reason.">
          <textarea value={turn.prompt} onChange={(e) => setTurn({ ...turn, prompt: e.target.value })} rows={2} placeholder="Plan and execute a nightly data refresh…" className={inputCls} />
        </Field>
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
          <Field label="Mode">
            <select value={turn.mode} onChange={(e) => setTurn({ ...turn, mode: e.target.value })} className={inputCls}>
              <option value="normal">normal</option>
              <option value="bot">bot</option>
            </select>
          </Field>
          <Field label="Paradigm" hint="Unknown paradigms are refused by the server with its reason.">
            <input value={turn.paradigm} onChange={(e) => setTurn({ ...turn, paradigm: e.target.value })} className={`${inputCls} font-mono`} />
          </Field>
          <Field label="Max waves" hint="1–500.">
            <input type="number" min={1} max={500} value={turn.maxWaves} onChange={(e) => setTurn({ ...turn, maxWaves: Number(e.target.value) })} className={inputCls} />
          </Field>
          <Field label="Hand off to" hint="Optional — leave empty to keep the run.">
            <input value={turn.handoffTo} onChange={(e) => setTurn({ ...turn, handoffTo: e.target.value })} placeholder="bot name" className={inputCls} />
          </Field>
        </div>
        <Btn onClick={onTurn}><Zap className="size-3.5" /> Run turn</Btn>
        {turnOutcome && (
          <div className="rounded-lg border border-border/50 bg-muted/30 p-2.5 space-y-1 text-[11px]">
            <p className="flex items-center gap-2 flex-wrap">
              <span className="font-semibold">Turn outcome</span>
              <Badge tone={statusTone(turnOutcome.status)}>{turnOutcome.status}</Badge>
              <span className="font-mono">{turnOutcome.run_id}</span>
              <span>· {turnOutcome.workflow_id} · {turnOutcome.mode}/{turnOutcome.paradigm} · {turnOutcome.waves} wave(s)</span>
            </p>
            {turnOutcome.failed_nodes.length > 0 && (
              <p className="text-destructive">failed nodes: {turnOutcome.failed_nodes.join(", ")}</p>
            )}
            {turnOutcome.reason && <p className="text-amber-600 dark:text-amber-400">reason: {turnOutcome.reason}</p>}
            {turnOutcome.handoff && <p className="font-mono break-all">handoff: {JSON.stringify(turnOutcome.handoff)}</p>}
          </div>
        )}
      </div>

      {/* Runs — there is no list-runs endpoint, so only runs started this
          session or loaded via durability/hydrate can be shown. */}
      {runIds.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm font-semibold">Runs (this session + hydrated)</p>
          <div className="flex gap-2 flex-wrap">
            {runIds.map((id) => (
              <button
                key={id}
                type="button"
                onClick={() => loadRun(id)}
                className={`px-2.5 py-1.5 rounded-lg text-[11px] font-mono border ${activeRunId === id ? "border-primary/50 bg-primary/10" : "border-border/60 text-muted-foreground hover:text-foreground"}`}
                title="Load this run"
              >
                {id}
                {runs[id] && <span className="ml-1.5 font-sans">· {runs[id].status}</span>}
              </button>
            ))}
          </div>
          {runIds.map((id) => runErrors[id] && (
            <ErrorBox key={id} message={`Run ${id}: ${runErrors[id]}`} onRetry={() => loadRun(id)} />
          ))}

          {activeRun && (
            <div className="rounded-xl border border-primary/30 bg-card p-4 space-y-3">
              <div className="flex items-center gap-2 flex-wrap">
                <p className="text-sm font-semibold font-mono flex-1 min-w-40">{activeRun.run_id}</p>
                <Badge tone={statusTone(activeRun.status)}>{activeRun.status}</Badge>
                <Badge tone="gray">graph v{activeRun.graph_version}</Badge>
              </div>
              <p className="text-[11px] text-muted-foreground font-mono">
                {activeRun.workflow_id} · updated {fmtIso(activeRun.updated_at)}
              </p>
              {activeRun.waiting_reason && (
                <p className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-2.5 py-1.5 text-[11px] text-amber-700 dark:text-amber-300">
                  {activeRun.waiting_reason}
                </p>
              )}
              <div className="flex gap-2 flex-wrap text-[11px] text-muted-foreground">
                <span>{activeRun.completed_nodes.length} completed</span>
                <span>{activeRun.failed_nodes.length} failed</span>
                <span>{activeRun.waiting_nodes.length} waiting</span>
                <span>{Object.keys(activeRun.node_states).length} nodes total</span>
              </div>
              <div className="flex gap-2 flex-wrap">
                <Btn variant="ghost" onClick={onStep}><Zap className="size-3.5" /> Step one wave</Btn>
                <Btn variant="ghost" onClick={() => onReplan(activeRun.run_id)} title="Synthesize failure-repair patch and advance (POST /workflows/runs/{id}/replan)">
                  <Wrench className="size-3.5" /> Auto-Replan
                </Btn>
                <Btn variant="ghost" onClick={() => onCompensate(activeRun.run_id)} title="Request verified saga compensation (POST /workflows/runs/{id}/compensate)">
                  <RotateCcw className="size-3.5" /> Request compensation
                </Btn>
                <Btn variant="ghost" onClick={() => loadRun(activeRun.run_id)}><RefreshCw className="size-3.5" /> Reload</Btn>
                <Btn variant="ghost" onClick={onReplay}>Replay</Btn>
                <Btn variant="ghost" onClick={onEvents}>Events</Btn>
                <Btn variant="ghost" onClick={onDurableEvents}><ScrollText className="size-3.5" /> Durable log</Btn>
                <Btn variant="ghost" onClick={onProject}>Write projection</Btn>
              </div>

              {/* Human approvals — POST /workflows/runs/{id}/approvals/{node} */}
              {activeRun.waiting_nodes.length > 0 && activeRun.status === "waiting_approval" && (
                <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-2.5 space-y-2">
                  <p className="text-[11px] font-semibold text-amber-700 dark:text-amber-300">
                    Waiting for approval: {activeRun.waiting_nodes.join(", ")}
                  </p>
                  <input
                    value={approvalFeedback}
                    onChange={(e) => setApprovalFeedback(e.target.value)}
                    placeholder="Feedback for the approver decision (optional)"
                    className={inputCls}
                  />
                  <div className="flex gap-2 flex-wrap">
                    {activeRun.waiting_nodes.map((nodeId) => (
                      <span key={nodeId} className="flex gap-1.5">
                        <Btn variant="ghost" onClick={() => onApproval(nodeId, true)} title={`Approve ${nodeId}`}>
                          <Check className="size-3.5" /> Approve {nodeId}
                        </Btn>
                        <Btn variant="danger" onClick={() => onApproval(nodeId, false)} title={`Deny ${nodeId}`}>
                          <X className="size-3.5" /> Deny
                        </Btn>
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Per-node states (server truth) */}
              <div className="flex gap-1.5 flex-wrap">
                {Object.entries(activeRun.node_states).map(([nodeId, state]) => (
                  <span key={nodeId} className="inline-flex items-center gap-1 rounded-lg border border-border/50 px-2 py-0.5 text-[10px] font-mono">
                    {nodeId}
                    <Badge tone={statusTone(state)}>{state}</Badge>
                  </span>
                ))}
              </div>

              {/* Run history — status transitions journaled by the engine */}
              {activeRun.history.length > 0 && (
                <div className="space-y-0.5">
                  <p className="text-[11px] font-semibold">History</p>
                  {activeRun.history.map((h, i) => (
                    <p key={i} className="text-[11px] font-mono text-muted-foreground break-all">
                      {String(h.from ?? "?")} → {String(h.to ?? "?")}
                      {typeof h.reason === "string" ? ` — ${h.reason}` : ""}
                      {typeof h.timestamp === "string" ? ` · ${fmtIso(h.timestamp)}` : ""}
                    </p>
                  ))}
                </div>
              )}

              {/* Patch — JSON operations, server validates and refuses bad patches */}
              <details className="rounded-lg border border-border/50 p-2.5">
                <summary className="text-[11px] font-semibold cursor-pointer">Patch run graph (advanced)</summary>
                <div className="mt-2 space-y-2">
                  <p className="text-[11px] text-muted-foreground">
                    Provide {"{"} "reason", "operations" [ …] {"}"}. run id and base graph version are filled from the live run; the server rejects invalid patches with its exact reason.
                  </p>
                  <textarea
                    value={patchText}
                    onChange={(e) => setPatchText(e.target.value)}
                    rows={4}
                    placeholder='{"reason": "skip flaky node", "operations": [{"op": "skip_node", "args": {"node_id": "n1"}}]}'
                    className={`${inputCls} font-mono`}
                  />
                  <Btn variant="ghost" onClick={onPatch}>Apply patch</Btn>
                </div>
              </details>

              {/* Event log (in-memory) */}
              {events && (
                <div className="space-y-1">
                  <p className="text-[11px] font-semibold">Events: {events.count} recorded</p>
                  {[...events.events].reverse().slice(0, 50).map((e) => (
                    <p key={e.event_id} className="text-[11px] font-mono text-muted-foreground break-all" title={JSON.stringify(e.payload)}>
                      {fmtIso(e.timestamp)} · {e.event_type}
                    </p>
                  ))}
                  {events.count > 50 && <p className="text-[11px] text-muted-foreground">showing newest 50 of {events.count}</p>}
                </div>
              )}

              {/* Durable (JSONL) event log + corrupt-tail disclosures */}
              {durableEvents && (
                <div className="space-y-1">
                  <p className="text-[11px] font-semibold">Durable log: {durableEvents.count} record(s)</p>
                  {durableEvents.corrupt_tail.length > 0 &&
                    durableEvents.corrupt_tail.map((c, i) => (
                      <p key={i} className="text-[11px] text-destructive break-all">
                        corrupt tail{typeof c.line_number === "number" ? ` line ${c.line_number}` : ""}: {String(c.error ?? JSON.stringify(c))}
                      </p>
                    ))}
                  {[...durableEvents.events].reverse().slice(0, 50).map((e, i) => (
                    <p key={i} className="text-[11px] font-mono text-muted-foreground break-all" title={JSON.stringify(e)}>
                      {JSON.stringify(e).slice(0, 160)}
                    </p>
                  ))}
                </div>
              )}

              {/* Replay verification */}
              {replay && (
                <div className="rounded-lg border border-border/50 bg-muted/30 p-2.5 space-y-1 text-[11px]">
                  <p className="flex items-center gap-2 flex-wrap">
                    <span className="font-semibold">Replay check</span>
                    <Badge tone={replay.matches_live ? "green" : "red"}>{replay.matches_live ? "matches live run" : "MISMATCH"}</Badge>
                    <span>
                      {replay.events_folded} event(s) folded · {replay.events_emitted_during_replay} emitted during replay (must be 0)
                    </span>
                  </p>
                  <p className="text-muted-foreground">covered: {replay.covered_fields.join(", ")}</p>
                  {replay.mismatches.map((m, i) => (
                    <p key={i} className="text-destructive break-all">
                      {m.field}: live={JSON.stringify(m.live)} vs replayed={JSON.stringify(m.replayed)}
                    </p>
                  ))}
                </div>
              )}

              {/* Projection report */}
              {project && (
                <p className="text-[11px] text-muted-foreground">
                  Projection: status {project.status} · last seq {project.last_seq} · {project.event_count} event(s) · graphs [{project.graph_versions.join(", ")}] · definition recorded: {project.definition_recorded ? "yes" : "no"}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ══ Panel 2: Goals & Missions ══════════════════════════════════════ */

function GoalsPanel(props: { refreshKey: number; onNotice: (m: string) => void }) {
  const [contracts, setContracts] = useState<GoalContract[]>([]);
  const [contractsLoading, setContractsLoading] = useState(true);
  const [contractsError, setContractsError] = useState<string | null>(null);
  const [missions, setMissions] = useState<Mission[]>([]);
  const [missionsLoading, setMissionsLoading] = useState(true);
  const [missionsError, setMissionsError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showCreateContract, setShowCreateContract] = useState(false);
  const [newObjective, setNewObjective] = useState("");
  const [plansByContract, setPlansByContract] = useState<Record<string, PlanVersion[]>>({});
  const [attemptsByPlan, setAttemptsByPlan] = useState<Record<string, TaskAttempt[]>>({});
  const [planDrafts, setPlanDrafts] = useState<Record<string, string>>({});
  const [intentDrafts, setIntentDrafts] = useState<Record<string, string>>({});
  const [attemptTarget, setAttemptTarget] = useState<Record<string, AttemptStatus>>({});
  const [missionTarget, setMissionTarget] = useState<Record<string, string>>({});
  const [threadDrafts, setThreadDrafts] = useState<Record<string, string>>({});
  const [audit, setAudit] = useState({ goal: "", subtasks: "" });
  const [report, setReport] = useState<IntegrityReport | null>(null);
  const [auditing, setAuditing] = useState(false);

  const loadContracts = async () => {
    setContractsLoading(true);
    setContractsError(null);
    try {
      setContracts(await listGoalContracts());
    } catch (e) {
      setContractsError(errMsg(e));
    } finally {
      setContractsLoading(false);
    }
  };

  const loadMissions = async () => {
    setMissionsLoading(true);
    setMissionsError(null);
    try {
      setMissions(await listMissions());
    } catch (e) {
      setMissionsError(errMsg(e));
    } finally {
      setMissionsLoading(false);
    }
  };

  useEffect(() => {
    loadContracts();
    loadMissions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.refreshKey]);

  const act = async (fn: () => Promise<void>) => {
    setActionError(null);
    try {
      await fn();
    } catch (e) {
      setActionError(errMsg(e));
    }
  };

  const onAddContract = () =>
    act(async () => {
      if (!newObjective.trim()) throw new Error("Write the objective first.");
      const contract = await createGoalContract(newObjective.trim());
      props.onNotice(`Contract created — "${contract.objective}" (${contract.status}).`);
      setNewObjective("");
      setShowCreateContract(false);
      await loadContracts();
    });

  const onSavePlan = (contractId: string) =>
    act(async () => {
      const raw = planDrafts[contractId]?.trim();
      if (!raw) throw new Error('Plan content is required, e.g. {"steps": ["…"]}.');
      const parsed = parseJson(raw, "Plan content") as Record<string, unknown>;
      const plan = await createGoalPlan(contractId, parsed);
      setPlansByContract((prev) => ({ ...prev, [contractId]: [...(prev[contractId] ?? []), plan] }));
      setPlanDrafts((prev) => ({ ...prev, [contractId]: "" }));
      props.onNotice(`Plan v${plan.version} created (${plan.status}).`);
    });

  const onApprovePlan = (contractId: string, version: number) =>
    act(async () => {
      const plan = await approveGoalPlan(contractId, version);
      setPlansByContract((prev) => ({
        ...prev,
        [contractId]: (prev[contractId] ?? []).map((p) => (p.id === plan.id ? plan : p)),
      }));
      props.onNotice(`Plan v${plan.version} is now "${plan.status}".`);
    });

  const onStartAttempt = (contractId: string, planId: string) =>
    act(async () => {
      const intent = (intentDrafts[planId] ?? "").trim();
      if (!intent) throw new Error("Describe the attempt's intent first.");
      const attempt = await createGoalAttempt(contractId, planId, intent);
      setAttemptsByPlan((prev) => ({ ...prev, [planId]: [...(prev[planId] ?? []), attempt] }));
      setIntentDrafts((prev) => ({ ...prev, [planId]: "" }));
      props.onNotice(`Attempt ${attempt.id} created (${attempt.status}).`);
    });

  const onAttemptTransition = (contractId: string, attempt: TaskAttempt) =>
    act(async () => {
      const to = attemptTarget[attempt.id] ?? "running";
      const updated = await transitionGoalAttempt(contractId, attempt.id, to);
      setAttemptsByPlan((prev) => ({
        ...prev,
        [updated.plan_id]: (prev[updated.plan_id] ?? []).map((a) => (a.id === updated.id ? updated : a)),
      }));
      props.onNotice(`Attempt ${updated.id} → "${updated.status}".`);
    });

  const onAudit = () =>
    act(async () => {
      const goal = audit.goal.trim();
      if (!goal) throw new Error("Write the mission goal to audit against.");
      const subtasks = audit.subtasks.split("\n").map((s) => s.trim()).filter(Boolean);
      setAuditing(true);
      try {
        setReport(await auditGoalIntegrity(goal, subtasks));
      } finally {
        setAuditing(false);
      }
    });

  const onAddMission = () =>
    act(async () => {
      if (!newObjective.trim()) throw new Error("Write the mission objective first.");
      const mission = await createMission(newObjective.trim());
      props.onNotice(`Mission ${mission.mission_id} created (${mission.status}).`);
      await loadMissions();
    });

  const onMissionTransition = (mission: Mission) =>
    act(async () => {
      const to = missionTarget[mission.mission_id] ?? "active";
      const updated = await transitionMission(mission.mission_id, to);
      props.onNotice(`Mission ${updated.mission_id} → "${updated.status}".`);
      await loadMissions();
    });

  const onAttachThread = (missionId: string) =>
    act(async () => {
      const threadId = (threadDrafts[missionId] ?? "").trim();
      if (!threadId) throw new Error("Paste the thread id to attach.");
      const updated = await attachMissionThread(missionId, threadId);
      setThreadDrafts((prev) => ({ ...prev, [missionId]: "" }));
      props.onNotice(`Thread attached — mission now has ${updated.thread_ids.length} thread(s).`);
      await loadMissions();
    });

  return (
    <div className="space-y-4">
      {actionError && <ErrorBox message={actionError} onRetry={() => setActionError(null)} />}

      {/* Goal contracts — /goals/contracts */}
      <div className="rounded-xl border border-border/60 bg-card p-4 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <p className="text-sm font-semibold flex-1">Goal contracts</p>
          <Btn onClick={() => setShowCreateContract((v) => !v)}>
            <Plus className="size-3.5" /> New contract
          </Btn>
        </div>
        {showCreateContract && (
          <div className="flex gap-2 items-end flex-wrap">
            <div className="flex-1 min-w-56">
              <Field label="Objective" hint="The goal this contract commits to.">
                <input value={newObjective} onChange={(e) => setNewObjective(e.target.value)} placeholder="Ship the weekly digest feature" className={inputCls} />
              </Field>
            </div>
            <Btn onClick={onAddContract}>Create</Btn>
          </div>
        )}
        {contractsLoading ? (
          <SkeletonList rows={2} />
        ) : contractsError ? (
          <ErrorBox message={contractsError} onRetry={loadContracts} />
        ) : contracts.length === 0 ? (
          <EmptyState title="No goal contracts yet" hint="A contract pins an objective you can plan against and track attempts on." action={<Btn onClick={() => setShowCreateContract(true)}><Plus className="size-3.5" /> New contract</Btn>} />
        ) : (
          <div className="space-y-2">
            {contracts.map((c) => (
              <div key={c.id} className="rounded-lg border border-border/50 p-3 space-y-2">
                <div className="flex items-center gap-2 flex-wrap">
                  <p className="text-xs font-semibold flex-1 min-w-40">{c.objective}</p>
                  <Badge tone={statusTone(c.status)}>{c.status}</Badge>
                </div>
                <p className="text-[10px] font-mono text-muted-foreground">
                  {c.id} · created {fmtEpoch(c.created_at)}
                </p>

                {/* Plans created this session (API has no list-plans route) */}
                {(plansByContract[c.id] ?? []).map((p) => (
                  <div key={p.id} className="rounded-md border border-border/40 bg-muted/20 p-2 space-y-1.5">
                    <p className="text-[11px] flex items-center gap-2 flex-wrap">
                      <span className="font-mono font-semibold">plan v{p.version}</span>
                      <Badge tone={statusTone(p.status)}>{p.status}</Badge>
                    </p>
                    <p className="text-[10px] font-mono text-muted-foreground break-all" title={JSON.stringify(p.content)}>
                      {JSON.stringify(p.content).slice(0, 140)}
                    </p>
                    <div className="flex gap-2 flex-wrap items-end">
                      {p.status === "draft" && (
                        <Btn variant="ghost" onClick={() => onApprovePlan(c.id, p.version)}>
                          <Check className="size-3.5" /> Approve v{p.version}
                        </Btn>
                      )}
                      <div className="flex-1 min-w-44">
                        <Field label="New attempt intent">
                          <input
                            value={intentDrafts[p.id] ?? ""}
                            onChange={(e) => setIntentDrafts((prev) => ({ ...prev, [p.id]: e.target.value }))}
                            placeholder="What this attempt will do"
                            className={inputCls}
                          />
                        </Field>
                      </div>
                      <Btn variant="ghost" onClick={() => onStartAttempt(c.id, p.id)}>Start attempt</Btn>
                    </div>
                    {(attemptsByPlan[p.id] ?? []).map((a) => (
                      <div key={a.id} className="flex items-center gap-2 flex-wrap text-[11px]">
                        <span className="font-mono">{a.id}</span>
                        <Badge tone={statusTone(a.status)}>{a.status}</Badge>
                        <span className="text-muted-foreground flex-1 min-w-24 truncate" title={a.intent}>{a.intent}</span>
                        <select
                          value={attemptTarget[a.id] ?? a.status}
                          onChange={(e) => setAttemptTarget((prev) => ({ ...prev, [a.id]: e.target.value as AttemptStatus }))}
                          className={`${inputCls} w-auto py-1`}
                        >
                          {ATTEMPT_STATUSES.map((s) => (
                            <option key={s} value={s}>{s}</option>
                          ))}
                        </select>
                        <Btn variant="ghost" onClick={() => onAttemptTransition(c.id, a)}>Apply</Btn>
                      </div>
                    ))}
                  </div>
                ))}

                {/* Create plan (content JSON) */}
                <div className="space-y-1.5">
                  <Field label="Plan content (JSON)" hint='The server stores exactly this object, e.g. {"steps": […]}.'>
                    <textarea
                      value={planDrafts[c.id] ?? ""}
                      onChange={(e) => setPlanDrafts((prev) => ({ ...prev, [c.id]: e.target.value }))}
                      rows={2}
                      placeholder='{"steps": ["design", "build", "verify"]}'
                      className={`${inputCls} font-mono`}
                    />
                  </Field>
                  <Btn variant="ghost" onClick={() => onSavePlan(c.id)}>Save plan version</Btn>
                </div>
              </div>
            ))}
            <p className="text-[11px] text-muted-foreground">
              Note: the API exposes no “list plans/attempts” route (goal_contracts.py has no GET for them), so plans and attempts appear here only after you create them in this session — ids come from the create responses.
            </p>
          </div>
        )}
      </div>

      {/* Goal integrity audit — POST /goal-integrity/audit */}
      <div className="rounded-xl border border-border/60 bg-card p-4 space-y-3">
        <p className="text-sm font-semibold">Goal-integrity audit</p>
        <p className="text-[11px] text-muted-foreground">
          Check proposed subtasks against a mission for semantic drift, scope creep and overengineering — scored by the server (0.0 aligned → 1.0 diverged).
        </p>
        <Field label="Mission goal">
          <textarea value={audit.goal} onChange={(e) => setAudit({ ...audit, goal: e.target.value })} rows={2} placeholder="Ship a weekly customer digest email" className={inputCls} />
        </Field>
        <Field label="Subtasks" hint="One per line.">
          <textarea value={audit.subtasks} onChange={(e) => setAudit({ ...audit, subtasks: e.target.value })} rows={3} placeholder={"Build the email template\nAdd a kafka event stream\nWrite tests"} className={inputCls} />
        </Field>
        <Btn onClick={onAudit} disabled={auditing}>{auditing ? "Auditing…" : "Audit subtasks"}</Btn>
        {report && (
          <div className="rounded-lg border border-border/50 bg-muted/30 p-2.5 space-y-1.5 text-[11px]">
            <p className="flex items-center gap-2 flex-wrap">
              <span className="font-semibold">Report</span>
              <Badge tone={report.is_aligned ? "green" : "red"}>{report.is_aligned ? "aligned" : "MISALIGNED"}</Badge>
              <Badge tone={report.scope_creep_detected ? "amber" : "gray"}>{report.scope_creep_detected ? "scope creep detected" : "no scope creep"}</Badge>
              <Badge tone={report.overengineering_detected ? "amber" : "gray"}>{report.overengineering_detected ? "overengineering detected" : "no overengineering"}</Badge>
              <span>drift score {report.drift_score} · {report.audited_subtasks_count} subtask(s)</span>
            </p>
            {report.findings.map((f, i) => (
              <p key={`f${i}`} className="text-muted-foreground">• {f}</p>
            ))}
            {report.recommendations.map((r, i) => (
              <p key={`r${i}`} className="text-amber-600 dark:text-amber-400">→ {r}</p>
            ))}
            {report.flagged_tasks.map((t, i) => (
              <p key={`t${i}`} className="text-destructive break-all">
                {t.task_id}: {t.reason} — “{t.description}”
              </p>
            ))}
          </div>
        )}
      </div>

      {/* Missions — /missions */}
      <div className="rounded-xl border border-border/60 bg-card p-4 space-y-3">
        <p className="text-sm font-semibold">Missions</p>
        <div className="flex gap-2 items-end flex-wrap">
          <div className="flex-1 min-w-56">
            <Field label="New mission objective" hint="3–5000 chars (server limit).">
              <input
                value={newObjective}
                onChange={(e) => setNewObjective(e.target.value)}
                placeholder="Migrate the billing service to the new API"
                className={inputCls}
              />
            </Field>
          </div>
          <Btn onClick={onAddMission}><Plus className="size-3.5" /> Create mission</Btn>
        </div>
        {missionsLoading ? (
          <SkeletonList rows={2} />
        ) : missionsError ? (
          <ErrorBox message={missionsError} onRetry={loadMissions} />
        ) : missions.length === 0 ? (
          <EmptyState title="No missions yet" hint="A mission is a long-lived objective above threads — it survives restarts and links the threads working toward it." />
        ) : (
          <div className="space-y-2">
            {missions.map((m) => (
              <div key={m.mission_id} className="rounded-lg border border-border/50 p-3 space-y-2">
                <div className="flex items-center gap-2 flex-wrap">
                  <p className="text-xs font-semibold flex-1 min-w-40">{m.objective}</p>
                  <Badge tone={statusTone(m.status)}>{m.status}</Badge>
                </div>
                <p className="text-[10px] font-mono text-muted-foreground">
                  {m.mission_id} · {m.thread_ids.length} thread(s) · {m.artifacts.length} artifact(s) · created {fmtEpoch(m.created_at)}
                </p>
                {m.thread_ids.length > 0 && (
                  <p className="text-[10px] font-mono text-muted-foreground break-all">threads: {m.thread_ids.join(", ")}</p>
                )}
                <div className="flex gap-2 flex-wrap items-end">
                  <select
                    value={missionTarget[m.mission_id] ?? "active"}
                    onChange={(e) => setMissionTarget((prev) => ({ ...prev, [m.mission_id]: e.target.value }))}
                    className={`${inputCls} w-auto py-1`}
                    title="Values accepted by the transition route; the store rejects illegal transitions"
                  >
                    {MISSION_TRANSITION_TARGETS.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                  <Btn variant="ghost" onClick={() => onMissionTransition(m)}>Transition</Btn>
                  <div className="flex-1 min-w-44">
                    <Field label="Attach thread id">
                      <input
                        value={threadDrafts[m.mission_id] ?? ""}
                        onChange={(e) => setThreadDrafts((prev) => ({ ...prev, [m.mission_id]: e.target.value }))}
                        placeholder="thr-…"
                        className={inputCls}
                      />
                    </Field>
                  </div>
                  <Btn variant="ghost" onClick={() => onAttachThread(m.mission_id)}>Attach</Btn>
                </div>
              </div>
            ))}
            <p className="text-[11px] text-muted-foreground">
              The server answers 404 when a transition is illegal for the mission’s current status (missions.py refuses outside its documented state machine) — nothing is applied client-side.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ══ Panel 3: Checkpoints & Jobs ════════════════════════════════════ */

function OpsPanel(props: { refreshKey: number; onNotice: (m: string) => void }) {
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [cpLoading, setCpLoading] = useState(true);
  const [cpError, setCpError] = useState<string | null>(null);
  const [diffs, setDiffs] = useState<Record<string, CheckpointDiff>>({});
  const [cpDraft, setCpDraft] = useState({ label: "", rootPath: "." });
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [jobsError, setJobsError] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState("");
  const [logs, setLogs] = useState<Record<string, JobLogs>>({});
  const [jobDraft, setJobDraft] = useState({ command: "", title: "", priority: "normal", timeout: 300 });
  const [actionError, setActionError] = useState<string | null>(null);

  const loadCheckpoints = async () => {
    setCpLoading(true);
    setCpError(null);
    try {
      setCheckpoints(await listCheckpoints());
    } catch (e) {
      setCpError(errMsg(e));
    } finally {
      setCpLoading(false);
    }
  };

  const loadJobs = async () => {
    setJobsLoading(true);
    setJobsError(null);
    try {
      setJobs(await listJobs(jobStatus ? { status: jobStatus } : {}));
    } catch (e) {
      setJobsError(errMsg(e));
    } finally {
      setJobsLoading(false);
    }
  };

  useEffect(() => {
    loadCheckpoints();
    loadJobs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.refreshKey, jobStatus]);

  const act = async (fn: () => Promise<void>) => {
    setActionError(null);
    try {
      await fn();
    } catch (e) {
      setActionError(errMsg(e));
    }
  };

  const checkpointRoot = (checkpointId: string) =>
    checkpoints.find((checkpoint) => checkpoint.checkpoint_id === checkpointId)?.root_path || ".";

  const onCreateCheckpoint = () =>
    act(async () => {
      if (!cpDraft.label.trim()) throw new Error("Give the checkpoint a label.");
      const created = await createCheckpoint(cpDraft.label.trim(), cpDraft.rootPath.trim() || ".");
      props.onNotice(`Checkpoint ${created.checkpoint_id} created — ${created.files_count} file(s).`);
      setCpDraft({ label: "", rootPath: cpDraft.rootPath });
      await loadCheckpoints();
    });

  const onDiff = (checkpointId: string) =>
    act(async () => {
      const diff = await getCheckpointDiff(checkpointId, checkpointRoot(checkpointId));
      setDiffs((prev) => ({ ...prev, [checkpointId]: diff }));
    });

  const onRollback = (checkpointId: string) =>
    act(async () => {
      if (!window.confirm(`Roll the workspace back to ${checkpointId}? Current files covered by that checkpoint will be overwritten.`)) return;
      const result = await rollbackCheckpoint(checkpointId, checkpointRoot(checkpointId));
      props.onNotice(`Rolled back to ${result.checkpoint_id} — ${result.restored_files_count} file(s) restored.`);
      await loadCheckpoints();
    });

  const onSubmitJob = () =>
    act(async () => {
      if (!jobDraft.command.trim()) throw new Error("Enter the command to run.");
      const result = await submitJob({
        command: jobDraft.command.trim(),
        title: jobDraft.title.trim() || undefined,
        priority: jobDraft.priority,
        timeout_seconds: Number(jobDraft.timeout) || 300,
      });
      props.onNotice(`Job ${result.job_id} queued as "${result.title}" (${result.priority}).`);
      await loadJobs();
    });

  const onLogs = (jobId: string) =>
    act(async () => {
      const jobLogs = await getJobLogs(jobId);
      setLogs((prev) => ({ ...prev, [jobId]: jobLogs }));
    });

  const onCancelJob = (jobId: string) =>
    act(async () => {
      const result = await cancelJob(jobId);
      props.onNotice(`Job ${result.job_id} → ${result.status}.`);
      await loadJobs();
    });

  return (
    <div className="space-y-4">
      {actionError && <ErrorBox message={actionError} onRetry={() => setActionError(null)} />}

      {/* Checkpoints — /checkpoints */}
      <div className="rounded-xl border border-border/60 bg-card p-4 space-y-3">
        <p className="text-sm font-semibold">Checkpoints (git-shadow snapshots)</p>
        <div className="flex gap-2 items-end flex-wrap">
          <div className="flex-1 min-w-44">
            <Field label="Label">
              <input value={cpDraft.label} onChange={(e) => setCpDraft({ ...cpDraft, label: e.target.value })} placeholder="Before billing refactor" className={inputCls} />
            </Field>
          </div>
          <div className="min-w-32">
            <Field label="Root path" hint="Server-side working directory.">
              <input value={cpDraft.rootPath} onChange={(e) => setCpDraft({ ...cpDraft, rootPath: e.target.value })} className={`${inputCls} font-mono`} />
            </Field>
          </div>
          <Btn onClick={onCreateCheckpoint}><Plus className="size-3.5" /> Create checkpoint</Btn>
        </div>
        {cpLoading ? (
          <SkeletonList rows={2} />
        ) : cpError ? (
          <ErrorBox message={cpError} onRetry={loadCheckpoints} />
        ) : checkpoints.length === 0 ? (
          <EmptyState title="No checkpoints yet" hint="A checkpoint snapshots workspace files so you can diff or roll back in one click. Checkpoints live in this backend process only — they are not persisted across restarts." />
        ) : (
          <div className="space-y-2">
            {checkpoints.map((c) => (
              <div key={c.checkpoint_id} className="rounded-lg border border-border/50 p-3 space-y-1.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <p className="text-xs font-semibold flex-1 min-w-40">{c.label}</p>
                  <Badge tone="gray">{c.files_count} file(s)</Badge>
                  <Badge tone={c.test_passed === true ? "green" : c.test_passed === false ? "red" : "gray"}>
                    {c.test_passed === true ? "tests passed" : c.test_passed === false ? `tests failed (${c.failure_count})` : "tests not run"}
                  </Badge>
                </div>
                <p className="text-[10px] font-mono text-muted-foreground break-all">
                  {c.checkpoint_id} · {fmtEpoch(c.created_at)}{c.git_ref ? ` · ${c.git_ref}` : " · no git ref"}
                </p>
                <div className="flex gap-2 flex-wrap">
                  <Btn variant="ghost" onClick={() => onDiff(c.checkpoint_id)}>
                    <GitCompare className="size-3.5" /> Diff
                  </Btn>
                  <Btn variant="danger" onClick={() => onRollback(c.checkpoint_id)} title="Overwrites current files covered by this checkpoint">
                    <RotateCcw className="size-3.5" /> Roll back
                  </Btn>
                </div>
                {diffs[c.checkpoint_id] && (
                  <div className="space-y-1">
                    <p className="text-[11px] font-semibold">{diffs[c.checkpoint_id].diff_count} file(s) differ from current workspace</p>
                    {Object.entries(diffs[c.checkpoint_id].diffs).map(([file, diff]) => (
                      <details key={file} className="rounded border border-border/40 p-1.5">
                        <summary className="text-[11px] font-mono cursor-pointer">{file}</summary>
                        <pre className="text-[10px] font-mono text-muted-foreground whitespace-pre-wrap break-all max-h-64 overflow-y-auto">{diff}</pre>
                      </details>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Jobs — /jobs */}
      <div className="rounded-xl border border-border/60 bg-card p-4 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <p className="text-sm font-semibold flex-1">Background jobs</p>
          <select value={jobStatus} onChange={(e) => { setJobStatus(e.target.value); }} className={`${inputCls} w-auto py-1`} title="Filter by status">
            <option value="">all statuses</option>
            {["queued", "running", "completed", "failed", "timed_out", "cancelled"].map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <Btn variant="ghost" onClick={loadJobs}><RefreshCw className="size-3.5" /> Reload</Btn>
        </div>
        <p className="text-[11px] text-muted-foreground">
          Submitting a host job requires an authenticated operator session — the server enforces this (jobs.py) and the API answers 403 otherwise.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
          <div className="sm:col-span-2">
            <Field label="Command" hint="Shell string or command to run on the host.">
              <input value={jobDraft.command} onChange={(e) => setJobDraft({ ...jobDraft, command: e.target.value })} placeholder="node --version" className={`${inputCls} font-mono`} />
            </Field>
          </div>
          <Field label="Title">
            <input value={jobDraft.title} onChange={(e) => setJobDraft({ ...jobDraft, title: e.target.value })} placeholder="Check node version" className={inputCls} />
          </Field>
          <Field label="Priority">
            <select value={jobDraft.priority} onChange={(e) => setJobDraft({ ...jobDraft, priority: e.target.value })} className={inputCls}>
              {["critical", "high", "normal", "low"].map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </Field>
        </div>
        <Btn onClick={onSubmitJob}><Plus className="size-3.5" /> Submit job</Btn>

        {jobsLoading ? (
          <SkeletonList rows={2} />
        ) : jobsError ? (
          <ErrorBox message={jobsError} onRetry={loadJobs} />
        ) : jobs.length === 0 ? (
          <EmptyState title={jobStatus ? `No jobs with status "${jobStatus}"` : "No background jobs"} hint={jobStatus ? "Clear the filter to see everything in the queue." : "Jobs submitted through the API show up here with live status, logs and cancellation."} />
        ) : (
          <div className="space-y-2">
            {jobs.map((j) => (
              <div key={j.job_id} className="rounded-lg border border-border/50 p-3 space-y-1.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <p className="text-xs font-semibold flex-1 min-w-40">{j.title ?? j.job_id}</p>
                  <Badge tone={statusTone(j.status)}>{j.status}</Badge>
                  {j.exit_code !== null && <Badge tone={j.exit_code === 0 ? "green" : "red"}>exit {j.exit_code}</Badge>}
                  {j.execution_seconds !== null && <Badge tone="gray">{j.execution_seconds.toFixed(1)}s</Badge>}
                </div>
                <p className="text-[10px] font-mono text-muted-foreground break-all">
                  {j.job_id} · started {fmtEpoch(j.started_at)} · finished {fmtEpoch(j.completed_at)}
                </p>
                {j.error && <p className="text-[11px] text-destructive break-all">{j.error}</p>}
                <div className="flex gap-2 flex-wrap">
                  <Btn variant="ghost" onClick={() => onLogs(j.job_id)}>
                    <ScrollText className="size-3.5" /> Logs
                  </Btn>
                  {(j.status === "queued" || j.status === "running") && (
                    <Btn variant="danger" onClick={() => onCancelJob(j.job_id)}>Cancel job</Btn>
                  )}
                </div>
                {logs[j.job_id] && (
                  <div className="rounded border border-border/40 bg-muted/20 p-2 space-y-1">
                    <p className="text-[11px] font-semibold">
                      logs · {logs[j.job_id].status}{logs[j.job_id].exit_code !== null ? ` · exit ${logs[j.job_id].exit_code}` : ""}
                    </p>
                    <pre className="text-[10px] font-mono whitespace-pre-wrap break-all max-h-52 overflow-y-auto">{logs[j.job_id].stdout || "(no stdout)"}</pre>
                    {logs[j.job_id].stderr && (
                      <pre className="text-[10px] font-mono text-destructive whitespace-pre-wrap break-all max-h-32 overflow-y-auto">{logs[j.job_id].stderr}</pre>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

