"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";

import { Badge, Btn, EmptyState, ErrorBox, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { ExecutionModeStatus, fetchExecutionMode } from "@/lib/plan";
import { FleetWorker, fetchFleetWorkers } from "@/lib/supervision";
import { LiveSubagent, fetchLiveSubagentsStrict } from "@/lib/subagents";

/**
 * Agent status for the dashboard.
 *
 * Every value rendered here comes from a live gateway endpoint
 * (`/api/plan-mode/mode`, `/api/supervision/fleet`, `/api/subagents/control`).
 * While a request is in flight — or if it fails — the surface says so
 * explicitly ("unavailable", with the real error) instead of guessing a mode
 * or showing zeroes that look like data.
 */

type Async<T> = { phase: "loading" } | { phase: "ready"; data: T } | { phase: "error"; message: string };

async function loadAsync<T>(fetcher: () => Promise<T>, set: (state: Async<T>) => void): Promise<void> {
  set({ phase: "loading" });
  try {
    set({ phase: "ready", data: await fetcher() });
  } catch (err) {
    set({ phase: "error", message: errMsg(err) });
  }
}

function BlockHeader(props: { title: string; onRefresh: () => void; children?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <p className="text-xs font-semibold flex-1">{props.title}</p>
      {props.children}
      <Btn variant="ghost" onClick={props.onRefresh} title={`Refresh ${props.title.toLowerCase()}`}>
        <RefreshCw className="size-3.5" /> Refresh
      </Btn>
    </div>
  );
}

function workerTone(status: string): "green" | "blue" | "amber" | "red" | "gray" {
  const s = status.toLowerCase();
  if (s === "busy") return "blue";
  if (s === "idle") return "green";
  if (s === "stalled" || s === "degraded") return "amber";
  if (s === "failed") return "red";
  return "gray";
}

function subagentTone(status: string): "green" | "blue" | "amber" | "red" | "gray" {
  const s = status.toLowerCase();
  if (s === "running" || s === "ready" || s === "initializing" || s === "recovering") return "blue";
  if (s === "completed") return "green";
  if (s === "failed") return "red";
  if (s === "stalled" || s === "blocked" || s === "waiting") return "amber";
  return "gray";
}

/**
 * Unified execution mode from `GET /api/plan-mode/mode`.
 *
 * Honesty contract: before the response resolves (or after a failure) the
 * badge reads "unavailable" — the component never renders a default or
 * remembered mode string, because it does not have one yet.
 */
function ExecutionModeBlock() {
  const [state, setState] = useState<Async<ExecutionModeStatus>>({ phase: "loading" });
  const load = useCallback(() => loadAsync(fetchExecutionMode, setState), []);
  useEffect(() => {
    void load();
  }, [load]);

  const status = state.phase === "ready" ? state.data : null;

  return (
    <div className="rounded-xl border border-border/60 bg-muted/30 p-3 space-y-1.5" data-testid="execution-mode">
      <BlockHeader title="Execution mode" onRefresh={() => void load()}>
        {status && <Badge tone="gray">source: {status.source || "unknown"}</Badge>}
        {status && <Badge tone={status.persisted ? "green" : "gray"}>{status.persisted ? "persisted" : "not persisted"}</Badge>}
      </BlockHeader>

      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] text-muted-foreground">Active:</span>
        {state.phase === "ready" ? (
          <Badge tone={state.data.mode.endsWith(".plan") ? "amber" : "blue"}>{state.data.mode}</Badge>
        ) : (
          <Badge tone="gray">unavailable</Badge>
        )}
        {state.phase === "loading" && (
          <span className="text-[11px] text-muted-foreground">still checking with the gateway…</span>
        )}
      </div>

      {state.phase === "ready" && (
        <p className="text-[11px] text-muted-foreground">
          {state.data.note || "The gateway reported no note for this mode."}
          {state.data.updated_at ? ` · updated ${state.data.updated_at}` : ""}
          {state.data.actor ? ` · by ${state.data.actor}` : ""}
        </p>
      )}

      {state.phase === "error" && (
        <ErrorBox message={`Execution mode unavailable — ${state.message}`} onRetry={() => void load()} />
      )}
    </div>
  );
}

