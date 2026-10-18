"use client";

/**
 * SwarmRunBoard — coordinator swarms on the chat surface.
 *
 * A coordinator swarm executes in the Gateway's swarm runtime, which is a
 * different mechanism from in-turn delegation: no `task_*` stream event
 * describes it, so the existing SwarmBoard / SubagentList (which read the run
 * stream) stay silent about it. This board closes that gap honestly:
 *
 * - It reads the owner-scoped Gateway swarm list — the same source Team Ops
 *   reads, remapped through `swarmRunRows` — never model prose, so no board
 *   row can appear without a real server row behind it.
 * - It shows only swarms that have not reached a final verdict. A finished
 *   swarm disappears from chat; history and lifecycle controls stay in Team
 *   Ops, because this surface is deliberately read-only (no pause/resume/
 *   cancel here — two owners for one action is how their copies drift).
 * - Polling follows the Team Ops discipline exactly: every 3s only while at
 *   least one row is refreshable (nontermination excluding paused), never
 *   with an overlapping read, and a failed refresh keeps the previous rows
 *   visible and names the Gateway's reason instead of turning into an empty
 *   board.
 * - Pinned to one swarm it consumes that swarm's event stream
 *   (`GET /api/swarms/{id}/events/stream`), so a state change is read when the
 *   runtime records it rather than on the next sweep. The stream is a change
 *   notification — every frame just re-reads the detail — and the 3s sweep
 *   stays as a fallback whose reason is rendered when the stream fails. The
 *   list mode keeps the sweep because the stream route is per swarm.
 * - Expanding a row reads detail and metrics as two independent requests and
 *   reuses the Team Ops structue/telemetry projections verbatim.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Layers3, ListChecks, Network, Users } from "lucide-react";
import { listSwarms, swarmDetails, swarmMetrics, hasRefreshableSwarms, isTerminalSwarmStatus, swarmProgress, swarmProgressLabel } from "@/lib/teamops";
import { subscribeSwarmEvents } from "@/lib/swarm-events";
import { swarmRunRows, type SwarmRunRow } from "@/lib/swarm-run-board";
import { pick } from "@/lib/http";
import {
  swarmStructureView,
  swarmTelemetryView,
  swarmModeBadge,
  type SwarmStructureView,
} from "@/lib/swarm-structure-view";

interface SwarmRunBoardProps {
  /** Hand the operator to Team Ops, which owns every lifecycle control. */
  onOpenTeamOps?: () => void;
  /** Pin the board to one swarm (composer dispatch). Omit for the full list. */
  swarmId?: string;
}

interface ExpandedState {
  detail: SwarmStructureView;
  structureError: string | null;
  metricError: string | null;
  telemetry: ReturnType<typeof swarmTelemetryView> | null;
  mode: ReturnType<typeof swarmModeBadge> | null;
}

