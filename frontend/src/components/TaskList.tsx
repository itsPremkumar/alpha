"use client";

/**
 * The live execution plan, as one reusable component.
 *
 * ## Why this exists
 *
 * `TodoBlock` was rendered but never fed: `ChatMessage.todos` had no writer
 * anywhere in `frontend/src`, so the plan the model wrote on the backend was
 * present in graph state and absent from every screen. Wiring the wire format
 * alone would have left the same dead component in place for the *next*
 * surface that needs a plan, so this is deliberately built as a primitive
 * rather than as the one chat-specific card it replaced.
 *
 * Three variants, because the same plan is shown at three scales:
 * - `panel` — the live panel above the composer. Densest; a run in flight.
 * - `inline` — the plan attached to a finished message in the transcript.
 * - `compact` — a single-line summary for sidebars and list rows.
 *
 * ## The rule this component exists to enforce
 *
 * A task list is an **observation, not a promise**. Everything rendered here
 * is a status the model actually reported. In particular:
 *
 * - There is no elapsed-time or tool-count derived "percentage". A plan that is
 *   1-of-3 done and one that is 9-of-10 done both render as a bar; the bar
 *   alone hides the difference between "almost finished" and "barely started",
 *   which is the thing a user watching a long task needs to know. So the
 *   breakdown is always spelled out next to the bar.
 * - `cancelled` is shown as its own outcome, struck through and labelled. The
 *   model is explicitly allowed to drop items that are no longer relevant; a
 *   UI that cannot say "dropped" keeps showing abandoned work as permanently
 *   pending, which reads to the user as a stuck agent.
 * - A truncated plan says so. `MAX_TODO_ITEMS` on the backend is a runaway
 *   guard, and presenting the first 200 of 340 items as the whole plan would
 *   be a quiet lie about what the model is doing.
 *
 * Every counter arrives pre-computed from `alpha.agents.todo_events`; nothing
 * here re-derives a count, because a second implementation is how two views of
 * one plan start disagreeing.
 */

import React, { useState } from "react";
import {
  Ban,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock,
  ListTodo,
  type LucideIcon,
} from "lucide-react";
import { useActivationKeys } from "@/lib/a11y";
import type { TodoItem, TodoStatus } from "@/types/chat";

/** Where the plan is shown. Affects density and which chrome is rendered. */
export type TaskListVariant = "panel" | "inline" | "compact";

export interface TaskListProps {
  /** The plan. An empty array with `reported` of 0 renders nothing. */
  todos: TodoItem[];
  /** Pre-computed counters. Omitted means "derive nothing", not "zero". */
  progress?: { total: number; completed: number; in_progress: number; pending: number; cancelled: number; settled: number };
  /** How many items the model actually sent, before any backend cap. */
  reported?: number;
  /** True when the backend clipped the list; the UI must say so. */
  truncated?: boolean;
  /**
   * False when no plan was ever reported for this run. A consumer that has
   * simply not received a plan yet must render nothing rather than an empty
   * checklist, which would read as "the agent made a plan of zero steps".
   */
  reportedAtAll?: boolean;
  /** Heading. Defaults suit each variant; override for a named surface. */
  title?: string;
  variant?: TaskListVariant;
  /** Initial disclosure state. `panel` opens on its own; `compact` never expands. */
  defaultOpen?: boolean;
  /** Hide the header row entirely, for a bare list in a dense layout. */
  hideHeader?: boolean;
  className?: string;
}

/** Per-status presentation. One table, so every variant colours identically. */
const STATUS_VIEW: Record<
  TodoStatus,
  { icon: LucideIcon; label: string; row: string; text: string; badge: string; spin?: boolean }
