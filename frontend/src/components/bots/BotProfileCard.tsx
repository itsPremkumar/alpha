"use client";

import React from "react";
import Link from "next/link";
import { BotProfile, botDisplayName, botInitials } from "@/types/bots";
import { absoluteStamp, isRecent, PRESENCE_WINDOW_SECONDS, relTime } from "@/lib/time";
import { completedRuns, totalRuns } from "@/lib/bots";
import {
  MessageSquare,
  Star,
  CheckCircle2,
  PauseCircle,
  XCircle,
  Building2,
  Mail,
  ExternalLink,
} from "lucide-react";

/**
 * Presence window: a bot counts as working when it was last seen inside this.
 * Matches the 90s active-now reading the roster is compared against; it is a
 * *display* threshold, not a liveness verdict — `alpha.bots.health` owns the
 * real healthy/stale/stalled/dead classification.
 */
const ACTIVE_WINDOW_SECONDS = PRESENCE_WINDOW_SECONDS;

interface BotProfileCardProps {
  bot: BotProfile;
  isActive: boolean;
  onSelect: (bot: BotProfile) => void;
  onChat: (bot: BotProfile) => void;
  /**
   * The headline, optionally disambiguated by the roster owner.
   *
   * Optional so every other caller (the detail panel, the dropdowns, omnisearch)
   * keeps the server's own `display_name` verbatim - a qualifier it cannot know
   * is needed would be an invented distinction. `BotGallery` is the surface that
   * renders the whole fleet at once, so it is the only one that can see a
   * collision and the only one that passes this.
   */
  rosterLabel?: string;
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

export function BotProfileCard({ bot, isActive, onSelect, onChat, rosterLabel }: BotProfileCardProps) {
  // Measured counters only. `total`/`succeeded` were never fields the Gateway
  // sends (it sends `total_runs`/`completed`), so this pair was permanently
  // `0`/`0` and the card claimed "0 tasks" for every bot on every render. An
  // unreported counter now reads as unreported rather than as zero work.
  const total = totalRuns(bot);
  const succeeded = completedRuns(bot);
  const successRate = total !== null && total > 0 && succeeded !== null
    ? Math.round((succeeded / total) * 100)
    : null;
  const working = isRecent(bot.last_active, ACTIVE_WINDOW_SECONDS);
  const lastSeen = relTime(bot.last_active);
  const lastSeenFull = absoluteStamp(bot.last_active);

  // Activity is a projection nobody may have requested: `unread_count === null`
  // means the server was not asked, which is a different claim from "0 unread".
  const activityRequested = bot.unread_count !== null && bot.unread_count !== undefined;
  const unread = activityRequested ? (bot.unread_count ?? 0) : 0;
  const withheld = bot.last_message_withheld === true;
  const messageAt = relTime(bot.last_message_at);
  const messageFull = absoluteStamp(bot.last_message_at);

  return (
    <div
      className={`group rounded-2xl border bg-card p-4 flex flex-col gap-3 transition-all hover:elev-2 cursor-pointer ${
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
            <h3 className="text-sm font-semibold truncate" title={rosterLabel ?? botDisplayName(bot)}>
              {rosterLabel ?? botDisplayName(bot)}
            </h3>
            {/* Presence is derived from the server's own `last_active`; a bot
                with no recorded activity never reads as working. */}
            <span
              className={`size-1.5 rounded-full shrink-0 ${working ? "bg-emerald-500" : "bg-border"}`}
              title={working ? "Seen working within the last 90 seconds" : lastSeenFull ? `Last seen ${lastSeenFull}` : "No activity recorded"}
            />
            {isActive && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary text-primary-foreground font-semibold">
                IN CHAT
              </span>
            )}
            {unread > 0 && (
              <span
                className="ml-auto text-[10px] min-w-4 px-1.5 py-0.5 rounded-full bg-primary text-primary-foreground font-semibold text-center"
                title={`${unread} unread message${unread === 1 ? "" : "s"}`}
              >
                {unread}
              </span>
            )}
          </div>
          <p className="text-[11px] text-muted-foreground truncate">{bot.role}</p>
          <div className="flex items-center gap-2 mt-1.5">
            {statusBadge(bot.status)}
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground font-medium">
              {bot.department}
            </span>
            <span
              className="text-[10px] text-muted-foreground truncate"
              title={lastSeenFull ?? "The Gateway has recorded no activity for this bot yet"}
            >
              {lastSeen ?? "no activity recorded"}
            </span>
          </div>
        </div>
      </div>

      {activityRequested && (
        <div
          className="flex items-start gap-1.5 text-[11px] text-muted-foreground min-w-0"
          title={messageFull ?? undefined}
        >
          <Mail className="size-3 mt-0.5 shrink-0 opacity-60" />
          {withheld ? (
            <span className="truncate italic">
              Last message withheld — the gateway found credential-shaped content
            </span>
          ) : bot.last_message_preview ? (
            <span className="truncate">
              <span className="font-medium text-foreground/80">{bot.last_message_sender ?? "unknown"}</span>
              {messageAt ? ` · ${messageAt}` : ""}: {bot.last_message_preview}
            </span>
          ) : (
            <span className="truncate italic">No messages yet</span>
          )}
        </div>
      )}

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
        <span>{total !== null ? `${total} tasks` : "tasks not measured"}</span>
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
          {/* Full detail is a real route, not a modal: `/bots/<name>` is
              shareable and survives a reload. `encodeURIComponent` matters —
              a bot name with a slash or a space would otherwise build a broken
              or unintended path. The card's own onClick opens the *editor*
              panel, so this must stopPropagation to be its own action. */}
          <Link
            href={`/bots/${encodeURIComponent(bot.name)}`}
            onClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-border text-[11px] font-semibold hover:bg-muted"
            title={`Open the full detail page for ${botDisplayName(bot)}`}
          >
            <ExternalLink className="size-3" /> Details
          </Link>
        </div>
      </div>
    </div>
  );
}
