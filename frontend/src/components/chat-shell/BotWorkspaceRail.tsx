"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Building2,
  ChevronRight,
  ChevronDown,
  FileText,
  FolderOpen,
  ListChecks,
  MessageSquare,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Settings,
  SquareStack,
} from "lucide-react";
import { BotProfile, botDisplayName, botInitials } from "@/types/bots";
import { Project } from "@/lib/projects";
import {
  botPresence,
  groupConversations,
  projectStatusText,
  railRowFor,
  readProjectRailRows,
  type ProjectRailRow,
} from "@/lib/chat-shell";
import { MeasuredCount, BotGlyph, LeadGlyph, PresenceLine } from "./Honest";
import { Menu, MenuGroup, MenuItem, MenuLink } from "./Menu";
import { NewProjectDialog } from "./NewProjectDialog";
import { threadTitle } from "@/lib/threads-ext";
import type { Thread } from "@/types/chat";
import type { WorkspaceView } from "@/lib/workspace-view";

/**
 * The bot workspace rail: who you are talking to, what they own, and where the
 * conversation sits.
 *
 * This is the hierarchy from the spec, rendered in the left column above the
 * existing sidebar content. It is **additive** — the conversation list, search,
 * storage footer, backup and restore below it are untouched.
 *
 * Everything here is a projection of what the Gateway reported, and each
 * element names its own source:
 *
 *   - the bot row is `GET /api/bots`; the dot is derived from that row's
 *     `last_active` and is drawn only when the Gateway sent one;
 *   - a project's conversation count is the length of
 *     `GET /projects/{id}/threads`, because `GET /projects` carries no count
 *     field at all;
 *   - whether the selected bot is on a project comes from
 *     `GET /projects/{id}/presence`, and is left as *unknown* when that read
 *     fails rather than resolved to "no".
 *
 * The one thing this rail deliberately does not claim is that the project list
 * is scoped to the selected bot. `GET /projects` has no bot filter, so the group
 * says so in words instead of implying a per-bot split the Gateway does not
 * provide.
 */

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

  const [collapsed, setCollapsed] = useState(false);
  const [rows, setRows] = useState<ProjectRailRow[] | null>(null);
  const [rowsError, setRowsError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [showAllProjects, setShowAllProjects] = useState(false);
  const [showAllStandalone, setShowAllStandalone] = useState(false);
  // A bot switch changes every derived number below it, so a slow read that
  // started under the previous bot must not be painted under the new one.
  const generationRef = useRef(0);

  const botName = activeBot ? activeBot.name : null;
  const projectKey = projects.map((project) => project.id).join("|");

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
      // The rail keeps the previous rows rather than blanking them, but it says
      // the refresh failed, because stale numbers presented as current are the
      // same defect class as invented ones.
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

  const groups = groupConversations(threads, projects);
  const presence = activeBot
    ? botPresence(activeBot)
    : { state: "unrecorded" as const, label: "Lead Agent auto-routes", raw: null };

  const projectRows = projects.map((project) => railRowFor(rows ?? [], project.id));
  const visibleProjects = showAllProjects ? projectRows : projectRows.slice(0, 4);
  const hiddenProjects = projectRows.length - visibleProjects.length;
  const standalone = groups.standalone.items;
  const visibleStandalone = showAllStandalone ? standalone : standalone.slice(0, 3);
  const hiddenStandalone = standalone.length - visibleStandalone.length;

  return (
    <div className="border-b border-border/60" data-shell="bot-workspace">
      {/* ── current agent ────────────────────────────────────────────── */}
      <div className="px-2 pt-2">
        <Menu
          label={`Bot menu for ${activeBot ? botDisplayName(activeBot) : "Lead Agent"}`}
          trigger={
            <span className="flex items-center gap-2 min-w-0 flex-1">
              {activeBot ? (
                <BotGlyph initials={botInitials(activeBot)} avatar={activeBot.avatar} presence={presence} />
              ) : (
                <LeadGlyph />
              )}
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-semibold leading-tight">
                  {activeBot ? botDisplayName(activeBot) : "Lead Agent"}
                </span>
                <PresenceLine view={presence} className="mt-0.5" />
              </span>
            </span>
          }
        >
          {(close) => (
            <>
              <MenuGroup caption="Start something">
                <MenuItem
                  icon={<MessageSquare className="size-3.5" />}
                  label="New Conversation"
                  hint="A plain chat with this bot. No project needed."
                  onClick={() => {
                    close();
                    onNewConversation(null);
                  }}
                />
                <MenuItem
                  icon={<Building2 className="size-3.5" />}
                  label="New Project"
                  hint="Creates the project through POST /api/projects and attaches this bot as lead."
                  onClick={() => {
                    close();
                    setCreating(true);
                  }}
                />
              </MenuGroup>
              <MenuGroup caption={`This bot's conversations (${standalone.length + groups.projects.reduce((sum, group) => sum + group.items.length, 0)})`}>
                {groups.projects.length === 0 && standalone.length === 0 ? (
                  <p className="px-2 py-1.5 text-[11px] text-muted-foreground italic">
                    The Gateway listed no conversations for this bot.
                  </p>
                ) : null}
                {groups.projects.map((group) => (
                  <MenuLink
                    key={group.projectId}
                    label={group.name ?? `Project ${group.projectId}`}
                    value={`${group.items.length}`}
                    muted={!group.name}
                    onClick={() => {
                      close();
                      if (group.projectId) onPickProject(group.projectId);
                    }}
                  />
                ))}
                {standalone.map((thread) => (
                  <MenuLink
                    key={thread.thread_id}
                    label={threadTitle(thread as unknown as Record<string, unknown>)}
                    value=""
                    onClick={() => {
                      close();
                      onSelectThread(thread.thread_id);
                    }}
                  />
                ))}
              </MenuGroup>
              <MenuGroup caption="This bot's projects">
                {projects.length === 0 ? (
                  <p className="px-2 py-1.5 text-[11px] text-muted-foreground italic">
                    The Gateway listed no projects.
                  </p>
                ) : (
                  projects.map((project) => (
                    <MenuLink
                      key={project.id}
                      label={project.name || "Untitled project"}
                      value={projectStatusText(project.status)}
                      onClick={() => {
                        close();
                        onPickProject(project.id);
                      }}
                    />
                  ))
                )}
              </MenuGroup>
              <MenuGroup caption="More">
                <MenuItem
                  icon={<Settings className="size-3.5" />}
                  label="Bot settings"
                  hint="Opens the Bots view for this bot's profile and reputation."
                  onClick={() => {
                    close();
                    onOpenView("bots");
                  }}
                />
              </MenuGroup>
            </>
          )}
        </Menu>

        <div className="flex items-center gap-1 mt-1.5">
          <button
            type="button"
            onClick={() => onNewConversation(null)}
            className="flex-1 inline-flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-lg bg-primary text-primary-foreground text-[11px] font-semibold hover:opacity-95"
            title="New Conversation: a plain chat with the selected bot and no project"
          >
            <Plus className="size-3.5" /> New Conversation
          </button>
          <button
            type="button"
            onClick={() => setCollapsed((value) => !value)}
            aria-expanded={!collapsed}
            aria-label={collapsed ? "Expand the bot workspace" : "Collapse the bot workspace"}
            title={collapsed ? "Expand the bot workspace" : "Collapse the bot workspace"}
            className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted"
          >
            {collapsed ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          </button>
        </div>

        {/* The bot switcher. Selecting one scopes every group below it, which
            is the first rule of the hierarchy. */}
        <label className="mt-1.5 flex items-center gap-1.5 px-1">
          <span className="text-[10px] text-muted-foreground shrink-0">Bot</span>
          <select
            value={botName ?? ""}
            onChange={(event) => {
              const next = event.target.value;
              onSelectBot(next ? bots.find((bot) => bot.name === next) ?? null : null);
            }}
            aria-label="Select the bot whose workspace you are working in"
            title="Selecting a bot scopes the projects and conversations below. The Gateway reports no bot filter on the project list, so the project group is not silently narrowed to this bot."
            className="flex-1 min-w-0 text-[11px] bg-muted/60 border border-border/80 rounded-lg px-1.5 py-1 text-foreground focus:outline-none focus:ring-1 focus:ring-primary/40 cursor-pointer"
          >
            <option value="">Lead Agent (auto-routes)</option>
            {bots.map((bot) => (
              <option key={bot.name} value={bot.name}>
                {botDisplayName(bot)}
              </option>
            ))}
          </select>
          {bots.length === 0 && (
            <span className="text-[10px] text-muted-foreground italic" title="GET /api/bots returned no rows.">
              none
            </span>
          )}
        </label>
      </div>

      {!collapsed && (
        <div className="px-2 pb-2 space-y-2">
          {/* ── projects ─────────────────────────────────────────────── */}
          <section aria-label="Projects">
            <div className="flex items-center gap-1 px-1 pt-1">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground flex-1">
                Projects{" "}
                {projects.length === 0 ? (
                  <span className="italic normal-case tracking-normal">— none listed</span>
                ) : (
                  <span className="tabular-nums">({projects.length})</span>
                )}
              </p>
              <button
                type="button"
                onClick={() => setCreating(true)}
                className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted"
                title="New Project: creates it through POST /api/projects"
                aria-label="New Project"
              >
                <Plus className="size-3.5" />
              </button>
              <button
                type="button"
                onClick={() => void loadRows()}
                disabled={refreshing}
                className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-40"
                title="Re-read each project's conversation count and team from the Gateway"
                aria-label="Refresh project counts"
              >
                <RefreshCw className={`size-3.5 ${refreshing ? "animate-spin" : ""}`} />
              </button>
            </div>

            {/* The Gateway has no per-bot project filter, and saying so is the
                difference between a scoped list and a list that looks scoped. */}
            {projects.length > 0 && (
              <p className="px-1 pt-0.5 text-[10px] text-muted-foreground/90">
                {activeBot
                  ? `The Gateway lists every project on this installation and exposes no per-bot filter, so this is not narrowed to ${botDisplayName(activeBot)}. The badge on each row is the one thing the Gateway does answer: whether that bot is on the project.`
                  : "The Gateway lists every project on this installation. Select a bot to see which ones it is on."}
              </p>
            )}

            {rowsError && (
              <p className="px-1 pt-1 text-[10px] text-destructive" role="alert">
                The project refresh failed: {rowsError} The counts below may be from the previous read.
              </p>
            )}

            {projects.length === 0 ? (
              <p className="px-1 pt-1 text-[11px] text-muted-foreground italic">
                GET /projects returned no projects. That is the server saying there are none, not a failed read.
              </p>
            ) : (
              <div className="mt-1 space-y-0.5">
                {visibleProjects.map((project) => (
                  <ProjectRow
                    key={project.projectId}
                    project={projects.find((candidate) => candidate.id === project.projectId) ?? null}
                    row={project}
                    active={project.projectId === activeProjectId}
                    onOpenOverview={() => {
                      onPickProject(project.projectId);
                      onOpenView("projects");
                    }}
                    onNewConversation={() => onNewConversation(project.projectId)}
                    onOpenFiles={() => {
                      onPickProject(project.projectId);
                      onNewConversation(project.projectId);
                      onOpenView("files");
                    }}
                    onOpenTasks={() => {
                      onPickProject(project.projectId);
                      onOpenView("kanban");
                    }}
                    onOpenSettings={() => {
                      onPickProject(project.projectId);
                      onOpenView("projects");
                    }}
                  />
                ))}
                {hiddenProjects > 0 && (
                  <button
                    type="button"
                    onClick={() => setShowAllProjects(true)}
                    className="w-full px-2 py-1 text-left text-[10px] text-primary hover:underline"
                  >
                    View all {hiddenProjects} more project{hiddenProjects === 1 ? "" : "s"}
                  </button>
                )}
                {showAllProjects && projectRows.length > 4 && (
                  <button
                    type="button"
                    onClick={() => setShowAllProjects(false)}
                    className="w-full px-2 py-1 text-left text-[10px] text-muted-foreground hover:text-foreground"
                  >
                    Show fewer
                  </button>
                )}
              </div>
            )}
          </section>

          {/* ── standalone conversations ─────────────────────────────── */}
          <section aria-label="Standalone conversations">
            <div className="flex items-center gap-1 px-1">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground flex-1 truncate">
                Standalone Conversations{" "}
                {standalone.length === 0 ? (
                  <span className="italic normal-case tracking-normal">— none</span>
                ) : (
                  <span className="tabular-nums">({standalone.length})</span>
                )}
              </p>
              {hiddenStandalone > 0 && (
                <button
                  type="button"
                  onClick={() => setShowAllStandalone(true)}
                  className="text-[10px] text-primary hover:underline"
                >
                  View all {hiddenStandalone}
                </button>
              )}
            </div>
            <p className="px-1 text-[10px] text-muted-foreground/90">
              Conversations with no project. Kept apart from projects on purpose.
            </p>
            {standalone.length === 0 ? (
              <p className="px-1 pt-1 text-[11px] text-muted-foreground italic">
                None. Every conversation in view belongs to a project.
              </p>
            ) : (
              <div className="mt-0.5 space-y-0.5">
                {visibleStandalone.map((thread) => (
                  <button
                    key={thread.thread_id}
                    type="button"
                    onClick={() => onSelectThread(thread.thread_id)}
                    className={`w-full flex items-center gap-1.5 px-2 py-1 rounded-lg text-left text-[11px] ${
                      thread.thread_id === activeThreadId
                        ? "bg-muted text-foreground font-medium"
                        : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                    }`}
                  >
                    <MessageSquare className="size-3 shrink-0 opacity-60" />
                    <span className="truncate">{threadTitle(thread as unknown as Record<string, unknown>)}</span>
                  </button>
                ))}
              </div>
            )}
          </section>
        </div>
      )}

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

/**
 * One project row: its name, the one number the Gateway actually reports for it,
 * whether the selected bot is on it, and the eight-item project menu.
 */
function ProjectRow(props: {
  project: Project | null;
  row: ProjectRailRow;
  active: boolean;
  onOpenOverview: () => void;
  onNewConversation: () => void;
  onOpenFiles: () => void;
  onOpenTasks: () => void;
  onOpenSettings: () => void;
}) {
  const { project, row } = props;
  const status = project ? projectStatusText(project.status) : "status not reported";
  return (
    <div className={`group rounded-lg ${props.active ? "bg-muted/70" : "hover:bg-muted/40"}`}>
      <div className="flex items-center gap-1 pl-2 pr-1">
        <button
          type="button"
          onClick={props.onOpenOverview}
          className="flex items-center gap-1.5 min-w-0 flex-1 text-left py-1"
          title={`${project?.name || row.projectId} — open this project's overview`}
        >
          <FolderOpen className="size-3 shrink-0 text-muted-foreground" />
          <span className="truncate text-[11px] font-medium">{project?.name || row.projectId}</span>
          {/* The count, with its own unknown case. `count` is the length of the
              server's paginated list, so 0 is a real measurement. */}
          <MeasuredCount
            value={row.conversationCount}
            source="GET /api/projects/{id}/threads"
            className="ml-auto shrink-0 text-[10px] text-muted-foreground"
          />
        </button>
        {row.leadsSelectedBot === true ? (
          <span className="shrink-0 text-[9px] px-1 py-0.5 rounded bg-primary/15 text-primary font-semibold" title={`${props.row.members?.join(", ") || "This bot"} — from GET /api/projects/{id}/presence`}>
            on it
          </span>
        ) : row.leadsSelectedBot === false ? (
          <span
            className="shrink-0 text-[9px] px-1 py-0.5 rounded bg-muted text-muted-foreground"
            title={`Not on this project's team. Members: ${row.members?.join(", ") || "none listed"} — from GET /api/projects/{id}/presence`}
          >
            not on
          </span>
        ) : (
          <span
            className="shrink-0 text-[9px] px-1 py-0.5 rounded bg-muted text-muted-foreground italic"
            title={
              row.crewError
                ? `The project team could not be read, so whether this bot is on it is unknown: ${row.crewError}`
                : "The project team was not read, so whether this bot is on it is unknown."
            }
          >
            team ?
          </span>
        )}
        <Menu
          compact
          label={`Project menu for ${project?.name || row.projectId}`}
          trigger={<MoreHorizontal className="size-3.5" />}
        >
          {(close) => (
            <>
              <MenuGroup caption={project?.name || row.projectId}>
                <p className="px-2 pt-0.5 pb-1 text-[10px] text-muted-foreground">
                  Status: {status}.{" "}
                  {row.conversationCount === null
                    ? "Conversation count not reported."
                    : `${row.conversationCount} conversation${row.conversationCount === 1 ? "" : "s"} in the loaded list.`}
                </p>
                <MenuItem
                  icon={<SquareStack className="size-3.5" />}
                  label="Project overview"
                  hint="Opens the Projects view with this project selected."
                  onClick={() => {
                    close();
                    props.onOpenOverview();
                  }}
                />
                <MenuItem
                  icon={<MessageSquare className="size-3.5" />}
                  label="New conversation in this project"
                  hint="Opens a blank conversation scoped to this project."
                  onClick={() => {
                    close();
                    props.onNewConversation();
                  }}
                />
                <MenuItem
                  icon={<FileText className="size-3.5" />}
                  label="Files and knowledge"
                  hint="The Gateway has no project-scoped file route. This opens the conversation-scoped Files view for a new conversation in this project."
                  onClick={() => {
                    close();
                    props.onOpenFiles();
                  }}
                />
                <MenuItem
                  icon={<ListChecks className="size-3.5" />}
                  label="Tasks"
                  hint="Opens the Kanban view. Task counts for a project come from GET /api/projects/{id}/state."
                  onClick={() => {
                    close();
                    props.onOpenTasks();
                  }}
                />
                <MenuItem
                  icon={<Settings className="size-3.5" />}
                  label="Project settings"
                  hint="Opens the Projects view, where the crew and collaboration policy are edited."
                  onClick={() => {
                    close();
                    props.onOpenSettings();
                  }}
                />
              </MenuGroup>
            </>
          )}
        </Menu>
      </div>
      {row.conversationError && (
        <p className="px-2 pb-1 text-[10px] text-destructive" role="alert">
          Conversation count unavailable: {row.conversationError}
        </p>
      )}
    </div>
  );
}
