"use client";

/**
 * The delegation tree — how *nested* delegation is shown (phase 3.4).
 *
 * The flat card list hides the one thing that matters once a subagent
 * delegates again: who is under whom. This renders the tree the run
 * reported, and nothing more:
 *
 * - an edge is drawn only between task ids this stream saw; a task whose
 *   `parent_id` named an unseen id renders at the top level with the count
 *   disclosed, never attached to a guessed parent;
 * - a task with no reported parent is a root;
 * - every status label is the task's own reported status, verbatim, and the
 *   indentation is layout geometry — the server-reported depth stays on the
 *   task and is never re-derived or overwritten here.
 *
 * Only a run with real nesting renders this; the flat `SubagentList` remains
 * the surface for ordinary delegation.
 */
import React from "react";
import {
  CheckCircle2,
  CircleDashed,
  CircleSlash,
  Clock,
  GitBranch,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { buildDelegationGraph, type DelegationNode } from "@/lib/delegation-graph";
import type { SubagentTask, SubagentTaskStatus } from "@/lib/sse-reducer";

interface DelegationGraphProps {
  tasks: SubagentTask[];
}

const STATUS_VIEW: Record<
  SubagentTaskStatus,
  { label: string; className: string; Icon: LucideIcon }
> = {
  running: { label: "running", className: "text-sky-500", Icon: CircleDashed },
  completed: { label: "done", className: "text-emerald-500", Icon: CheckCircle2 },
  failed: { label: "failed", className: "text-red-500", Icon: XCircle },
  cancelled: { label: "cancelled", className: "text-muted-foreground", Icon: CircleSlash },
  timed_out: { label: "timed out", className: "text-amber-500", Icon: Clock },
};

function nodeLabel(task: SubagentTask): string {
  return task.description ?? task.subagentType ?? task.agentName ?? task.id;
}

function TreeNode({ node }: { node: DelegationNode }) {
  const view = STATUS_VIEW[node.task.status];
  return (
    <div>
      <div
        className="flex items-center gap-1.5 rounded-md px-1.5 py-1"
        style={{ marginLeft: (node.depth - 1) * 14 }}
      >
        {node.depth > 1 ? (
          <GitBranch className="size-3 shrink-0 text-muted-foreground/60" aria-hidden="true" />
        ) : null}
        <view.Icon className={`size-3.5 shrink-0 ${view.className}`} aria-hidden="true" />
        <span className="truncate text-[11px] text-foreground/90">{nodeLabel(node.task)}</span>
        {node.task.subagentType ? (
          <span className="shrink-0 rounded bg-muted px-1 py-px text-[10px] text-muted-foreground">
            {node.task.subagentType}
          </span>
        ) : null}
        <span className={`shrink-0 text-[10px] ${view.className}`}>{view.label}</span>
      </div>
      {node.children.map((child) => (
        <TreeNode key={child.task.id} node={child} />
      ))}
    </div>
  );
}

export function DelegationGraph({ tasks }: DelegationGraphProps) {
  const graph = buildDelegationGraph(tasks);
  if (!graph.roots.some((node) => node.children.length > 0)) return null;

  return (
    <div className="my-2 rounded-lg border border-border/60 bg-muted/20 p-2.5" data-delegation-graph>
      <div className="mb-1.5 flex items-center gap-1.5 px-1 text-[11px] font-medium text-muted-foreground">
        <GitBranch className="size-3.5" aria-hidden="true" />
        <span>Delegation tree · {tasks.length} agents</span>
      </div>
      {graph.roots.map((node) => (
        <TreeNode key={node.task.id} node={node} />
      ))}
      {/* Disclosures: a render that silently flattened a broken edge would
          claim a tree the run did not report. */}
      {graph.unseenParents > 0 ? (
        <p className="mt-1.5 px-1 text-[10px] text-amber-500">
          {graph.unseenParents} task{graph.unseenParents === 1 ? "" : "s"} reference a parent not
          seen in this stream — shown at the top level.
        </p>
      ) : null}
      {graph.cycleDetached > 0 ? (
        <p className="mt-1.5 px-1 text-[10px] text-amber-500">
          {graph.cycleDetached} task{graph.cycleDetached === 1 ? "" : "s"} detached: the reported
          parent chain formed a cycle — shown at the top level.
        </p>
      ) : null}
    </div>
  );
}
