"use client";

import React, { useState, useRef, useEffect } from "react";
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
  Settings,
  ChevronDown,
  LayoutGrid,
  Workflow,
  Hammer,
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
  | "integration"
  | "settings"
  | "workflows"
  | "forge";

export type TabCategory = "core" | "collaboration" | "operations" | "system";

export interface WorkspaceTabItem {
  id: WorkspaceView;
  label: string;
  icon: React.ReactNode;
  blurb: string;
  category: TabCategory;
  isPrimary?: boolean;
}

export const WORKSPACE_TABS: WorkspaceTabItem[] = [
  // Primary / Core
  { id: "chat", label: "Chat", icon: <MessageSquare className="size-3.5" />, blurb: "Talk to the agent", category: "core", isPrimary: true },
  { id: "warroom", label: "War Room", icon: <Building2 className="size-3.5" />, blurb: "Autonomous AI Software Enterprise War Room", category: "core", isPrimary: true },
  { id: "bots", label: "Bots", icon: <Bot className="size-3.5" />, blurb: "Specialist profiles & team ops", category: "core", isPrimary: true },
  { id: "kanban", label: "Board", icon: <SquareKanban className="size-3.5" />, blurb: "Full project kanban board", category: "core", isPrimary: true },
  { id: "messages", label: "Messages", icon: <MessagesSquare className="size-3.5" />, blurb: "Agent chats & group rooms", category: "collaboration", isPrimary: true },
  { id: "dashboard", label: "Usage", icon: <LayoutDashboard className="size-3.5" />, blurb: "Activity, tokens & cost", category: "operations", isPrimary: true },

  // Collaboration & Team
  { id: "team", label: "Team Ops", icon: <Users className="size-3.5" />, blurb: "Groups, swarms & jobs", category: "collaboration" },
  { id: "workforce", label: "Workforce", icon: <Factory className="size-3.5" />, blurb: "Bot inbox, presence, curator & oversight", category: "collaboration" },
  { id: "projects", label: "Projects", icon: <FolderKanban className="size-3.5" />, blurb: "Group conversations", category: "collaboration" },
  { id: "channels", label: "Channels", icon: <Plug className="size-3.5" />, blurb: "Chat apps & integrations", category: "collaboration" },

  // Operations & Execution
  { id: "runs", label: "Runs", icon: <History className="size-3.5" />, blurb: "Run history per conversation", category: "operations" },
  { id: "files", label: "Files", icon: <FolderOpen className="size-3.5" />, blurb: "Uploads & generated files", category: "operations" },
  { id: "scheduled", label: "Scheduled", icon: <CalendarClock className="size-3.5" />, blurb: "Recurring background work", category: "operations" },
  { id: "subagents", label: "Subagents", icon: <Network className="size-3.5" />, blurb: "Helpers the agent spawns", category: "operations" },
  { id: "skills", label: "Skills", icon: <Blocks className="size-3.5" />, blurb: "Abilities you can toggle", category: "operations" },
  { id: "memory", label: "Memory", icon: <Brain className="size-3.5" />, blurb: "What the agent remembers", category: "operations" },
  { id: "agents", label: "Agents", icon: <Sparkles className="size-3.5" />, blurb: "Custom personas", category: "operations" },
  { id: "workflows", label: "Workflows", icon: <Workflow className="size-3.5" />, blurb: "Dynamic flows, goals, checkpoints & jobs", category: "operations" },
  { id: "forge", label: "Forge", icon: <Hammer className="size-3.5" />, blurb: "Skill workshop, evolution, policy & benchmarks", category: "operations" },

  // System & Platform
  { id: "system", label: "System", icon: <ServerCog className="size-3.5" />, blurb: "Live status, shortcuts, apps", category: "system" },
  { id: "integration", label: "Integration", icon: <PlugZap className="size-3.5" />, blurb: "Wiring status & opt-in capabilities", category: "system" },
  { id: "settings", label: "Settings", icon: <Settings className="size-3.5" />, blurb: "Model selection, theme, API diagnostics", category: "system", isPrimary: true },
];