/** Worker heartbeats from `GET /api/supervision/fleet`. */
function WorkersBlock() {
  const [state, setState] = useState<Async<FleetWorker[]>>({ phase: "loading" });
  const load = useCallback(() => loadAsync(fetchFleetWorkers, setState), []);
  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="rounded-xl border border-border/60 bg-muted/30 p-3 space-y-1.5" data-testid="worker-status">
      <BlockHeader title="Workers" onRefresh={() => void load()}>
        {state.phase === "ready" && <Badge tone="gray">{state.data.length} reporting</Badge>}
      </BlockHeader>

      {state.phase === "loading" && <SkeletonList rows={2} />}
      {state.phase === "error" && (
        <ErrorBox message={`Worker status unavailable — ${state.message}`} onRetry={() => void load()} />
      )}
      {state.phase === "ready" && state.data.length === 0 && (
        <EmptyState
          title="No workers have reported in yet."
          hint="The gateway lists a worker here as soon as it receives a heartbeat from one."
        />
      )}
      {state.phase === "ready" && state.data.length > 0 && (
        <div className="space-y-1.5">
          {state.data.map((w) => (
            <div key={w.worker_id} className="flex items-center gap-2 rounded-lg bg-muted/40 px-2.5 py-1.5 flex-wrap">
              <span className="font-mono text-[11px] font-semibold">{w.worker_id}</span>
              <Badge tone={workerTone(w.status)}>{w.status}</Badge>
              {w.current_action && (
                <span className="text-[11px] text-muted-foreground flex-1 min-w-24 truncate" title={w.current_action}>
                  {w.current_action}
                </span>
              )}
              {w.unresolved_anomalies_count !== null && w.unresolved_anomalies_count > 0 && (
                <Badge tone="amber">
                  {w.unresolved_anomalies_count} anomal{w.unresolved_anomalies_count === 1 ? "y" : "ies"}
                </Badge>
              )}
              {w.last_heartbeat_elapsed_seconds !== null && (
                <span className="font-mono text-[10px] text-muted-foreground" title="Seconds since the last heartbeat">
                  hb {w.last_heartbeat_elapsed_seconds.toFixed(1)}s ago
                </span>
              )}
              {w.progress_percent !== null && (
                <span className="font-mono text-[10px] text-muted-foreground">{w.progress_percent.toFixed(0)}%</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Live subagents from `GET /api/subagents/control`. */
function SubagentsBlock() {
  const [state, setState] = useState<Async<LiveSubagent[]>>({ phase: "loading" });
  const load = useCallback(() => loadAsync(fetchLiveSubagentsStrict, setState), []);
  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="rounded-xl border border-border/60 bg-muted/30 p-3 space-y-1.5" data-testid="subagent-status">
      <BlockHeader title="Subagents" onRefresh={() => void load()}>
        {state.phase === "ready" && <Badge tone="gray">{state.data.length} registered</Badge>}
      </BlockHeader>

      {state.phase === "loading" && <SkeletonList rows={2} />}
      {state.phase === "error" && (
        <ErrorBox message={`Subagent status unavailable — ${state.message}`} onRetry={() => void load()} />
      )}
      {state.phase === "ready" && state.data.length === 0 && (
        <EmptyState
          title="No subagents registered right now."
          hint="Spawned subagents appear here with their live status until they finish or are cancelled."
        />
      )}
      {state.phase === "ready" && state.data.length > 0 && (
        <div className="space-y-1.5">
          {state.data.map((s) => (
            <div key={s.id} className="flex items-center gap-2 rounded-lg bg-muted/40 px-2.5 py-1.5 flex-wrap">
              <span className="font-mono text-[11px] font-semibold">{s.id}</span>
              <Badge tone={subagentTone(s.status)}>{s.status}</Badge>
              {s.role && <Badge tone="gray">{s.role}</Badge>}
              {s.objective && (
                <span className="text-[11px] text-muted-foreground flex-1 min-w-24 truncate" title={s.objective}>
                  {s.objective}
                </span>
              )}
              {s.parent && <span className="font-mono text-[10px] text-muted-foreground">parent: {s.parent}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function AgentStatusPanel() {
  return (
    <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Activity className="size-4 text-primary" />
        <p className="text-xs font-semibold flex-1">Agent status</p>
      </div>
      <ExecutionModeBlock />
      <WorkersBlock />
      <SubagentsBlock />
    </div>
  );
}
