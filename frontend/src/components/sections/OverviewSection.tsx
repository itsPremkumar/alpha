"use client";

import React, { useEffect, useMemo, useState } from "react";
import { Section, StatCard, Badge, Btn, ErrorBox } from "@/components/ui";
import { fetchOverview, type OverviewSnapshot, type OverviewDomainId } from "@/lib/overview";
import {
  SAMPLE_AGENTS,
  SAMPLE_BOTS,
  SAMPLE_CHANNELS,
  SAMPLE_FACTS,
  SAMPLE_PROJECTS,
  SAMPLE_RUNS,
  SAMPLE_SCHEDULED,
  SAMPLE_SKILLS,
  SAMPLE_WORKFLOWS,
} from "@/lib/demo-data";
import type { WorkspaceView } from "@/components/NavTabs";
import { errMsg } from "@/lib/http";
import {
  MessageSquare,
  Bot,
  Sparkles,
  Network,
  Factory,
  FolderKanban,
  SquareKanban,
  Workflow,
  Building2,
  History,
  Search,
  FolderOpen,
  CalendarClock,
  Brain,
  Blocks,
  Hammer,
  MessagesSquare,
  LayoutDashboard,
  ServerCog,
  PlugZap,
  Radar,
  Users,
  Plug,
  Settings,
  Compass,
  RefreshCw,
  Eye,
} from "lucide-react";

interface AtlasCard {
  view: WorkspaceView;
  title: string;
  blurb: string;
  group: string;
  icon: React.ReactNode;
  domain?: OverviewDomainId;
  youSee: string;
  howTo: string;
}

