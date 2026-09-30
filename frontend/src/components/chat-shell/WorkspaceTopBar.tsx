"use client";

import React, { useState, useEffect, useRef } from "react";
import { Search, Bell, Settings, Check, User, ExternalLink, ShieldCheck, Sparkles } from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";
import type { WorkspaceView } from "@/lib/workspace-view";

export interface WorkspaceTopBarProps {
  onOpenSearch: () => void;
  onOpenSettings: () => void;
  onOpenView?: (view: WorkspaceView) => void;
  gatewayOk: boolean | null;
  userInitials?: string;
  userName?: string;
  botLabel?: string | null;
  projectLabel?: string | null;
  threadLabel?: string | null;
}

export function WorkspaceTopBar(props: WorkspaceTopBarProps) {
  const {
    onOpenSearch,
    onOpenSettings,
    onOpenView,
    gatewayOk,
    userInitials = "MK",
    userName = "MK",
    botLabel,
    projectLabel,
    threadLabel,
  } = props;
  const [profileOpen, setProfileOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const profileRef = useRef<HTMLDivElement>(null);
  const notifRef = useRef<HTMLDivElement>(null);

  // Global Ctrl+K / Cmd+K listener
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        onOpenSearch();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onOpenSearch]);

  // Click outside listener for dropdowns
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (profileRef.current && !profileRef.current.contains(e.target as Node)) {
        setProfileOpen(false);
      }
      if (notifRef.current && !notifRef.current.contains(e.target as Node)) {
        setNotificationsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <header className="h-14 border-b border-border/60 bg-card/60 backdrop-blur-md px-4 flex items-center justify-between gap-4 shrink-0 z-30 select-none">
      {/* Brand logo & workspace label */}
      <div className="flex items-center gap-3 min-w-0 shrink-0">
        <BrandLogo logoSize={28} textClassName="text-sm font-bold text-foreground tracking-tight" priority />
        <div className="hidden sm:flex items-center gap-1.5 text-[11px] text-muted-foreground/80 border-l border-border/60 pl-3">
          <span className="font-semibold text-foreground">{botLabel || "Lead Agent"}</span>
          <span className="text-muted-foreground/60">›</span>
          <span className="truncate max-w-28 text-muted-foreground">{projectLabel || "Standalone"}</span>
          {threadLabel && (
            <>
              <span className="text-muted-foreground/60">›</span>
              <span className="truncate max-w-32 text-foreground/80 font-normal">{threadLabel}</span>
            </>
          )}
        </div>
      </div>

      {/* Global omni-search bar (Ctrl+K) */}
      <button
        type="button"
        onClick={onOpenSearch}
        className="flex-1 max-w-lg mx-auto flex items-center justify-between gap-3 px-3.5 py-1.5 rounded-xl border border-border/70 bg-muted/40 hover:bg-muted/70 text-muted-foreground hover:text-foreground text-xs transition-all shadow-2xs group cursor-pointer focus:outline-none focus:ring-1 focus:ring-primary/40"
        aria-label="Search agents, projects, conversations"
        title="Search agents, projects, conversations (Ctrl + K)"
      >
        <div className="flex items-center gap-2.5 truncate">
          <Search className="size-3.5 shrink-0 text-muted-foreground group-hover:text-primary transition-colors" />
          <span className="truncate text-[11px] font-normal">Search agents, projects, conversations...</span>
        </div>
        <kbd className="hidden md:inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-border/80 bg-background/80 text-[10px] font-mono text-muted-foreground">
          <span className="text-[9px]">Ctrl</span>+<span>K</span>
        </kbd>
      </button>

      {/* Right controls: Notifications, Settings, User avatar */}
      <div className="flex items-center gap-2 shrink-0">
        {/* Connection status dot */}
        <div
          className="hidden lg:flex items-center gap-1.5 px-2 py-1 rounded-full text-[10px] font-medium bg-muted/40 border border-border/60"
          title={gatewayOk === false ? "Gateway is offline" : gatewayOk ? "Gateway connected" : "Connecting to Gateway..."}
        >
          <span
            className={`size-2 rounded-full ${
              gatewayOk === false
                ? "bg-destructive animate-pulse"
                : gatewayOk
                ? "bg-emerald-500"
                : "bg-amber-400 animate-pulse"
            }`}
          />
          <span className="text-muted-foreground text-[10px]">
            {gatewayOk === false ? "Offline" : gatewayOk ? "Online" : "Connecting"}
          </span>
        </div>

        {/* Notifications toggle */}
        <div className="relative" ref={notifRef}>
          <button
            type="button"
            onClick={() => setNotificationsOpen((v) => !v)}
            className={`relative p-2 rounded-xl text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors ${
              notificationsOpen ? "bg-muted text-foreground" : ""
            }`}
            title="Notifications & Activity"
            aria-label="Notifications"
          >
            <Bell className="size-4" />
            <span className="absolute top-1.5 right-1.5 size-2 rounded-full bg-primary ring-2 ring-card" />
          </button>

          {notificationsOpen && (
            <div className="absolute right-0 mt-2 w-72 rounded-2xl border border-border bg-card shadow-xl p-3 text-xs z-50 animate-in fade-in zoom-in-95 duration-100">
              <div className="flex items-center justify-between border-b border-border/60 pb-2 mb-2">
                <span className="font-semibold text-foreground text-xs">Notifications</span>
                <span className="text-[10px] text-muted-foreground">System live</span>
              </div>
              <div className="space-y-2 text-left">
                <div className="p-2 rounded-xl bg-muted/40 border border-border/50">
                  <div className="flex items-center gap-1.5 text-primary font-medium text-[11px]">
                    <Sparkles className="size-3" /> Alpha Fleet Ready
                  </div>
                  <p className="text-[10px] text-muted-foreground mt-0.5">
                    Multi-bot workspace active with full sandbox & memory isolation.
                  </p>
                </div>
                <div className="p-2 rounded-xl bg-muted/40 border border-border/50">
                  <div className="flex items-center gap-1.5 text-emerald-500 font-medium text-[11px]">
                    <ShieldCheck className="size-3" /> Autonomous Loops
                  </div>
                  <p className="text-[10px] text-muted-foreground mt-0.5">
                    Supervisor, Workforce, and Safe Run Recovery services are operational.
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Settings button */}
        <button
          type="button"
          onClick={onOpenSettings}
          className="p-2 rounded-xl text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
          title="Open Settings"
          aria-label="Open Settings"
        >
          <Settings className="size-4" />
        </button>

        {/* User avatar circle */}
        <div className="relative" ref={profileRef}>
          <button
            type="button"
            onClick={() => setProfileOpen((v) => !v)}
            className="size-8 rounded-full bg-primary text-primary-foreground font-bold text-xs flex items-center justify-center shadow-sm hover:opacity-90 transition-opacity ring-2 ring-background focus:outline-none"
            title={`Operator: ${userName}`}
            aria-label="User profile menu"
          >
            {userInitials}
          </button>

          {profileOpen && (
            <div className="absolute right-0 mt-2 w-56 rounded-2xl border border-border bg-card shadow-xl p-2 text-xs z-50 animate-in fade-in zoom-in-95 duration-100">
              <div className="px-2 py-1.5 border-b border-border/60 mb-1">
                <p className="font-semibold text-foreground text-xs">{userName}</p>
                <p className="text-[10px] text-muted-foreground truncate">Operator • Lead Administrator</p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setProfileOpen(false);
                  onOpenSettings();
                }}
                className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground text-left text-xs transition-colors"
              >
                <Settings className="size-3.5" /> Workspace Settings
              </button>
              {onOpenView && (
                <button
                  type="button"
                  onClick={() => {
                    setProfileOpen(false);
                    onOpenView("system");
                  }}
                  className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground text-left text-xs transition-colors"
                >
                  <ExternalLink className="size-3.5" /> System Monitor
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
