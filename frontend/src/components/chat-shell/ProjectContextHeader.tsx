"use client";

import React from "react";
import { ChevronRight, FolderOpen, MessageSquare, Plus, Repeat } from "lucide-react";
import { BotProfile, botDisplayName, botInitials } from "@/types/bots";
import { Project } from "@/lib/projects";
import { WorkspaceView } from "@/lib/workspace-view";
import { botPresence, contextSentence, conversationTitle, projectStatusText } from "@/lib/chat-shell";
import { BotGlyph, LeadGlyph, PresenceLine } from "./Honest";
import type { Thread } from "@/types/chat";

/**
 * The project context header: which bot, which project, which conversation.
 *
 * The spec's rule is that these three are never ambiguous. This strip is a
 * breadcrumb, so it is additive to whatever the chat header already shows — it
 * replaces nothing and removes no control.
 *
 * Every segment states its own confidence:
 *
 *   - the bot segment is present because a bot is selected, and its dot is
 *     drawn only when the Gateway sent that bot a `last_active`;
 *   - the project segment says "No project" when there is none, which is a
 *     *state*, not a gap — a missing project and a failed project read look
 *     different;
 *   - the conversation segment says "New conversation" when nothing has been
 *     sent, which is also a state rather than an absence.
 *
 * "Switch Project" writes through `onPickProject`, the same call the existing
 * header's project `<select>` makes, so the two controls cannot disagree.
 */
export function ProjectContextHeader(props: {
  bot: BotProfile | null;
  bots: BotProfile[];
  project: Project | null;
  /** True when a project is selected but its name could not be resolved. */
  projectKnown: boolean;
  thread: Thread | null;
  projects: Project[];
  onSwitchProject: (projectId: string | null) => void;
  onSelectBot: (bot: BotProfile | null) => void;
  onNewConversation: () => void;
  onOpenView: (view: WorkspaceView) => void;
}) {
  const {
    bot,
    project,
    projectKnown,
    thread,
    projects,
    onSwitchProject,
    onSelectBot,
    onNewConversation,
    onOpenView,
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

  return (
    <div
      className="mx-auto max-w-4xl flex items-center gap-1.5 flex-wrap rounded-xl border border-border/60 bg-card/40 px-2.5 py-1.5"
      data-shell="project-context"
    >
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
          <span className="text-[11px] font-semibold truncate">Lead Agent</span>
          <span className="text-[10px] text-muted-foreground">auto-routes, sees every conversation</span>
        </span>
      )}

      <ChevronRight className="size-3 shrink-0 text-muted-foreground/60" aria-hidden="true" />

      {/* project segment */}
      <span className="flex items-center gap-1 min-w-0">
        <FolderOpen className="size-3 shrink-0 text-muted-foreground" aria-hidden="true" />
        {project ? (
          <span className="text-[11px] font-medium truncate max-w-40" title={`${project.name} — status: ${projectStatusText(project.status)}`}>
            {project.name}
          </span>
        ) : projectKnown ? (
          <span className="text-[11px] text-muted-foreground italic">No project</span>
        ) : (
          <span
            className="text-[11px] text-destructive italic"
            title="A project is selected but its name is not in the loaded project list, so it cannot be named here."
          >
            project not in the loaded list
          </span>
        )}
      </span>

      <ChevronRight className="size-3 shrink-0 text-muted-foreground/60" aria-hidden="true" />

      {/* conversation segment */}
      <span className="flex items-center gap-1 min-w-0">
        <MessageSquare className="size-3 shrink-0 text-muted-foreground" aria-hidden="true" />
        {title ? (
          <span className="text-[11px] truncate max-w-56" title={title}>{title}</span>
        ) : (
          <span className="text-[11px] text-muted-foreground italic">New conversation</span>
        )}
      </span>

      <span className="flex-1" />

      <button
        type="button"
        onClick={onNewConversation}
        className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-border text-[10px] font-semibold hover:bg-muted"
        title="New Conversation: a blank conversation with the selected bot, not in any project"
      >
        <Plus className="size-3" /> New Conversation
      </button>

      <button
        type="button"
        onClick={() => {
          onSwitchProject(null);
          onNewConversation();
        }}
        className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-border text-[10px] font-semibold hover:bg-muted"
        title="Start a conversation with this bot outside any project"
      >
        <Repeat className="size-3" /> Switch to standalone
      </button>

      <span className="flex items-center gap-1">
        <label className="sr-only" htmlFor="chat-shell-switch-project">Switch project</label>
        <select
          id="chat-shell-switch-project"
          value={props.project?.id ?? ""}
          onChange={(event) => onSwitchProject(event.target.value || null)}
          className="text-[10px] bg-muted/60 border border-border/80 rounded-lg px-1.5 py-1 text-foreground focus:outline-none focus:ring-1 focus:ring-primary/40 cursor-pointer max-w-40"
          title="Switch Project: scope the open conversation to a different project, or to none"
        >
          <option value="">Switch Project: none</option>
          {projects.map((candidate) => (
            <option key={candidate.id} value={candidate.id}>
              Switch Project: {candidate.name || candidate.id}
            </option>
          ))}
        </select>
        {bot && (
          <select
            value={bot.name}
            onChange={(event) => onSelectBot(props.bots.find((candidate) => candidate.name === event.target.value) ?? null)}
            className="text-[10px] bg-muted/60 border border-border/80 rounded-lg px-1.5 py-1 text-foreground focus:outline-none focus:ring-1 focus:ring-primary/40 cursor-pointer max-w-32"
            title="Switch bot. Selecting one scopes every group in the sidebar."
            aria-label="Switch bot"
          >
            {props.bots.map((candidate) => (
              <option key={candidate.name} value={candidate.name}>
                {botDisplayName(candidate)}
              </option>
            ))}
          </select>
        )}
        {project && (
          <button
            type="button"
            onClick={() => onOpenView("projects")}
            className="px-2 py-1 rounded-lg border border-border text-[10px] font-semibold hover:bg-muted"
            title="Open the Projects view for this project's detail, crew and settings"
          >
            Project detail
          </button>
        )}
      </span>

      <p className="w-full text-[10px] text-muted-foreground/90 pt-0.5" data-shell="context-sentence">
        {sentence.text}
      </p>
    </div>
  );
}
