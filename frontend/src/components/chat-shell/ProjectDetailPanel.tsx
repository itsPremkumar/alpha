"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  Activity,
  ExternalLink,
  FileText,
  Gavel,
  ListChecks,
  MessagesSquare,
  RefreshCw,
  Settings2,
  ShieldCheck,
  X,
  Plus,
  Upload,
  FolderPlus,
  Folder,
  ChevronDown,
  Clock,
  Code2,
  Bot,
} from "lucide-react";
import {
  projectThreads,
  type Project,
  type ProjectThread,
} from "@/lib/projects";
import {
  projectQuickActions,
  projectStatusText,
  projectConversationRows,
  type QuickAction,
  type ProjectConversationRow,
  UNAVAILABLE_SOURCES,
} from "@/lib/chat-shell";
import { ProjectOverviewPanel } from "@/components/sections/ProjectOverviewPanel";
import { ProjectCrewPanel } from "@/components/sections/ProjectCrewPanel";
import { WorkspaceView } from "@/lib/workspace-view";
import { Badge, Btn, SkeletonList } from "@/components/ui";
import {
  CountTile,
  Failed,
  Loading,
  ServerSaidNothing,
  SourceUnavailable,
} from "./Honest";
import type { BotProfile } from "@/types/bots";
import { botDisplayName } from "@/types/bots";

export interface ProjectDetailPanelProps {
  project: Project | null;
  activeBot?: BotProfile | null;
  projects?: Project[];
  onOpenView: (view: WorkspaceView) => void;
  onOpenThread: (threadId: string) => void;
  onNewConversationInProject: () => void;
  onClose?: () => void;
  onPickProject?: (projectId: string | null) => void;
  onNewProject?: () => void;
}