> = {
  completed: {
    icon: CheckCircle2,
    label: "Done",
    row: "opacity-70",
    text: "line-through text-muted-foreground",
    badge: "bg-success/10 text-success border-success/30",
  },
  in_progress: {
    icon: Clock,
    label: "In progress",
    row: "bg-info/5",
    text: "text-foreground font-medium",
    badge: "bg-info/10 text-info border-info/30",
    spin: true,
  },
  pending: {
    icon: Circle,
    label: "Pending",
    row: "",
    text: "text-foreground",
    badge: "bg-muted text-muted-foreground border-border",
  },
  cancelled: {
    icon: Ban,
    label: "Dropped",
    row: "opacity-60",
    text: "line-through text-muted-foreground/80",
    badge: "bg-warning/10 text-warning border-warning/30",
  },
};

/**
 * Bar width as a fraction of *settled* work, not `completed` alone.
 *
 * A dropped item is an outcome the run reached, so counting it as progress is
 * correct — but only alongside the spelled-out breakdown, because "settled" is
 * a different claim from "succeeded" and a user must be able to tell them
 * apart. The fraction is clipped to [0, 1] because a backend count that
 * disagrees with the item list would otherwise render an overfull bar.
 */
function settledFraction(progress: TaskListProps["progress"]): number {
  if (!progress || progress.total <= 0) return 0;
  return Math.max(0, Math.min(1, progress.settled / progress.total));
}

