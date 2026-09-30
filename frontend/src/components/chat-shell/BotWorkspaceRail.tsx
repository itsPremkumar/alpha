"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Building2,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  FileText,
  Folder,
  FolderOpen,
  ListChecks,
  MessageSquare,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Settings,
  SquareStack,
  Code2,
  ShieldCheck,
  CheckCircle2,
  Palette,
  Megaphone,
  BarChart3,
  Bot,
  Sparkles,
  Layers,
  SlidersHorizontal,
} from "lucide-react";
import { BotProfile, botDisplayName, botInitials } from "@/types/bots";
import { Project } from "@/lib/projects";
import {
  botPresence,
  dedupeBots,
  groupConversations,
  projectStatusText,
  railRowFor,
  readProjectRailRows,
  type ProjectRailRow,
} from "@/lib/chat-shell";
import { MeasuredCount, LeadGlyph, PresenceLine, PresenceDot } from "./Honest";
import { BotDropdownMenu } from "./BotDropdownMenu";
import { ProjectDropdownMenu } from "./ProjectDropdownMenu";
import { NewProjectDialog } from "./NewProjectDialog";
import { threadTitle } from "@/lib/threads-ext";
import type { Thread } from "@/types/chat";
import type { WorkspaceView } from "@/lib/workspace-view";

export interface BotWorkspaceRailProps {
  bots: BotProfile[];
  activeBot: BotProfile | null;
  /** Every conversation the parent already scoped to the selected bot. */
  threads: Thread[];
  projects: Project[];
  activeThreadId: string | null;
  /** The project the *open* conversation belongs to, or `null`. */
  activeProjectId: string | null;
  onSelectBot: (bot: BotProfile | null) => void;
  onSelectThread: (threadId: string) => void;
  /** Open a blank conversation, optionally already scoped to a project. */
  onNewConversation: (projectId: string | null) => void;
  onPickProject: (projectId: string | null) => void;
  onOpenView: (view: WorkspaceView) => void;
  onProjectsChanged: () => void;
}

