// components/sections/WorkflowRunInspector.tsx — measured view of ONE workflow run.
//
// Rendered by WorkflowsSection (not a workspace view of its own), so it follows
// the ProjectCrewPanel pattern: reachable through its parent section, and the
// client contract is src/lib/workflows.ts.
//
// The honesty rules this component is built around:
//
// - A measurement that could not be taken renders as unknown, never as zero. The
//   timeline reads `timeline_source`: when the server reports "unavailable" this
//   says so instead of drawing an empty chart that looks like "everything was
//   instant".
// - Execution is never presented as acceptance. There is no "verified" badge
//   anywhere in this file, because the server sends no such claim and a
//   completed run is not a verified run.
// - A dry run is drawn as a dry run. The simulation panel always shows the
//   server's `execution_label`, so a projection can never be mistaken for work.
import React, { useCallback, useEffect, useState } from "react";
import {
  Activity,
  GitBranch,
  PauseCircle,
  PlayCircle,
  Radio,
  Scissors,
  Timer,
} from "lucide-react";

import { Badge, Btn, EmptyState, ErrorBox, Field, Notice, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import {
  forkWorkflowRun,
  getRunHistory,
  getRunReport,
  listWorkflowExecutors,
  resumeWorkflowRun,
  signalWorkflowRun,
  simulateWorkflow,
  suspendWorkflowRun,
  sweepWorkflowWaits,
  type ExecutorListing,
  type ForkResult,
  type RunHistory,
  type RunReport,
  type SimulationResult,
  type WorkflowRun,
} from "@/lib/workflows";

const NODE_TONES: Record<string, "green" | "amber" | "gray" | "blue" | "purple" | "cyan" | "red" | "indigo"> = {
  succeeded: "green",
  running: "blue",
  ready: "blue",
  pending: "gray",
  waiting: "amber",
  failed: "red",
  skipped: "gray",
  cancelled: "gray",
  aborted: "red",
};

function tone(status: string) {
  return NODE_TONES[status] ?? "gray";
}

function secs(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 1) return `${Math.round(value * 1000)}ms`;
  return `${value.toFixed(2)}s`;
}

