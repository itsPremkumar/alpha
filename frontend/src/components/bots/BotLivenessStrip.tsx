"use client";

import React from "react";
import {
  Activity,
  AlertTriangle,
  Bot,
  Gauge,
  Moon,
  TimerOff,
  TriangleAlert,
} from "lucide-react";
import { ErrorBox } from "@/components/ui";
import type { FleetHealthSummary } from "@/lib/bot-working-status";

/**
 * The fleet liveness strip: the server's own verdict about each bot, summed.
 *
 * This is a *different strip* from `FleetHealthBar` and they must not merge.
 * `FleetHealthBar` counts registry fields on the roster the client already has
 * (total / active / paused / reputation / tasks); this one reports what
 * `alpha.bots.health` measures — heartbeats, leases, stalls. A fleet where
 * every bot's registry `status` says "active" can still be entirely stalled,
 * and only the second strip can say so.
 *
 * Three states, three renderings, and the strip owns all of them:
 * loading → an `aria-busy` skeleton with no number in it; failed → the server's
 * own reason with a retry; ok → the counters, each `—` with a labelled reason
 * when the server sent none. A count the server did not report is never `0`.
 */

export interface BotLivenessStripProps {
  state: "loading" | "error" | "ok";
  overview?: {
    summary: FleetHealthSummary;
    fleet_health_score: number | null;
    timestamp: string | null;
    stalled_workers: Array<{
      bot_name: string | null;
      active_task_id: string | null;
    }>;
  } | null;
  reason?: string;
  onRetry?: () => void;
  /**
   * How many bots have a run in flight right now, from the run store.
   *
   * `"loading"` / `"unavailable"` are states, not numbers: a read that has not
   * landed and a read that failed are neither of them "0 bots working", which
   * would claim the store measured an idle fleet.
   */
  workingCount?: number | null | "loading" | "unavailable";
  workingReason?: string | null;
}

/** One count cell. `value === null` renders a dash plus the words, never 0. */
function Count(props: {
  label: string;
  value: number | null;
  icon: React.ReactNode;
  tone?: string;
}) {
  const reported = props.value !== null;
  return (
    <div className="rounded-xl border border-border/60 bg-card px-3 py-2.5 flex items-center gap-2.5">
      <div
        className={`size-8 rounded-lg flex items-center justify-center ${props.tone ?? "bg-muted text-foreground"}`}
      >
        {props.icon}
      </div>
      <div className="min-w-0">
        <div className="text-sm font-bold leading-none">
          {reported ? (
            props.value
          ) : (
            <span title="The Gateway did not report this counter">—</span>
          )}
        </div>
        <div className="text-[10px] text-muted-foreground mt-1 truncate">
          {reported ? props.label : `${props.label} — not reported`}
        </div>
      </div>
    </div>
  );
}

export function BotLivenessStrip({
  state,
  overview,
  reason,
  onRetry,
  workingCount,
  workingReason,
}: BotLivenessStripProps) {
  if (state === "loading") {
    return (
      <div
        className="grid grid-cols-2 sm:grid-cols-6 gap-2"
        aria-busy="true"
        aria-label="Fleet liveness report loading"
      >
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="rounded-xl border border-border/60 bg-card px-3 py-2.5 animate-pulse"
          >
            <div className="h-4 rounded bg-muted w-10" />
            <div className="h-2.5 rounded bg-muted w-16 mt-2" />
          </div>
        ))}
      </div>
    );
  }

  if (state === "error" || !overview) {
    return (
      <ErrorBox
        message={`Liveness report unavailable — ${reason ?? "the Gateway did not answer"}. Cards below show their presence reading only.`}
        onRetry={onRetry}
      />
    );
  }

  const s = overview.summary;
  const stalledNames = overview.stalled_workers
    .map((w) => w.bot_name)
    .filter((n): n is string => typeof n === "string" && n.length > 0);

  return (
    <div className="space-y-2" data-liveness-strip="">
      <div className="grid grid-cols-2 sm:grid-cols-6 gap-2">
        <Count
          label="Healthy"
          value={s.healthy}
          icon={<Activity className="size-4" />}
          tone="bg-emerald-500/10 text-emerald-600"
        />
        <Count
          label="Stale"
          value={s.stale}
          icon={<TimerOff className="size-4" />}
          tone="bg-amber-500/10 text-amber-600"
        />
        <Count
          label="Stalled"
          value={s.stalled}
          icon={<TriangleAlert className="size-4" />}
          tone="bg-destructive/10 text-destructive"
        />
        <Count
          label="No heartbeat"
          value={s.dead}
          icon={<AlertTriangle className="size-4" />}
          tone="bg-muted text-muted-foreground"
        />
        <Count
          label="Sleeping"
          value={s.sleeping}
          icon={<Moon className="size-4" />}
          tone="bg-muted text-muted-foreground"
        />
        <Count
          label="Bots seen"
          value={s.total}
          icon={<Bot className="size-4" />}
          tone="bg-primary/10 text-primary"
        />
      </div>
      <div className="flex items-center gap-2 flex-wrap text-[11px] text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <Gauge className="size-3.5 opacity-60" />
          Fleet health score{" "}
          {overview.fleet_health_score !== null ? (
            <span className="font-semibold text-foreground">
              {overview.fleet_health_score.toFixed(2)}
            </span>
          ) : (
            <span title="The Gateway did not report a fleet health score">
              not reported
            </span>
          )}
        </span>
        {overview.timestamp && <span>· measured {overview.timestamp}</span>}
        {stalledNames.length > 0 && (
          <span className="text-destructive">
            · stalled: {stalledNames.join(", ")}
          </span>
        )}
        {/* The run-store reading, in words when it is absent. A bot with a run
            in flight is the one thing this strip can name rather than infer, so
            its absence has to be visible too. */}
        {workingCount !== undefined && (
          <span data-working-count={String(workingCount)}>
            · bots with a run in flight{" "}
            {typeof workingCount === "number" ? (
              <span className="font-semibold text-foreground">{workingCount}</span>
            ) : workingCount === "loading" ? (
              <span title="The run-store read has not landed yet">still reading</span>
            ) : (
              <span title={workingReason ?? "The Gateway did not report a run-store reading"}>
                not reported
              </span>
            )}
          </span>
        )}
      </div>
    </div>
  );
}
