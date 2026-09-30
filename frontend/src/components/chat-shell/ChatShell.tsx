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
import { WorkspaceTopBar } from "./WorkspaceTopBar";
import { OmnisearchModal } from "./OmnisearchModal";
import { BotDropdownMenu } from "./BotDropdownMenu";
import { ProjectDropdownMenu } from "./ProjectDropdownMenu";
import { BotGlyph, LeadGlyph, PresenceLine } from "./Honest";
import { botPresence } from "@/lib/chat-shell";
import type { Thread } from "@/types/chat";

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

/**
 * The sidebar rail component: renders the left navigation hierarchy (AI Agents,
 * Current Agent, Projects, Standalone conversations, and New Project trigger).
 */
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

  return (
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
  );
}

/**
 * The landing state, mounted into the empty message viewport.
 */
export { ChatShellLanding as ChatShellEmptyState };
export {
  BotWorkspaceRail,
  ProjectContextHeader,
  ProjectDetailPanel,
  WorkspaceTopBar,
  OmnisearchModal,
  BotDropdownMenu,
  ProjectDropdownMenu,
};

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
