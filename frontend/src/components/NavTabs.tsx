"use client";

import React, { useState, useRef, useEffect, useLayoutEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import { PANEL_WIDTH, placeFloatingPanel } from "@/lib/workspace-menu-geometry";
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
  Radar,
  Scale,
  Search,
  Compass,
  ShieldCheck,
} from "lucide-react";

export type WorkspaceView =
  | "overview"
  | "chat"
  | "warroom"
  | "deliberation"
  | "bots"
  | "company"
  | "messages"
  | "peers"
  | "external-alpha"
  | "kanban"
  | "runs"
  | "run-inspector"
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
  | "forge"
  | "supervisor"
  | "protocols"
  | "reliability";

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
  { id: "overview", label: "Overview", icon: <Compass className="size-3.5" />, blurb: "Every agent, project, run and system surface — organized", category: "core", isPrimary: true },
  { id: "chat", label: "Chat", icon: <MessageSquare className="size-3.5" />, blurb: "Talk to the agent", category: "core", isPrimary: true },
  { id: "warroom", label: "War Room", icon: <Building2 className="size-3.5" />, blurb: "Autonomous AI Software Enterprise War Room", category: "core", isPrimary: true },
  { id: "deliberation", label: "Deliberation", icon: <Scale className="size-3.5" />, blurb: "Staged group deliberation: quorum, dissent and taint", category: "core" },
  { id: "bots", label: "Bots", icon: <Bot className="size-3.5" />, blurb: "Specialist profiles & team ops", category: "core", isPrimary: true },
  { id: "projects", label: "Projects", icon: <FolderKanban className="size-3.5" />, blurb: "Every project — solo crews and team crews, end to end", category: "core", isPrimary: true },
  { id: "company", label: "Companies", icon: <Building2 className="size-3.5" />, blurb: "Autonomous organizations: charter, workforce, board, budget & loop", category: "core", isPrimary: true },
  { id: "kanban", label: "Board", icon: <SquareKanban className="size-3.5" />, blurb: "Full project kanban board", category: "core", isPrimary: true },
  { id: "messages", label: "Messages", icon: <MessagesSquare className="size-3.5" />, blurb: "Agent chats & group rooms", category: "collaboration", isPrimary: true },
  { id: "peers", label: "Alpha Network", icon: <Network className="size-3.5" />, blurb: "Discover, pair & message other Alpha installations", category: "collaboration" },
  { id: "external-alpha", label: "External Alpha", icon: <MessagesSquare className="size-3.5" />, blurb: "Read every cross-installation conversation, local turn & delivery receipt", category: "collaboration" },
  { id: "dashboard", label: "Usage", icon: <LayoutDashboard className="size-3.5" />, blurb: "Activity, tokens & cost", category: "operations", isPrimary: true },

  // Collaboration & Team
  { id: "team", label: "Team Ops", icon: <Users className="size-3.5" />, blurb: "Groups, swarms & jobs", category: "collaboration" },
  { id: "workforce", label: "Workforce", icon: <Factory className="size-3.5" />, blurb: "Bot inbox, presence, curator & oversight", category: "collaboration" },
  { id: "channels", label: "Channels", icon: <Plug className="size-3.5" />, blurb: "Chat apps & integrations", category: "collaboration" },

  // Operations & Execution
  { id: "runs", label: "Runs", icon: <History className="size-3.5" />, blurb: "Run history per conversation", category: "operations" },
  { id: "run-inspector", label: "Run inspector", icon: <Search className="size-3.5" />, blurb: "One run in full: prompt, tools, events, files, tokens", category: "operations" },
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
  { id: "supervisor", label: "Supervisor", icon: <Radar className="size-3.5" />, blurb: "Autonomy loops, Sentinel repairs & signals", category: "system" },
  { id: "protocols", label: "Protocols", icon: <Network className="size-3.5" />, blurb: "A2A, agent messages, deliveries & MoA", category: "system" },
  { id: "reliability", label: "Validation", icon: <ShieldCheck className="size-3.5" />, blurb: "Real tasks the agent ran, and whether the work happened", category: "system" },
  { id: "settings", label: "Settings", icon: <Settings className="size-3.5" />, blurb: "Model selection, theme, API diagnostics", category: "system", isPrimary: true },
];

/**
 * Menu order for the "More Views" dropdown, one entry per `TabCategory`.
 *
 * The dropdown used to hardcode three groups — collaboration, operations,
 * system — while `deliberation` was declared with `category: "core"`, so that
 * tab was rendered by no group at all: it could not be reached from the
 * navigation it belongs to, and arriving there by other means left an active
 * label ("Deliberation") on a menu that did not contain it. Driving the menu
 * from this list makes membership total by construction — every tab category
 * has exactly one heading, so adding a category without a group fails the
 * source-pin test in `src/lib/workspace-nav.test.mjs` instead of silently
 * hiding a view.
 */
const SECONDARY_GROUPS: { category: TabCategory; heading: string }[] = [
  { category: "core", heading: "Core" },
  { category: "collaboration", heading: "Collaboration & Team" },
  { category: "operations", heading: "Operations & Resources" },
  { category: "system", heading: "System & Architecture" },
];

const MENU_ID = "workspace-more-views-menu";

/**
 * `useLayoutEffect` measures the portalled panel before the browser paints it,
 * so the menu never flashes at the top-left corner for a frame. Server rendering
 * has no layout, so it gets the no-op version — and the portal only mounts on the
 * client anyway, via `portalReady`.
 */
