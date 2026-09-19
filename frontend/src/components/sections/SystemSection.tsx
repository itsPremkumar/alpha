"use client";

import React, { useEffect, useRef, useState } from "react";
import { probeAll, Probe } from "@/lib/system";
import { listCommands, searchCommands, commandCategories, executeCommand, SlashCommand } from "@/lib/commands";
import { fetchMcpConfig, setMcpServerEnabled, addMcpServer, deleteMcpServer, resetMcpCache, McpServer } from "@/lib/mcp";
import { evaluatePlan, dispatchPlan, navigateBrowser } from "@/lib/plan";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, Field, SkeletonList, inputCls } from "@/components/ui";
import { SystemMonitorSection } from "@/components/sections/SystemMonitorSection";
import { errMsg } from "@/lib/http";
import { RefreshCw, Terminal, PlugZap, Trash2, Wand2, Globe, Pause, Play } from "lucide-react";

export function SystemSection(props: { threadId: string | null; browserActive: boolean }) {
  const [probes, setProbes] = useState<Probe[]>([]);
  const [probing, setProbing] = useState(true);
  const [auto, setAuto] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const [commands, setCommands] = useState<SlashCommand[]>([]);
  const [cmdSearch, setCmdSearch] = useState("");
  const [categories, setCategories] = useState<Array<{ name: string; count: number }>>([]);
  const [cmdResult, setCmdResult] = useState<string | null>(null);

  const [servers, setServers] = useState<McpServer[]>([]);
  const [newServer, setNewServer] = useState({ name: "", command: "", args: "" });

  const [planPrompt, setPlanPrompt] = useState("");
  const [planResult, setPlanResult] = useState<string | null>(null);
  const [planBusy, setPlanBusy] = useState(false);
  const [browserUrl, setBrowserUrl] = useState("");
  const [browserOut, setBrowserOut] = useState<string | null>(null);

  const refreshProbes = async () => {
    setProbing(true);
    setError(null);
    try {
      setProbes(await probeAll());
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setProbing(false);
    }
  };

  const refreshSlow = async () => {
    try {
      const [cmds, cats, mcps] = await Promise.all([listCommands(), commandCategories(), fetchMcpConfig()]);
      setCommands(cmds);
      setCategories(cats);
      setServers(mcps);
    } catch (e) {
      setError(errMsg(e));
    }
  };

  useEffect(() => {
    refreshProbes();
    refreshSlow();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (auto) {
      timer.current = window.setInterval(refreshProbes, 15000);
    }
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto]);

  const flash = (m: string) => {
    setNotice(m);
    window.setTimeout(() => setNotice(null), 4000);
  };

  const onSearchCmds = async (q: string) => {
    setCmdSearch(q);
    try {
      setCommands(q.trim() ? await searchCommands(q.trim()) : await listCommands());
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const onRunCmd = async (name: string) => {
    try {
      const out = await executeCommand(name.startsWith("/") ? name : `/${name}`, props.threadId ? { thread_id: props.threadId } : undefined);
      setCmdResult(out);
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const onAddServer = async () => {
    if (!newServer.name.trim() || !newServer.command.trim()) {
      setError("Name the connection and give the program to run (e.g. npx).");
      return;
    }
    try {
      await addMcpServer(newServer.name.trim(), newServer.command.trim(), newServer.args.split(/\s+/).filter(Boolean));
      setNewServer({ name: "", command: "", args: "" });
      flash("Connection added — tools reload automatically.");
      setServers(await fetchMcpConfig());
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const onPlan = async (dispatch: boolean) => {
    if (planPrompt.trim().length < 2) {
      setError("Describe the goal first (2+ characters).");
      return;
    }
    setPlanBusy(true);
    try {
      const r = dispatch ? await dispatchPlan(planPrompt.trim()) : await evaluatePlan(planPrompt.trim());
      setPlanResult(JSON.stringify(r, null, 2).slice(0, 8000));
      if (dispatch) flash("Plan dispatched to the execution subsystems.");
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setPlanBusy(false);
    }
  };

  const onNavigate = async () => {
    if (!props.threadId) {
      setError("Open or start a chat first — the browser belongs to a conversation.");
      return;
    }
    if (!browserUrl.trim()) return;
    try {
      const r = await navigateBrowser(props.threadId, browserUrl.trim());
      setBrowserOut(JSON.stringify(r, null, 2).slice(0, 3000));
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const online = probes.filter((p) => p.ok === true).length;

  return (
    <Section
      title="System control center"
      hint="Everything the server can do, checked live. Green means ready — the rest of the app lights up automatically from the same checks."
      actions={
        <>
          <Btn variant={auto ? "primary" : "ghost"} onClick={() => setAuto((v) => !v)} title="Re-check every 15 seconds">
            {auto ? <Pause className="size-3.5" /> : <Play className="size-3.5" />} Live {auto ? "on" : "off"}
          </Btn>
          <Btn variant="ghost" onClick={() => { refreshProbes(); refreshSlow(); }}>
            <RefreshCw className="size-3.5" /> Check now
          </Btn>
        </>
      }
    >
      {error && <ErrorBox message={error} onRetry={() => { refreshProbes(); refreshSlow(); }} />}
      {notice && <Notice message={notice} />}

      {/* Host resources — continuous backend monitor, RAM first. */}
      <div className="rounded-2xl border border-border/60 bg-card p-4">
        <SystemMonitorSection />
      </div>

      {/* Live status grid */}
      <div className="rounded-2xl border border-border/60 bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <p className="text-xs font-semibold flex-1">Live capabilities ({online}/{probes.length} ready)</p>
          {probing && <span className="text-[11px] text-muted-foreground animate-pulse">checking…</span>}
        </div>
        {probing && probes.length === 0 ? (
          <SkeletonList rows={3} />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {probes.map((p) => (
              <div key={p.key} className="flex items-start gap-2.5 rounded-xl bg-muted/40 px-3 py-2.5" title={`${p.ms}ms`}>
                <span className={`mt-1 size-2.5 rounded-full shrink-0 ${p.ok === null ? "bg-muted-foreground" : p.ok ? "bg-emerald-500" : "bg-destructive"}`} />
                <div className="min-w-0">
                  <p className="text-xs font-semibold">{p.label}</p>
                  <p className="text-[11px] text-muted-foreground">{p.blurb}</p>
                  <p className={`text-[11px] font-mono mt-0.5 break-words ${p.ok ? "text-emerald-600" : "text-muted-foreground"}`}>{p.detail}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Slash commands */}
      <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Terminal className="size-4 text-primary" />
          <p className="text-xs font-semibold flex-1">Shortcut commands ({commands.length})</p>
        </div>
        <p className="text-[11px] text-muted-foreground">Type <code className="font-mono">/help</code> in chat, or run one here. Shortcuts do multi-step chores in one tap.</p>
        <input value={cmdSearch} onChange={(e) => onSearchCmds(e.target.value)} placeholder="Search shortcuts… e.g. goal, swarm, plan" className={inputCls} aria-label="Search shortcut commands" />
        {categories.length > 0 && (
          <div className="flex gap-1.5 flex-wrap">
            {categories.slice(0, 12).map((c) => (
              <Badge key={c.name} tone="gray">{c.name} ({c.count})</Badge>
            ))}
          </div>
        )}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 max-h-64 overflow-y-auto">
          {commands.slice(0, 40).map((c) => (
            <button key={c.name} type="button" onClick={() => onRunCmd(c.name)} className="text-left rounded-xl bg-muted/40 hover:bg-muted px-3 py-2 transition-colors" title={c.usage || c.description}>
              <span className="text-[11px] font-mono font-semibold text-primary">/{c.name}</span>
              <span className="text-[11px] text-muted-foreground"> — {c.description || "No description"}</span>
            </button>
          ))}
        </div>
        {commands.length === 0 && <EmptyState title="No shortcuts found" hint="The server did not return any commands." />}
        {cmdResult && (
          <div>
            <p className="text-[11px] font-semibold mb-1">Result</p>
            <pre className="text-[11px] font-mono whitespace-pre-wrap max-h-64 overflow-y-auto rounded-xl bg-muted/40 p-3">{cmdResult}</pre>
          </div>
        )}
      </div>

      {/* MCP app connections */}
      <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <PlugZap className="size-4 text-primary" />
          <p className="text-xs font-semibold flex-1">App connections ({servers.length})</p>
          <Btn variant="ghost" onClick={() => resetMcpCache().then((m) => flash(m)).catch((e) => setError(errMsg(e)))}>Reload tools</Btn>
        </div>
        <p className="text-[11px] text-muted-foreground">Connect external apps (search, docs, dev tools…) so the agent can use them. Needs admin rights.</p>
        {servers.length === 0 ? (
          <EmptyState title="No apps connected" hint="Add one below — e.g. name “search”, program “npx”, arguments “-y my-search-server”." />
        ) : (
          <div className="space-y-1.5">
            {servers.map((s) => (
              <div key={s.name} className="flex items-center gap-2 rounded-xl bg-muted/40 px-3 py-2">
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-mono font-semibold truncate">{s.name}</p>
                  {s.command && <p className="text-[10px] font-mono text-muted-foreground truncate">{s.command}</p>}
                </div>
                <Badge tone={s.enabled ? "green" : "gray"}>{s.enabled ? "on" : "off"}</Badge>
                <button
                  type="button"
                  role="switch"
                  aria-checked={s.enabled}
                  aria-label={`Turn ${s.name} ${s.enabled ? "off" : "on"}`}
                  onClick={() => setMcpServerEnabled(s.name, !s.enabled).then(() => fetchMcpConfig().then(setServers)).catch((e) => setError(errMsg(e)))}
                  className={`relative w-10 h-6 rounded-full transition-colors shrink-0 ${s.enabled ? "bg-primary" : "bg-muted"}`}
                >
                  <span className={`absolute top-0.5 size-5 rounded-full bg-white shadow transition-all ${s.enabled ? "left-[18px]" : "left-0.5"}`} />
                </button>
                <button type="button" onClick={() => window.confirm(`Remove connection "${s.name}"?`) && deleteMcpServer(s.name).then(() => fetchMcpConfig().then(setServers)).catch((e) => setError(errMsg(e)))} className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-destructive" title={`Remove ${s.name}`}>
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          <Field label="Name">
            <input value={newServer.name} onChange={(e) => setNewServer({ ...newServer, name: e.target.value })} placeholder="search" className={`${inputCls} font-mono`} />
          </Field>
          <Field label="Program">
            <input value={newServer.command} onChange={(e) => setNewServer({ ...newServer, command: e.target.value })} placeholder="npx" className={`${inputCls} font-mono`} />
          </Field>
          <Field label="Arguments">
            <input value={newServer.args} onChange={(e) => setNewServer({ ...newServer, args: e.target.value })} onKeyDown={(e) => e.key === "Enter" && onAddServer()} placeholder="-y my-server" className={`${inputCls} font-mono`} />
          </Field>
        </div>
        <Btn onClick={onAddServer}>Add connection</Btn>
      </div>

      {/* Strategic planner */}
      <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-2.5">
        <div className="flex items-center gap-2">
          <Wand2 className="size-4 text-primary" />
          <p className="text-xs font-semibold">Strategic planner</p>
        </div>
        <p className="text-[11px] text-muted-foreground">Describe a big goal — the server reviews it across 8 dimensions (team, risks, models…) and optionally launches the work itself (admin).</p>
        <Field label="Big goal">
          <div className="flex gap-2">
            <input value={planPrompt} onChange={(e) => setPlanPrompt(e.target.value)} placeholder="Launch a customer feedback system…" className={inputCls} />
            <Btn variant="ghost" onClick={() => onPlan(false)} disabled={planBusy || planPrompt.trim().length < 2}>Review</Btn>
            <Btn onClick={() => window.confirm("Launch this plan for real across the execution systems?") && onPlan(true)} disabled={planBusy || planPrompt.trim().length < 2}>Review + launch</Btn>
          </div>
        </Field>
        {planBusy && <p className="text-[11px] text-muted-foreground animate-pulse">Thinking across 8 dimensions…</p>}
        {planResult && <pre className="text-[11px] font-mono whitespace-pre-wrap max-h-80 overflow-y-auto rounded-xl bg-muted/40 p-3">{planResult}</pre>}
      </div>

      {/* Live browser */}
      <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-2.5">
        <div className="flex items-center gap-2">
          <Globe className="size-4 text-primary" />
          <p className="text-xs font-semibold">Live browser</p>
          {!props.browserActive && <Badge tone="amber">needs browser capability</Badge>}
        </div>
        <p className="text-[11px] text-muted-foreground">Point the agent's browser tab at a page. Full remote control streams over websocket (see server docs).</p>
        <Field label="Open URL in agent browser">
          <div className="flex gap-2">
            <input value={browserUrl} onChange={(e) => setBrowserUrl(e.target.value)} onKeyDown={(e) => e.key === "Enter" && onNavigate()} placeholder="https://example.com" className={`${inputCls} font-mono`} />
            <Btn onClick={onNavigate} disabled={!browserUrl.trim()}>Open</Btn>
          </div>
        </Field>
        {browserOut && <pre className="text-[11px] font-mono whitespace-pre-wrap max-h-48 overflow-y-auto rounded-xl bg-muted/40 p-3">{browserOut}</pre>}
      </div>
    </Section>
  );
}
