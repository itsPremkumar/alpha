"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  Bot,
  ChevronDown,
  Code2,
  Folder,
  FolderPlus,
  MessageSquare,
  Plus,
  Search,
  Settings,
  ShieldCheck,
  CheckCircle2,
  Palette,
  Megaphone,
  BarChart3,
  Sparkles,
  ExternalLink,
} from "lucide-react";
import { BotProfile, botDisplayName, botInitials } from "@/types/bots";
import { botPresence, PresenceView } from "@/lib/chat-shell";
import { PresenceDot } from "./Honest";
import type { WorkspaceView } from "@/lib/workspace-view";

export interface BotDropdownMenuProps {
  bots: BotProfile[];
  activeBot: BotProfile | null;
  onSelectBot: (bot: BotProfile | null) => void;
  onNewConversation: () => void;
  onNewProject: () => void;
  onViewConversations?: () => void;
  onViewProjects?: () => void;
  onOpenSettings?: () => void;
  onOpenView?: (view: WorkspaceView) => void;
  children?: React.ReactNode;
}

export function BotDropdownMenu({
  bots,
  activeBot,
  onSelectBot,
  onNewConversation,
  onNewProject,
  onViewConversations,
  onViewProjects,
  onOpenSettings,
  onOpenView,
  children,
}: BotDropdownMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const presence: PresenceView = activeBot
    ? botPresence(activeBot)
    : { state: "unrecorded", label: "Lead Agent auto-routes", raw: null };

  const botTitle = activeBot ? botDisplayName(activeBot) : "Lead Agent";
  const botRole = activeBot ? activeBot.role || "Specialist Agent" : "Autonomous Orchestrator";

  return (
    <div ref={rootRef} className="relative inline-block text-left">
      {children ? (
        /* The custom trigger the rail passes in.
         *
         * This was a bare `<div onClick>`, which is not a control: it is not
         * focusable, it cannot be opened from the keyboard, and a screen reader
         * announces it as an unnamed group. The `else` branch below already does
         * this correctly with a real `<button>` carrying `aria-haspopup` and
         * `aria-expanded`, so the trigger now mirrors it.
         *
         * `role`/`tabIndex`/the key handler are what make Enter and Space work
         * here; `aria-expanded` is what tells assistive technology the menu is
         * currently closed, so this is not decoration. */
        <div
          role="button"
          tabIndex={0}
          aria-haspopup="true"
          aria-expanded={open}
          aria-label={`Agent options for ${botTitle}`}
          onClick={() => setOpen((v) => !v)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              setOpen((v) => !v);
            }
          }}
          className="cursor-pointer"
        >
          {children}
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 px-2.5 py-1.5 rounded-xl border border-border/70 bg-card/60 hover:bg-card text-xs transition-all shadow-2xs cursor-pointer group"
          title={`Bot options for ${botTitle}`}
          aria-haspopup="true"
          aria-expanded={open}
        >
          <div className="size-5 rounded-md bg-primary/10 text-primary flex items-center justify-center font-bold text-[10px] shrink-0 border border-primary/20">
            {activeBot?.avatar ? (
              <span>{activeBot.avatar}</span>
            ) : (
              <Bot className="size-3 text-primary" />
            )}
          </div>
          <span className="font-semibold text-foreground truncate max-w-32">{botTitle}</span>
          <ChevronDown
            className={`size-3 text-muted-foreground transition-transform duration-200 ${
              open ? "rotate-180 text-foreground" : "group-hover:text-foreground"
            }`}
          />
        </button>
      )}

      {open && (
        <div
          role="menu"
          aria-label={`Menu for ${botTitle}`}
          className="absolute left-0 mt-1.5 w-72 rounded-2xl border border-border bg-card shadow-2xl p-2 z-50 animate-in fade-in zoom-in-95 duration-100 divide-y divide-border/40"
        >
          {/* Active Bot Summary Header */}
          <div className="px-2.5 py-2 flex items-center gap-2.5">
            <div className="size-9 rounded-xl bg-primary/10 text-primary border border-primary/30 flex items-center justify-center font-bold text-sm shrink-0">
              {activeBot?.avatar ? (
                <span>{activeBot.avatar}</span>
              ) : (
                <Bot className="size-4 text-primary" />
              )}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="font-semibold text-xs text-foreground truncate">{botTitle}</span>
                <PresenceDot view={presence} />
              </div>
              <p className="text-[10px] text-muted-foreground truncate">{botRole}</p>
            </div>
          </div>
          {/* Quick Switch Agent Submenu */}
          <div className="pt-1.5">
            <div className="px-2 pb-1 flex items-center justify-between text-[10px] font-semibold text-muted-foreground uppercase tracking-wide">
              <span>Switch AI Agent</span>
              {onOpenView && (
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    onOpenView("bots");
                  }}
                  className="hover:text-primary transition-colors lowercase font-normal"
                >
                  All profiles â†’
                </button>
              )}
            </div>

            <div className="max-h-40 overflow-y-auto space-y-0.5 pr-0.5">
              {/* Lead Agent */}
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  onSelectBot(null);
                }}
                className={`w-full flex items-center justify-between px-2 py-1.5 rounded-lg text-left text-xs transition-colors cursor-pointer ${
                  activeBot === null
                    ? "bg-primary text-primary-foreground font-semibold"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
                }`}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <Bot className="size-3.5 shrink-0" />
                  <span className="truncate">Lead Agent</span>
                </div>
                <span className="text-[9px] opacity-75">Auto-routes</span>
              </button>

              {/* Other bots */}
              {bots.map((b) => {
                const isCurrent = activeBot?.name === b.name;
                return (
                  <button
                    key={b.name}
                    type="button"
                    onClick={() => {
                      setOpen(false);
                      onSelectBot(b);
                    }}
                    className={`w-full flex items-center justify-between px-2 py-1.5 rounded-lg text-left text-xs transition-colors cursor-pointer ${
                      isCurrent
                        ? "bg-primary text-primary-foreground font-semibold"
                        : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
                    }`}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      {b.avatar ? (
                        <span className="text-xs shrink-0">{b.avatar}</span>
                      ) : (
                        <Code2 className="size-3.5 shrink-0" />
                      )}
                      <span className="truncate">{botDisplayName(b)}</span>
                    </div>
                    <span className="text-[9px] opacity-75 truncate max-w-24">
                      {b.role || "Agent"}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Core Bot Actions (Items 1-5 required by User Specification) */}
          <div className="py-1 space-y-0.5">
            {/* 1. New Conversation */}
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onNewConversation();
              }}
              className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left text-xs text-foreground hover:bg-primary/10 hover:text-primary transition-colors cursor-pointer group"
            >
              <Plus className="size-3.5 text-primary shrink-0" />
              <div className="min-w-0 flex-1">
                <span className="font-medium block truncate">New Conversation</span>
                <span className="text-[10px] text-muted-foreground block truncate">
                  Start direct chat with {botTitle}
                </span>
              </div>
            </button>

            {/* 2. New Project */}
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onNewProject();
              }}
              className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left text-xs text-foreground hover:bg-primary/10 hover:text-primary transition-colors cursor-pointer group"
            >
              <FolderPlus className="size-3.5 text-primary shrink-0" />
              <div className="min-w-0 flex-1">
                <span className="font-medium block truncate">New Project</span>
                <span className="text-[10px] text-muted-foreground block truncate">
                  Create project assigned to {botTitle}
                </span>
              </div>
            </button>

            {/* 3. Bot's conversations */}
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                if (onViewConversations) onViewConversations();
              }}
              className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left text-xs text-foreground hover:bg-muted/70 transition-colors cursor-pointer"
            >
              <MessageSquare className="size-3.5 text-muted-foreground shrink-0" />
              <div className="min-w-0 flex-1">
                <span className="font-medium block truncate">Bot&apos;s conversations</span>
                <span className="text-[10px] text-muted-foreground block truncate">
                  View all chats with {botTitle}
                </span>
              </div>
            </button>

            {/* 4. Bot's projects */}
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                if (onViewProjects) {
                  onViewProjects();
                } else if (onOpenView) {
                  onOpenView("projects");
                }
              }}
              className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left text-xs text-foreground hover:bg-muted/70 transition-colors cursor-pointer"
            >
              <Folder className="size-3.5 text-muted-foreground shrink-0" />
              <div className="min-w-0 flex-1">
                <span className="font-medium block truncate">Bot&apos;s projects</span>
                <span className="text-[10px] text-muted-foreground block truncate">
                  View projects led by {botTitle}
                </span>
              </div>
            </button>

            {/* 5. Bot settings */}
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                if (onOpenSettings) {
                  onOpenSettings();
                } else if (onOpenView) {
                  onOpenView("bots");
                }
              }}
              className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left text-xs text-foreground hover:bg-muted/70 transition-colors cursor-pointer"
            >
              <Settings className="size-3.5 text-muted-foreground shrink-0" />
              <div className="min-w-0 flex-1">
                <span className="font-medium block truncate">Bot settings</span>
                <span className="text-[10px] text-muted-foreground block truncate">
                  Configure instructions & model
                </span>
              </div>
            </button>
          </div>

        </div>
      )}
    </div>
  );
}
