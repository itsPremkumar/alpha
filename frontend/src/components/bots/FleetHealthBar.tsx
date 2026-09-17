"use client";

import React from "react";
import { FleetHealth } from "@/types/bots";
import { Bot, CheckCircle2, PauseCircle, Star, ListChecks } from "lucide-react";

export function FleetHealthBar({ health }: { health: FleetHealth }) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
      <div className="rounded-xl border border-border/60 bg-card px-3 py-2.5 flex items-center gap-2.5">
        <div className="size-8 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
          <Bot className="size-4" />
        </div>
        <div>
          <div className="text-sm font-bold leading-none">{health.total}</div>
          <div className="text-[10px] text-muted-foreground mt-1">Total bots</div>
        </div>
      </div>
      <div className="rounded-xl border border-border/60 bg-card px-3 py-2.5 flex items-center gap-2.5">
        <div className="size-8 rounded-lg bg-emerald-500/10 text-emerald-600 flex items-center justify-center">
          <CheckCircle2 className="size-4" />
        </div>
        <div>
          <div className="text-sm font-bold leading-none">{health.active}</div>
          <div className="text-[10px] text-muted-foreground mt-1">Active</div>
        </div>
      </div>
      <div className="rounded-xl border border-border/60 bg-card px-3 py-2.5 flex items-center gap-2.5">
        <div className="size-8 rounded-lg bg-amber-500/10 text-amber-600 flex items-center justify-center">
          <PauseCircle className="size-4" />
        </div>
        <div>
          <div className="text-sm font-bold leading-none">{health.paused}</div>
          <div className="text-[10px] text-muted-foreground mt-1">Paused</div>
        </div>
      </div>
      <div className="rounded-xl border border-border/60 bg-card px-3 py-2.5 flex items-center gap-2.5">
        <div className="size-8 rounded-lg bg-amber-500/10 text-amber-600 flex items-center justify-center">
          <Star className="size-4" />
        </div>
        <div>
          <div className="text-sm font-bold leading-none">{health.avg_reputation.toFixed(2)}</div>
          <div className="text-[10px] text-muted-foreground mt-1">Avg reputation</div>
        </div>
      </div>
      <div className="rounded-xl border border-border/60 bg-card px-3 py-2.5 flex items-center gap-2.5 col-span-2 sm:col-span-1">
        <div className="size-8 rounded-lg bg-secondary text-foreground flex items-center justify-center">
          <ListChecks className="size-4" />
        </div>
        <div>
          <div className="text-sm font-bold leading-none">{health.total_tasks}</div>
          <div className="text-[10px] text-muted-foreground mt-1">Tasks done</div>
        </div>
      </div>
    </div>
  );
}