export function SwarmRunBoard({ onOpenTeamOps, swarmId }: SwarmRunBoardProps) {
  const [rows, setRows] = useState<SwarmRunRow[]>([]);
  const [readError, setReadError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<ExpandedState | null>(null);
  const [streamFallback, setStreamFallback] = useState<string | null>(null);
  const [streamEndedTick, setStreamEndedTick] = useState(0);
  const loadingRef = useRef(false);
  const refreshTimer = useRef<number | null>(null);

  // When pinned to a single swarm (composer dispatch), read that swarm's
  // detail directly instead of the full list.
  const loadList = useCallback(async () => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    try {
      if (swarmId) {
        const detail = await swarmDetails(swarmId);
        const status = String(pick(detail, ["status", "state"], ""));
        const objective = String(pick(detail, ["objective", "goal"], ""));
        const mode = String(pick(detail, ["mode"], ""));
        const maxConcurrency = typeof detail.max_concurrency === "number" ? detail.max_concurrency : undefined;
        const progress = (detail.progress ?? undefined) as Record<string, number> | undefined;
        const row: SwarmRunRow = {
          id: swarmId,
          objective,
          status,
          paused: status === "paused",
          progressLabel: swarmProgressLabel(swarmProgress(progress)),
          topology: mode,
          requestedConcurrency:
            typeof maxConcurrency === "number" && maxConcurrency > 0
              ? `ceiling ${maxConcurrency}`
              : "auto-sized",
        };
        setRows([row]);
      } else {
        const swarms = await listSwarms();
        setRows(swarmRunRows(swarms));
      }
      setReadError(null);
    } catch (e) {
      // Keep the previous rows visible: a failed refresh must not read as
      // "nothing is running".
      setReadError(e instanceof Error ? e.message : String(e));
    } finally {
      loadingRef.current = false;
      setLoaded(true);
    }
  }, [swarmId]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  // Bursts collapse into one re-read: one event batch can carry a dozen
  // frames, and each one would otherwise trigger its own detail read.
  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current !== null) return;
    refreshTimer.current = window.setTimeout(() => {
      refreshTimer.current = null;
      void loadList();
    }, 200);
  }, [loadList]);
  useEffect(
    () => () => {
      if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current);
    },
    [],
  );

  const refreshable = hasRefreshableSwarms(rows.map((r) => ({ status: r.status })));
  // Pinned mode streams that swarm's events; a terminal row means the server
  // has nothing further to report on it. A paused swarm still streams, because
  // the resume event must arrive even though polling intentionally rests.
  const streaming = Boolean(swarmId) && !rows.some((r) => isTerminalSwarmStatus(r.status));
  useEffect(() => {
    if (!swarmId || !streaming) return;
    setStreamFallback(null);
    const handle = subscribeSwarmEvents(swarmId, {
      onEvent: scheduleRefresh,
      onEnd: () => {
        // The server closed its observation window. Re-read first and only
        // then allow a re-subscribe — the effect's `streaming` gate decides
        // whether the swarm is still running; without this, a long swarm would
        // freeze silently after the server's window closed.
        void loadList().finally(() => setStreamEndedTick((t) => t + 1));
      },
      onError: (reason) => {
        setStreamFallback(reason);
      },
    });
    return () => handle.close();
  }, [swarmId, streaming, streamEndedTick, loadList, scheduleRefresh]);

  useEffect(() => {
    if (!refreshable) return;
    // While the pinned swarm's event stream is healthy it IS the refresh
    // clock. The sweep runs only as a disclosed fallback once the stream has
    // failed (or in list mode, where there is no single swarm to stream).
    if (swarmId && streaming && !streamFallback) return;
    const timer = window.setInterval(() => {
      void loadList();
    }, 3_000);
    return () => window.clearInterval(timer);
  }, [refreshable, loadList, swarmId, streaming, streamFallback]);

  const openId = expandedId;
  useEffect(() => {
    if (!openId) return;
    let cancelled = false;
    const loadExpanded = async () => {
      const [detail, metrics] = await Promise.allSettled([swarmDetails(openId), swarmMetrics(openId)]);
      if (cancelled) return;
      // `undefined` on a rejection keeps the honest "could not be read" shape
      // from the projection itself — no hand-built placeholder rows.
      let structure: SwarmStructureView = swarmStructureView(undefined, undefined);
      let structureError: string | null = null;
      if (detail.status === "fulfilled") {
        structure = swarmStructureView(detail.value, undefined);
      } else {
        structureError = detail.reason instanceof Error ? detail.reason.message : String(detail.reason);
      }
      let metricError: string | null = null;
      let telemetry: ExpandedState["telemetry"] = null;
      if (metrics.status === "fulfilled") {
        telemetry = swarmTelemetryView(metrics.value);
      } else {
        metricError = metrics.reason instanceof Error ? metrics.reason.message : String(metrics.reason);
      }
      // The mode badge also carries the requested topology from the detail
      // read; on a metrics-only failure it still renders.
      const root = detail.status === "fulfilled" ? ((detail.value ?? {}) as Record<string, unknown>) : undefined;
      setExpanded({
        detail: structure,
        structureError,
        metricError,
        telemetry,
        mode: root ? swarmModeBadge(root.mode) : null,
      });
    };

    void loadExpanded();
    return () => {
      cancelled = true;
    };
  }, [openId, rows]);

  if (!loaded) return null;
  if (rows.length === 0) {
    if (!readError) return null; // A successful read with nothing running hides the board.
    // A failed FIRST read must not read as "nothing is running": name the
    // reason and hand off to Team Ops. No auto-retry storms from chat; Team
    // Ops' own refresh and the next mount re-attempt.
    return (
      <div className="my-2 rounded-lg border border-border/60 bg-muted/20 px-2.5 py-2 text-[10px] text-muted-foreground" data-swarm-run-board>
        <p>
          The swarm list could not be read ({readError}) — this is not a claim that no swarm is running.
        </p>
        {onOpenTeamOps && (
          <button
            type="button"
            className="mt-0.5 underline decoration-border/60 underline-offset-2 hover:text-foreground"
            onClick={onOpenTeamOps}
          >
            controls &amp; history in Team Ops
          </button>
        )}
      </div>
    );
  }

  const modelNote =
    readError && rows.length > 0
      ? `The last swarm refresh failed (${readError}) — the rows below are the previous measured state, not a live answer.`
      : null;
  // A degraded stream is disclosed where the rows are, not buried: the reader
  // must know the board is on the 3s sweep, and why.
  const streamNote =
    swarmId && streaming && streamFallback
      ? `The live event stream for this swarm failed (${streamFallback}) — refreshing every 3 seconds instead.`
      : null;

  return (
    <div className="my-2 rounded-lg border border-border/60 bg-muted/20 p-2.5" data-swarm-run-board>
      <div className="mb-2 flex items-center gap-1.5 px-1 text-[11px] font-medium text-muted-foreground">
        <Network className="size-3.5" aria-hidden="true" />
        <span>
          {swarmId ? "Pinned swarm" : `Coordinator swarms · ${rows.length} running`}
        </span>
        <span className="text-border/70">|</span>
        <button
          type="button"
          className="underline decoration-border/60 underline-offset-2 hover:text-foreground"
          onClick={onOpenTeamOps}
        >
          controls & history in Team Ops
        </button>
      </div>

      {modelNote && <p className="px-1 pb-2 text-[10px] text-amber-600 dark:text-amber-400">{modelNote}</p>}
      {streamNote && (
        <p className="px-1 pb-2 text-[10px] text-amber-600 dark:text-amber-400" data-swarm-stream-fallback>
          {streamNote}
        </p>
      )}

      <div className="space-y-1.5">
        {rows.map((row) => (
          <div key={row.id} className="rounded-md border border-border/50 bg-background/60 px-2.5 py-2">
            <button
              type="button"
              className="flex w-full items-start gap-2 text-left"
              onClick={() => setExpandedId((cur) => (cur === row.id ? null : row.id))}
              aria-expanded={expandedId === row.id}
            >
              {expandedId === row.id ? (
                <ChevronDown className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
              ) : (
                <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
              )}
              <Layers3 className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[11px] font-medium">{row.objective}</span>
                <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-muted-foreground">
                  <span className="font-mono">{row.id}</span>
                  <span className={row.paused ? "text-amber-500" : "text-sky-500"}>{row.paused ? "paused" : row.status}</span>
                  {row.topology && <span>{row.topology}</span>}
                  <span>{row.requestedConcurrency}</span>
                </span>
                <span className="mt-0.5 flex items-center gap-1 text-[10px] text-muted-foreground">
                  <ListChecks className="size-3" aria-hidden="true" />
                  {row.progressLabel}
                </span>
              </span>
            </button>

            {expandedId === row.id && (
              <div className="mt-2 space-y-2 border-t border-border/40 pt-2">
                {expanded?.mode && (
                  <p className="text-[10px] text-muted-foreground">
                    Topology: <span className="font-mono">{expanded.mode.mode}</span> · {expanded.mode.note}
                  </p>
                )}
                {expanded?.structureError && (
                  <p className="text-[10px] text-red-500">This swarm&apos;s task DAG could not be read ({expanded.structureError}).</p>
                )}
                {expanded?.metricError && (
                  <p className="text-[10px] text-red-500">This swarm&apos;s measured telemetry could not be read ({expanded.metricError}).</p>
                )}
                {expanded && expanded.detail.taskReadNote && !expanded.structureError && (
                  <p className="text-[10px] text-muted-foreground">{expanded.detail.taskReadNote}</p>
                )}
                {expanded && expanded.detail.tasks.length > 0 && (
                  <ul className="space-y-1 pl-1">
                    {expanded.detail.tasks.map((task) => (
                      <li key={task.taskId} className="flex items-center gap-1.5 text-[10px]">
                        <span className={`size-1.5 shrink-0 rounded-full ${task.stateTone === "green" ? "bg-emerald-500" : task.stateTone === "red" ? "bg-red-500" : task.stateTone === "blue" ? "bg-sky-500" : task.stateTone === "amber" ? "bg-amber-500" : "bg-muted-foreground/40"}`} aria-hidden="true" />
                        <span className="truncate font-mono">{task.taskId}</span>
                        <span className="text-muted-foreground">· {task.stateNote}</span>
                        <span className="truncate text-muted-foreground">· {task.objective ?? task.objectiveNote}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {expanded?.telemetry && (
                  <p className="flex items-start gap-1.5 text-[10px] text-muted-foreground">
                    <Users className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
                    <span>
                      Parallelism: {expanded.telemetry.parallelism} · Meaningful completion: {expanded.telemetry.completion} ·
                      Advisory risks: {expanded.telemetry.riskSignals.length > 0 ? expanded.telemetry.riskSignals.join(", ") : "none reported"}
                    </span>
                  </p>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
