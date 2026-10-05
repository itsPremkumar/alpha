"use client";

import React, { useMemo, useState } from "react";
import { BotProfile, collidingBotLabels, botRosterLabel } from "@/types/bots";
import { uniqueDepartments, computeFleetHealth } from "@/lib/bots";
import { isRecent, PRESENCE_WINDOW_SECONDS } from "@/lib/time";
import { BotProfileCard } from "./BotProfileCard";
import { FleetHealthBar } from "./FleetHealthBar";
import { Search, RotateCcw } from "lucide-react";

interface BotGalleryProps {
  bots: BotProfile[];
  activeBotName: string | null;
  isLoading: boolean;
  onSelect: (bot: BotProfile) => void;
  onChat: (bot: BotProfile) => void;
  onRefresh: () => void;
}

export function BotGallery({ bots, activeBotName, isLoading, onSelect, onChat, onRefresh }: BotGalleryProps) {
  const [search, setSearch] = useState("");
  const [department, setDepartment] = useState<string>("all");
  const [status, setStatus] = useState<string>("all");

  const departments = useMemo(() => uniqueDepartments(bots), [bots]);
  const health = useMemo(() => computeFleetHealth(bots), [bots]);

  /**
   * "Working right now" is a display reading off `last_active`, not a liveness
   * verdict — `alpha.bots.health` owns healthy/stale/stalled/dead. The strip is
   * hidden when the window is empty so an idle fleet is not given a header that
   * says nothing.
   */
  const activeNow = useMemo(
    () => bots.filter((b) => isRecent(b.last_active, PRESENCE_WINDOW_SECONDS)),
    [bots],
  );
  const unreadTotal = useMemo(
    () => bots.reduce((sum, b) => sum + (typeof b.unread_count === "number" ? b.unread_count : 0), 0),
    [bots],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return bots.filter((b) => {
      if (department !== "all" && b.department !== department) return false;
      if (status !== "all" && b.status !== status) return false;
      if (!q) return true;
      const hay = `${b.name} ${b.display_name} ${b.role} ${b.department} ${(b.capabilities || []).join(" ")} ${(b.skills || []).join(" ")}`.toLowerCase();
      return hay.includes(q);
    });
  }, [bots, search, department, status]);

  /**
   * Which display labels collide in the FULL roster, not the filtered one.
   *
   * Deliberately `bots` and not `filtered`: a filter that hides five of the
   * eight "Data Engineer" cards would otherwise make the remaining three look
   * unique, and the qualifier would disappear as the user typed. The collision
   * is a property of the fleet, not of the current view.
   */
  const collidingLabels = useMemo(() => collidingBotLabels(bots), [bots]);

  return (
    <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-5 space-y-4 max-w-6xl w-full mx-auto">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-base font-semibold tracking-tight">Bot Team</h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            Browse every specialist profile, compare capabilities, and pick who answers in chat.
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1.5 rounded-lg border border-border hover:bg-muted"
        >
          <RotateCcw className="size-3.5" /> Refresh
        </button>
      </div>

      {/* The fleet strip must not render while the roster is still in flight.
          `computeFleetHealth([])` returns total/active/paused of 0, so gating
          only the card grid below left this strip asserting a *measured empty
          fleet* for the whole load. Measured here: the roster takes ~20s to
          arrive (it is fetched with `activity=true`, which costs a per-bot
          inbox read plus a secret scan), and for those 20s the header read
          "0 Total bots / 0 Active / 0 Paused" against a server that reports 57.

          That is the specific thing the client honesty rules forbid -- a count
          the server did not report is `null`, never `0`. The grid below
          already had a skeleton for exactly this reason; this strip did not,
          because it sat outside that branch. */}
      {isLoading ? (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2" aria-busy="true" aria-label="Fleet health loading">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="rounded-xl border border-border/60 bg-card px-3 py-2.5 animate-pulse">
              <div className="h-4 rounded bg-muted w-10" />
              <div className="h-2.5 rounded bg-muted w-16 mt-2" />
            </div>
          ))}
        </div>
      ) : (
        <FleetHealthBar health={health} />
      )}

      {activeNow.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap rounded-xl border border-border/60 bg-card px-3 py-2">
          <span className="text-[11px] font-medium text-muted-foreground inline-flex items-center gap-1.5">
            <span className="size-1.5 rounded-full bg-emerald-500" />
            Working now
          </span>
          {activeNow.map((b) => (
            <button
              key={b.name}
              type="button"
              onClick={() => onSelect(b)}
              title={`Open ${b.display_name || b.name}`}
              className="text-[11px] px-2 py-0.5 rounded-full border border-border/60 hover:border-primary/50 hover:bg-muted"
            >
              {b.display_name || b.name}
            </button>
          ))}
          {unreadTotal > 0 && (
            <span className="ml-auto text-[11px] text-muted-foreground">
              {unreadTotal} unread across the team
            </span>
          )}
        </div>
      )}

      <div className="flex flex-col sm:flex-row gap-2">
        <div className="relative flex-1">
          <Search className="size-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            /* A placeholder is not a label: it vanishes as soon as the field has
               content, and a screen reader may not announce it at all. Measured
               as `unlabelled_inputs: INPUT[text]` on this view. */
            aria-label="Search bots"
            placeholder="Search bots by name, role, skill, capability..."
            className="w-full bg-card border border-border/70 rounded-xl pl-8 pr-3 py-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40"
          />
        </div>
        <div className="flex gap-2">
          <select
            value={department}
            onChange={(e) => setDepartment(e.target.value)}
            aria-label="Filter by department"
            className="text-xs bg-card border border-border/70 rounded-xl px-2.5 py-2 font-medium cursor-pointer focus:outline-none focus:ring-1 focus:ring-primary/40"
          >
            <option value="all">All departments</option>
            {departments.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Filter by status"
            className="text-xs bg-card border border-border/70 rounded-xl px-2.5 py-2 font-medium cursor-pointer focus:outline-none focus:ring-1 focus:ring-primary/40"
          >
            <option value="all">Any status</option>
            <option value="active">Active</option>
            <option value="paused">Paused</option>
            <option value="disabled">Disabled</option>
          </select>
        </div>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="rounded-2xl border border-border/60 bg-card p-4 space-y-3 animate-pulse">
              <div className="flex gap-3">
                <div className="size-11 rounded-xl bg-muted" />
                <div className="flex-1 space-y-2">
                  <div className="h-3 rounded bg-muted w-2/3" />
                  <div className="h-2.5 rounded bg-muted w-1/2" />
                </div>
              </div>
              <div className="h-2.5 rounded bg-muted w-full" />
              <div className="h-2.5 rounded bg-muted w-3/4" />
            </div>
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-14 text-xs text-muted-foreground">
          No bots match these filters. Try clearing search or department.
        </div>
      ) : (
        <>
          <p className="text-[11px] text-muted-foreground">
            Showing {filtered.length} of {bots.length} bots
            {department !== "all" ? ` in ${department}` : ""}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 pb-6">
            {filtered.map((bot) => (
              <BotProfileCard
                key={bot.name}
                bot={bot}
                isActive={bot.name === activeBotName}
                onSelect={onSelect}
                onChat={onChat}
                rosterLabel={botRosterLabel(bot, collidingLabels)}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
