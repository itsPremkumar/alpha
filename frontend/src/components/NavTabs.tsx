"use client";

import React from "react";
import {
  MessageSquare,
  Bot,
  History,
  FolderOpen,
  CalendarClock,
  Network,
  Blocks,
  Brain,
  FolderKanban,
  LayoutDashboard,
  Sparkles,
  Users,
  Plug,
  ServerCog,
  MessagesSquare,
  SquareKanban,
  Factory,
  Building2,
  PlugZap,
} from "lucide-react";

export type WorkspaceView =
  | "chat"
  | "warroom"
  | "bots"
  | "messages"
  | "kanban"
  | "runs"
  | "files"
  | "scheduled"
  | "subagents"
  | "skills"
  | "memory"
  | "projects"
  | "dashboard"
  | "agents"
  | "team"
  | "channels"
  | "workforce"
  | "system"
  | "integration";

export const WORKSPACE_TABS: Array<{ id: WorkspaceView; label: string; icon: React.ReactNode; blurb: string }> = [
  { id: "chat", label: "Chat", icon: <MessageSquare className="size-3.5" />, blurb: "Talk to the agent" },
  { id: "warroom", label: "War Room", icon: <Building2 className="size-3.5" />, blurb: "Autonomous AI Software Enterprise War Room" },
  { id: "bots", label: "Bots", icon: <Bot className="size-3.5" />, blurb: "Specialist profiles & team ops" },
  { id: "messages", label: "Messages", icon: <MessagesSquare className="size-3.5" />, blurb: "Agent chats & group rooms, WhatsApp-style" },
  { id: "kanban", label: "Board", icon: <SquareKanban className="size-3.5" />, blurb: "Full project kanban board" },
  { id: "runs", label: "Runs", icon: <History className="size-3.5" />, blurb: "Run history per conversation" },
  { id: "files", label: "Files", icon: <FolderOpen className="size-3.5" />, blurb: "Uploads & generated files" },
  { id: "scheduled", label: "Scheduled", icon: <CalendarClock className="size-3.5" />, blurb: "Recurring background work" },
  { id: "subagents", label: "Subagents", icon: <Network className="size-3.5" />, blurb: "Helpers the agent spawns" },
  { id: "skills", label: "Skills", icon: <Blocks className="size-3.5" />, blurb: "Abilities you can toggle" },
  { id: "memory", label: "Memory", icon: <Brain className="size-3.5" />, blurb: "What the agent remembers" },
  { id: "projects", label: "Projects", icon: <FolderKanban className="size-3.5" />, blurb: "Group conversations" },
  { id: "dashboard", label: "Usage", icon: <LayoutDashboard className="size-3.5" />, blurb: "Activity, tokens & cost" },
  { id: "agents", label: "Agents", icon: <Sparkles className="size-3.5" />, blurb: "Custom personas" },
  { id: "team", label: "Team Ops", icon: <Users className="size-3.5" />, blurb: "Groups, swarms & jobs" },
  { id: "channels", label: "Channels", icon: <Plug className="size-3.5" />, blurb: "Chat apps & integrations" },
  { id: "workforce", label: "Workforce", icon: <Factory className="size-3.5" />, blurb: "Bot inbox, presence, curator & oversight" },
  { id: "system", label: "System", icon: <ServerCog className="size-3.5" />, blurb: "Live status, shortcuts, apps" },
  { id: "integration", label: "Integration", icon: <PlugZap className="size-3.5" />, blurb: "Wiring status & opt-in capabilities" },
];

/** Wrapping tab bar: everything visible, nothing hidden in menus. */
export function NavTabs(props: { view: WorkspaceView; onChange: (v: WorkspaceView) => void; badge?: Partial<Record<WorkspaceView, number>> }) {
  return (
    <nav aria-label="Workspace sections" className="flex items-center gap-1 flex-wrap">
      {WORKSPACE_TABS.map((t) => {
        const active = props.view === t.id;
        const count = props.badge?.[t.id];
        return (
          <button
            key={t.id}
            type="button"
            title={t.blurb}
            onClick={() => props.onChange(t.id)}
            className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-semibold transition-colors ${
              active ? "bg-primary text-primary-foreground shadow" : "text-muted-foreground hover:text-foreground hover:bg-muted"
            }`}
          >
            {t.icon}
            {t.label}
            {typeof count === "number" && count > 0 && (
              <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-bold ${active ? "bg-white/20" : "bg-muted text-muted-foreground"}`}>
                {count}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
}
