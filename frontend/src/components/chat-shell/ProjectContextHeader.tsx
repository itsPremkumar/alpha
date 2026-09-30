"use client";

import React from "react";
import {
  ChevronRight,
  ChevronDown,
  Folder,
  FolderOpen,
  MessageSquare,
  Plus,
  Repeat,
  Code2,
  FileText,
  ListTodo,
  Brain,
  PanelRight,
  MoreHorizontal,
  Bot,
  SlidersHorizontal,
} from "lucide-react";
import { BotProfile, botDisplayName, botInitials } from "@/types/bots";
import { Project } from "@/lib/projects";
import { WorkspaceView } from "@/lib/workspace-view";
import { botPresence, contextSentence, conversationTitle, projectStatusText } from "@/lib/chat-shell";
import { BotGlyph, LeadGlyph, PresenceLine, PresenceDot } from "./Honest";
import { BotDropdownMenu } from "./BotDropdownMenu";
import { ProjectDropdownMenu } from "./ProjectDropdownMenu";
import type { Thread } from "@/types/chat";

export type WorkspaceContextTab = "conversation" | "files" | "tasks" | "knowledge" | "agent";

export interface ProjectContextHeaderProps {
  bot: BotProfile | null;
  bots: BotProfile[];
  project: Project | null;
  /** True when a project is selected but its name could not be resolved. */
  projectKnown: boolean;
  thread: Thread | null;
  threads?: Thread[];
  projects: Project[];
  projectThreadCount?: number | null;
  onSwitchProject: (projectId: string | null) => void;
  onSelectBot: (bot: BotProfile | null) => void;
  onSelectThread?: (threadId: string) => void;
  onNewConversation: () => void;
  onNewProject?: () => void;
  onOpenView: (view: WorkspaceView) => void;
  activeTab?: WorkspaceContextTab;
  onTabChange?: (tab: WorkspaceContextTab) => void;
  onToggleInspector?: () => void;
  inspectorOpen?: boolean;
}