export function WorkflowRunInspector(props: {
  runId: string;
  workflowId: string;
  run: WorkflowRun | null;
  onRunChange: (run: WorkflowRun) => void;
  onNotice: (message: string) => void;
}) {
  const { runId, workflowId, run, onRunChange, onNotice } = props;
  const [report, setReport] = useState<RunReport | null>(null);
  const [history, setHistory] = useState<RunHistory | null>(null);
  const [executors, setExecutors] = useState<ExecutorListing | null>(null);
  const [simulation, setSimulation] = useState<SimulationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [signalEvent, setSignalEvent] = useState("");
  const [signalPayload, setSignalPayload] = useState("");
  const [forkNotes, setForkNotes] = useState<ForkResult | null>(null);
  // Reset is opt-in and defaults to UNCHECKED: repeating a side effect the source
  // run already performed is the dangerous direction, so it must be chosen.
  const [resetCompleted, setResetCompleted] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [r, h, e] = await Promise.all([getRunReport(runId), getRunHistory(runId), listWorkflowExecutors()]);
      setReport(r);
      setHistory(h);
      setExecutors(e);
    } catch (err) {
      setError(errMsg(err));
    }
  }, [runId]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = useCallback(
    async (key: string, fn: () => Promise<void>) => {
      setBusy(key);
      setError(null);
      try {
        await fn();
      } catch (err) {
        setError(errMsg(err));
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  const obs = report?.observability;
  const isSuspended = run?.status === "suspended";
  const canSignal = run?.status === "waiting_event";

  return (
    <div className="space-y-3">
      {error && <ErrorBox message={error} onRetry={() => void load()} />}

      {/* ── Graph ─────────────────────────────────────────────────────── */}
      <div className="rounded-xl border border-border/60 bg-card/40 p-3 space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <Activity className="h-4 w-4 text-muted-foreground" />
          <span className="text-[11px] font-semibold uppercase tracking-wide">Graph</span>
          {report && <Badge tone="gray">v{report.provenance.graph_version ?? "—"}</Badge>}
          {report && <span className="text-[11px] text-muted-foreground">{obs?.nodes_completed}/{obs?.nodes_total} nodes complete</span>}
          {run?.waiting_nodes && run.waiting_nodes.length > 0 && (
            <Badge tone="amber">{run.waiting_nodes.length} waiting</Badge>
          )}
        </div>

        {!obs || obs.nodes_total === 0 ? (
          <EmptyState title="No node detail reported" hint="The server returned no per-node state for this run." />
        ) : (
          <>
            <div className="flex flex-wrap gap-1.5">
              {(obs.timeline.length > 0
                ? obs.timeline.map((t) => ({ id: t.node_id, status: t.status }))
                : []
              ).map((n) => (
                <Badge key={n.id} tone={tone(n.status)}>
                  {n.id}
                </Badge>
              ))}
              {obs.timeline.length === 0 && (
                <span className="text-[11px] text-muted-foreground">
                  Timeline unavailable — the server reported no measured executions for this run.
                </span>
              )}
            </div>

            {obs.timed_out_nodes.length > 0 && (
              <Notice
                message={`Deadline exceeded: ${obs.timed_out_nodes.join(", ")}. The in-flight work was fenced and its late result discarded, not adopted.`}
              />
            )}
          </>
        )}
      </div>

      {/* ── Timeline + critical path ───────────────────────────────────── */}
      <div className="rounded-xl border border-border/60 bg-card/40 p-3 space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <Timer className="h-4 w-4 text-muted-foreground" />
          <span className="text-[11px] font-semibold uppercase tracking-wide">Timeline</span>
          {obs && obs.timeline_source !== "event_log" && <Badge tone="amber">measurements unavailable</Badge>}
          {obs && obs.timeline_source === "event_log" && (
            <span className="text-[11px] text-muted-foreground">
              {obs.measured_executions}/{obs.timed_executions} measured · total {secs(obs.total_measured_seconds)} ·{" "}
              {obs.waves_dispatched} wave(s)
            </span>
          )}
        </div>

        {obs && obs.timeline_source === "event_log" && obs.timeline.length > 0 ? (
          <>
            <div className="space-y-1">
              {obs.slowest_nodes.map((n) => {
                const share = obs.total_measured_seconds > 0 ? n.duration_seconds / obs.total_measured_seconds : 0;
                return (
                  <div key={n.node_id} className="flex items-center gap-2 text-[11px]">
                    <span className="w-32 shrink-0 truncate font-mono">{n.node_id}</span>
                    <div className="h-2 flex-1 rounded-full bg-border/40 overflow-hidden">
                      <div
                        className="h-full bg-primary/70"
                        style={{ width: `${Math.max(1, Math.min(100, Math.round(share * 100)))}%` }}
                      />
                    </div>
                    <span className="w-16 shrink-0 text-right tabular-nums">{secs(n.duration_seconds)}</span>
                    <Badge tone={tone(n.status)}>{n.status}</Badge>
                  </div>
                );
              })}
            </div>
            <div className="text-[11px] text-muted-foreground">
              Critical path:{" "}
              {obs.critical_path.path.length > 0 ? (
                <span className="font-mono">{obs.critical_path.path.join(" → ")}</span>
              ) : (
                "not yet measurable"
              )}{" "}
              ({secs(obs.critical_path.total_seconds)})
              {!obs.critical_path.complete && obs.critical_path.reason ? ` — ${obs.critical_path.reason}` : ""}
            </div>
          </>
        ) : (
          <p className="text-[11px] text-muted-foreground">
            The server reported no measured executions for this run, so there is no timeline to draw. This is an
            unknown, not a zero-length run.
          </p>
        )}
      </div>

      {/* ── Run control ───────────────────────────────────────────────── */}
      <div className="rounded-xl border border-border/60 bg-card/40 p-3 space-y-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide">Run control</span>
        <div className="flex flex-wrap gap-2">
          <Btn
            variant="ghost"
            disabled={busy !== null || isSuspended || !run || run.status === "completed" || run.status === "failed"}
            onClick={() =>
              void act("suspend", async () => {
                const next = await suspendWorkflowRun(runId, "suspended from the Workflows inspector");
                onRunChange(next);
                onNotice(`Run ${runId} suspended. Stepping it now returns the real status without doing work.`);
                await load();
              })
            }
            title="Park the run without inventing a terminal outcome"
          >
            <PauseCircle className="h-3.5 w-3.5 mr-1" /> Suspend
          </Btn>
          <Btn
            variant="ghost"
            disabled={busy !== null || !isSuspended}
            onClick={() =>
              void act("resume", async () => {
                const next = await resumeWorkflowRun(runId, "resumed from the Workflows inspector");
                onRunChange(next);
                onNotice(`Run ${runId} resumed.`);
                await load();
              })
            }
          >
            <PlayCircle className="h-3.5 w-3.5 mr-1" /> Resume
          </Btn>
          <Btn
            variant="ghost"
            disabled={busy !== null}
            onClick={() =>
              void act("sweep", async () => {
                const next = await sweepWorkflowWaits(runId);
                onRunChange(next);
                onNotice(
                  next.status === "failed"
                    ? "An external wait expired and the run failed closed."
                    : "No external wait had passed its deadline.",
                );
                await load();
              })
            }
            title="Fail any external wait whose deadline has already passed"
          >
            <Scissors className="h-3.5 w-3.5 mr-1" /> Sweep expired waits
          </Btn>
        </div>

        {canSignal && (
          <div className="space-y-2 border-t border-border/50 pt-2">
            <Notice message="This run is parked in waiting_event. Only a matching signal will release it." />
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="Event name" hint="Must match the event_wait node's config.event exactly.">
                <input className={inputCls} value={signalEvent} onChange={(e) => setSignalEvent(e.target.value)} placeholder="deploy.approved" />
              </Field>
              <Field label="Payload (JSON)" hint="Delivered verbatim to the waiting node.">
                <input
                  className={inputCls}
                  value={signalPayload}
                  onChange={(e) => setSignalPayload(e.target.value)}
                  placeholder='{"approver":"prem"}'
                />
              </Field>
            </div>
            <Btn
              variant="primary"
              disabled={busy !== null || signalEvent.trim() === ""}
              onClick={() =>
                void act("signal", async () => {
                  let payload: unknown = null;
                  if (signalPayload.trim()) {
                    try {
                      payload = JSON.parse(signalPayload);
                    } catch {
                      throw new Error("Payload must be valid JSON, or left empty.");
                    }
                  }
                  const result = await signalWorkflowRun(runId, signalEvent.trim(), payload);
                  onRunChange(result.run);
                  onNotice(
                    result.unmatched
                      ? `No node was waiting for "${result.event}", so nothing changed.`
                      : `Released ${result.released_nodes.join(", ") || "no node"}.`,
                  );
                  await load();
                })
              }
            >
              <Radio className="h-3.5 w-3.5 mr-1" /> Send signal
            </Btn>
          </div>
        )}
      </div>

      {/* ── Fork + simulate ───────────────────────────────────────────── */}
      <div className="rounded-xl border border-border/60 bg-card/40 p-3 space-y-2">
        <div className="flex items-center gap-2">
          <GitBranch className="h-4 w-4 text-muted-foreground" />
          <span className="text-[11px] font-semibold uppercase tracking-wide">Fork &amp; simulate</span>
        </div>

        <div className="flex flex-wrap gap-2">
          <Btn
            variant="ghost"
            disabled={busy !== null || !history || history.count === 0}
            onClick={() =>
              void act("fork", async () => {
                const result = await forkWorkflowRun(runId, { reset_completed_nodes: resetCompleted });
                setForkNotes(result);
                onNotice(`Forked into run ${result.run_id}, inheriting ${result.inherited_completed_nodes.length} completed node(s). The source run is unchanged.`);
                await load();
              })
            }
            title="Branch a new run from this run's current history"
          >
            <GitBranch className="h-3.5 w-3.5 mr-1" /> Fork from here
          </Btn>
          <Btn
            variant="ghost"
            disabled={busy !== null}
            onClick={() =>
              void act("simulate", async () => {
                const result = await simulateWorkflow({ workflow_id: workflowId });
                setSimulation(result);
              })
            }
            title="Dry-run the workflow with no side effects"
          >
            <Scissors className="h-3.5 w-3.5 mr-1" /> Dry run
          </Btn>
        </div>

        <label className="flex items-start gap-2 text-[11px]">
          <input type="checkbox" checked={resetCompleted} onChange={(e) => setResetCompleted(e.target.checked)} className="mt-0.5" />
          <span>
            Re-run nodes this run already completed.{" "}
            <span className="text-muted-foreground">
              Off by default: the fork inherits completed work instead of repeating it, and idempotency keys are
              per-run so they cannot protect a repeated side effect.
            </span>
          </span>
        </label>

        {forkNotes && (
          <div className="space-y-1 text-[11px]">
            <div>
              Fork <span className="font-mono">{forkNotes.run_id}</span> from{" "}
              <span className="font-mono">{forkNotes.source_run_id}</span> at event{" "}
              <span className="font-mono">#{forkNotes.forked_at_index}</span> ({forkNotes.replayed_events} events
              replayed).
            </div>
            <div className="text-muted-foreground">
              Inherited completed: {forkNotes.inherited_completed_nodes.join(", ") || "none"}
            </div>
            {forkNotes.notes.map((n, i) => (
              <Notice key={i} message={n} />
            ))}
          </div>
        )}

        {simulation && (
          <div className="space-y-1 text-[11px]">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge tone="purple">{simulation.execution_label}</Badge>
              <span className="text-muted-foreground">
                {simulation.waves} wave(s) · {simulation.nodes_visited.length} node(s) reached
              </span>
            </div>
            <div className="text-muted-foreground">
              This is a projection of what the scheduler would reach. No node executed, no token was spent, and no
              acceptance is claimed.
            </div>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(simulation.node_outcomes).map(([id, status]) => (
                <Badge key={id} tone={tone(status)}>
                  {id}: {status}
                </Badge>
              ))}
            </div>
            {simulation.notes.map((n, i) => (
              <Notice key={i} message={n} />
            ))}
          </div>
        )}
      </div>

      {/* ── Executors ─────────────────────────────────────────────────── */}
      <div className="rounded-xl border border-border/60 bg-card/40 p-3 space-y-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-wide">Bound executors</span>
        {!executors ? (
          <p className="text-[11px] text-muted-foreground">Executor registry not reported.</p>
        ) : (
          <>
            <div className="flex flex-wrap gap-1.5">
              {executors.bound.map((name) => (
                <Badge key={name} tone={executors.domain_bound.includes(name) ? "green" : "gray"}>
                  {name}
                </Badge>
              ))}
              {executors.bound.length === 0 && (
                <span className="text-[11px] text-muted-foreground">
                  No executor is bound, so any node needing one fails honestly.
                </span>
              )}
            </div>
            {executors.domain_bound.length === 0 && executors.domain_executors.length > 0 && (
              <p className="text-[11px] text-muted-foreground">
                The real executors ({executors.domain_executors.join(", ")}) are available but not bound. They spend
                money and reach the network, so binding them is an explicit opt-in.
              </p>
            )}
            <p className="text-[11px] text-muted-foreground">{executors.note}</p>
          </>
        )}
      </div>

      {/* ── History ───────────────────────────────────────────────────── */}
      {history && history.count > 0 && (
        <details className="rounded-xl border border-border/60 bg-card/40 p-3">
          <summary className="text-[11px] font-semibold uppercase tracking-wide cursor-pointer">
            Event history ({history.count})
          </summary>
          <div className="mt-2 max-h-72 overflow-auto space-y-0.5">
            {history.events
              .slice()
              .reverse()
              .map((e) => (
                <div key={`${e.index}-${e.event_id}`} className="flex items-baseline gap-2 text-[11px] font-mono">
                  <span className="w-10 shrink-0 text-muted-foreground tabular-nums">{e.index}</span>
                  <span className="w-52 shrink-0 truncate">{e.event_type}</span>
                  <span className="w-32 shrink-0 truncate text-muted-foreground">{e.node_id ?? ""}</span>
                  <span className="flex-1 truncate text-muted-foreground">{e.reason ?? ""}</span>
                </div>
              ))}
          </div>
        </details>
      )}
    </div>
  );
}

export default WorkflowRunInspector;
