"use client";

import React, { useCallback, useEffect, useState } from "react";
import { createArenaRun, listArenaRuns, startArenaRun, type ArenaRun } from "@/lib/arena";
import { get, send, errMsg } from "@/lib/http";
import { Badge, Btn, EmptyState, ErrorBox, Field, inputCls } from "@/components/ui";
import { Play, RefreshCw, Swords } from "lucide-react";

type Profile = { profile: string; agents: number; wave: number; description: string };
type ArenaInfo = { enabled: boolean; default_agents: number; max_agents: number };
type Modes = { modes: Array<{ mode: string; description: string }>; implemented_modes: string[] };

export function ArenaPanel() {
  const [runs, setRuns] = useState<ArenaRun[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [modes, setModes] = useState<Modes | null>(null);
  const [config, setConfig] = useState<ArenaInfo | null>(null);
  const [task, setTask] = useState("");
  const [profile, setProfile] = useState("standard");
  const [mode, setMode] = useState("decide");
  const [agents, setAgents] = useState(5);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmRun, setConfirmRun] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [runRows, profileInfo, arenaConfig] = await Promise.all([
        listArenaRuns(), get<Modes>("/arena/profiles"), get<ArenaInfo>("/arena/config"),
      ]);
      setRuns(runRows);
      setModes(profileInfo);
      setProfiles((profileInfo as Modes & { profiles: Profile[] }).profiles ?? []);
      setConfig(arenaConfig);
    } catch (cause) {
      setError(errMsg(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const create = async () => {
    if (!task.trim()) return;
    setBusy("create"); setError(null); setNotice(null);
    try {
      if (mode === "plan") {
        const estimatePayload: Record<string, unknown> = { task: task.trim(), agents, mode };
        if (profile !== "custom") estimatePayload.profile = profile;
        const estimate = await send<Record<string, unknown>>("/arena/estimate", "POST", estimatePayload);
        const plan = estimate.plan as Record<string, unknown> | undefined;
        setNotice(`Estimate ready: ${String(plan?.total_calls ?? "call count not reported")} projected subagent calls. No agents were started.`);
      } else {
        const row = await createArenaRun(task.trim(), { mode: "decide", profile, agents });
        setNotice(`Arena draft created (${row.run_id}). Start it below when ready.`);
        await refresh();
      }
      setTask("");
    } catch (cause) { setError(errMsg(cause)); }
    finally { setBusy(null); }
  };

  const start = async (runId: string, confirm = false) => {
    setBusy(runId); setError(null); setNotice(null);
    try {
      const result = await startArenaRun(runId, confirm);
      setConfirmRun(null);
      setNotice(`Arena ${result.run_id}: ${result.status}.`);
      await refresh();
    } catch (cause) {
      const message = errMsg(cause);
      if (!confirm && /confirm=true|confirmation ceiling/i.test(message)) setConfirmRun(runId);
      else setError(message);
    } finally { setBusy(null); }
  };

  return <div className="space-y-3">
    <div className="rounded-2xl border border-border/60 bg-card p-4">
      <Field label="Arena competition" hint="Independent competitors work on one task; a bracket judge selects a champion. Start is a separate action and may request cost confirmation.">
        <textarea value={task} onChange={(event) => setTask(event.target.value)} rows={2} className={inputCls} aria-label="Arena task" placeholder="Describe the task candidates should solve…" />
        <div className="mt-2 grid gap-2 sm:grid-cols-3">
          <label className="text-[10px] text-muted-foreground">Profile
            <select value={profile} onChange={(event) => { setProfile(event.target.value); const spec = profiles.find((item) => item.profile === event.target.value); if (spec) setAgents(spec.agents); }} className={inputCls}>
              {profiles.map((item) => <option key={item.profile} value={item.profile}>{item.profile} · {item.agents} competitors</option>)}
              <option value="custom">custom count</option>
            </select>
          </label>
          <label className="text-[10px] text-muted-foreground">Run mode
            <select value={mode} onChange={(event) => setMode(event.target.value)} className={inputCls} aria-label="Arena run mode">
              {(modes?.modes ?? []).map((item) => <option key={item.mode} value={item.mode} disabled={!modes?.implemented_modes.includes(item.mode)}>{item.mode} · {modes?.implemented_modes.includes(item.mode) ? item.description : "not executable in this build"}</option>)}
            </select>
          </label>
          <label className="text-[10px] text-muted-foreground">Competitors (operator max {config?.max_agents ?? "unknown"})
            <input type="number" min={1} max={config?.max_agents} value={agents} onChange={(event) => { setProfile("custom"); setAgents(Number(event.target.value)); }} className={inputCls} aria-label="Arena competitor count" />
          </label>
        </div>
        {config?.enabled === false && <p className="mt-2 text-xs text-amber-600">Arena is disabled by Gateway configuration.</p>}
        <div className="mt-2 flex justify-end"><Btn onClick={() => void create()} disabled={!task.trim() || busy !== null || loading || config?.enabled === false || (mode !== "plan" && !modes?.implemented_modes.includes(mode))}><Swords className="size-3.5" />{mode === "plan" ? "Estimate only" : "Create arena draft"}</Btn></div>
      </Field>
      <p className="mt-2 text-[10px] text-muted-foreground">Safe preset: standard uses 5 competitors. Operator ceiling and run budgets are enforced by the Gateway; estimates are not execution verification.</p>
    </div>
    {notice && <p role="status" className="rounded-lg bg-muted p-3 text-xs">{notice}</p>}
    {error && <ErrorBox message={`Arena action or read failed: ${error}`} onRetry={() => void refresh()} />}
    <div className="flex items-center justify-between"><h3 className="text-xs font-semibold">Arena runs</h3><Btn variant="ghost" onClick={() => void refresh()}><RefreshCw className="size-3.5"/>Refresh</Btn></div>
    {loading ? <p className="text-xs text-muted-foreground">Loading arena state…</p> : runs.length === 0 ? <EmptyState title="No arena runs" hint="Create a draft above to compare solutions." /> : runs.map((run) => <div key={run.run_id} className="flex flex-wrap items-center gap-2 rounded-xl border border-border/60 bg-card p-3">
      <div className="min-w-0 flex-1"><p className="break-all font-mono text-[10px]">{run.run_id}</p><p className="mt-1 text-xs text-muted-foreground">{run.task || "Task summary not returned"}</p></div>
      <Badge tone={run.status === "completed" ? "green" : run.status === "failed" ? "red" : "gray"}>{run.status || "status unknown"}</Badge>
      <span className="text-[10px] text-muted-foreground">{run.profile ?? "profile unknown"} · {run.agents_n ?? "?"} agents</span>
      {run.status === "draft" && <Btn onClick={() => void start(run.run_id, confirmRun === run.run_id)} disabled={busy !== null}><Play className="size-3.5"/>{busy === run.run_id ? "Starting…" : confirmRun === run.run_id ? "Confirm spend & start" : "Start"}</Btn>}
    </div>)}
  </div>;
}