export function NavTabs(props: {
  view: WorkspaceView;
  onChange: (v: WorkspaceView) => void;
  badge?: Partial<Record<WorkspaceView, number>>;
}) {
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setDropdownOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const primaryTabs = WORKSPACE_TABS.filter((t) => t.isPrimary);
  const secondaryTabs = WORKSPACE_TABS.filter((t) => !t.isPrimary);
  const activeSecondary = secondaryTabs.find((t) => t.id === props.view);

  return (
    <nav aria-label="Workspace navigation" className="flex items-center gap-1.5 flex-wrap">
      {/* Primary fast-access tabs */}
      {primaryTabs.map((t) => {
        const active = props.view === t.id;
        const count = props.badge?.[t.id];
        return (
          <button
            key={t.id}
            type="button"
            title={t.blurb}
            onClick={() => props.onChange(t.id)}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold transition-all duration-150 ${
              active
                ? "bg-primary text-primary-foreground shadow-sm ring-1 ring-primary/30"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/80 bg-muted/30"
            }`}
          >
            {t.icon}
            {t.label}
            {typeof count === "number" && count > 0 && (
              <span
                className={`text-[10px] px-1.5 py-0.2 rounded-full font-bold ${
                  active ? "bg-white/20" : "bg-muted text-muted-foreground"
                }`}
              >
                {count}
              </span>
            )}
          </button>
        );
      })}

      {/* More / All Modules Dropdown */}
      <div className="relative inline-block text-left" ref={dropdownRef}>
        <button
          type="button"
          onClick={() => setDropdownOpen((prev) => !prev)}
          title="All workspace modules and operational views"
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold transition-all duration-150 ${
            activeSecondary
              ? "bg-primary/10 text-primary border border-primary/30 shadow-xs"
              : "text-muted-foreground hover:text-foreground hover:bg-muted/80 bg-muted/30 border border-transparent"
          }`}
        >
          <LayoutGrid className="size-3.5" />
          <span>{activeSecondary ? activeSecondary.label : "More Views"}</span>
          <ChevronDown className={`size-3 transition-transform duration-200 ${dropdownOpen ? "rotate-180" : ""}`} />
        </button>

        {dropdownOpen && (
          <div className="absolute left-0 mt-1.5 w-64 rounded-2xl border border-border/80 bg-card shadow-xl z-50 p-2 space-y-2 focus:outline-none animate-in fade-in zoom-in-95 duration-100">
            {/* Category: Collaboration */}
            <div>
              <div className="px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                Collaboration & Team
              </div>
              <div className="space-y-0.5">
                {secondaryTabs
                  .filter((t) => t.category === "collaboration")
                  .map((t) => {
                    const active = props.view === t.id;
                    return (
                      <button
                        key={t.id}
                        type="button"
                        onClick={() => {
                          props.onChange(t.id);
                          setDropdownOpen(false);
                        }}
                        className={`w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-xl text-xs font-medium text-left transition-colors ${
                          active
                            ? "bg-primary text-primary-foreground font-semibold"
                            : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
                        }`}
                      >
                        <span className={active ? "text-primary-foreground" : "text-primary"}>{t.icon}</span>
                        <div className="flex-1 min-w-0">
                          <span className="block truncate">{t.label}</span>
                          <span className={`block text-[10px] truncate ${active ? "text-white/80" : "text-muted-foreground"}`}>{t.blurb}</span>
                        </div>
                      </button>
                    );
                  })}
              </div>
            </div>

            {/* Category: Operations */}
            <div className="pt-1 border-t border-border/50">
              <div className="px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                Operations & Resources
              </div>
              <div className="space-y-0.5">
                {secondaryTabs
                  .filter((t) => t.category === "operations")
                  .map((t) => {
                    const active = props.view === t.id;
                    return (
                      <button
                        key={t.id}
                        type="button"
                        onClick={() => {
                          props.onChange(t.id);
                          setDropdownOpen(false);
                        }}
                        className={`w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-xl text-xs font-medium text-left transition-colors ${
                          active
                            ? "bg-primary text-primary-foreground font-semibold"
                            : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
                        }`}
                      >
                        <span className={active ? "text-primary-foreground" : "text-primary"}>{t.icon}</span>
                        <div className="flex-1 min-w-0">
                          <span className="block truncate">{t.label}</span>
                          <span className={`block text-[10px] truncate ${active ? "text-white/80" : "text-muted-foreground"}`}>{t.blurb}</span>
                        </div>
                      </button>
                    );
                  })}
              </div>
            </div>

            {/* Category: System */}
            <div className="pt-1 border-t border-border/50">
              <div className="px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                System & Architecture
              </div>
              <div className="space-y-0.5">
                {secondaryTabs
                  .filter((t) => t.category === "system")
                  .map((t) => {
                    const active = props.view === t.id;
                    return (
                      <button
                        key={t.id}
                        type="button"
                        onClick={() => {
                          props.onChange(t.id);
                          setDropdownOpen(false);
                        }}
                        className={`w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-xl text-xs font-medium text-left transition-colors ${
                          active
                            ? "bg-primary text-primary-foreground font-semibold"
                            : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
                        }`}
                      >
                        <span className={active ? "text-primary-foreground" : "text-primary"}>{t.icon}</span>
                        <div className="flex-1 min-w-0">
                          <span className="block truncate">{t.label}</span>
                          <span className={`block text-[10px] truncate ${active ? "text-white/80" : "text-muted-foreground"}`}>{t.blurb}</span>
                        </div>
                      </button>
                    );
                  })}
              </div>
            </div>
          </div>
        )}
      </div>
    </nav>
  );
}
