"use client";

import React, { useState, useEffect, useMemo, useRef } from "react";
import { Search, X, Bot, Folder, MessageSquare, Plus, Settings, ArrowRight, Code2 } from "lucide-react";
import type { BotProfile } from "@/types/bots";
import { botDisplayName } from "@/types/bots";
import type { Project } from "@/lib/projects";
import type { Thread } from "@/types/chat";
import { threadTitle } from "@/lib/threads-ext";

export interface OmnisearchModalProps {
  isOpen: boolean;
  onClose: () => void;
  bots: BotProfile[];
  projects: Project[];
  threads: Thread[];
  onSelectBot: (bot: BotProfile | null) => void;
  onSelectProject: (projectId: string | null) => void;
  onSelectThread: (threadId: string) => void;
  onNewConversation: () => void;
  onNewProject: () => void;
  onOpenSettings: () => void;
}

export function OmnisearchModal({
  isOpen,
  onClose,
  bots,
  projects,
  threads,
  onSelectBot,
  onSelectProject,
  onSelectThread,
  onNewConversation,
  onNewProject,
  onOpenSettings,
}: OmnisearchModalProps) {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setQuery("");
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  const q = query.trim().toLowerCase();

  const filteredBots = useMemo(() => {
    if (!q) return bots.slice(0, 6);
    return bots.filter(
      (b) =>
        b.name.toLowerCase().includes(q) ||
        (b.display_name && b.display_name.toLowerCase().includes(q)) ||
        (b.role && b.role.toLowerCase().includes(q)),
    );
  }, [bots, q]);

  const filteredProjects = useMemo(() => {
    if (!q) return projects.slice(0, 5);
    return projects.filter(
      (p) =>
        (p.name && p.name.toLowerCase().includes(q)) ||
        p.id.toLowerCase().includes(q),
    );
  }, [projects, q]);

  const filteredThreads = useMemo(() => {
    if (!q) return threads.slice(0, 5);
    return threads.filter((t) => {
      const title = threadTitle(t as unknown as Record<string, unknown>).toLowerCase();
      return title.includes(q) || t.thread_id.toLowerCase().includes(q);
    });
  }, [threads, q]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-20 px-4 bg-background/80 backdrop-blur-sm animate-in fade-in duration-150"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl rounded-2xl border border-border bg-card shadow-2xl overflow-hidden flex flex-col max-h-[75vh] animate-in zoom-in-95 duration-150"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search header input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border/60 bg-muted/20">
          <Search className="size-4 shrink-0 text-muted-foreground" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search agents, projects, conversations, actions..."
            className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
            aria-label="Omni-search"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery("")}
              className="p-1 rounded-md text-muted-foreground hover:text-foreground"
            >
              <X className="size-3.5" />
            </button>
          )}
          <kbd className="px-1.5 py-0.5 rounded border border-border text-[10px] font-mono text-muted-foreground">
            ESC
          </kbd>
        </div>

        {/* Results container */}
        <div className="overflow-y-auto p-3 space-y-4">
          {/* Quick Actions */}
          <div>
            <p className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Quick Actions
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-1.5">
              <button
                type="button"
                onClick={() => {
                  onClose();
                  onNewConversation();
                }}
                className="flex items-center gap-2 px-3 py-2 rounded-xl bg-muted/40 hover:bg-primary/10 hover:text-primary text-xs font-medium text-left transition-colors"
              >
                <Plus className="size-3.5 text-primary" />
                <span>New Conversation</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  onClose();
                  onNewProject();
                }}
                className="flex items-center gap-2 px-3 py-2 rounded-xl bg-muted/40 hover:bg-primary/10 hover:text-primary text-xs font-medium text-left transition-colors"
              >
                <Folder className="size-3.5 text-primary" />
                <span>New Project</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  onClose();
                  onOpenSettings();
                }}
                className="flex items-center gap-2 px-3 py-2 rounded-xl bg-muted/40 hover:bg-primary/10 hover:text-primary text-xs font-medium text-left transition-colors"
              >
                <Settings className="size-3.5 text-primary" />
                <span>Settings</span>
              </button>
            </div>
          </div>

          {/* AI Agents section */}
          {filteredBots.length > 0 && (
            <div>
              <p className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center justify-between">
                <span>AI Agents ({filteredBots.length})</span>
                <span className="text-[9px] font-normal lowercase">Select to switch bot</span>
              </p>
              <div className="space-y-1">
                {filteredBots.map((bot) => (
                  <button
                    key={bot.name}
                    type="button"
                    onClick={() => {
                      onClose();
                      onSelectBot(bot);
                    }}
                    className="w-full flex items-center justify-between gap-3 px-3 py-2 rounded-xl hover:bg-muted/60 text-left transition-colors group"
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <div className="size-7 rounded-lg bg-primary/10 text-primary flex items-center justify-center font-bold text-xs shrink-0">
                        {bot.avatar || <Code2 className="size-3.5" />}
                      </div>
                      <div className="truncate">
                        <span className="text-xs font-semibold text-foreground group-hover:text-primary transition-colors">
                          {botDisplayName(bot)}
                        </span>
                        <span className="text-[11px] text-muted-foreground ml-2 truncate">
                          {bot.role || "Autonomous Specialist"}
                        </span>
                      </div>
                    </div>
                    <ArrowRight className="size-3.5 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Projects section */}
          {filteredProjects.length > 0 && (
            <div>
              <p className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center justify-between">
                <span>Projects ({filteredProjects.length})</span>
                <span className="text-[9px] font-normal lowercase">Select to scope workspace</span>
              </p>
              <div className="space-y-1">
                {filteredProjects.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => {
                      onClose();
                      onSelectProject(p.id);
                    }}
                    className="w-full flex items-center justify-between gap-3 px-3 py-2 rounded-xl hover:bg-muted/60 text-left transition-colors group"
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <Folder className="size-4 text-primary shrink-0" />
                      <div className="truncate">
                        <span className="text-xs font-medium text-foreground">{p.name || p.id}</span>
                        <span className="text-[10px] text-muted-foreground ml-2 font-mono">
                          {p.status || "active"}
                        </span>
                      </div>
                    </div>
                    <ArrowRight className="size-3.5 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Conversations section */}
          {filteredThreads.length > 0 && (
            <div>
              <p className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center justify-between">
                <span>Recent Conversations ({filteredThreads.length})</span>
              </p>
              <div className="space-y-1">
                {filteredThreads.map((t) => (
                  <button
                    key={t.thread_id}
                    type="button"
                    onClick={() => {
                      onClose();
                      onSelectThread(t.thread_id);
                    }}
                    className="w-full flex items-center justify-between gap-3 px-3 py-2 rounded-xl hover:bg-muted/60 text-left transition-colors group"
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <MessageSquare className="size-3.5 text-muted-foreground shrink-0" />
                      <span className="text-xs text-foreground truncate">
                        {threadTitle(t as unknown as Record<string, unknown>)}
                      </span>
                    </div>
                    <span className="text-[10px] text-muted-foreground font-mono shrink-0">
                      {t.thread_id.slice(0, 8)}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {filteredBots.length === 0 && filteredProjects.length === 0 && filteredThreads.length === 0 && (
            <div className="py-8 text-center text-muted-foreground text-xs">
              No matching agents, projects or conversations found for &ldquo;{query}&rdquo;.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
