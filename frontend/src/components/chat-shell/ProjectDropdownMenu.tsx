"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  LayoutGrid,
  ListTodo,
  Brain,
  MessageSquare,
  Plus,
  Settings,
  SquareStack,
  Layers,
  Sparkles,
} from "lucide-react";
import { Project } from "@/lib/projects";
import { threadTitle } from "@/lib/threads-ext";
import type { Thread } from "@/types/chat";
import type { WorkspaceView } from "@/lib/workspace-view";

export interface ProjectDropdownMenuProps {
  project: Project | null;
  projects: Project[];
  /** Conversations belonging to this project (or all conversations if needed). */
  projectThreads?: Thread[];
  activeThreadId?: string | null;
  onSwitchProject: (projectId: string | null) => void;
  onNewConversation: (projectId: string | null) => void;
  onSelectThread?: (threadId: string) => void;
  onOpenOverview?: () => void;
  onOpenFiles?: () => void;
  onOpenTasks?: () => void;
  onOpenKnowledge?: () => void;
  onOpenSettings?: () => void;
  onOpenView?: (view: WorkspaceView) => void;
  children?: React.ReactNode;
}

export function ProjectDropdownMenu({
  project,
  projects,
  projectThreads = [],
  activeThreadId,
  onSwitchProject,
  onNewConversation,
  onSelectThread,
  onOpenOverview,
  onOpenFiles,
  onOpenTasks,
  onOpenKnowledge,
  onOpenSettings,
  onOpenView,
  children,
}: ProjectDropdownMenuProps) {
  const [open, setOpen] = useState(false);
  const [conversationsOpen, setConversationsOpen] = useState(false);
  const [switchOpen, setSwitchOpen] = useState(false);
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

  const projectName = project ? project.name : "Standalone Chat";
  const threadCount = projectThreads.length;

  return (
    <div ref={rootRef} className="relative inline-block text-left">
      {children ? (
        <div onClick={() => setOpen((v) => !v)} className="cursor-pointer">
          {children}
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 px-3 py-1.5 rounded-xl border border-border/70 bg-card/60 hover:bg-card text-xs transition-all shadow-2xs cursor-pointer group"
          title={`Project options for ${projectName}`}
          aria-haspopup="true"
          aria-expanded={open}
        >
          <Folder className="size-3.5 text-primary shrink-0" />
          <span className="font-semibold text-foreground truncate max-w-40">{projectName}</span>
          <span className="text-[10px] text-muted-foreground shrink-0">
            • {project ? `${threadCount} chats` : "No project"}
          </span>
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
          aria-label={`Menu for ${projectName}`}
          className="absolute left-0 mt-1.5 w-80 rounded-2xl border border-border bg-card shadow-2xl p-2 z-50 animate-in fade-in zoom-in-95 duration-100 divide-y divide-border/40"
        >
          {/* Active Project Summary Header */}
          <div className="px-2.5 py-2 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 min-w-0">
              <div className="size-8 rounded-lg bg-primary/10 text-primary flex items-center justify-center shrink-0 border border-primary/20">
                <Folder className="size-4" />
              </div>
              <div className="min-w-0">
                <span className="font-semibold text-xs text-foreground block truncate">
                  {projectName}
                </span>
                <span className="text-[10px] text-muted-foreground block truncate">
                  {project ? `${threadCount} conversations in project` : "Individual chat outside any project"}
                </span>
              </div>
            </div>
            {project && (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-primary/10 text-primary font-semibold uppercase tracking-wider shrink-0">
                {project.status || "active"}
              </span>
            )}
          </div>

          {/* Core Project Actions (Items required by User Specification Point 8) */}
          <div className="py-1 space-y-0.5">
            {/* 1. Project Overview */}
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                if (onOpenOverview) onOpenOverview();
                else if (onOpenView) onOpenView("projects");
              }}
              className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left text-xs text-foreground hover:bg-muted/70 transition-colors cursor-pointer"
            >
              <SquareStack className="size-3.5 text-primary shrink-0" />
              <div className="min-w-0 flex-1">
                <span className="font-medium block truncate">Project overview</span>
                <span className="text-[10px] text-muted-foreground block truncate">
                  Inspect goals, instructions & details
                </span>
              </div>
            </button>

            {/* 2. New Conversation */}
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onNewConversation(project ? project.id : null);
              }}
              className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left text-xs text-foreground hover:bg-primary/10 hover:text-primary transition-colors cursor-pointer"
            >
              <Plus className="size-3.5 text-primary shrink-0" />
              <div className="min-w-0 flex-1">
                <span className="font-medium block truncate">New conversation</span>
                <span className="text-[10px] text-muted-foreground block truncate">
                  {project ? `Start chat inside ${project.name}` : "Start standalone chat"}
                </span>
              </div>
            </button>

            {/* 3. Existing Project Conversations */}
            <div className="rounded-lg transition-colors">
              <button
                type="button"
                role="menuitem"
                onClick={() => setConversationsOpen((v) => !v)}
                className="w-full flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg text-left text-xs text-foreground hover:bg-muted/70 transition-colors cursor-pointer"
              >
                <div className="flex items-center gap-2.5 min-w-0">
                  <MessageSquare className="size-3.5 text-muted-foreground shrink-0" />
                  <div className="min-w-0">
                    <span className="font-medium block truncate">Existing project conversations</span>
                    <span className="text-[10px] text-muted-foreground block truncate">
                      {threadCount > 0 ? `${threadCount} conversations recorded` : "No chats in this project yet"}
                    </span>
                  </div>
                </div>
                <ChevronRight
                  className={`size-3 text-muted-foreground transition-transform shrink-0 ${
                    conversationsOpen ? "rotate-90" : ""
                  }`}
                />
              </button>

              {conversationsOpen && (
                <div className="ml-5 pl-2 border-l border-border/70 my-1 space-y-0.5 max-h-40 overflow-y-auto">
                  {projectThreads.length === 0 ? (
                    <p className="px-2 py-1 text-[11px] text-muted-foreground italic">
                      No conversations in this project yet.
                    </p>
                  ) : (
                    projectThreads.map((t) => {
                      const isCurrent = t.thread_id === activeThreadId;
                      const title = threadTitle(t as unknown as Record<string, unknown>);
                      return (
                        <button
                          key={t.thread_id}
                          type="button"
                          onClick={() => {
                            setOpen(false);
                            onSelectThread?.(t.thread_id);
                          }}
                          className={`w-full flex items-center gap-2 px-2 py-1 rounded-md text-left text-[11px] transition-colors cursor-pointer ${
                            isCurrent
                              ? "bg-primary text-primary-foreground font-semibold"
                              : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                          }`}
                        >
                          <MessageSquare className="size-3 shrink-0" />
                          <span className="truncate flex-1">{title}</span>
                        </button>
                      );
                    })
                  )}
                </div>
              )}
            </div>

            {/* 4. Files & Knowledge */}
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                if (onOpenFiles) onOpenFiles();
                else if (onOpenView) onOpenView("files");
              }}
              className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left text-xs text-foreground hover:bg-muted/70 transition-colors cursor-pointer"
            >
              <FileText className="size-3.5 text-muted-foreground shrink-0" />
              <div className="min-w-0 flex-1">
                <span className="font-medium block truncate">Files & Knowledge</span>
                <span className="text-[10px] text-muted-foreground block truncate">
                  Inspect workspace documents & memory
                </span>
              </div>
            </button>

            {/* 5. Tasks */}
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                if (onOpenTasks) onOpenTasks();
                else if (onOpenView) onOpenView("kanban");
              }}
              className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left text-xs text-foreground hover:bg-muted/70 transition-colors cursor-pointer"
            >
              <ListTodo className="size-3.5 text-muted-foreground shrink-0" />
              <div className="min-w-0 flex-1">
                <span className="font-medium block truncate">Tasks</span>
                <span className="text-[10px] text-muted-foreground block truncate">
                  View project kanban & backlog
                </span>
              </div>
            </button>

            {/* 6. Project Settings */}
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                if (onOpenSettings) onOpenSettings();
                else if (onOpenView) onOpenView("projects");
              }}
              className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left text-xs text-foreground hover:bg-muted/70 transition-colors cursor-pointer"
            >
              <Settings className="size-3.5 text-muted-foreground shrink-0" />
              <div className="min-w-0 flex-1">
                <span className="font-medium block truncate">Project settings</span>
                <span className="text-[10px] text-muted-foreground block truncate">
                  Edit instructions, members & status
                </span>
              </div>
            </button>
          </div>

          {/* Switch Project Submenu */}
          <div className="pt-1.5">
            <button
              type="button"
              onClick={() => setSwitchOpen((v) => !v)}
              className="w-full flex items-center justify-between px-2 py-1 text-[10px] font-semibold text-muted-foreground uppercase tracking-wide hover:text-foreground transition-colors cursor-pointer"
            >
              <span>Switch Project ({projects.length})</span>
              <ChevronRight
                className={`size-3 transition-transform ${switchOpen ? "rotate-90" : ""}`}
              />
            </button>

            {switchOpen && (
              <div className="max-h-40 overflow-y-auto space-y-0.5 pr-0.5 mt-1">
                {/* Standalone (No project) */}
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    onSwitchProject(null);
                  }}
                  className={`w-full flex items-center justify-between px-2 py-1.5 rounded-lg text-left text-xs transition-colors cursor-pointer ${
                    project === null
                      ? "bg-primary text-primary-foreground font-semibold"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
                  }`}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <MessageSquare className="size-3.5 shrink-0" />
                    <span className="truncate">Standalone (No project)</span>
                  </div>
                </button>

                {/* All Projects */}
                {projects.map((p) => {
                  const isCurrent = project?.id === p.id;
                  return (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => {
                        setOpen(false);
                        onSwitchProject(p.id);
                      }}
                      className={`w-full flex items-center justify-between px-2 py-1.5 rounded-lg text-left text-xs transition-colors cursor-pointer ${
                        isCurrent
                          ? "bg-primary text-primary-foreground font-semibold"
                          : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
                      }`}
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <Folder className="size-3.5 shrink-0" />
                        <span className="truncate">{p.name || p.id}</span>
                      </div>
                      <span className="text-[10px] opacity-75">{p.status || "active"}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
