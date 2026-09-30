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
} from "@/lib/chat-shell";
import { ProjectOverviewPanel } from "@/components/sections/ProjectOverviewPanel";
import { ProjectCrewPanel } from "@/components/sections/ProjectCrewPanel";
import { WorkspaceView } from "@/lib/workspace-view";
import { Badge, Btn, ErrorBox, SkeletonList } from "@/components/ui";
import {
  CountTile,
  Failed,
  Loading,
  ServerSaidNothing,
  SourceUnavailable,
} from "./Honest";
import { UNAVAILABLE_SOURCES } from "@/lib/chat-shell";

/**
 * The project detail panel: Overview, Conversations, Files, Tasks, Settings.
 *
 * The panel is honest about which of those five the Gateway can actually fill,
 * and the split is the whole design:
 *
 * | Section      | Source                                                   | Can it be filled? |
 * | ------------ | -------------------------------------------------------- | ----------------- |
 * | Overview     | `GET /projects/{id}/state` + `/decisions` + `/events` + `/approvals` + `/locks` + `/checkpoints` + `/handoffs` + `/constitution` (via `ProjectOverviewPanel`) | yes |
 * | Conversations| `GET /projects/{id}/threads`, all pages                  | yes |
 * | Files        | **no project-scoped route exists**                       | shell + disclosure |
 * | Tasks        | counters from `/state`; **no task list route exists**     | counters only, disclosure for the list |
 * | Settings     | `GET /projects/{id}/crew` + `PATCH /projects/{id}/collaboration` (via `ProjectCrewPanel`) | yes |
 *
 * Files and Tasks are the two the reference design drew numbers in — `Files 5`
 * and `Tasks 2`. Neither number has a source. Rendering an empty Files panel
 * would read as "this project has no files", which is false: the Gateway simply
 * does not expose project-scoped files at all, so the panel says so instead of
 * showing a count. The Tasks section shows the four task counters `/state` does
 * report, and states that there is no task list behind them.
 *
 * Every read is independent. One that fails states the server's reason and the
 * rest still render, and the panel says up front when it is partial.
 */
