"use client";

import React, { useEffect, useState } from "react";
import { BotProfile, botDisplayName, botInitials } from "@/types/bots";
import { X, MessageSquare, Star, Cpu, Wrench, Layers, ListChecks, ShieldCheck, GitBranch, Building2 } from "lucide-react";
import { createProject } from "@/lib/projects";
import { errMsg } from "@/lib/http";

interface BotDetailPanelProps {
  bot: BotProfile | null;
  onClose: () => void;
  onChat: (bot: BotProfile) => void;
  onProjectCreated?: (projectId: string) => void | Promise<void>;
}

export function BotDetailPanel({ bot, onClose, onChat, onProjectCreated }: BotDetailPanelProps) {
  const [projectOpen, setProjectOpen] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [projectInstructions, setProjectInstructions] = useState("");
  const [projectBusy, setProjectBusy] = useState(false);
  const [projectError, setProjectError] = useState<string | null>(null);

  useEffect(() => {
    setProjectOpen(false);
    setProjectName("");
    setProjectInstructions("");
    setProjectError(null);
  }, [bot?.name]);

  if (!bot) return null;
  const total = Number(bot.task_stats?.total) || 0;
  const succeeded = Number(bot.task_stats?.succeeded) || 0;
  const failed = Number(bot.task_stats?.failed) || 0;

  const submitProject = async () => {
    if (!projectName.trim() || projectBusy) return;
    setProjectBusy(true);
    setProjectError(null);
    try {
      const project = await createProject(
        projectName.trim(),
        projectInstructions.trim(),
        [{ name: bot.name, role: "lead" }],
      );
      await onProjectCreated?.(project.id);
      onClose();
    } catch (error) {
      setProjectError(errMsg(error));
    } finally {
      setProjectBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <aside className="relative w-full max-w-md h-full bg-card border-l border-border shadow-2xl flex flex-col overflow-hidden">
        <div className="p-5 border-b border-border/60 flex items-start gap-3">
          <div className="size-12 rounded-2xl bg-primary/10 text-primary flex items-center justify-center text-xl font-bold shrink-0">
            {bot.avatar ? <span>{bot.avatar}</span> : <span>{botInitials(bot)}</span>}
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-base font-semibold">{botDisplayName(bot)}</h2>
            <p className="text-xs text-muted-foreground">{bot.role}</p>
            <p className="text-[11px] text-muted-foreground font-mono mt-0.5">
              @{bot.name} • v{bot.version} • {bot.department}
              {bot.reports_to ? ` • reports to ${bot.reports_to}` : ""}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground"
            title="Close"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          <div className="grid grid-cols-3 gap-2">
            <div className="rounded-xl bg-muted/40 p-2.5 text-center">
              <div className="flex items-center justify-center gap-1 text-amber-500">
                <Star className="size-3.5" />
                <span className="text-sm font-bold text-foreground">
                  {bot.reputation_score != null ? bot.reputation_score.toFixed(2) : "unverified"}
                </span>
              </div>
              <div className="text-[10px] text-muted-foreground mt-0.5">Reputation</div>
            </div>
            <div className="rounded-xl bg-muted/40 p-2.5 text-center">
              <div className="text-sm font-bold">{total}</div>
              <div className="text-[10px] text-muted-foreground mt-0.5">Total tasks</div>
            </div>
            <div className="rounded-xl bg-muted/40 p-2.5 text-center">
              <div className="text-sm font-bold text-emerald-600">{succeeded}/{total || "—"}</div>
              <div className="text-[10px] text-muted-foreground mt-0.5">Succeeded{failed ? ` • ${failed} failed` : ""}</div>
            </div>
          </div>

          <section className="rounded-xl border border-border/60 bg-muted/20 p-3 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div>
                <h3 className="text-xs font-semibold inline-flex items-center gap-1.5">
                  <Building2 className="size-3.5 text-primary" /> Create a project with {botDisplayName(bot)}
                </h3>
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  This bot is attached as the project lead. Add more bots from the Projects page.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setProjectOpen((open) => !open)}
                className="text-[11px] font-medium text-primary hover:underline"
              >
                {projectOpen ? "Close" : "Create"}
              </button>
            </div>
            {projectOpen && (
              <div className="space-y-2 pt-1">
                <label className="block space-y-1">
                  <span className="text-[10px] font-semibold">Project name</span>
                  <input
                    value={projectName}
                    onChange={(event) => setProjectName(event.target.value)}
                    maxLength={128}
                    placeholder={`${botDisplayName(bot)} project`}
                    className="w-full rounded-lg border border-border/70 bg-card px-2.5 py-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40"
                  />
                </label>
                <label className="block space-y-1">
                  <span className="text-[10px] font-semibold">Project instructions</span>
                  <textarea
                    value={projectInstructions}
                    onChange={(event) => setProjectInstructions(event.target.value)}
                    rows={3}
                    placeholder="Shared goal, working agreements, and expected outcomes…"
                    className="w-full rounded-lg border border-border/70 bg-card px-2.5 py-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40"
                  />
                </label>
                {projectError && <p className="text-[11px] text-destructive">{projectError}</p>}
                <button
                  type="button"
                  onClick={() => void submitProject()}
                  disabled={!projectName.trim() || projectBusy}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-[11px] font-semibold text-primary-foreground disabled:opacity-40"
                >
                  <Building2 className="size-3.5" /> {projectBusy ? "Creating…" : "Create project"}
                </button>
              </div>
            )}
          </section>

          {bot.soul && (
            <section>
              <h3 className="text-xs font-semibold mb-1.5">Soul / Personality</h3>
              <p className="text-xs text-muted-foreground leading-relaxed whitespace-pre-wrap rounded-xl bg-muted/30 border border-border/50 p-3">
                {bot.soul}
              </p>
            </section>
          )}

          {bot.responsibilities.length > 0 && (
            <section>
              <h3 className="text-xs font-semibold mb-1.5 inline-flex items-center gap-1.5">
                <ListChecks className="size-3.5 text-primary" /> Responsibilities
              </h3>
              <ul className="space-y-1">
                {bot.responsibilities.map((r) => (
                  <li key={r} className="text-xs text-muted-foreground flex gap-2">
                    <span className="text-primary mt-0.5">•</span>
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {bot.capabilities.length > 0 && (
            <section>
              <h3 className="text-xs font-semibold mb-1.5 inline-flex items-center gap-1.5">
                <ShieldCheck className="size-3.5 text-primary" /> Capabilities
              </h3>
              <div className="flex flex-wrap gap-1.5">
                {bot.capabilities.map((c) => (
                  <span key={c} className="text-[11px] px-2 py-1 rounded-lg bg-secondary font-mono">{c}</span>
                ))}
              </div>
            </section>
          )}

          <div className="grid grid-cols-1 gap-3">
            {bot.skills.length > 0 && (
              <section className="rounded-xl border border-border/50 p-3">
                <h3 className="text-xs font-semibold mb-1.5 inline-flex items-center gap-1.5">
                  <Layers className="size-3.5 text-primary" /> Skills ({bot.skills.length})
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {bot.skills.map((s) => (
                    <span key={s} className="text-[11px] px-2 py-0.5 rounded-md bg-primary/10 text-primary font-medium">{s}</span>
                  ))}
                </div>
              </section>
            )}
            {bot.toolsets.length > 0 && (
              <section className="rounded-xl border border-border/50 p-3">
                <h3 className="text-xs font-semibold mb-1.5 inline-flex items-center gap-1.5">
                  <Wrench className="size-3.5 text-primary" /> Toolsets ({bot.toolsets.length})
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {bot.toolsets.map((t) => (
                    <span key={t} className="text-[11px] px-2 py-0.5 rounded-md bg-muted font-mono">{t}</span>
                  ))}
                </div>
              </section>
            )}
          </div>

          <section className="rounded-xl border border-border/50 p-3 space-y-1.5 text-[11px] text-muted-foreground">
            <div className="flex items-center gap-1.5">
              <Cpu className="size-3.5" />
              <span>Model: <span className="text-foreground font-medium font-mono">{bot.model || "default"}</span></span>
            </div>
            {bot.succession_fallback && (
              <div className="flex items-center gap-1.5">
                <GitBranch className="size-3.5" />
                <span>Fallback: <span className="text-foreground font-medium font-mono">{bot.succession_fallback}</span></span>
              </div>
            )}
            {bot.last_active && <div>Last active: {new Date(bot.last_active).toLocaleString()}</div>}
          </section>
        </div>

        <div className="p-4 border-t border-border/60 flex gap-2">
          <button
            type="button"
            onClick={() => onChat(bot)}
            className="flex-1 min-w-0 inline-flex items-center justify-center gap-2 px-3 py-2 rounded-xl bg-primary text-primary-foreground text-xs font-semibold hover:opacity-95"
          >
            <MessageSquare className="size-4" /> Chat with {botDisplayName(bot)}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-2 rounded-xl border border-border text-xs font-medium hover:bg-muted"
          >
            Close
          </button>
        </div>
      </aside>
    </div>
  );
}
