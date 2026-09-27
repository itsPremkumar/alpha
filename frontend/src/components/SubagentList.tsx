"use client";

import React, { useEffect, useState } from "react";
import { Bot, CheckCircle2, CircleDashed, CircleSlash, Clock, XCircle } from "lucide-react";
import type { SubagentTask, SubagentTaskStatus } from "@/lib/sse-reducer";
import type { ToolTiming } from "@/lib/activity";
import { formatDuration, openTiming, settleTiming } from "@/lib/activity";

interface SubagentListProps {
  tasks: SubagentTask[];
}

/**
 * One live row per delegated subagent, folded from `task_*` custom events.
 *
 * This is the "what is happening inside" surface: while the lead agent is
 * delegating, the transcript alone shows nothing but a spinner, so each task
 * reports its own status, latest step, step progress, and token spend as soon
 * as the run reports it.
 *
 * Everything on the row is copied from the event — status, step numbers,
 * tokens, error text. Fields the run never sent are rendered as *nothing*, not
 * as zero: an absent usage block means "not reported", never "0 tokens".
 * Durations are client-observed (see `ToolTiming`), so a task restored from
 * history shows no duration rather than an invented one.
 */
export function SubagentList({ tasks }: SubagentListProps) {
  const [timings, setTimings] = useState<Record<string, ToolTiming>>({});
  const [now, setNow] = useState(() => Date.now());

  // Record first sighting and settlement. The `changed` guard keeps frames with
  // no subagent news from producing a new object (and therefore a re-render).
  useEffect(() => {
    setTimings((previous) => {
      const stamp = Date.now();
      let changed = false;
      const next = { ...previous };
      for (const task of tasks) {
        const existing = next[task.id];
        if (!existing) {
          next[task.id] = openTiming(stamp, task.status !== "running");
          changed = true;
        } else if (existing.end === undefined && task.status !== "running") {
          next[task.id] = settleTiming(existing, stamp);
          changed = true;
        }
      }
      return changed ? next : previous;
    });
  }, [tasks]);

  const openWork = tasks.some((task) => task.status === "running" && timings[task.id]?.end === undefined && timings[task.id]?.observedRunning);

  // Tick only while a duration is genuinely being measured.
  useEffect(() => {
    if (!openWork) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [openWork]);

  if (tasks.length === 0) return null;
  const running = tasks.filter((task) => task.status === "running").length;

  return (
    <div className="my-2 space-y-1" data-subagent-count={tasks.length}>
      <div className="flex items-center gap-1.5 px-1 text-[11px] font-medium text-muted-foreground">
        <Bot className="size-3.5" aria-hidden="true" />
        <span>
          {tasks.length} subagent{tasks.length === 1 ? "" : "s"}
          {running > 0 ? ` · ${running} running` : ""}
        </span>
      </div>

      {tasks.map((task) => (
        <SubagentRow key={task.id} task={task} timing={timings[task.id]} now={now} />
      ))}
    </div>
  );
}

const STATUS_VIEW: Record<SubagentTaskStatus, { label: string; className: string; Icon: typeof Bot }> = {
  running: { label: "running", className: "text-sky-500", Icon: CircleDashed },
  completed: { label: "completed", className: "text-emerald-500", Icon: CheckCircle2 },
  failed: { label: "failed", className: "text-red-500", Icon: XCircle },
  cancelled: { label: "cancelled", className: "text-muted-foreground", Icon: CircleSlash },
  timed_out: { label: "timed out", className: "text-amber-500", Icon: Clock },
};

function SubagentRow({ task, timing, now }: { task: SubagentTask; timing?: ToolTiming; now: number }) {
  const view = STATUS_VIEW[task.status];
  const Icon = view.Icon;
  const duration = formatDuration(timing, now);
  const label = task.description || "Subagent";
  const steps =
    task.messageIndex !== undefined
      ? task.totalMessages !== undefined
        ? `${task.messageIndex}/${task.totalMessages} steps`
        : `step ${task.messageIndex}`
      : null;
  const tokens = task.usage ? `${task.usage.total_tokens} tokens` : null;

  return (
    <div className="flex items-start gap-2 rounded-lg border border-border/60 bg-muted/30 px-2.5 py-1.5 text-xs" data-subagent-status={task.status}>
      <Icon className={`mt-0.5 size-3.5 shrink-0 ${view.className} ${task.status === "running" ? "animate-spin" : ""}`} aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate font-medium text-foreground/90" title={task.description}>
            {label}
          </span>
          <span className={`shrink-0 text-[10px] ${view.className}`}>{view.label}</span>
          {duration && <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">{duration}</span>}
        </div>

        {task.message && (
          <div className="truncate text-[11px] text-muted-foreground" title={task.message}>
            {task.message}
          </div>
        )}

        {(steps || tokens || task.error) && (
          <div className="mt-0.5 flex items-center gap-2 text-[10px] text-muted-foreground">
            {steps && <span className="shrink-0 tabular-nums">{steps}</span>}
            {tokens && <span className="shrink-0 tabular-nums">{tokens}</span>}
            {task.error && (
              <span className="min-w-0 truncate text-red-500" title={task.error}>
                {task.error}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