const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

export function NavTabs(props: {
  view: WorkspaceView;
  onChange: (v: WorkspaceView) => void;
  badge?: Partial<Record<WorkspaceView, number>>;
}) {
  const [dropdownOpen, setDropdownOpen] = useState(false);
  // The panel is portalled to `document.body`, which does not exist during
  // server rendering.
  const [portalReady, setPortalReady] = useState(false);
  const [panel, setPanel] = useState<{ top: number; left: number; maxHeight: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setPortalReady(true);
  }, []);

  const closeDropdown = useCallback(() => {
    setDropdownOpen(false);
    setPanel(null);
  }, []);

  // A view change from outside this component (the thread sidebar's
  // `onOpenView`, or a restored view) must not leave the menu open over a header
  // that now names a different view.
  useEffect(() => {
    closeDropdown();
  }, [props.view, closeDropdown]);

  const placePanel = useCallback(() => {
    const trigger = triggerRef.current;
    const content = panelRef.current;
    if (!trigger || !content) return;
    const rect = trigger.getBoundingClientRect();
    const box = placeFloatingPanel({
      trigger: { top: rect.top, bottom: rect.bottom, left: rect.left },
      viewport: { width: window.innerWidth, height: window.innerHeight },
      naturalHeight: content.scrollHeight,
      panelWidth: PANEL_WIDTH,
    });
    setPanel({ top: box.top, left: box.left, maxHeight: box.maxHeight });
  }, []);

  useIsomorphicLayoutEffect(() => {
    if (!dropdownOpen) return;
    placePanel();

    function handleClickOutside(event: MouseEvent) {
      const target = event.target as Node;
      // Both refs, because the panel is portalled out of the trigger's wrapper.
      // Testing only the trigger closes the menu on the panel's own `mousedown`
      // and unmounts the clicked row before its `click` can land.
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setDropdownOpen(false);
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setDropdownOpen(false);
    }

    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKeyDown);
    window.addEventListener("resize", placePanel);
    // Capture phase, so scrolling an ancestor container (the nav is wrapped in
    // `overflow-x-auto`) keeps the panel under its trigger.
    window.addEventListener("scroll", placePanel, true);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("resize", placePanel);
      window.removeEventListener("scroll", placePanel, true);
    };
  }, [dropdownOpen, placePanel]);

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
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold transition-all dur-fast ${
              active
                ? "bg-primary text-primary-foreground elev-1 ring-1 ring-primary/30"
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
      <div className="relative inline-block text-left">
        <button
          ref={triggerRef}
          type="button"
          onClick={() => (dropdownOpen ? closeDropdown() : setDropdownOpen(true))}
          title="All workspace modules and operational views"
          aria-expanded={dropdownOpen}
          aria-controls={dropdownOpen ? MENU_ID : undefined}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold transition-all dur-fast ${
            activeSecondary
              ? "bg-primary/10 text-primary border border-primary/30 shadow-xs"
              : "text-muted-foreground hover:text-foreground hover:bg-muted/80 bg-muted/30 border border-transparent"
          }`}
        >
          <LayoutGrid className="size-3.5" />
          <span>{activeSecondary ? activeSecondary.label : "More Views"}</span>
          <ChevronDown className={`size-3 transition-transform dur-fast ${dropdownOpen ? "rotate-180" : ""}`} />
        </button>
      </div>

      {/*
        The panel is portalled to `document.body`, NOT rendered here.

        An absolutely-positioned panel is still clipped by every ancestor
        overflow, and in this shell all of them clip: `ChatView` wraps the nav in
        `overflow-x-auto` (whose `overflow-y` *computes* to `auto`, so it clips
        vertically as well) inside a `<main class="overflow-hidden">`. Measured in
        the running app, a 256x1060 menu was laid out inside a 30px-tall clip box
        and left 4 visible pixels: the click toggled state and showed nothing. It
        also carried no `max-height` and no internal scroll, so its last group
        (`System & Architecture`) sat below the fold of any viewport, not just a
        short one. `placeFloatingPanel` now caps, flips and clamps it against the
        real viewport; see `src/lib/workspace-menu-geometry.ts`.
      */}
      {dropdownOpen &&
        portalReady &&
        createPortal(
          <div
            ref={panelRef}
            id={MENU_ID}
            aria-label="All workspace views"
            style={{
              position: "fixed",
              top: panel?.top ?? 0,
              left: panel?.left ?? 0,
              width: PANEL_WIDTH,
              maxHeight: panel?.maxHeight ?? undefined,
              // Positioned in a layout effect before paint, so never seen.
              visibility: panel ? undefined : "hidden",
            }}
            className="z-[100] overflow-y-auto overscroll-contain rounded-2xl border border-border/80 bg-card elev-3 p-2 space-y-2 focus:outline-none animate-in fade-in zoom-in-95 dur-fast"
          >
            {SECONDARY_GROUPS.map((group, index) => (
              <div key={group.category} className={index === 0 ? undefined : "pt-1 border-t border-border/50"}>
                <div className="px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">{group.heading}</div>
                <div className="space-y-0.5">
                  {secondaryTabs
                    .filter((t) => t.category === group.category)
                    .map((t) => {
                      const active = props.view === t.id;
                      return (
                        <button
                          key={t.id}
                          type="button"
                          onClick={() => {
                            props.onChange(t.id);
                            closeDropdown();
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
            ))}
          </div>,
          document.body,
        )}
    </nav>
  );
}
