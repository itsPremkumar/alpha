"use client";

/**
 * The swarm board — how parallel delegation is shown.
 *
 * When a lead agent delegates to two or more subagents
 * at once, the flat row list hides the one thing that
 * matters: these ran *together*. The board lays the live
 * tasks out as parallel columns over a shared spine, so
 * a wave of work reads as a wave — the visualization
 * agent dashboards (agent-swarm, AgentGUI, Claude Swarm)
 * use — while each column keeps its honest status.
 *
 * Only tasks the run reported are shown. A single task
 * renders no board (the SubagentCard already covers it);
 * an empty list renders nothing at all.
 */

import React from "react";
import {
  Bot,
  CheckCircle2,
  CircleDashed,
  CircleSlash,
  Clock,
  Network,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import type { SubagentTask, SubagentTaskStatus } from "@/lib/sse-reducer";

interface SwarmBoardProps {
  tasks: SubagentTask[];
}

const STATUS_VIEW: Record<
  SubagentTaskStatus,
  { label: string; className: string; Icon: LucideIcon }
> = {
  running: { label: "running", className: "text-sky-500", Icon: CircleDashed },
  completed: {
    label: "done",
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

export function SwarmBoard({ tasks }: SwarmBoardProps) {
  if (tasks.length < 2) return null;
  const running = tasks.filter((t) => t.status === "running").length;
  const done = tasks.filter((t) => t.status !== "running").length;

  return (
    <div
      className="my-2 rounded-lg border border-border/60 bg-muted/20 p-2.5"
      data-swarm-board
    >
      <div className="mb-2 flex items-center gap-1.5 px-1 text-[11px] font-medium text-muted-foreground">
        <Network className="size-3.5" aria-hidden="true" />
        <span>
          Swarm · {tasks.length} agents
          {running > 0 ? ` · ${running} running` : ""}
          {done > 0 ? ` · ${done} settled` : ""}
        </span>
      </div>

      {/* Spine + parallel columns */}
      <div className="relative">
        <div
          className="absolute left-2 top-0 h-full w-px bg-border/70"
          aria-hidden="true"
        />
        <div className="space-y-1.5">
          {tasks.map((task) => {
            const view = STATUS_VIEW[task.status];
            const Icon = view.Icon;
            return (
              <div
                key={task.id}
                className="relative flex items-center gap-2 pl-5"
                data-swarm-status={task.status}
              >
                {/* Node on the spine */}
                <span
                  className={`absolute left-0 top-1/2 size-2 -translate-y-1/2 rounded-full border-2 border-background ${
                    task.status === "running"
                      ? "bg-sky-500 animate-pulse"
                      : task.status === "completed"
                        ? "bg-emerald-500"
                        : task.status === "failed"
                          ? "bg-red-500"
                          : "bg-muted-foreground/50"
                  }`}
                  aria-hidden="true"
                />
                <Icon
                  className={`size-3 shrink-0 ${view.className} ${task.status === "running" ? "animate-spin" : ""}`}
                  aria-hidden="true"
                />
                <Bot
                  className="size-3 shrink-0 text-muted-foreground/60"
                  aria-hidden="true"
                />
                <span
                  className="min-w-0 flex-1 truncate text-[11px] text-foreground/85"
                  title={task.description || "Subagent"}
                >
                  {task.description || "Subagent"}
                </span>
                {task.modelName && (
                  <span className="shrink-0 rounded-full border border-border/50 bg-background/50 px-1 py-px font-mono text-[9px] text-muted-foreground">
                    {task.modelName}
                  </span>
                )}
                <span
                  className={`shrink-0 text-[9px] font-medium ${view.className}`}
                >
                  {view.label}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
