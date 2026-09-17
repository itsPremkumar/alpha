"use client";

import React, { useEffect, useRef, useState } from "react";
import { BotProfile, botDisplayName, botInitials } from "@/types/bots";
import { uniqueDepartments } from "@/lib/bots";
import { ChevronDown, Bot, Search, Check, Sparkles } from "lucide-react";

interface ActiveBotPickerProps {
  bots: BotProfile[];
  activeBot: BotProfile | null;
  onPick: (bot: BotProfile | null) => void;
}

/**
 * Bot-first organization dropdown: search across all bots, grouped by
 * department with live status. Picking a bot scopes history + projects to it.
 */
export function ActiveBotPicker({ bots, activeBot, onPick }: ActiveBotPickerProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  useEffect(() => {
    if (open) {
      setSearch("");
      window.setTimeout(() => searchRef.current?.focus(), 30);
    }
  }, [open ]);

  const q = search.trim().toLowerCase();
  const filtered = bots.filter((b) => {
    if (!q) return true;
    return `${b.name} ${b.display_name} ${b.role} ${b.department} ${(b.capabilities || []).join(" ")}`.toLowerCase().includes(q);
  });
  const departments = uniqueDepartments(filtered);

  const statusDot = (s: string) =>
    s === "active" ? "bg-emerald-500" : s === "paused" ? "bg-amber-500" : "bg-muted-foreground";

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Choose who answers — each bot keeps its own history and projects"
        className="flex items-center gap-2 text-xs bg-muted/60 border border-border/80 rounded-lg pl-1.5 pr-2 py-1 font-medium hover:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/40 max-w-56"
      >
        <span className="size-6 rounded-md bg-primary/10 text-primary flex items-center justify-center text-xs font-bold overflow-hidden shrink-0">
          {activeBot ? (
            activeBot.avatar ? <span>{activeBot.avatar}</span> : <span className="text-[10px]">{botInitials(activeBot)}</span>
          ) : (
            <Bot className="size-3.5" />
          )}
        </span>
        <span className="truncate">{activeBot ? botDisplayName(activeBot) : "Lead Agent"}</span>
        <span className="text-[10px] text-muted-foreground hidden sm:inline">{bots.length} bots</span>
        <ChevronDown className={`size-3.5 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute right-0 mt-1.5 w-80 max-w-[90vw] rounded-2xl border border-border bg-card shadow-2xl z-50 overflow-hidden" role="listbox" aria-label="Choose a bot">
          <div className="p-2 border-b border-border/60">
            <div className="relative">
              <Search className="size-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                ref={searchRef}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && filtered.length > 0) {
                    onPick(filtered[0]);
                    setOpen(false);
                  }
                  if (e.key === "Escape") setOpen(false);
                }}
                placeholder={`Search ${bots.length} bots…`}
                aria-label="Search bots"
                className="w-full bg-muted/50 border border-border/60 rounded-xl pl-8 pr-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40"
              />
            </div>
          </div>

          <div className="max-h-80 overflow-y-auto p-1.5">
            {/* Lead agent first */}
            <button
              type="button"
              role="option"
              aria-selected={!activeBot}
              onClick={() => {
                onPick(null);
                setOpen(false);
              }}
              className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-xl text-left hover:bg-muted/60 ${!activeBot ? "bg-primary/10" : ""}`}
            >
              <span className="size-8 rounded-lg bg-secondary flex items-center justify-center shrink-0">
                <Sparkles className="size-4 text-primary" />
              </span>
              <span className="flex-1 min-w-0">
                <span className="block text-xs font-semibold">Lead Agent</span>
                <span className="block text-[11px] text-muted-foreground truncate">Auto-routes • sees all conversations</span>
              </span>
              {!activeBot && <Check className="size-4 text-primary shrink-0" />}
            </button>

            {departments.map((dept) => (
              <div key={dept}>
                <p className="px-2.5 pt-2 pb-1 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                  {dept} ({filtered.filter((b) => (b.department || "general") === dept).length})
                </p>
                {filtered
                  .filter((b) => (b.department || "general") === dept)
                  .map((b) => {
                    const selected = activeBot?.name === b.name;
                    const total = Number(b.task_stats?.total) || 0;
                    return (
                      <button
                        key={b.name}
                        type="button"
                        role="option"
                        aria-selected={selected}
                        onClick={() => {
                          onPick(b);
                          setOpen(false);
                        }}
                        className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-xl text-left hover:bg-muted/60 ${selected ? "bg-primary/10" : ""}`}
                        title={`${b.role} • reputation ${(b.reputation_score ?? 0).toFixed(2)} • ${total} tasks`}
                      >
                        <span className="relative size-8 rounded-lg bg-primary/10 text-primary flex items-center justify-center text-sm font-bold shrink-0 overflow-hidden">
                          {b.avatar ? <span>{b.avatar}</span> : <span className="text-[10px]">{botInitials(b)}</span>}
                          <span className={`absolute -bottom-0.5 -right-0.5 size-2.5 rounded-full border-2 border-card ${statusDot(b.status)}`} />
                        </span>
                        <span className="flex-1 min-w-0">
                          <span className="block text-xs font-semibold truncate">{botDisplayName(b)}</span>
                          <span className="block text-[11px] text-muted-foreground truncate">{b.role}</span>
                        </span>
                        {total > 0 && <span className="text-[10px] font-mono text-muted-foreground shrink-0">{total}</span>}
                        {selected && <Check className="size-4 text-primary shrink-0" />}
                      </button>
                    );
                  })}
              </div>
            ))}

            {filtered.length === 0 && (
              <p className="text-center text-[11px] text-muted-foreground py-6">No bots match “{search}”.</p>
            )}
          </div>

          <p className="px-3 py-2 text-[10px] text-muted-foreground border-t border-border/60">
            Each bot keeps separate history & projects. Shared projects show every bot's part.
          </p>
        </div>
      )}
    </div>
  );
}