const CARDS: AtlasCard[] = [
  // Talk & agents
  { view: "chat", title: "Chat", blurb: "Talk to the lead agent", group: "Talk & agents", icon: <MessageSquare className="size-4" />, youSee: "Active thread, model, goal, usage, suggestions", howTo: "Pick a bot, set a goal, send a message. Attach files when needed." },
  { view: "bots", title: "Bots", blurb: "Specialist profiles & presence", group: "Talk & agents", icon: <Bot className="size-4" />, domain: "bots", youSee: "Name, role, department, model, toolsets, reputation, last active", howTo: "Open a profile to chat, pause, or edit. Presence dot = active recently." },
  { view: "agents", title: "Agents", blurb: "Custom personas", group: "Talk & agents", icon: <Sparkles className="size-4" />, domain: "agents", youSee: "Name, description, model, soul prompt", howTo: "Create a persona once, reuse it in any chat or project." },
  { view: "subagents", title: "Subagents", blurb: "Helpers the agent spawns", group: "Talk & agents", icon: <Network className="size-4" />, youSee: "Task id, status, steps, result receipt", howTo: "They appear automatically during a run; open one to see what it did." },
  { view: "workforce", title: "Workforce", blurb: "Inbox, presence, oversight", group: "Talk & agents", icon: <Factory className="size-4" />, youSee: "Per-bot queue, live project, approvals", howTo: "Select the project first — actions run against the selected project." },
  // Projects & work
  { view: "projects", title: "Projects", blurb: "Every project, solo and team crews", group: "Projects & work", icon: <FolderKanban className="size-4" />, domain: "projects", youSee: "Members, threads, crew type (solo / crew / unknown)", howTo: "Filter by crew type, expand a card, or View more for the full read." },
  { view: "kanban", title: "Board", blurb: "Full project kanban", group: "Projects & work", icon: <SquareKanban className="size-4" />, youSee: "Columns, cards, assignee, due state", howTo: "Drag cards across columns; WIP limits are shown per column." },
  { view: "workflows", title: "Workflows", blurb: "Dynamic flows, goals, checkpoints, jobs", group: "Projects & work", icon: <Workflow className="size-4" />, domain: "workflows", youSee: "Name, status, runs, last run, executors", howTo: "Open a workflow to see runs, checkpoints, and the event timeline." },
  { view: "warroom", title: "War Room", blurb: "Autonomous delivery room", group: "Projects & work", icon: <Building2 className="size-4" />, youSee: "Mission, phases, live activity", howTo: "Start here for a big multi-step build; watch phases complete." },
  { view: "runs", title: "Runs", blurb: "Run history per conversation", group: "Projects & work", icon: <History className="size-4" />, domain: "runs", youSee: "Status, model, tokens, thread link", howTo: "Click a thread title to jump back into that conversation." },
  { view: "run-inspector", title: "Run inspector", blurb: "One run in full detail", group: "Projects & work", icon: <Search className="size-4" />, youSee: "Prompt, tool calls, events, files, tokens", howTo: "Pick a run from Runs; every tool call shows its result or its absence." },
  { view: "scheduled", title: "Scheduled", blurb: "Recurring background work", group: "Projects & work", icon: <CalendarClock className="size-4" />, domain: "scheduled", youSee: "Title, cron/interval, timezone, next run, status", howTo: "Create with a cron string; pause instead of deleting to keep history." },
  { view: "files", title: "Files", blurb: "Uploads & generated files", group: "Projects & work", icon: <FolderOpen className="size-4" />, youSee: "Name, size, thread, preview", howTo: "Uploads attach to the active thread; generated files list their run." },
  // Knowledge
  { view: "memory", title: "Memory", blurb: "What the agent remembers", group: "Knowledge", icon: <Brain className="size-4" />, domain: "memory", youSee: "Facts, working memory, episodic traces, semantic graph", howTo: "Delete a fact to forget it; recall shows why a fact was used." },
  { view: "skills", title: "Skills", blurb: "Abilities you can toggle", group: "Knowledge", icon: <Blocks className="size-4" />, domain: "skills", youSee: "Name, description, version, source, on/off", howTo: "Toggle a skill off to remove it from the next run." },
  { view: "forge", title: "Forge", blurb: "Skill workshop, evolution & benchmarks", group: "Knowledge", icon: <Hammer className="size-4" />, youSee: "Proposals, eval scores, policy state", howTo: "Propose a skill, watch its eval, then promote it." },
  { view: "messages", title: "Messages", blurb: "Agent chats & group rooms", group: "Knowledge", icon: <MessagesSquare className="size-4" />, youSee: "Rooms, roster, relay receipts", howTo: "Group rooms show direct vs inherited members separately." },
  // Platform
  { view: "dashboard", title: "Usage", blurb: "Activity, tokens & cost", group: "Platform", icon: <LayoutDashboard className="size-4" />, youSee: "Runs, threads, agents, tokens, cost (or unpriced)", howTo: "Unpriced cost is not $0 — it means no pricing is configured." },
  { view: "channels", title: "Channels", blurb: "Chat apps & integrations", group: "Platform", icon: <Plug className="size-4" />, domain: "channels", youSee: "Provider, enabled, connected, status reason", howTo: "A disabled provider is off by config — enable it before debugging." },
  { view: "team", title: "Team Ops", blurb: "Groups, swarms & jobs", group: "Platform", icon: <Users className="size-4" />, youSee: "Group tree, depth, relay receipts", howTo: "Authority is at most one parent; visibility can fan out." },
  { view: "company", title: "Companies", blurb: "Charter, workforce, board, budget", group: "Platform", icon: <Building2 className="size-4" />, youSee: "Charter, headcount, budget burn", howTo: "Start with the charter — every loop reports against it." },
  { view: "peers", title: "Alpha Network", blurb: "Other Alpha installations", group: "Platform", icon: <Network className="size-4" />, youSee: "Provider, trust, delivery receipts", howTo: "Discovery is not access — pair with a code first." },
  { view: "system", title: "System", blurb: "Live status, shortcuts, apps", group: "Platform", icon: <ServerCog className="size-4" />, youSee: "Version, uptime, vitals, network link", howTo: "Unknown internet is not offline — retry only when offered." },
  { view: "integration", title: "Integration", blurb: "Wiring status & capabilities", group: "Platform", icon: <PlugZap className="size-4" />, youSee: "Tool, router, middleware, loop wiring + coverage", howTo: "A missing wire names its module — fix the registry, regen the manifest." },
  { view: "supervisor", title: "Supervisor", blurb: "Autonomy loops & repairs", group: "Platform", icon: <Radar className="size-4" />, youSee: "Loop state (off looks off), signals, repairs", howTo: "A disabled loop shows its reason — it is not idle." },
  { view: "protocols", title: "Protocols", blurb: "A2A, messages, deliveries, MoA", group: "Platform", icon: <Plug className="size-4" />, youSee: "Counterpart, envelope, receipt", howTo: "Queued is not failed — receipts say what was confirmed." },
  { view: "settings", title: "Settings", blurb: "Models, theme, diagnostics", group: "Platform", icon: <Settings className="size-4" />, youSee: "Active model, reasoning ladder, update state", howTo: "Reasoning rungs come from the server — Default means send nothing." },
];

