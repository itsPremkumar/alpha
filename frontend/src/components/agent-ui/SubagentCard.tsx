"use client";

/**
 * The subagent card — how a delegated task is shown.
 *
 * One card per delegated subagent, folded from the run's
 * `task_*` custom events. It carries the task's own
 * description, the model the run reported for it (a badge,
 * absent when the run named none), the step position, the
 * latest streamed step text, the cumulative token spend,
 * and a client-observed duration. Everything is copied from
 * the event: an absent usage block renders "not reported",
 * never "0 tokens".
 *
 * The card expands to a small transcript of the steps the
 * subagent streamed — the nested view Claude Code and the
 * agent-debug panels expose — so delegation is inspectable
 * without leaving the chat.
 */

import React, { useEffect, useRef, useState } from "react";
import {
  Bot,
  CheckCircle2,
  CircleDashed,
  CircleSlash,
  ChevronDown,
  Clock,
  Cpu,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import type { SubagentTask, SubagentTaskStatus } from "@/lib/sse-reducer";
import type { ToolTiming } from "@/lib/activity";
import { formatDuration, openTiming, settleTiming } from "@/lib/activity";

interface SubagentCardProps {
  task: SubagentTask;
}

const STATUS_VIEW: Record<
  SubagentTaskStatus,
  { label: string; className: string; Icon: LucideIcon }
> = {
  running: { label: "running", className: "text-sky-500", Icon: CircleDashed },
  completed: {
    label: "completed",
    className: "text-emerald-500",
    Icon: CheckCircle2,
  },
  failed: { label: "failed", className: "text-red-500", Icon: XCircle },
  cancelled: {
    label: "cancelled",
    className: "text-muted-foreground",
    Icon: CircleSlash,
  },
  timed_out: { label: "timed out", className: "text-amber-500", Icon: Clock },
};

export function SubagentCard({ task }: SubagentCardProps) {
  const [open, setOpen] = useState(false);
  const [timing, setTiming] = useState<ToolTiming | undefined>(undefined);
  const [now, setNow] = useState(() => Date.now());

  // Client-observed duration, recorded here so replaying a frame
  // cannot move the timestamp. Referential-stability guard keeps
  // frames with no subagent news from re-rendering the card.
  useEffect(() => {
    setTiming((previous) => {
      const stamp = Date.now();
      if (!previous) return openTiming(stamp, task.status !== "running");
      if (previous.end === undefined && task.status !== "running")
        return settleTiming(previous, stamp);
      return previous;
    });
  }, [task.status]);

  const openWork =
    task.status === "running" &&
    timing?.end === undefined &&
    timing?.observedRunning;
  useEffect(() => {
    if (!openWork) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [openWork]);

  const view = STATUS_VIEW[task.status];
  const Icon = view.Icon;
  const duration = formatDuration(timing, now);
  const steps =
    task.messageIndex !== undefined
      ? task.totalMessages !== undefined
        ? `${task.messageIndex}/${task.totalMessages}`
        : `${task.messageIndex}`
      : null;
  const tokens = task.usage ? task.usage.total_tokens : null;

  return (
    <div
      className="my-1.5 overflow-hidden rounded-lg border border-border/70 bg-muted/30 text-xs"
      data-subagent-status={task.status}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-muted/60 transition-colors"
      >
        <Icon
          className={`mt-0.5 size-3.5 shrink-0 ${view.className} ${task.status === "running" ? "animate-spin" : ""}`}
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span
              className="truncate font-medium text-foreground/90"
              title={task.description || "Subagent"}
            >
              {task.description || "Subagent"}
            </span>
            {task.modelName && (
              <span
                className="inline-flex shrink-0 items-center gap-1 rounded-full border border-border/60 bg-background/60 px-1.5 py-px font-mono text-[9px] text-muted-foreground"
                title="Model the run reported for this subagent"
              >
                <Cpu className="size-2.5" aria-hidden="true" />
                {task.modelName}
              </span>
            )}
            <span
              className={`shrink-0 text-[10px] font-medium ${view.className}`}
            >
              {view.label}
            </span>
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-muted-foreground">
            {steps && (
              <span className="shrink-0 tabular-nums">step {steps}</span>
            )}
            {tokens !== null && (
              <span className="shrink-0 tabular-nums">{tokens} tokens</span>
            )}
            {duration && (
              <span className="shrink-0 font-mono tabular-nums">
                {duration}
              </span>
            )}
            {task.error && (
              <span
                className="min-w-0 truncate text-red-500"
                title={task.error}
              >
                {task.error}
              </span>
            )}
          </div>
          {task.message && (
            <div
              className="mt-0.5 truncate text-[11px] text-muted-foreground"
              title={task.message}
            >
              {task.message}
            </div>
          )}
        </div>
        <ChevronDown
          className={`size-3.5 shrink-0 text-muted-foreground transition-transform ${open ? "" : "-rotate-90"}`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="border-t border-border/50 px-3 py-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Subagent transcript
          </div>
          {task.message ? (
            <div className="space-y-1">
              <div className="flex items-start gap-2 text-[11px]">
                <span className="mt-0.5 shrink-0 font-mono text-[9px] tabular-nums text-muted-foreground">
                  {task.messageIndex ?? "·"}
                </span>
                <span className="whitespace-pre-wrap break-words text-foreground/70">
                  {task.message}
                </span>
              </div>
            </div>
          ) : (
            <div className="text-[11px] text-muted-foreground">
              {task.status === "running"
                ? "Subagent started — no steps streamed yet"
                : "No step transcript reported"}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