export function ProjectDetailPanel(props: {
  project: Project;
  onOpenView: (view: WorkspaceView) => void;
  onOpenThread: (threadId: string) => void;
  onNewConversationInProject: () => void;
}) {
  const { project } = props;
  const [tab, setTab] = useState<"overview" | "conversations" | "files" | "tasks" | "settings">("overview");
  const [threads, setThreads] = useState<ProjectThread[] | null>(null);
  const [threadsError, setThreadsError] = useState<string | null>(null);
  const [loadingThreads, setLoadingThreads] = useState(false);
  const [quickResult, setQuickResult] = useState<{ route: string; ok: boolean; text: string } | null>(null);
  const [quickBusy, setQuickBusy] = useState<string | null>(null);

  const loadThreads = useCallback(async () => {
    setLoadingThreads(true);
    setThreadsError(null);
    try {
      setThreads(await projectThreads(project.id));
    } catch (error) {
      // A failed read must not leave the previous list on screen looking like a
      // fresh answer; it is cleared and the failure is shown.
      setThreads(null);
      setThreadsError(error instanceof Error ? error.message : String(error));
    } finally {
      setLoadingThreads(false);
    }
  }, [project.id]);

  useEffect(() => {
    void loadThreads();
  }, [loadThreads]);

  useEffect(() => {
    setQuickResult(null);
  }, [project.id]);

  const rows: ProjectConversationRow[] = threads ? projectConversationRows(threads) : [];
  const phase: string | null = null;

  const runQuickAction = async (action: QuickAction) => {
    if (!action.enabled) return;
    setQuickBusy(action.id);
    setQuickResult(null);
    try {
      // Quick actions are *reads and one declared mutation*. Every one names the
      // exact route it will call, and the result panel shows the server's own
      // response or its own reason.
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

  const tabs: Array<[typeof tab, string, React.ReactNode]> = [
    ["overview", "Overview", <ShieldCheck className="size-3" key="i" />],
    ["conversations", "Conversations", <MessagesSquare className="size-3" key="i" />],
    ["files", "Files", <FileText className="size-3" key="i" />],
    ["tasks", "Tasks", <ListChecks className="size-3" key="i" />],
    ["settings", "Settings", <Settings2 className="size-3" key="i" />],
  ];

  return (
    <div className="space-y-2.5" data-shell="project-detail">
      <div className="flex items-start justify-between gap-2 flex-wrap">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold truncate">{project.name}</h3>
          <p className="text-[11px] text-muted-foreground">
            {/* The server's own status word, and its own id. The id is shown
                because a project with no readable name still has one, and
                pretending otherwise would hide it. */}
            status <Badge tone="gray">{projectStatusText(project.status)}</Badge>{" "}
            <span className="font-mono text-[10px]" title="The project id the Gateway assigned. Every route below is scoped to it.">
              {project.id}
            </span>
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <Btn variant="ghost" onClick={props.onNewConversationInProject} title="Open a blank conversation scoped to this project">
            <MessagesSquare className="size-3.5" /> New conversation
          </Btn>
          <Btn variant="ghost" onClick={() => props.onOpenView("projects")} title="Open the Projects view">
            <ExternalLink className="size-3.5" /> Projects
          </Btn>
        </div>
      </div>

      <div className="flex items-center gap-1 flex-wrap" role="tablist" aria-label="Project detail sections">
        {tabs.map(([id, label, icon]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={`inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium ${
              tab === id ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted"
            }`}
          >
            {icon} {label}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="space-y-2.5">
          <QuickActions
            projectId={project.id}
            phase={phase}
            busy={quickBusy}
            result={quickResult}
            onRun={runQuickAction}
          />
          <ProjectOverviewPanel projectId={project.id} onOpenLive={(id) => props.onOpenView("projects")} />
        </div>
      )}

      {tab === "conversations" && (
        <div className="space-y-2">
          <div className="flex items-center gap-1.5">
            <p className="text-[11px] font-semibold inline-flex items-center gap-1.5">
              <MessagesSquare className="size-3.5 text-primary" /> Conversations in this project
            </p>
            <button
              type="button"
              onClick={() => void loadThreads()}
              disabled={loadingThreads}
              className="ml-auto inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-border text-[10px] hover:bg-muted disabled:opacity-40"
            >
              <RefreshCw className={`size-3 ${loadingThreads ? "animate-spin" : ""}`} /> Refresh
            </button>
          </div>
          {loadingThreads && threads === null ? (
            <SkeletonList rows={2} />
          ) : threadsError ? (
            <Failed what="This project's conversations" reason={threadsError} onRetry={() => void loadThreads()} />
          ) : rows.length === 0 ? (
            <ServerSaidNothing what="The Gateway returned no conversations for this project. That is the server's answer, not a failed read." />
          ) : (
            <>
              <p className="text-[11px] text-muted-foreground">
                {rows.length} conversation{rows.length === 1 ? "" : "s"}, all pages of{" "}
                <code className="font-mono text-[10px]">GET /api/projects/{"{id}"}/threads</code>.
              </p>
              <ul className="space-y-1">
                {rows.map((row) => (
                  <li key={row.threadId}>
                    <button
                      type="button"
                      onClick={() => props.onOpenThread(row.threadId)}
                      className="w-full text-left rounded-lg border border-border/60 bg-card px-2.5 py-1.5 hover:border-primary/40"
                      title={row.absolute ?? "The Gateway sent no timestamp for this conversation."}
                    >
                      <span className="block text-[11px] font-medium truncate">
                        {row.displayName ?? (
                          <span className="text-muted-foreground italic">no display name reported</span>
                        )}
                      </span>
                      <span className="block text-[10px] text-muted-foreground">
                        {row.relative ? <span>{row.relative}</span> : <span className="italic">{row.whenText}</span>}
                        <span className="font-mono text-[9px] ml-1 opacity-70">{row.threadId}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {tab === "files" && (
        <div className="space-y-2">
          <SourceUnavailable
            what={UNAVAILABLE_SOURCES.projectFiles.what}
            reason={UNAVAILABLE_SOURCES.projectFiles.reason}
            route={UNAVAILABLE_SOURCES.projectFiles.missingRoute}
          />
          <p className="text-[11px] text-muted-foreground">
            The route that does exist is{" "}
            <code className="font-mono text-[10px]">{UNAVAILABLE_SOURCES.projectFiles.actualRoute}</code>, which is
            scoped to one conversation rather than to the project.
          </p>
          <button
            type="button"
            onClick={() => props.onOpenView("files")}
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border text-[11px] font-semibold hover:bg-muted"
            title="Opens the Files view, which lists the files of a conversation"
          >
            <FileText className="size-3.5" /> Open the conversation-scoped Files view
          </button>
        </div>
      )}

      {tab === "tasks" && (
        <TasksSection projectId={project.id} onOpenView={props.onOpenView} />
      )}

      {tab === "settings" && (
        <div className="space-y-2">
          <p className="text-[11px] text-muted-foreground">
            Name, instructions, the crew roster and the collaboration policy. The crew inspector below is the
            existing, live control surface; nothing here reimplements it.
          </p>
          <ProjectCrewPanel projectId={project.id} projectName={project.name} />
        </div>
      )}
    </div>
  );
}

/**
 * The Tasks section.
 *
 * The four task counters come from `GET /projects/{id}/state`, which is a real
 * measurement. The task *list* does not exist: there is no
 * `GET /projects/{id}/tasks`, so the panel states that rather than rendering an
 * empty board, and the button opens the company-wide board whose actual route
 * is named.
 */
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
    <div className="space-y-2">
      <div className="flex items-center gap-1.5">
        <p className="text-[11px] font-semibold inline-flex items-center gap-1.5">
          <ListChecks className="size-3.5 text-primary" /> Task counters
        </p>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="ml-auto inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-border text-[10px] hover:bg-muted disabled:opacity-40"
        >
          <RefreshCw className={`size-3 ${loading ? "animate-spin" : ""}`} /> Refresh
        </button>
      </div>

      {loading && !state ? (
        <Loading what="Reading the project state…" />
      ) : error ? (
        <Failed what="The project state" reason={error} onRetry={() => void load()} />
      ) : (
        <>
          <p className="text-[10px] text-muted-foreground">
            From <code className="font-mono">GET /api/projects/{"{id}"}/state</code>. Each tile is that field, or an
            explicit "not reported" when the Gateway did not send it.
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5">
            <CountTile label="active" value={read("active_tasks")} source="state.active_tasks" />
            <CountTile label="blocked" value={read("blocked_tasks")} source="state.blocked_tasks" />
            <CountTile label="completed" value={read("completed_tasks")} source="state.completed_tasks" />
            <CountTile label="failed" value={read("failed_tasks")} source="state.failed_tasks" />
          </div>
        </>
      )}

      <SourceUnavailable
        what={UNAVAILABLE_SOURCES.projectTaskList.what}
        reason={UNAVAILABLE_SOURCES.projectTaskList.reason}
        route={UNAVAILABLE_SOURCES.projectTaskList.missingRoute}
      />

      <button
        type="button"
        onClick={() => props.onOpenView("kanban")}
        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border text-[11px] font-semibold hover:bg-muted"
        title="Opens the company board, which is served by GET /api/company/kanban/tasks and is not scoped to a project"
      >
        <ListChecks className="size-3.5" /> Open the company board
      </button>
      <p className="text-[10px] text-muted-foreground">
        The board is served by <code className="font-mono">GET /api/company/kanban/tasks</code> and belongs to the
        company, not to this project, so its cards are not counted as this project's tasks.
      </p>
    </div>
  );
}

/**
 * Quick actions: every one names the exact route it will call, and the result
 * shows the server's response or its reason.
 *
 * The phase action is the only mutating one and it is offered disabled unless a
 * phase was actually chosen, because `POST /projects/{id}/phase` rejects an
 * empty phase with a 422 and a control that can only fail implies a pending
 * state that does not exist.
 */
function QuickActions(props: {
  projectId: string;
  phase: string | null;
  busy: string | null;
  result: { route: string; ok: boolean; text: string } | null;
  onRun: (action: QuickAction) => void;
}) {
  const actions = projectQuickActions(props.projectId, props.phase);
  return (
    <div className="space-y-1.5">
      <p className="text-[11px] font-semibold inline-flex items-center gap-1.5">
        <Activity className="size-3.5 text-primary" /> Quick actions
      </p>
      <div className="flex flex-wrap gap-1.5">
        {actions.map((action) => (
          <button
            key={action.id}
            type="button"
            disabled={!action.enabled || props.busy !== null}
            onClick={() => props.onRun(action)}
            title={
              action.enabled
                ? `${action.method} ${action.route}`
                : `${action.method} ${action.route} — unavailable: ${action.reason}`
            }
            className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-border/70 text-[10px] font-medium hover:border-primary/50 hover:bg-muted/50 disabled:opacity-40"
          >
            {props.busy === action.id ? <RefreshCw className="size-3 animate-spin" /> : <Gavel className="size-3 text-muted-foreground" />}
            <span className="flex flex-col leading-tight text-left">
              <span>{action.label}</span>
              <code className="text-[9px] text-muted-foreground font-mono">{action.route}</code>
            </span>
          </button>
        ))}
      </div>
      {props.result && (
        <div
          className={`rounded-lg border px-2.5 py-1.5 ${props.result.ok ? "border-border/60 bg-card/60" : "border-destructive/40 bg-destructive/5"}`}
          role={props.result.ok ? undefined : "alert"}
          data-read={props.result.ok ? "ok" : "failed"}
        >
          <p className="text-[10px] font-semibold">
            <code className="font-mono">{props.result.route}</code>{" "}
            {props.result.ok ? (
              <span className="text-muted-foreground font-normal">returned</span>
            ) : (
              <span className="text-destructive">did not answer</span>
            )}
          </p>
          <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted-foreground font-mono">
            {props.result.text}
          </pre>
        </div>
      )}
    </div>
  );
}

/** Turn a declared route template into the path the client sends. */
function pathOf(action: QuickAction): string {
  const marker = "/api/projects/";
  const start = action.route.indexOf(marker);
  if (start < 0) return action.route.replace("/api", "");
  return action.route.slice(start + "/api".length);
}

function render(body: unknown): string {
  if (body === null) return "null (the server sent an empty body)";
  if (typeof body === "string") return body;
  try {
    return JSON.stringify(body, null, 2);
  } catch {
    return String(body);
  }
}