export function ProjectDetailPanel(props: ProjectDetailPanelProps) {
  const {
    project,
    activeBot,
    projects = [],
    onOpenView,
    onOpenThread,
    onNewConversationInProject,
    onClose,
    onPickProject,
    onNewProject,
  } = props;

  const [tab, setTab] = useState<"overview" | "conversations" | "files" | "tasks" | "settings">("overview");
  const [threads, setThreads] = useState<ProjectThread[] | null>(null);
  const [threadsError, setThreadsError] = useState<string | null>(null);
  const [loadingThreads, setLoadingThreads] = useState(false);
  const [quickResult, setQuickResult] = useState<{ route: string; ok: boolean; text: string } | null>(null);
  const [quickBusy, setQuickBusy] = useState<string | null>(null);

  const loadThreads = useCallback(async () => {
    if (!project) {
      setThreads([]);
      return;
    }
    setLoadingThreads(true);
    setThreadsError(null);
    try {
      setThreads(await projectThreads(project.id));
    } catch (error) {
      setThreads(null);
      setThreadsError(error instanceof Error ? error.message : String(error));
    } finally {
      setLoadingThreads(false);
    }
  }, [project?.id]);

  useEffect(() => {
    void loadThreads();
  }, [loadThreads]);

  useEffect(() => {
    setQuickResult(null);
  }, [project?.id]);

  const rows: ProjectConversationRow[] = threads ? projectConversationRows(threads) : [];
  const phase: string | null = null;

  const runQuickAction = async (action: QuickAction) => {
    if (!action.enabled) return;
    setQuickBusy(action.id);
    setQuickResult(null);
    try {
      const { get } = await import("@/lib/http");
      const body = action.method === "GET" ? await get<unknown>(pathOf(action)) : null;
      setQuickResult({ route: action.route, ok: true, text: render(body) });
    } catch (error) {
      setQuickResult({
        route: action.route,
        ok: false,
        text: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setQuickBusy(null);
    }
  };

  const tabs: Array<[typeof tab, string, React.ReactNode, string | number | null]> = [
    ["overview", "Overview", <ShieldCheck className="size-3.5" key="i" />, null],
    ["conversations", "Conversations", <MessagesSquare className="size-3.5" key="i" />, rows.length],
    ["files", "Files", <FileText className="size-3.5" key="i" />, null],
    ["tasks", "Tasks", <ListChecks className="size-3.5" key="i" />, null],
    ["settings", "Settings", <Settings2 className="size-3.5" key="i" />, null],
  ];

  if (!project) {
    return (
      <div className="flex flex-col h-full bg-card/60 border-l border-border/60 select-none overflow-y-auto p-4 space-y-4" data-shell="project-detail">
        <div className="flex items-center justify-between pb-3 border-b border-border/50">
          <div className="flex items-center gap-2">
            <Folder className="size-4 text-primary" />
            <span className="font-semibold text-xs text-foreground">Project Inspector</span>
          </div>
          {onClose && (
            <button type="button" onClick={onClose} className="p-1 rounded text-muted-foreground hover:text-foreground">
              <X className="size-4" />
            </button>
          )}
        </div>
        <div className="text-center py-8 space-y-3">
          <div className="size-12 rounded-xl bg-muted/60 text-muted-foreground mx-auto flex items-center justify-center">
            <Folder className="size-6" />
          </div>
          <div className="space-y-1">
            <h4 className="text-xs font-semibold text-foreground">No Project Selected</h4>
            <p className="text-[11px] text-muted-foreground max-w-xs mx-auto">
              Select or create a project to inspect files, team tasks, and conversations.
            </p>
          </div>
          {projects.length > 0 && (
            <div className="pt-2 text-left space-y-1.5">
              <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Select Project</span>
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {projects.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => onPickProject?.(p.id)}
                    className="w-full text-left px-2.5 py-1.5 rounded-lg border border-border/60 hover:border-primary/40 hover:bg-muted/40 text-xs flex items-center gap-2 transition-colors"
                  >
                    <Folder className="size-3.5 text-primary shrink-0" />
                    <span className="truncate flex-1 font-medium">{p.name || p.id}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {onNewProject && (
            <Btn variant="ghost" onClick={onNewProject} className="w-full gap-1.5 text-xs mt-2">
              <FolderPlus className="size-3.5" /> Create New Project
            </Btn>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-card/60 border-l border-border/60 select-none overflow-y-auto" data-shell="project-detail">
      {/* ── 1. Inspector Header ───────────────────────────────────────── */}
      <div className="p-3.5 border-b border-border/50 flex items-center justify-between gap-2 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <div className="size-7 rounded-lg bg-primary/10 text-primary flex items-center justify-center shrink-0">
            <Folder className="size-4" />
          </div>
          <span className="font-semibold text-xs text-foreground truncate">
            Project: {project.name || project.id}
          </span>
        </div>

        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
            title="Close Inspector"
            aria-label="Close Inspector"
          >
            <X className="size-4" />
          </button>
        )}
      </div>

      {/* ── 2. Segmented Pill Tabs ───────────────────────────────────── */}
      <div className="p-2 border-b border-border/50 shrink-0">
        <div className="flex items-center gap-1 overflow-x-auto pb-0.5" role="tablist">
          {tabs.map(([id, label, icon, count]) => {
            const isSelected = tab === id;
            return (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={isSelected}
                onClick={() => setTab(id)}
                className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-medium transition-all shrink-0 cursor-pointer ${
                  isSelected
                    ? "bg-primary text-primary-foreground font-semibold shadow-xs"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                }`}
              >
                {icon}
                <span>{label}</span>
                {count !== null && (
                  <span
                    className={`text-[10px] px-1.5 py-0.2 rounded-full tabular-nums ${
                      isSelected ? "bg-white/20 text-white" : "bg-muted text-muted-foreground"
                    }`}
                  >
                    {count}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* ── 3. Tab Contents ──────────────────────────────────────────── */}
      <div className="p-3 space-y-4 flex-1 overflow-y-auto">
        {/* OVERVIEW TAB (Mirrors the Reference Design) */}
        {tab === "overview" && (
          <div className="space-y-4">
            {/* Project Details Card */}
            <div className="space-y-2">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Project Details
              </p>
              <div className="rounded-xl border border-border/70 bg-card p-3 space-y-2 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Name</span>
                  <span className="font-semibold text-foreground truncate max-w-44">
                    {project.name || "Untitled"}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Agent</span>
                  <div className="flex items-center gap-1.5 font-medium text-foreground">
                    <div className="size-5 rounded-md bg-primary/10 text-primary flex items-center justify-center text-[10px]">
                      {activeBot?.avatar || <Code2 className="size-3" />}
                    </div>
                    <span>{activeBot ? botDisplayName(activeBot) : "Lead Agent"}</span>
                  </div>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Created</span>
                  <span className="text-foreground">
                    {project.created_at
                      ? new Date(project.created_at).toLocaleDateString(undefined, {
                          month: "short",
                          day: "numeric",
                          year: "numeric",
                        })
                      : "Apr 26, 2025"}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Status</span>
                  <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-500">
                    <span className="size-1.5 rounded-full bg-emerald-500" />
                    {projectStatusText(project.status)}
                  </span>
                </div>
              </div>
            </div>

            {/* Recent Conversations */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Recent Conversations
                </p>
                <button
                  type="button"
                  onClick={() => setTab("conversations")}
                  className="text-[11px] text-primary hover:underline font-medium"
                >
                  View all →
                </button>
              </div>

              {loadingThreads && threads === null ? (
                <SkeletonList rows={2} />
              ) : rows.length === 0 ? (
                <div className="rounded-xl border border-dashed border-border/70 p-3 text-center text-xs text-muted-foreground">
                  No conversations in this project yet.
                </div>
              ) : (
                <div className="space-y-1">
                  {rows.slice(0, 3).map((row) => (
                    <button
                      key={row.threadId}
                      type="button"
                      onClick={() => onOpenThread(row.threadId)}
                      className="w-full flex items-center justify-between gap-2 p-2 rounded-xl border border-border/60 bg-card hover:border-primary/40 text-left transition-colors cursor-pointer group"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <MessagesSquare className="size-3.5 text-muted-foreground group-hover:text-primary transition-colors shrink-0" />
                        <span className="text-xs font-medium text-foreground truncate">
                          {row.displayName || "Conversation"}
                        </span>
                      </div>
                      <span className="text-[10px] text-muted-foreground shrink-0">
                        {row.relative || row.whenText}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Quick Actions */}
            <div className="space-y-2">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Quick Actions
              </p>
              <div className="space-y-1.5">
                <button
                  type="button"
                  onClick={onNewConversationInProject}
                  className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-xl bg-primary text-primary-foreground font-semibold text-xs shadow-xs hover:opacity-95 transition-opacity cursor-pointer"
                >
                  <Plus className="size-3.5" />
                  <span>New Conversation</span>
                </button>

                <button
                  type="button"
                  onClick={() => onOpenView("files")}
                  className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-xl border border-border/70 bg-card hover:bg-muted text-foreground text-xs font-medium transition-colors cursor-pointer"
                >
                  <Upload className="size-3.5 text-muted-foreground" />
                  <span>Add Files</span>
                </button>

                {onNewProject && (
                  <button
                    type="button"
                    onClick={onNewProject}
                    className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-xl border border-border/70 bg-card hover:bg-muted text-foreground text-xs font-medium transition-colors cursor-pointer"
                  >
                    <FolderPlus className="size-3.5 text-muted-foreground" />
                    <span>Create New Project</span>
                  </button>
                )}
              </div>
            </div>

            {/* Switch Project */}
            {projects.length > 0 && onPickProject && (
              <div className="space-y-2 pt-1 border-t border-border/50">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Switch Project
                </p>
                <div className="relative">
                  <select
                    value={project.id}
                    onChange={(e) => onPickProject(e.target.value || null)}
                    className="w-full text-xs bg-card border border-border/80 rounded-xl px-3 py-2 text-foreground focus:outline-none focus:ring-1 focus:ring-primary/40 font-medium cursor-pointer appearance-none"
                    aria-label="Switch project"
                  >
                    {projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name || p.id}
                      </option>
                    ))}
                    <option value="">None (Standalone)</option>
                  </select>
                  <ChevronDown className="size-3.5 text-muted-foreground absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
                </div>
              </div>
            )}
          </div>
        )}

        {/* CONVERSATIONS TAB */}
        {tab === "conversations" && (
          <div className="space-y-2">
            <div className="flex items-center justify-between pb-1">
              <span className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                <MessagesSquare className="size-3.5 text-primary" />
                <span>Conversations ({rows.length})</span>
              </span>
              <button
                type="button"
                onClick={() => void loadThreads()}
                disabled={loadingThreads}
                className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted text-xs"
              >
                <RefreshCw className={`size-3 ${loadingThreads ? "animate-spin" : ""}`} />
              </button>
            </div>

            {loadingThreads && threads === null ? (
              <SkeletonList rows={3} />
            ) : threadsError ? (
              <Failed what="Conversations" reason={threadsError} onRetry={() => void loadThreads()} />
            ) : rows.length === 0 ? (
              <ServerSaidNothing what="No conversations recorded in this project." />
            ) : (
              <ul className="space-y-1.5">
                {rows.map((row) => (
                  <li key={row.threadId}>
                    <button
                      type="button"
                      onClick={() => onOpenThread(row.threadId)}
                      className="w-full text-left rounded-xl border border-border/60 bg-card p-2.5 hover:border-primary/50 transition-colors"
                    >
                      <span className="block text-xs font-medium text-foreground truncate">
                        {row.displayName || "Conversation"}
                      </span>
                      <span className="block text-[10px] text-muted-foreground mt-0.5">
                        {row.relative || row.whenText} • <span className="font-mono">{row.threadId.slice(0, 6)}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {/* FILES TAB */}
        {tab === "files" && (
          <div className="space-y-3 text-xs">
            <SourceUnavailable
              what={UNAVAILABLE_SOURCES.projectFiles.what}
              reason={UNAVAILABLE_SOURCES.projectFiles.reason}
              route={UNAVAILABLE_SOURCES.projectFiles.missingRoute}
            />
            <button
              type="button"
              onClick={() => onOpenView("files")}
              className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-xl border border-border text-xs font-medium hover:bg-muted"
            >
              <FileText className="size-3.5" />
              <span>Open Conversation Files View</span>
            </button>
          </div>
        )}

        {/* TASKS TAB */}
        {tab === "tasks" && (
          <div className="space-y-3">
            <TasksSection projectId={project.id} onOpenView={onOpenView} />
          </div>
        )}

        {/* SETTINGS TAB */}
        {tab === "settings" && (
          <div className="space-y-3 text-xs">
            <p className="text-muted-foreground text-[11px]">
              Manage project team, collaboration policy, and autonomy configuration.
            </p>
            <ProjectCrewPanel projectId={project.id} projectName={project.name} />
          </div>
        )}
      </div>
    </div>
  );
}

function TasksSection(props: { projectId: string; onOpenView: (view: WorkspaceView) => void }) {
  const [state, setState] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { get } = await import("@/lib/http");
      const body = await get<Record<string, unknown>>(
        `/projects/${encodeURIComponent(props.projectId)}/state`,
      );
      setState(body && typeof body === "object" ? body : null);
      if (!body || typeof body !== "object") {
        setError("The server returned an unreadable project state.");
      }
    } catch (err) {
      setState(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [props.projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const read = (key: string): number | null => {
    const value = state?.[key];
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  };

  return (
    <div className="space-y-3 text-xs">
      <div className="flex items-center justify-between">
        <span className="font-semibold text-foreground flex items-center gap-1.5">
          <ListChecks className="size-3.5 text-primary" />
          <span>Task Counters</span>
        </span>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted"
        >
          <RefreshCw className={`size-3 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {loading && !state ? (
        <Loading what="Reading project task state..." />
      ) : error ? (
        <Failed what="Task state" reason={error} onRetry={() => void load()} />
      ) : (
        <div className="grid grid-cols-2 gap-2">
          <CountTile label="Active Tasks" value={read("active_tasks")} source="state.active_tasks" />
          <CountTile label="Blocked" value={read("blocked_tasks")} source="state.blocked_tasks" />
          <CountTile label="Completed" value={read("completed_tasks")} source="state.completed_tasks" />
          <CountTile label="Failed" value={read("failed_tasks")} source="state.failed_tasks" />
        </div>
      )}

      <SourceUnavailable
        what={UNAVAILABLE_SOURCES.projectTaskList.what}
        reason={UNAVAILABLE_SOURCES.projectTaskList.reason}
        route={UNAVAILABLE_SOURCES.projectTaskList.missingRoute}
      />

      <button
        type="button"
        onClick={() => props.onOpenView("kanban")}
        className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-xl border border-border text-xs font-semibold hover:bg-muted"
      >
        <ListChecks className="size-3.5" />
        <span>Open Company Kanban Board</span>
      </button>
    </div>
  );
}

function pathOf(action: QuickAction): string {
  const marker = "/api/projects/";
  const start = action.route.indexOf(marker);
  if (start < 0) return action.route.replace("/api", "");
  return action.route.slice(start + "/api".length);
}

function render(body: unknown): string {
  if (body === null) return "null (empty body)";
  if (typeof body === "string") return body;
  try {
    return JSON.stringify(body, null, 2);
  } catch {
    return String(body);
  }
}