const GROUPS = ["Talk & agents", "Projects & work", "Knowledge", "Platform"];

function demoDomain(id: OverviewDomainId): { count: number; detail: string; preview: string[] } {
  switch (id) {
    case "bots":
      return { count: SAMPLE_BOTS.length, detail: "2/3 active (sample)", preview: SAMPLE_BOTS.map((b) => b.display_name) };
    case "projects":
      return { count: SAMPLE_PROJECTS.length, detail: "2 active (sample)", preview: SAMPLE_PROJECTS.map((p) => p.name) };
    case "workflows":
      return { count: SAMPLE_WORKFLOWS.length, detail: "1 active, 1 paused (sample)", preview: SAMPLE_WORKFLOWS.map((w) => w.name) };
    case "skills":
      return { count: SAMPLE_SKILLS.length, detail: "2/3 enabled (sample)", preview: SAMPLE_SKILLS.map((s) => s.name) };
    case "memory":
      return { count: SAMPLE_FACTS.length, detail: `${SAMPLE_FACTS.length} facts (sample)`, preview: SAMPLE_FACTS.map((f) => f.content.slice(0, 48)) };
    case "scheduled":
      return { count: SAMPLE_SCHEDULED.length, detail: "1 active, 1 paused (sample)", preview: SAMPLE_SCHEDULED.map((t) => t.title) };
    case "channels":
      return { count: SAMPLE_CHANNELS.length, detail: "1/3 connected (sample)", preview: SAMPLE_CHANNELS.map((c) => c.name) };
    case "agents":
      return { count: SAMPLE_AGENTS.length, detail: "2 personas (sample)", preview: SAMPLE_AGENTS.map((a) => a.display_name) };
    case "runs":
      return { count: SAMPLE_RUNS.length, detail: "2 completed, 1 failed (sample)", preview: SAMPLE_RUNS.map((r) => r.thread_title) };
  }
}

