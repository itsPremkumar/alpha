"use client";

import React, { useEffect, useState } from "react";
import { fetchConsoleStats, fetchConsoleRuns, fetchConsoleUsage, fetchOpsVersion, ConsoleStats, ConsoleRun } from "@/lib/workspace";
import { supervisionFleet, supervisionAnomalies, recoverWorker } from "@/lib/supervision";
import { Section, EmptyState, ErrorBox, StatCard, Btn, Badge, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { RefreshCw, ShieldCheck } from "lucide-react";

function Bar(props: { label: string; value: number; max: number }) {
  const pct = props.max > 0 ? Math.min(100, Math.round((props.value / props.max) * 100)) : 0;
  return (
    <div>
      <div className="flex justify-between text-[10px] text-muted-foreground mb-0.5">
        <span className="truncate">{props.label}</span>
        <span className="font-mono ml-2">{props.value.toLocaleString()}</span>
      </div>
      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function DashboardSection(props: { onOpenThread: (id: string) => void }) {
  const [stats, setStats] = useState<ConsoleStats | null>(null);
  const [runs, setRuns] = useState<ConsoleRun[]>([]);
  const [series, setSeries] = useState<Array<{ day: string; tokens: number }>>([]);
  const [byModel, setByModel] = useState<Array<{ model: string; tokens: number; cost: number | null }>>([]);
  const [version, setVersion] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, r, u, v] = await Promise.all([
        fetchConsoleStats(),
        fetchConsoleRuns(15),
        fetchConsoleUsage(),
        fetchOpsVersion(),
      ]);
      setStats(s);
      setRuns(r);
      setSeries(u.series.slice(-14));
      setByModel(u.byModel.slice(0, 8));
      setVersion(v);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const maxTokens = Math.max(1, ...series.map((p) => p.tokens));

  return (
    <Section
      title="Usage & activity"
      hint="How much the team has worked, which models it used, and what it cost. Needs a SQL database backend ” shows an error on in-memory deployments."
      actions={
        <>
          {version && <Badge tone="gray">server {version}</Badge>}
          <Btn variant="ghost" onClick={load}>
            <RefreshCw className="size-3.5" /> Refresh
          </Btn>
        </>
      }
    >
      {error && <ErrorBox message={error} onRetry={load} />}
      {loading ? (
        <SkeletonList rows={5} />
      ) : !stats ? (
        <EmptyState title="No data" hint="Usage reporting needs a SQL database on the server." action={<Btn variant="ghost" onClick={load}>Try again</Btn>} />
      ) : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
            <StatCard label="Runs" value={stats.runs.toLocaleString()} />
            <StatCard label="Conversations" value={stats.threads.toLocaleString()} />
            <StatCard label="Agents" value={stats.agents.toLocaleString()} />
            <StatCard label="Tokens" value={stats.tokens.toLocaleString()} />
            <StatCard
              label="Cost"
              value={stats.cost !== null ? `${stats.cost.toFixed(2)}${stats.currency ? ` ${stats.currency}` : ""}` : "”"}
              sub={stats.cost === null ? "no pricing set" : undefined}
            />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-2">
              <p className="text-xs font-semibold">Tokens per day (last 14)</p>
              {series.length === 0 ? (
                <p className="text-[11px] text-muted-foreground">No daily data yet.</p>
              ) : (
                series.map((p) => <Bar key={p.day} label={p.day} value={p.tokens} max={maxTokens} />)
              )}
            </div>
            <div className="rounded-2xl border border-border/60 bg-card p-4">
              <p className="text-xs font-semibold mb-2">By model</p>
              {byModel.length === 0 ? (
                <p className="text-[11px] text-muted-foreground">No per-model data yet.</p>
              ) : (
                <div className="space-y-2">
                  {byModel.map((m) => (
                    <div key={m.model} className="flex items-center gap-2 text-[11px]">
                      <span className="font-mono flex-1 truncate">{m.model}</span>
                      <span className="font-mono text-muted-foreground">{m.tokens.toLocaleString()} tok</span>
                      {m.cost !== null && <span className="font-mono">{m.cost.toFixed(3)}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <WatchdogBlock />

          <div className="rounded-2xl border border-border/60 bg-card p-4">
            <p className="text-xs font-semibold mb-2">Recent runs</p>            {runs.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">No runs recorded yet.</p>
            ) : (
              <div className="space-y-1.5">
                {runs.map((r) => (
                  <div key={r.run_id} className="flex items-center gap-2 text-[11px] rounded-lg bg-muted/40 px-2.5 py-1.5 flex-wrap">
                    <Badge tone={r.status === "success" || r.status === "completed" ? "green" : "gray"}>{r.status}</Badge>
                    <button type="button" onClick={() => r.thread_id && props.onOpenThread(r.thread_id)} className="font-medium flex-1 min-w-32 text-left truncate hover:text-primary" title={r.thread_id}>
                      {r.thread_title}
                    </button>
                    <span className="font-mono text-muted-foreground">{r.model}</span>
                    <span className="font-mono text-muted-foreground">{r.tokens > 0 ? `${r.tokens.toLocaleString()} tok` : ""}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </Section>
  );
}

function WatchdogBlock() {
  const [fleet, setFleet] = React.useState<Record<string, unknown> | null>(null);
  const [anomalies, setAnomalies] = React.useState<Array<Record<string, unknown>>>([]);
  const [loaded, setLoaded] = React.useState(false);
  const [msg, setMsg] = React.useState<string | null>(null);

  const load = async () => {
    const [f, a] = await Promise.all([supervisionFleet(), supervisionAnomalies()]);
    setFleet(f);
    setAnomalies(a);
    setLoaded(true);
  };

  React.useEffect(() => {
    load().catch(() => setLoaded(true));
  }, []);

  if (!loaded) return null;
  if (!fleet && anomalies.length === 0) return null;

  return (
    <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-2">
      <div className="flex items-center gap-2">
        <ShieldCheck className="size-4 text-emerald-500" />
        <p className="text-xs font-semibold flex-1">Safety watchdog</p>
        <Badge tone={anomalies.length > 0 ? undefined : "green"}>
          {anomalies.length > 0 ? `${anomalies.length} issue${anomalies.length > 1 ? "s" : ""}` : "all healthy"}
        </Badge>
      </div>
      {msg && <p className="text-[11px] text-emerald-600">{msg}</p>}
      {anomalies.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">Workers are reporting in normally. Frozen or looping workers appear here with a one-tap fix.</p>
      ) : (
        <div className="space-y-1.5">
          {anomalies.slice(0, 8).map((a, i) => {
            const worker = String(a.worker_id ?? a.worker ?? "worker");
            return (
              <div key={i} className="flex items-center gap-2 rounded-xl bg-muted/40 px-3 py-2 flex-wrap">
                <span className="text-[11px] font-mono font-semibold flex-1 min-w-32">{worker}</span>
                <span className="text-[11px] text-muted-foreground flex-1 min-w-40">{String(a.anomaly_type ?? a.type ?? a.description ?? "anomaly").slice(0, 120)}</span>
                <Btn variant="ghost" onClick={() => recoverWorker(worker).then((m) => { setMsg(`Recovery started for ${worker}.`); setAnomalies((prev) => prev.filter((_, j) => j !== i)); }).catch((e) => setMsg(errMsg(e)))}>
                  Fix it
                </Btn>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
