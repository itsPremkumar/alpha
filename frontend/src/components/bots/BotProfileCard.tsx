"use client";

import React from "react";
import { BotProfile, botDisplayName, botInitials } from "@/types/bots";
import { MessageSquare, Star, CheckCircle2, PauseCircle, XCircle, Building2 } from "lucide-react";

interface BotProfileCardProps {
  bot: BotProfile;
  isActive: boolean;
  onSelect: (bot: BotProfile) => void;
  onChat: (bot: BotProfile) => void;
}

function statusBadge(status: string) {
  if (status === "active")
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600">
        <CheckCircle2 className="size-3" /> Active
      </span>
    );
  if (status === "paused")
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-600">
        <PauseCircle className="size-3" /> Paused
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-muted text-muted-foreground">
      <XCircle className="size-3" /> {status}
    </span>
  );
}

export function BotProfileCard({ bot, isActive, onSelect, onChat }: BotProfileCardProps) {
  const total = Number(bot.task_stats?.total) || 0;
  const succeeded = Number(bot.task_stats?.succeeded) || 0;
  const successRate = total > 0 ? Math.round((succeeded / total) * 100) : null;

  return (
    <div
      className={`group rounded-2xl border bg-card p-4 flex flex-col gap-3 transition-all hover:shadow-md cursor-pointer ${
        isActive ? "border-primary ring-1 ring-primary/40" : "border-border/60 hover:border-primary/40"
      }`}
      onClick={() => onSelect(bot)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter") onSelect(bot);
      }}
    >
      <div className="flex items-start gap-3">
        <div className="size-11 rounded-xl bg-primary/10 text-primary flex items-center justify-center text-lg font-bold shrink-0 overflow-hidden">
          {bot.avatar ? <span>{bot.avatar}</span> : <span>{botInitials(bot)}</span>}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold truncate">{botDisplayName(bot)}</h3>
            {isActive && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary text-primary-foreground font-semibold">
                IN CHAT
              </span>
            )}
          </div>
          <p className="text-[11px] text-muted-foreground truncate">{bot.role}</p>
          <div className="flex items-center gap-2 mt-1.5">
            {statusBadge(bot.status)}
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground font-medium">
              {bot.department}
            </span>
          </div>
        </div>
      </div>

      {bot.capabilities.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {bot.capabilities.slice(0, 4).map((c) => (
            <span
              key={c}
              className="text-[10px] px-2 py-0.5 rounded-md bg-secondary text-secondary-foreground font-mono"
            >
              {c}
            </span>
          ))}
          {bot.capabilities.length > 4 && (
            <span className="text-[10px] px-2 py-0.5 rounded-md bg-muted text-muted-foreground">
              +{bot.capabilities.length - 4}
            </span>
          )}
        </div>
      )}

      <div className="flex items-center justify-between gap-2 flex-wrap text-[11px] text-muted-foreground border-t border-border/50 pt-2.5 mt-auto">
        <span className="inline-flex items-center gap-1">
          <Star className="size-3.5 text-amber-500" />
          {bot.reputation_score != null ? bot.reputation_score.toFixed(2) : "unverified"}
          {successRate !== null && <span className="ml-1">• {successRate}% ok</span>}
        </span>
        <span>{total} tasks</span>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onSelect(bot);
            }}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-border text-[11px] font-semibold hover:bg-muted"
            title={`Create a project led by ${botDisplayName(bot)}`}
          >
            <Building2 className="size-3" /> Project
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onChat(bot);
            }}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-primary text-primary-foreground text-[11px] font-semibold hover:opacity-90"
        >
          <MessageSquare className="size-3" /> Chat
        </button>
        </div>
      </div>
    </div>
  );
}