export function TaskList({
  todos,
  progress,
  reported,
  truncated,
  reportedAtAll = true,
  title,
  variant = "panel",
  defaultOpen,
  hideHeader = false,
  className = "",
}: TaskListProps) {
  const isCompact = variant === "compact";
  const [open, setOpen] = useState(defaultOpen ?? variant === "panel");
  const onToggle = useActivationKeys(() => setOpen((value) => !value));

  // Nothing was ever reported: render nothing at all. An empty checklist would
  // claim the agent made a zero-step plan, which is a different statement.
  if (!reportedAtAll && todos.length === 0) return null;

  const counts = progress ?? { total: todos.length, completed: 0, in_progress: 0, pending: 0, cancelled: 0, settled: 0 };
  const shown = reported ?? todos.length;
  const fraction = settledFraction(counts);

  const headline = title ?? (variant === "inline" ? "Plan" : "Execution Plan");

  if (isCompact) {
    return (
      <span className={`inline-flex items-center gap-1.5 text-[11px] text-muted-foreground ${className}`} data-testid="task-list-compact">
        <ListTodo className="size-3 shrink-0" aria-hidden="true" />
        <span className="font-mono">
          {counts.completed}/{counts.total || shown}
        </span>
        {counts.in_progress > 0 && <span className="text-info">· {counts.in_progress} running</span>}
        {counts.cancelled > 0 && <span className="text-warning">· {counts.cancelled} dropped</span>}
        <span className="sr-only">
          {counts.completed} of {counts.total} steps done, {counts.in_progress} in progress, {counts.pending} pending, {counts.cancelled} dropped.
        </span>
      </span>
    );
  }

  return (
    <section
      className={`overflow-hidden rounded-xl border border-border/80 bg-muted/20 ${className}`}
      aria-label={headline}
      data-testid="task-list"
      data-variant={variant}
    >
      {!hideHeader && (
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          onKeyDown={onToggle}
          aria-expanded={open}
          aria-controls={`task-list-body-${variant}`}
          className="flex w-full items-center justify-between gap-2 bg-muted/50 px-3 py-2 text-left font-medium text-foreground transition-colors hover:bg-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          <span className="flex min-w-0 items-center gap-2">
            {open ? <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" /> : <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />}
            <ListTodo className="size-4 shrink-0 text-primary" aria-hidden="true" />
            <span className="truncate">{headline}</span>
            <span className="shrink-0 rounded border border-border bg-background px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
              {counts.completed}/{counts.total || shown} done
            </span>
          </span>
          {/* The status breakdown is announced, not just drawn. A plan whose
              progress only exists as a bar width is invisible to a screen
              reader, and this list updates on its own during a run. */}
          <span className="sr-only" role="status" aria-live="polite">
            {counts.completed} of {counts.total} steps done. {counts.in_progress} in progress. {counts.pending} pending. {counts.cancelled} dropped.
          </span>
        </button>
      )}

      {open && (
        <div id={`task-list-body-${variant}`} className="border-t border-border/50">
          {/* A bar plus a breakdown, never a bare bar. See the file header. */}
          {variant === "panel" && counts.total > 0 && (
            <div className="px-3 pt-2.5">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted" role="presentation">
                <div
                  className="h-full rounded-full bg-success transition-[width] duration-300"
                  style={{ width: `${fraction * 100}%` }}
                  data-testid="task-list-bar"
                />
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
                <Count label="Done" value={counts.completed} className="text-success" />
                <Count label="Running" value={counts.in_progress} className="text-info" />
                <Count label="Pending" value={counts.pending} />
                <Count label="Dropped" value={counts.cancelled} className={counts.cancelled > 0 ? "text-warning" : undefined} />
              </div>
            </div>
          )}

          <ul className={`space-y-1 p-2.5 ${variant === "panel" ? "pt-2" : ""}`}>
            {todos.map((todo) => {
              const view = STATUS_VIEW[todo.status] ?? STATUS_VIEW.pending;
              const Icon = view.icon;
              return (
                <li
                  key={todo.id}
                  className={`flex items-start gap-2 rounded px-2 py-1 transition-colors hover:bg-muted/40 ${view.row}`}
                  data-status={todo.status}
                >
                  <Icon
                    className={`mt-0.5 size-3.5 shrink-0 ${view.spin ? "animate-spin text-info" : todo.status === "completed" ? "text-success" : todo.status === "cancelled" ? "text-warning" : "text-muted-foreground"}`}
                    aria-hidden="true"
                  />
                  <span className={`min-w-0 flex-1 break-words text-xs ${view.text}`}>
                    {todo.content}
                    {/* The status is in the accessible name, not only in the
                        colour and icon — a colour-only status is invisible to a
                        screen reader and ambiguous to a colourblind reader. */}
                    <span className="sr-only"> ({view.label})</span>
                  </span>
                  {variant === "panel" && (
                    <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${view.badge}`} aria-hidden="true">
                      {view.label}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>

          {truncated && shown > todos.length && (
            <p className="border-t border-border/50 px-3 py-1.5 text-[10px] text-warning" data-testid="task-list-truncated">
              Showing {todos.length} of {shown} steps — the model reported more than the view displays.
            </p>
          )}
          {todos.length === 0 && reportedAtAll && (
            <p className="px-3 py-2 text-[11px] text-muted-foreground">The model reported a plan of zero steps.</p>
          )}
        </div>
      )}
    </section>
  );
}

function Count({ label, value, className = "" }: { label: string; value: number; className?: string }) {
  if (!value) return null;
  return (
    <span className={className}>
      <span className="font-mono">{value}</span> {label.toLowerCase()}
    </span>
  );
}

/**
 * A single status pill. Exported so a list row, a run card, or a subagent header
 * can label one step without pulling in the whole list.
 */
export function TaskStatusBadge({ status }: { status: TodoStatus }) {
  const view = STATUS_VIEW[status] ?? STATUS_VIEW.pending;
  const Icon = view.icon;
  return (
    <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] ${view.badge}`}>
      <Icon className={`size-3 ${view.spin ? "animate-spin" : ""}`} aria-hidden="true" />
      {view.label}
    </span>
  );
}

/**
 * A one-line "3 of 7 done" summary for places with no room for a list —
 * a run row, a thread row, a workforce card.
 *
 * Renders nothing when no plan was reported, so a caller can drop it in
 * unconditionally instead of guarding every call site.
 */
export function TaskListSummary(props: Omit<TaskListProps, "variant">) {
  const has = (props.reportedAtAll ?? true) || props.todos.length > 0;
  return has ? <TaskList {...props} variant="compact" /> : null;
}

export default TaskList;