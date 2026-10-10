"use client";

import React from "react";
import {
  Activity,
  AlertTriangle,
  Circle,
  Moon,
  Pause,
  type LucideIcon,
} from "lucide-react";
import type { WorkingStatus, WorkingTone } from "@/lib/bot-working-status";

/**
 * The badge and the detail line that render a `WorkingStatus`.
 *
 * They exist as one pair rather than two inline JSX branches inside
 * `BotProfileCard` because this project's honesty rules make the *sentence* and
 * the *word* one claim: a card showing "Working" over "no heartbeat for over
 * five minutes" would be a badge arguing with its own tooltip. Everything about
 * the status — icon, colour, label, detail — comes from
 * `lib/bot-working-status.ts`, so there is exactly one place where "working"
 * gets its tone and text.
 */

const TONE: Record<WorkingTone, string> = {
  good: "bg-emerald-500/10 text-emerald-600",
  warn: "bg-amber-500/10 text-amber-600",
  bad: "bg-destructive/10 text-destructive",
  muted: "bg-muted text-muted-foreground",
};

const ICON: Record<string, LucideIcon> = {
  working: Activity,
  idle: Circle,
  sleeping: Moon,
  suspended: Circle,
  archived: Circle,
  stalled: AlertTriangle,
  dead: AlertTriangle,
  paused: Pause,
  unclassified: Circle,
  unknown: Circle,
};

function iconFor(status: WorkingStatus): LucideIcon {
  return ICON[status.key] ?? Circle;
}

/**
 * The short badge. The full sentence is on `title`, so a truncated narrow card
 * still carries the evidence.
 */
export function WorkingStatusBadge({
  status,
}: {
  status: WorkingStatus | null | undefined;
}) {
  if (!status) return null;
  const Icon = iconFor(status);
  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full ${TONE[status.tone]}`}
      title={status.detail}
      data-working-key={status.key}
    >
      <Icon className="size-3" aria-hidden="true" />
      {status.label}
    </span>
  );
}

/**
 * The detail line: what was measured, and the number behind it.
 *
 * Rendered rather than only tooltipped because the whole point of the liveness
 * engine is the detail — "working on task run_42 with a heartbeat 12s ago"
 * answers a question a green dot cannot.
 */
export function WorkingStatusDetail({
  status,
}: {
  status: WorkingStatus | null | undefined;
}) {
  if (!status) return null;
  const Icon = iconFor(status);
  return (
    <div
      className="flex items-start gap-1.5 text-[11px] text-muted-foreground min-w-0"
      title={status.detail}
    >
      <Icon className="size-3 mt-0.5 shrink-0 opacity-60" aria-hidden="true" />
      <span className="truncate" data-working-detail={status.key}>
        {status.detail}
      </span>
    </div>
  );
}
