"use client";

import React, { useMemo, useState } from "react";
import { BotProfile, botDisplayName } from "@/types/bots";
import { Project } from "@/lib/projects";
import { WorkspaceView } from "@/lib/workspace-view";
import { threadTitle } from "@/lib/threads-ext";
import { BotWorkspaceRail } from "./BotWorkspaceRail";
import { ProjectContextHeader } from "./ProjectContextHeader";
import { ChatShellLanding } from "./ChatShellLanding";
import { ProjectDetailPanel } from "./ProjectDetailPanel";
import { BotGlyph, LeadGlyph, PresenceLine } from "./Honest";
import { botPresence } from "@/lib/chat-shell";
import { Btn } from "@/components/ui";
import type { Thread } from "@/types/chat";

/**
 * The three chat-shell pieces, assembled from one prop set.
 *
 * They live together because they answer the same three questions from the same
 * state — *which bot, which project, which conversation* — and answering them
 * from three independently-derived copies is how a breadcrumb and a sidebar
 * start disagreeing. Everything they display is passed in; this component
 * fetches nothing, so it cannot become a second source of truth.
 *
 * Three pieces, three homes, chosen so nothing had to be removed:
 *
 *   - `BotWorkspaceRail` goes in the left column, above the conversation list
 *     (passed to `ThreadSidebar` as its `rail` prop);
 *   - `ProjectContextHeader` goes above the conversation, in the chat header;
 *   - `ChatShellLanding` and `ProjectDetailPanel` go in the message viewport,
 *     shown only when the viewport is empty.
 */
export interface ChatShellProps {
  bots: BotProfile[];
  activeBot: BotProfile | null;
  threads: Thread[];
  projects: Project[];
  activeThreadId: string | null;
  /** The project the open conversation is in, resolved or not. */
  activeProjectId: string | null;
  onSelectBot: (bot: BotProfile | null) => void;
  onSelectThread: (threadId: string) => void;
  /** Blank conversation; `projectId` scopes it. */
  onNewConversation: (projectId: string | null) => void;
  onPickProject: (projectId: string | null) => void;
  onOpenView: (view: WorkspaceView) => void;
  onProjectsChanged: () => void | Promise<void>;
}

export function ChatShell(props: ChatShellProps) {
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

  const project = useMemo(
    () => projects.find((candidate) => candidate.id === activeProjectId) ?? null,
    [projects, activeProjectId],
  );
  const thread = useMemo(
    () => threads.find((candidate) => candidate.thread_id === activeThreadId) ?? null,
    [threads, activeThreadId],
  );
  const [detailOpen, setDetailOpen] = useState(false);

  const presence = activeBot
    ? botPresence(activeBot)
    : { state: "unrecorded" as const, label: "Lead Agent auto-routes", raw: null };

  return (
    <>
      <BotWorkspaceRail
        bots={bots}
        activeBot={activeBot}
        threads={threads}
        projects={projects}
        activeThreadId={activeThreadId}
        activeProjectId={activeProjectId}
        onSelectBot={onSelectBot}
        onSelectThread={onSelectThread}
        onNewConversation={onNewConversation}
        onPickProject={onPickProject}
        onOpenView={onOpenView}
        onProjectsChanged={onProjectsChanged}
      />

      <div className="shrink-0 px-4 pt-2">
        <div className="max-w-4xl mx-auto">
          <div className="flex items-start gap-2">
            <div className="flex-1 min-w-0">
              <ProjectContextHeader
                bot={activeBot}
                bots={bots}
                project={project}
                projectKnown={!activeProjectId || project !== null}
                thread={thread}
                projects={projects}
                onSwitchProject={onPickProject}
                onSelectBot={onSelectBot}
                onNewConversation={() => onNewConversation(null)}
                onOpenView={onOpenView}
              />
            </div>
            {project && (
              <Btn
                variant="ghost"
                onClick={() => setDetailOpen((value) => !value)}
                title="Open the project detail panel: overview, conversations, files, tasks and settings"
              >
                {detailOpen ? "Hide project detail" : "Project detail"}
              </Btn>
            )}
          </div>

          {detailOpen && project && (
            <div className="mt-2 rounded-2xl border border-border/60 bg-card/40 p-3">
              <ProjectDetailPanel
                project={project}
                onOpenView={onOpenView}
                onOpenThread={onSelectThread}
                onNewConversationInProject={() => onNewConversation(project.id)}
              />
            </div>
          )}
        </div>
      </div>
    </>
  );
}

/**
 * The landing state, mounted into the empty message viewport.
 *
 * It is a separate export rather than part of `ChatShell` because it lives in a
 * different DOM region: the rail and the context header are chrome, while this
 * is the conversation surface, and it renders only when the transcript is
 * empty. Re-exported here so both pieces of the shell are imported from one
 * place.
 */
export { ChatShellLanding as ChatShellEmptyState };

/** The one-line "who and where" summary, for callers that want just that. */
export function ChatShellSummary(props: {
  activeBot: BotProfile | null;
  activeProjectId: string | null;
  activeThreadId: string | null;
  projects: Project[];
  threads: Thread[];
}) {
  const project = props.projects.find((candidate) => candidate.id === props.activeProjectId) ?? null;
  const thread = props.threads.find((candidate) => candidate.thread_id === props.activeThreadId) ?? null;
  const presence = props.activeBot
    ? botPresence(props.activeBot)
    : { state: "unrecorded" as const, label: "Lead Agent auto-routes", raw: null };
  return (
    <span className="inline-flex items-center gap-1.5 text-[10px] text-muted-foreground">
      {props.activeBot ? (
        <BotGlyph
          initials={props.activeBot.display_name.slice(0, 2).toUpperCase()}
          avatar={props.activeBot.avatar}
          presence={presence}
        />
      ) : (
        <LeadGlyph />
      )}
      <span className="truncate">
        {props.activeBot ? botDisplayName(props.activeBot) : "Lead Agent"}
        {project ? ` in ${project.name}` : " outside any project"}
        {thread ? ` — ${threadTitle(thread as unknown as Record<string, unknown>)}` : " — new conversation"}
      </span>
      <PresenceLine view={presence} />
    </span>
  );
}