export function BotWorkspaceRail(props: BotWorkspaceRailProps) {
  const {
    bots,
    activeBot,
    threads,
    projects,
    activeThreadId,
    activeProjectId,
    onSelectBot,
    onSelectThread,
    onNewConversation,
    onPickProject,
    onOpenView,
    onProjectsChanged,
  } = props;

  const [standaloneCollapsed, setStandaloneCollapsed] = useState(false);
  const [projectsCollapsed, setProjectsCollapsed] = useState(false);
  const [projectScope, setProjectScope] = useState<"bot" | "all">("bot");

  const [rows, setRows] = useState<ProjectRailRow[] | null>(null);
  const [rowsError, setRowsError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [showAllProjects, setShowAllProjects] = useState(false);
  const [showAllStandalone, setShowAllStandalone] = useState(false);
  const [expandedProjects, setExpandedProjects] = useState<Record<string, boolean>>({});
  const generationRef = useRef(0);

  const botName = activeBot ? activeBot.name : null;

  const loadRows = useCallback(async () => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    setRefreshing(true);
    try {
      const next = await readProjectRailRows(projects, botName);
      if (generationRef.current !== generation) return;
      setRows(next);
      setRowsError(null);
    } catch (error) {
      if (generationRef.current !== generation) return;
      setRowsError(error instanceof Error ? error.message : String(error));
    } finally {
      if (generationRef.current === generation) setRefreshing(false);
    }
  }, [projects, botName]);

  useEffect(() => {
    if (projects.length === 0) {
      generationRef.current += 1;
      setRows(null);
      setRowsError(null);
      return;
    }
    void loadRows();
  }, [projects, loadRows]);

  // Keep active project expanded
  useEffect(() => {
    if (activeProjectId) {
      setExpandedProjects((prev) => ({ ...prev, [activeProjectId]: true }));
    }
  }, [activeProjectId]);

  const groups = groupConversations(threads, projects);

  // One row per bot, for BOTH the inline list and the dropdown below. The raw
  // roster can carry the same bot twice (it is assembled from the bot registry
  // plus a crew-membership read), which rendered as the same specialist listed
  // twice with two rows both claiming the selection. De-duplicating here rather
  // than in each surface is what keeps the two from disagreeing about how many
  // bots there are.
  const rosterBots = useMemo(() => dedupeBots(bots), [bots]);

  const presence = activeBot
    ? botPresence(activeBot)
    : { state: "unrecorded" as const, label: "Lead Agent auto-routes", raw: null };

  const projectRows = projects.map((project) => railRowFor(rows ?? [], project.id));

  // Determine projects scoped to this bot
  const botProjectRows = projectRows.filter((row) => {
    if (!botName) return true;
    if (row.leadsSelectedBot === true) return true;
    const projGroup = groups.projects.find((g) => g.projectId === row.projectId);
    if (projGroup && projGroup.items.length > 0) return true;
    return false;
  });

  const displayedProjectRows =
    projectScope === "bot" && botProjectRows.length > 0 ? botProjectRows : projectRows;

  const visibleProjects = showAllProjects ? displayedProjectRows : displayedProjectRows.slice(0, 6);
  const hiddenProjects = displayedProjectRows.length - visibleProjects.length;

  const standalone = groups.standalone.items;
  const visibleStandalone = showAllStandalone ? standalone : standalone.slice(0, 6);
  const hiddenStandalone = standalone.length - visibleStandalone.length;

  const toggleProjectExpand = (id: string, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    setExpandedProjects((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const currentBotDisplayName = activeBot ? botDisplayName(activeBot) : "Lead Agent";

  return (
    <div className="flex flex-col h-full select-none divide-y divide-border/40" data-shell="bot-workspace">
      {/* ── 1. Roster summary ───────────────────────────────────────────
          The scrolling "AI Agents (N)" profile list was removed here. It
          duplicated the agent selector that already exists directly below
          (BotDropdownMenu), so the rail showed the same choice twice: once as
          a scrollable list with its own scrollbar and once as the dropdown
          card. Two selectors for one decision is a defect even when they
          agree, and here they did not have to: the list was a fixed-height
          scroller, so a long roster was reachable only by scrolling a nested
          pane inside an already-scrolling sidebar.

          BotDropdownMenu is a complete replacement, not a subset: it carries
          its own Lead Agent row (onSelectBot(null), "Auto-routes") and one row
          per de-duplicated bot, so nothing became unreachable by removing the
          list. The measured count stays, because the count is real data - what
          was removed is the duplicate way of choosing, not the information. */}
      <section className="px-3 py-2 bg-card/10">
        <button
          type="button"
          onClick={() => onOpenView("bots")}
          className="w-full flex items-center justify-between gap-2 text-xs text-muted-foreground hover:text-primary transition-colors cursor-pointer"
        >
          <span className="flex items-center gap-1.5 min-w-0">
            <Sparkles className="size-3.5 text-primary shrink-0" />
            <span className="truncate">
              {bots.length > 0
                ? `${bots.length} agent${bots.length === 1 ? "" : "s"} available`
                : "No agents reported"}
            </span>
          </span>
          <span className="shrink-0 text-[10px] font-medium">All profiles →</span>
        </button>
      </section>

      {/* ── 2. Current Agent Card & Primary New Chat ──────────────────── */}
      <section className="p-3 bg-card/30 space-y-2">
        <BotDropdownMenu
          bots={rosterBots}
          activeBot={activeBot}
          onSelectBot={onSelectBot}
          onNewConversation={() => onNewConversation(null)}
          onNewProject={() => setCreating(true)}
          onViewConversations={() => setStandaloneCollapsed(false)}
          onViewProjects={() => {
            setProjectsCollapsed(false);
            setProjectScope("bot");
          }}
          onOpenSettings={() => onOpenView("bots")}
          onOpenView={onOpenView}
        >
          <div className="flex items-center justify-between gap-2 p-2.5 rounded-xl border border-border/70 bg-card/70 hover:bg-card hover:border-primary/40 transition-all cursor-pointer group shadow-2xs">
            <div className="flex items-center gap-2.5 min-w-0">
              {activeBot ? (
                <div className="size-8 rounded-lg bg-primary/10 text-primary flex items-center justify-center font-bold text-sm shrink-0 border border-primary/20">
                  {activeBot.avatar || <Code2 className="size-4" />}
                </div>
              ) : (
                <div className="size-8 rounded-lg bg-primary/10 text-primary flex items-center justify-center shrink-0 border border-primary/20">
                  <Bot className="size-4" />
                </div>
              )}
              <div className="min-w-0">
                <span className="block truncate text-xs font-semibold text-foreground group-hover:text-primary transition-colors">
                  {currentBotDisplayName}
                </span>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <PresenceDot view={presence} />
                  <span className="text-[10px] text-muted-foreground truncate">
                    {activeBot ? activeBot.role : "Autonomous Supervisor"}
                  </span>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-1 text-muted-foreground group-hover:text-foreground shrink-0">
              <ChevronDown className="size-3.5" />
            </div>
          </div>
        </BotDropdownMenu>

        {/* Primary "+ New Conversation" Button (direct with selected bot) */}
        <button
          type="button"
          onClick={() => onNewConversation(null)}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-xl bg-primary text-primary-foreground font-semibold text-xs hover:opacity-95 transition-opacity shadow-xs cursor-pointer"
          title={`Start a new conversation with ${currentBotDisplayName}`}
        >
          <Plus className="size-4" />
          <span>New Conversation</span>
        </button>
      </section>

      {/* ── 3. Standalone Conversations (Outside Any Project) ────────── */}
      <section className="p-3 space-y-1.5" aria-label="Standalone conversations">
        <div className="flex items-center justify-between pb-0.5 px-0.5">
          <button
            type="button"
            onClick={() => setStandaloneCollapsed((v) => !v)}
            className="text-xs font-semibold text-foreground hover:text-primary flex items-center gap-1.5 cursor-pointer transition-colors"
          >
            <MessageSquare className="size-3.5 text-primary" />
            <span>Standalone</span>
            {standalone.length > 0 && (
              <span className="text-[10px] text-muted-foreground font-normal">({standalone.length})</span>
            )}
            {standaloneCollapsed ? (
              <ChevronDown className="size-3 text-muted-foreground" />
            ) : (
              <ChevronUp className="size-3 text-muted-foreground" />
            )}
          </button>
          <button
            type="button"
            onClick={() => onNewConversation(null)}
            className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors cursor-pointer"
            title="Start standalone conversation"
            aria-label="Start standalone conversation"
          >
            <Plus className="size-3.5" />
          </button>
        </div>

        {!standaloneCollapsed && (
          <>
            {standalone.length === 0 ? (
              <p className="px-2 py-2 text-[11px] text-muted-foreground italic">
                No standalone chats with {currentBotDisplayName} yet.
              </p>
            ) : (
              <div className="space-y-0.5 mt-1">
                {visibleStandalone.map((thread) => {
                  const isActive = thread.thread_id === activeThreadId;
                  const title = threadTitle(thread as unknown as Record<string, unknown>);

                  return (
                    <button
                      key={thread.thread_id}
                      type="button"
                      onClick={() => onSelectThread(thread.thread_id)}
                      className={`w-full flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg text-left text-xs transition-colors cursor-pointer ${
                        isActive
                          ? "bg-primary text-primary-foreground font-semibold shadow-2xs"
                          : "hover:bg-muted/60 text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <MessageSquare className={`size-3 shrink-0 ${isActive ? "text-primary-foreground" : "text-muted-foreground"}`} />
                        <span className="truncate">{title}</span>
                      </div>
                    </button>
                  );
                })}

                {hiddenStandalone > 0 ? (
                  <button
                    type="button"
                    onClick={() => setShowAllStandalone(true)}
                    className="w-full text-left text-[11px] text-primary hover:underline px-2 py-0.5 font-medium cursor-pointer"
                  >
                    Show {hiddenStandalone} more chats...
                  </button>
                ) : showAllStandalone && standalone.length > 6 ? (
                  <button
                    type="button"
                    onClick={() => setShowAllStandalone(false)}
                    className="w-full text-left text-[10px] text-muted-foreground hover:text-foreground px-2 py-0.5 cursor-pointer"
                  >
                    Show fewer
                  </button>
                ) : null}
              </div>
            )}
          </>
        )}
      </section>

      {/* ── 4. Projects & Nested Project Conversations ───────────────── */}
      <section className="p-3 space-y-1.5 flex-1 min-h-0 overflow-y-auto" aria-label="Projects">
        <div className="flex items-center justify-between pb-0.5 px-0.5">
          <button
            type="button"
            onClick={() => setProjectsCollapsed((v) => !v)}
            className="text-xs font-semibold text-foreground hover:text-primary flex items-center gap-1.5 cursor-pointer transition-colors"
          >
            <Folder className="size-3.5 text-primary" />
            <span>Projects</span>
            {displayedProjectRows.length > 0 && (
              <span className="text-[10px] text-muted-foreground font-normal">
                ({displayedProjectRows.length})
              </span>
            )}
            {projectsCollapsed ? (
              <ChevronDown className="size-3 text-muted-foreground" />
            ) : (
              <ChevronUp className="size-3 text-muted-foreground" />
            )}
          </button>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setCreating(true)}
              className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors cursor-pointer"
              title={`Create project for ${currentBotDisplayName}`}
              aria-label="Create new project"
            >
              <Plus className="size-3.5" />
            </button>
            <button
              type="button"
              onClick={() => void loadRows()}
              disabled={refreshing}
              className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors disabled:opacity-40 cursor-pointer"
              title="Refresh project counts"
              aria-label="Refresh project counts"
            >
              <RefreshCw className={`size-3 ${refreshing ? "animate-spin" : ""}`} />
            </button>
            <button
              type="button"
              onClick={() => onOpenView("projects")}
              className="text-[10px] text-muted-foreground hover:text-primary font-medium pl-1 cursor-pointer"
              title="Manage all projects"
            >
              Manage
            </button>
          </div>
        </div>

        {/* Project Scope Filter Pills (Bot vs All) */}
        {!projectsCollapsed && botName && botProjectRows.length > 0 && botProjectRows.length < projectRows.length && (
          <div className="flex items-center gap-1 px-1 py-1">
            <button
              type="button"
              onClick={() => setProjectScope("bot")}
              className={`px-2 py-0.5 rounded-md text-[10px] font-medium transition-colors cursor-pointer ${
                projectScope === "bot"
                  ? "bg-primary/20 text-primary font-semibold"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
              }`}
            >
              {currentBotDisplayName} ({botProjectRows.length})
            </button>
            <button
              type="button"
              onClick={() => setProjectScope("all")}
              className={`px-2 py-0.5 rounded-md text-[10px] font-medium transition-colors cursor-pointer ${
                projectScope === "all"
                  ? "bg-primary/20 text-primary font-semibold"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
              }`}
            >
              All Projects ({projectRows.length})
            </button>
          </div>
        )}

        {rowsError && (
          <p className="px-1 text-[10px] text-destructive" role="alert">
            Refresh failed: {rowsError}
          </p>
        )}

        {!projectsCollapsed && (
          <>
            {displayedProjectRows.length === 0 ? (
              <div className="px-2 py-3 rounded-xl border border-dashed border-border/70 text-center space-y-1.5">
                <p className="text-[11px] text-muted-foreground italic">
                  {botName ? `No projects for ${currentBotDisplayName} yet` : "No projects yet"}
                </p>
                <button
                  type="button"
                  onClick={() => setCreating(true)}
                  className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline font-medium cursor-pointer"
                >
                  <Plus className="size-3" /> Create a project
                </button>
              </div>
            ) : (
              <div className="space-y-1 mt-1">
                {visibleProjects.map((row) => {
                  const project = projects.find((p) => p.id === row.projectId) ?? null;
                  const isSelected = row.projectId === activeProjectId;
                  const isExpanded = Boolean(expandedProjects[row.projectId]);
                  const count = row.conversationCount;
                  const projGroup = groups.projects.find((g) => g.projectId === row.projectId);
                  const projThreads = projGroup ? projGroup.items : [];

                  return (
                    <div key={row.projectId} className="rounded-xl transition-colors">
                      {/* Project Row Header */}
                      <div
                        className={`group flex items-center justify-between gap-1.5 px-2 py-1.5 rounded-lg text-xs transition-colors ${
                          isSelected
                            ? "bg-primary/10 border border-primary/30 text-primary font-semibold"
                            : "hover:bg-muted/60 text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        <button
                          type="button"
                          onClick={(e) => toggleProjectExpand(row.projectId, e)}
                          className="p-0.5 rounded text-muted-foreground hover:text-foreground transition-colors shrink-0 cursor-pointer"
                          title={isExpanded ? "Collapse project conversations" : "Expand project conversations"}
                        >
                          {isExpanded ? (
                            <ChevronDown className="size-3 text-primary" />
                          ) : (
                            <ChevronRight className="size-3" />
                          )}
                        </button>

                        <button
                          type="button"
                          onClick={() => {
                            onPickProject(row.projectId);
                            toggleProjectExpand(row.projectId);
                          }}
                          className="flex items-center gap-1.5 min-w-0 flex-1 text-left cursor-pointer"
                          title={`Project: ${project?.name || row.projectId}`}
                        >
                          {isExpanded ? (
                            <FolderOpen className={`size-3.5 shrink-0 ${isSelected ? "text-primary" : "text-muted-foreground"}`} />
                          ) : (
                            <Folder className={`size-3.5 shrink-0 ${isSelected ? "text-primary" : "text-muted-foreground"}`} />
                          )}
                          <span className="truncate">{project?.name || row.projectId}</span>
                        </button>

                        <div className="flex items-center gap-1 shrink-0">
                          {/* Measured count badge */}
                          <span
                            className={`text-[10px] px-1.5 py-0.2 rounded-full tabular-nums ${
                              isSelected
                                ? "bg-primary/20 text-primary font-bold"
                                : "bg-muted text-muted-foreground"
                            }`}
                            title={count !== null ? `${count} conversations in this project` : "Conversation count unknown"}
                          >
                            {count !== null ? count : "—"}
                          </span>

                          {/* Start a conversation in THIS project, without first
                              expanding the row. The expanded panel already
                              offered "New in Project", but reaching it meant two
                              clicks on a collapsed row, while the Standalone
                              group has had a one-click "+" on its header all
                              along - so the same action cost different numbers
                              of clicks depending on where the conversation
                              belonged. This is that control, for parity. */}
                          <button
                            type="button"
                            onClick={() => onNewConversation(row.projectId)}
                            className="p-1 rounded text-muted-foreground hover:text-primary transition-colors cursor-pointer"
                            title={`Start a new conversation in ${project?.name || row.projectId}`}
                            aria-label={`New conversation in ${project?.name || row.projectId}`}
                          >
                            <Plus className="size-3" />
                          </button>

                          {/* Project Options Menu using ProjectDropdownMenu */}
                          <ProjectDropdownMenu
                            project={project}
                            projects={projects}
                            projectThreads={projThreads}
                            activeThreadId={activeThreadId}
                            onSwitchProject={onPickProject}
                            onNewConversation={(id) => onNewConversation(id)}
                            onSelectThread={onSelectThread}
                            onOpenOverview={() => {
                              onPickProject(row.projectId);
                              onOpenView("projects");
                            }}
                            onOpenFiles={() => {
                              onPickProject(row.projectId);
                              onOpenView("files");
                            }}
                            onOpenTasks={() => {
                              onPickProject(row.projectId);
                              onOpenView("kanban");
                            }}
                            onOpenKnowledge={() => {
                              onPickProject(row.projectId);
                              onOpenView("memory");
                            }}
                            onOpenSettings={() => {
                              onPickProject(row.projectId);
                              onOpenView("projects");
                            }}
                            onOpenView={onOpenView}
                          >
                            <button
                              type="button"
                              className="p-1 rounded text-muted-foreground hover:text-foreground opacity-60 group-hover:opacity-100 transition-opacity cursor-pointer"
                              title={`Project options for ${project?.name || row.projectId}`}
                            >
                              <MoreHorizontal className="size-3" />
                            </button>
                          </ProjectDropdownMenu>
                        </div>
                      </div>

                      {/* ── Hierarchical Nested Project Conversations ───────── */}
                      {isExpanded && (
                        <div className="ml-4 pl-2 border-l-2 border-primary/20 space-y-0.5 my-1 pt-0.5">
                          {/* New inside project button & quick links */}
                          <div className="flex items-center justify-between gap-1 pr-1">
                            <button
                              type="button"
                              onClick={() => onNewConversation(row.projectId)}
                              className="flex items-center gap-1.5 px-2 py-1 rounded-md text-left text-[11px] text-primary hover:bg-primary/10 transition-colors font-medium cursor-pointer"
                              title="Start new conversation in this project"
                            >
                              <Plus className="size-3 shrink-0" />
                              <span>New in Project</span>
                            </button>
                            <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
                              <button
                                type="button"
                                onClick={() => {
                                  onPickProject(row.projectId);
                                  onOpenView("files");
                                }}
                                className="hover:text-primary transition-colors cursor-pointer"
                                title="Project files"
                              >
                                Files
                              </button>
                              <span>•</span>
                              <button
                                type="button"
                                onClick={() => {
                                  onPickProject(row.projectId);
                                  onOpenView("kanban");
                                }}
                                className="hover:text-primary transition-colors cursor-pointer"
                                title="Project tasks"
                              >
                                Tasks
                              </button>
                            </div>
                          </div>

                          {projThreads.length === 0 ? (
                            <span className="block px-2 py-1 text-[10px] text-muted-foreground italic">
                              No conversations in this project yet.
                            </span>
                          ) : (
                            projThreads.map((thread) => {
                              const isCurrent = thread.thread_id === activeThreadId;
                              const title = threadTitle(thread as unknown as Record<string, unknown>);

                              return (
                                <button
                                  key={thread.thread_id}
                                  type="button"
                                  onClick={() => onSelectThread(thread.thread_id)}
                                  className={`w-full flex items-center justify-between gap-1.5 px-2 py-1 rounded-md text-left text-[11px] transition-colors cursor-pointer ${
                                    isCurrent
                                      ? "bg-primary text-primary-foreground font-semibold shadow-2xs"
                                      : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                                  }`}
                                >
                                  <div className="flex items-center gap-1.5 min-w-0 flex-1">
                                    <MessageSquare className="size-2.5 shrink-0 opacity-70" />
                                    <span className="truncate">{title}</span>
                                  </div>
                                </button>
                              );
                            })
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}

                {/* Clean pagination toggle */}
                {hiddenProjects > 0 ? (
                  <button
                    type="button"
                    onClick={() => setShowAllProjects(true)}
                    className="w-full text-left text-[11px] text-primary hover:underline px-2 py-0.5 font-medium cursor-pointer"
                  >
                    Show {hiddenProjects} more projects...
                  </button>
                ) : showAllProjects && displayedProjectRows.length > 6 ? (
                  <button
                    type="button"
                    onClick={() => setShowAllProjects(false)}
                    className="w-full text-left text-[10px] text-muted-foreground hover:text-foreground px-2 py-0.5 cursor-pointer"
                  >
                    Show fewer
                  </button>
                ) : null}

              </div>
            )}
          </>
        )}
      </section>

      {creating && (
        <NewProjectDialog
          botName={botName}
          botDisplayName={activeBot ? botDisplayName(activeBot) : null}
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            onProjectsChanged();
          }}
        />
      )}
    </div>
  );
}