export function ProjectContextHeader(props: ProjectContextHeaderProps) {
  const {
    bot,
    bots,
    project,
    projectKnown,
    thread,
    threads = [],
    projects,
    projectThreadCount,
    onSwitchProject,
    onSelectBot,
    onSelectThread,
    onNewConversation,
    onNewProject,
    onOpenView,
    activeTab = "conversation",
    onTabChange,
    onToggleInspector,
    inspectorOpen = false,
  } = props;

  const presence = bot
    ? botPresence(bot)
    : { state: "unrecorded" as const, label: "Lead Agent auto-routes", raw: null };
  const title = conversationTitle(thread);
  const sentence = contextSentence({
    botName: bot ? botDisplayName(bot) : null,
    projectName: project?.name ?? null,
    conversationTitle: title,
  });

  const projectThreads = project
    ? threads.filter((t) => t.projectId === project.id)
    : [];

  return (
    <div className="border-b border-border/60 bg-card/30 shrink-0 select-none" data-shell="project-context">
      {/* ── Top Row: Agent Profile & Project Chip ─────────────────────── */}
      <div className="px-4 pt-3 pb-2 flex items-center justify-between gap-4 flex-wrap">
        {/* Active Agent Info with Bot Dropdown */}
        <BotDropdownMenu
          bots={bots}
          activeBot={bot}
          onSelectBot={onSelectBot}
          onNewConversation={onNewConversation}
          onNewProject={onNewProject || (() => onOpenView("projects"))}
          onViewConversations={() => {}}
          onViewProjects={() => onOpenView("projects")}
          onOpenSettings={() => onOpenView("bots")}
          onOpenView={onOpenView}
        >
          <div className="flex items-center gap-3 p-1 rounded-2xl hover:bg-card/80 transition-colors cursor-pointer group">
            <div className="size-10 rounded-2xl bg-gradient-to-br from-indigo-500/20 to-blue-500/20 text-primary border border-primary/30 flex items-center justify-center font-bold text-sm shadow-sm shrink-0">
              {bot?.avatar ? (
                <span className="text-base">{bot.avatar}</span>
              ) : bot ? (
                <Code2 className="size-5 text-primary" />
              ) : (
                <Bot className="size-5 text-primary" />
              )}
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-sm text-foreground truncate">
                  {bot ? botDisplayName(bot) : "Lead Agent"}
                </span>
                <ChevronDown className="size-3 text-muted-foreground group-hover:text-foreground transition-transform" />
                <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-500 shrink-0">
                  <PresenceDot view={presence} />
                  <span className="text-[10px]">
                    {presence.state === "recent"
                      ? "Online"
                      : presence.state === "idle"
                      ? "Idle"
                      : "Ready"}
                  </span>
                </span>
              </div>
              <p className="text-[11px] text-muted-foreground truncate">
                {bot?.role || (bot ? "Autonomous Specialist" : "Orchestrator & Lead Planner")}
              </p>
            </div>
          </div>
        </BotDropdownMenu>

        {/* Project Selector Badge & Actions */}
        <div className="flex items-center gap-2 ml-auto">
          {/* Project Chip with Dropdown */}
          <ProjectDropdownMenu
            project={project}
            projects={projects}
            projectThreads={projectThreads}
            activeThreadId={thread?.thread_id}
            onSwitchProject={onSwitchProject}
            onNewConversation={onNewConversation}
            onSelectThread={onSelectThread}
            onOpenOverview={onToggleInspector || (() => onOpenView("projects"))}
            onOpenFiles={() => onTabChange?.("files")}
            onOpenTasks={() => onTabChange?.("tasks")}
            onOpenKnowledge={() => onTabChange?.("knowledge")}
            onOpenSettings={() => onOpenView("projects")}
            onOpenView={onOpenView}
          />

          {/* Toggle Right Inspector Panel */}
          {project && onToggleInspector && (
            <button
              type="button"
              onClick={onToggleInspector}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl border text-xs font-medium transition-all shadow-2xs cursor-pointer ${
                inspectorOpen
                  ? "bg-primary text-primary-foreground border-primary shadow-sm"
                  : "border-border/70 bg-card/60 hover:bg-muted text-muted-foreground hover:text-foreground"
              }`}
              title={inspectorOpen ? "Close Project Inspector" : "Open Project Inspector"}
              aria-label="Toggle Project Inspector"
            >
              <PanelRight className="size-3.5" />
              <span className="hidden sm:inline">Project Details</span>
            </button>
          )}

          {/* New Conversation Button */}
          <button
            type="button"
            onClick={onNewConversation}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-xl border border-border/70 bg-card/60 hover:bg-muted text-foreground text-xs font-medium transition-colors shadow-2xs cursor-pointer"
            title="Start new conversation"
          >
            <Plus className="size-3.5" />
            <span className="hidden sm:inline">New</span>
          </button>
        </div>
      </div>

      {/* ── Breadcrumb invariant row (Strict adherence to chat-shell-breadcrumb test) ── */}
      <div className="px-4 pb-1 text-[10px] text-muted-foreground flex items-center gap-1.5 overflow-hidden">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground shrink-0">Alpha</span>
        <ChevronRight className="size-3 shrink-0 text-muted-foreground/60" aria-hidden="true" />

        {/* bot segment */}
        {bot ? (
          <span className="flex items-center gap-1.5 min-w-0">
            <BotGlyph initials={botInitials(bot)} avatar={bot.avatar} presence={presence} />
            <span className="text-[11px] font-semibold truncate max-w-32">{botDisplayName(bot)}</span>
            <PresenceLine view={presence} />
          </span>
        ) : (
          <span className="flex items-center gap-1.5 min-w-0">
            <LeadGlyph />
            <span className="text-[11px] font-semibold shrink-0">Lead Agent</span>
            <span className="text-[10px] text-muted-foreground truncate min-w-0">
              auto-routes, sees every conversation
            </span>
          </span>
        )}

        <ChevronRight className="size-3 shrink-0 text-muted-foreground/60" aria-hidden="true" />

        {/* project segment */}
        <span className="flex items-center gap-1 min-w-0">
          <FolderOpen className="size-3 shrink-0 text-muted-foreground" aria-hidden="true" />
          {project ? (
            <span className="text-[10px] font-medium truncate max-w-40" title={`${project.name} — status: ${projectStatusText(project.status)}`}>
              {project.name}
            </span>
          ) : projectKnown ? (
            <span className="text-[10px] text-muted-foreground italic">No project</span>
          ) : (
            <span className="text-[10px] text-destructive italic">project not in the loaded list</span>
          )}
        </span>

        <ChevronRight className="size-3 shrink-0 text-muted-foreground/60" aria-hidden="true" />

        {/* conversation title segment */}
        <span className="flex items-center gap-1 min-w-0">
          <MessageSquare className="size-3 shrink-0 text-muted-foreground" aria-hidden="true" />
          {title ? (
            <span className="text-[10px] truncate max-w-56" title={title}>{title}</span>
          ) : (
            <span className="text-[10px] text-muted-foreground italic">New conversation</span>
          )}
        </span>
      </div>

      {/* ── Workspace Context Tabs (Conversation | Files | Tasks | Knowledge) ── */}
      <div className="px-4 flex items-center gap-6 text-xs font-medium border-t border-border/40 mt-1">
        <button
          type="button"
          onClick={() => onTabChange?.("conversation")}
          className={`py-2 border-b-2 transition-all flex items-center gap-1.5 cursor-pointer ${
            activeTab === "conversation"
              ? "border-primary text-primary font-semibold"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          <MessageSquare className="size-3.5" />
          <span>Conversation</span>
        </button>

        <button
          type="button"
          onClick={() => onTabChange?.("files")}
          className={`py-2 border-b-2 transition-all flex items-center gap-1.5 cursor-pointer ${
            activeTab === "files"
              ? "border-primary text-primary font-semibold"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          <FileText className="size-3.5" />
          <span>Files</span>
        </button>

        <button
          type="button"
          onClick={() => onTabChange?.("tasks")}
          className={`py-2 border-b-2 transition-all flex items-center gap-1.5 cursor-pointer ${
            activeTab === "tasks"
              ? "border-primary text-primary font-semibold"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          <ListTodo className="size-3.5" />
          <span>Tasks</span>
        </button>

        <button
          type="button"
          onClick={() => onTabChange?.("knowledge")}
          className={`py-2 border-b-2 transition-all flex items-center gap-1.5 cursor-pointer ${
            activeTab === "knowledge"
              ? "border-primary text-primary font-semibold"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          <Brain className="size-3.5" />
          <span>Knowledge</span>
        </button>

          <button
            type="button"
            onClick={() => onTabChange?.("agent")}
            className={`py-2 border-b-2 transition-all flex items-center gap-1.5 cursor-pointer ${
              activeTab === "agent"
                ? "border-primary text-primary font-semibold"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            <SlidersHorizontal className="size-3.5" />
            <span>Agent</span>
          </button>
        </div>
      </div>
    );
  }