export function OverviewSection(props: { onOpenView: (v: WorkspaceView) => void }) {
  const [snapshot, setSnapshot] = useState<OverviewSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [demo, setDemo] = useState(false);

  const load = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const snap = await fetchOverview();
      setSnapshot(snap);
      // If everything failed, offer the labeled sample preview automatically —
      // but never silently: the banner names it as sample data.
      if (snap.domains.every((d) => d.error !== null)) {
        try {
          const params = new URLSearchParams(window.location.search);
          if (params.get("demo") === "1") setDemo(true);
        } catch {}
      }
    } catch (e) {
      setLoadError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      if (params.get("demo") === "1") setDemo(true);
    } catch {}
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const byDomain = useMemo(() => {
    const map = new Map<OverviewDomainId, { count: number | null; error: string | null; detail: string | null; preview: string[] }>();
    for (const d of snapshot?.domains ?? []) map.set(d.id, d);
    return map;
  }, [snapshot]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return CARDS;
    return CARDS.filter(
      (c) =>
        c.title.toLowerCase().includes(q) ||
        c.blurb.toLowerCase().includes(q) ||
        c.youSee.toLowerCase().includes(q) ||
        c.group.toLowerCase().includes(q),
    );
  }, [query]);

  const liveCounts = (["bots", "projects", "workflows", "skills", "memory", "scheduled", "channels", "agents", "runs"] as OverviewDomainId[]).map(
    (id) => byDomain.get(id)?.count ?? null,
  );
  const measured = liveCounts.filter((n): n is number => n !== null);
  const failedNames = snapshot?.failed ?? [];

  return (
    <Section
      title="Overview — everything in this workspace"
      hint="One organized map of every agent, project, run, memory, skill and system surface. Counts are measured live; a dash means the server did not answer, never zero. Open any card for the full detail."
      actions={
        <>
          {demo ? <Badge tone="amber">Sample data — backend unavailable</Badge> : snapshot ? <Badge tone={failedNames.length === 0 ? "green" : "amber"}>{failedNames.length === 0 ? "all live" : `${failedNames.length} unanswered`}</Badge> : null}
          <Btn variant="ghost" onClick={() => setDemo((v) => !v)} title="Toggle clearly-labeled sample data for visual checks">
            <Eye className="size-3.5" /> {demo ? "Hide sample" : "Preview sample"}
          </Btn>
          <Btn variant="ghost" onClick={load} title="Re-read every subsystem">
            <RefreshCw className="size-3.5" /> Refresh
          </Btn>
        </>
      }
    >
      {demo && (
        <div className="rounded-xl border border-amber-500/40 bg-amber-500/5 px-3 py-2.5 text-xs">
          <span className="font-semibold">Sample data preview.</span>{" "}
          <span className="text-muted-foreground">
            Every number below is an illustration for layout checks — not your workspace. Turn it off to see live Gateway state.
          </span>
        </div>
      )}
      {loadError && <ErrorBox message={loadError} onRetry={load} />}
      {failedNames.length > 0 && !demo && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-2.5 text-xs text-muted-foreground">
          {failedNames.length} subsystem{failedNames.length === 1 ? "" : "s"} did not answer: {failedNames.join(", ")}. Those cards show the
          server&apos;s reason — the rest is live.
        </div>
      )}

      <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Compass className="size-4 text-primary" />
          <p className="text-xs font-semibold flex-1 min-w-40">Workspace at a glance</p>
          <span className="text-[10px] text-muted-foreground">
            {loading ? "reading live state…" : snapshot ? `read ${new Date(snapshot.fetchedAt).toLocaleTimeString()}` : "not read yet"}
          </span>
        </div>
        {loading ? (
          <p className="text-[11px] text-muted-foreground">Reading bots, projects, workflows, skills, memory, schedules, channels, agents and runs — each on its own request…</p>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
            {[
              { label: "Bots", v: demo ? demoDomain("bots").count : (byDomain.get("bots")?.count ?? null) },
              { label: "Projects", v: demo ? demoDomain("projects").count : (byDomain.get("projects")?.count ?? null) },
              { label: "Workflows", v: demo ? demoDomain("workflows").count : (byDomain.get("workflows")?.count ?? null) },
              { label: "Skills", v: demo ? demoDomain("skills").count : (byDomain.get("skills")?.count ?? null) },
              { label: "Agents", v: demo ? demoDomain("agents").count : (byDomain.get("agents")?.count ?? null) },
            ].map((s) => (
              <StatCard key={s.label} label={s.label} value={s.v === null ? "—" : s.v.toLocaleString()} sub={s.v === null && !demo ? "not reported" : demo ? "sample" : measured.length > 0 ? "live" : undefined} />
            ))}
          </div>
        )}
        <div className="flex items-center gap-2">
          <Search className="size-3.5 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter everything — try 'memory', 'run', 'channel'…"
            aria-label="Filter overview cards"
            className="flex-1 bg-card border border-border/70 rounded-xl px-3 py-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40 placeholder:text-muted-foreground"
          />
          {query && (
            <span className="text-[10px] text-muted-foreground">
              {filtered.length}/{CARDS.length} shown
            </span>
          )}
        </div>
        <p className="text-[10px] text-muted-foreground">
          Legend: <strong>—</strong> = server did not report (not zero) · <strong>0</strong> = server measured zero ·{" "}
          <strong>amber card</strong> = that subsystem did not answer, reason shown.
        </p>
      </div>

      {filtered.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border/70 bg-card/50 px-6 py-10 text-center">
          <p className="text-sm font-medium">No surface matches “{query}”</p>
          <p className="text-xs text-muted-foreground mt-1">Try “bot”, “project”, “memory”, “run” or clear the filter.</p>
          <div className="mt-3 flex justify-center">
            <Btn variant="ghost" onClick={() => setQuery("")}>Clear filter</Btn>
          </div>
        </div>
      ) : (
        GROUPS.map((group) => {
          const cards = filtered.filter((c) => c.group === group);
          if (cards.length === 0) return null;
          return (
            <div key={group} className="space-y-2">
              <div className="flex items-center gap-2 pt-1">
                <h3 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">{group}</h3>
                <span className="text-[10px] text-muted-foreground">· {cards.length}</span>
                <div className="flex-1 h-px bg-border/60" />
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2.5">
                {cards.map((card) => {
                  const live = card.domain ? byDomain.get(card.domain) : undefined;
                  const sample = card.domain && demo ? demoDomain(card.domain) : null;
                  const count = sample ? sample.count : (live?.count ?? null);
                  const detail = sample ? sample.detail : (live?.detail ?? null);
                  const preview = sample ? sample.preview : (live?.preview ?? []);
                  const error = !demo ? live?.error ?? null : null;
                  return (
                    <article
                      key={card.view}
                      className={`rounded-2xl border bg-card p-3.5 space-y-2 transition-colors hover:border-primary/40 ${error ? "border-amber-500/40" : "border-border/60"}`}
                    >
                      <div className="flex items-start gap-2">
                        <span className="size-8 rounded-xl bg-primary/10 text-primary flex items-center justify-center shrink-0">{card.icon}</span>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <h4 className="text-[13px] font-semibold leading-tight">{card.title}</h4>
                            {card.domain && (
                              <Badge tone={error ? "amber" : count === null ? "gray" : "blue"} title={error ?? detail ?? (demo ? "Sample count" : "Live count")}>
                                {demo ? `${count} sample` : count === null ? "— not reported" : `${count} live`}
                              </Badge>
                            )}
                          </div>
                          <p className="text-[11px] text-muted-foreground">{card.blurb}</p>
                        </div>
                      </div>
                      {error ? (
                        <p className="text-[11px] text-amber-700 dark:text-amber-300 rounded-lg bg-amber-500/10 px-2.5 py-1.5">
                          Did not answer — {error}
                        </p>
                      ) : (
                        <>
                          {detail && <p className="text-[11px] font-mono text-muted-foreground">{detail}</p>}
                          {preview.length > 0 && (
                            <ul className="text-[11px] text-muted-foreground space-y-0.5">
                              {preview.map((p, i) => (
                                <li key={i} className="truncate">· {p}</li>
                              ))}
                            </ul>
                          )}
                        </>
                      )}
                      <div className="rounded-xl bg-muted/40 px-2.5 py-2 space-y-1">
                        <p className="text-[10px]"><span className="font-semibold">You see: </span><span className="text-muted-foreground">{card.youSee}</span></p>
                        <p className="text-[10px]"><span className="font-semibold">How to: </span><span className="text-muted-foreground">{card.howTo}</span></p>
                      </div>
                      <Btn variant="ghost" onClick={() => props.onOpenView(card.view)} className="w-full" title={`Open ${card.title}`}>
                        Open {card.title} →
                      </Btn>
                    </article>
                  );
                })}
              </div>
            </div>
          );
        })
      )}
    </Section>
  );
}
