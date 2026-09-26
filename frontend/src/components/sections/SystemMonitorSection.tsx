"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Activity, Cpu, Globe, HardDrive, MemoryStick, MonitorSmartphone, RefreshCw, TriangleAlert } from "lucide-react";
import { Badge, EmptyState, ErrorBox, SkeletonList, StatCard } from "@/components/ui";
import { errMsg } from "@/lib/http";
import {
  SystemAlert,
  SystemProcessesResponse,
  SystemVitals,
  fetchSystemAlerts,
  fetchSystemHistory,
  fetchSystemProcesses,
  fetchSystemVitals,
} from "@/lib/systemMonitor";

/** Outcome of one optional widget fetch: success data or the failure message. */
type FetchOutcome<T> = { data: T } | { error: string };

async function attempt<T>(fn: () => Promise<T>): Promise<FetchOutcome<T>> {
  try {
    return { data: await fn() };
  } catch (e) {
    return { error: errMsg(e) };
  }
}

function formatGiB(mb: number): string {
  if (!Number.isFinite(mb) || mb <= 0) return "—";
  return `${(mb / 1024).toFixed(1)} GiB`;
}

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let v = n;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u += 1;
  }
  return `${v.toFixed(1)} ${units[u]}`;
}

function formatUptime(totalSeconds: number): string {
  if (!Number.isFinite(totalSeconds) || totalSeconds <= 0) return "—";
  const d = Math.floor(totalSeconds / 86400);
  const h = Math.floor((totalSeconds % 86400) / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

/** RAM load color: green below 70%, amber below 90%, red at or above 90%. */
function ramTone(percent: number): "green" | "amber" | "red" {
  if (percent >= 90) return "red";
  if (percent >= 70) return "amber";
  return "green";
}

function RamBar({ percent }: { percent: number }) {
  const tone = ramTone(percent);
  const cls =
    tone === "red" ? "bg-red-500" : tone === "amber" ? "bg-amber-500" : "bg-emerald-500";
  return (
    <div
      className="h-2.5 w-full rounded-full bg-muted overflow-hidden"
      role="progressbar"
      aria-valuenow={Math.round(percent)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={`RAM usage ${percent.toFixed(1)} percent`}
    >
      <div className={`h-full rounded-full transition-all ${cls}`} style={{ width: `${Math.min(100, Math.max(0, percent))}%` }} />
    </div>
  );
}

function AlertList({ alerts }: { alerts: SystemAlert[] }) {
  if (alerts.length === 0) {
    return <Badge tone="green">All clear — no active alerts</Badge>;
  }
  return (
    <ul className="space-y-1.5">
      {alerts.map((a) => (
        <li
          key={a.key}
          className={`flex items-start gap-2 rounded-xl border px-3 py-2 text-xs ${
            a.severity === "critical"
              ? "border-red-500/40 bg-red-500/5"
              : "border-amber-500/40 bg-amber-500/5"
          }`}
        >
          <TriangleAlert className="size-3.5 mt-0.5 shrink-0" />
          <span>{a.message}</span>
          <span className="ml-auto shrink-0">
            <Badge tone={a.severity === "critical" ? "red" : "amber"}>{a.severity}</Badge>
          </span>
        </li>
      ))}
    </ul>
  );
}

function RamSparkline({ points }: { points: Array<{ timestamp: number; percent: number }> }) {
  if (points.length < 2) return null;
  const w = 220;
  const h = 36;
  const min = Math.min(...points.map((p) => p.percent));
  const max = Math.max(...points.map((p) => p.percent));
  const span = Math.max(1, max - min);
  const step = w / (points.length - 1);
  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(h - 3 - ((p.percent - min) / span) * (h - 6)).toFixed(1)}`)
    .join(" ");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className="mt-2 w-full max-w-55" aria-hidden="true">
      <path d={path} fill="none" stroke="currentColor" strokeWidth="1.5" className="text-primary" />
    </svg>
  );
}

export function SystemMonitorSection() {
  const [vitals, setVitals] = useState<SystemVitals | null>(null);
  const [alerts, setAlerts] = useState<SystemAlert[]>([]);
  const [procData, setProcData] = useState<SystemProcessesResponse | null>(null);
  const [trend, setTrend] = useState<Array<{ timestamp: number; percent: number }>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  // Per-widget fetch failures: a transient fetch error must render as
  // "unavailable — retry", never as a silent data drop and never as a
  // host-property claim (that is what the backend `degraded` flag is for).
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [alertsError, setAlertsError] = useState<string | null>(null);
  const [procError, setProcError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const refresh = useCallback(async (initial = false) => {
    if (initial) setLoading(true);
    else setRefreshing(true);
    setError(null);
    try {
      const [v, history, liveAlerts, topProcs] = await Promise.all([
        fetchSystemVitals(),
        attempt(() => fetchSystemHistory(5)),
        attempt(() => fetchSystemAlerts()),
        attempt(() => fetchSystemProcesses(5)),
      ]);
      setVitals(v);
      if ("error" in liveAlerts) {
        setAlertsError(liveAlerts.error);
        setAlerts(v.alerts); // snapshot from vitals — labeled below as fallback
      } else {
        setAlertsError(null);
        setAlerts(liveAlerts.data);
      }
      if ("error" in topProcs) {
        setProcError(topProcs.error);
      } else {
        setProcError(null);
        setProcData(topProcs.data);
      }
      if ("error" in history) {
        // Do not silently drop (or keep stale) the trend: surface the failure.
        setHistoryError(history.error);
        setTrend([]);
      } else {
        setHistoryError(null);
        setTrend(history.data.points.map((p) => ({ timestamp: p.timestamp, percent: p.ram_percent })));
      }
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void refresh(true);
    timer.current = window.setInterval(() => {
      if (!document.hidden) void refresh(false);
    }, 5000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [refresh]);

  if (loading && !vitals) {
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Activity className="size-3.5 animate-pulse" />
          <span>Reading host resources…</span>
        </div>
        <SkeletonList rows={4} />
      </div>
    );
  }

  if (error && !vitals) {
    return (
      <div className="space-y-3">
        <ErrorBox message={error} onRetry={() => void refresh(true)} />
        <EmptyState
          title="Host metrics unavailable"
          hint="The Gateway system monitor could not be reached. Start the backend and try again."
        />
      </div>
    );
  }

  if (!vitals) return null;

  const ram = vitals.ram;
  const tone = ramTone(ram.percent);
  const gpu = vitals.gpus.length > 0 ? vitals.gpus[0] : null;

  return (
    <div className="space-y-4">
      {error && <ErrorBox message={error} onRetry={() => void refresh(false)} />}

      <div className="flex items-center gap-2 flex-wrap">
        <h3 className="text-sm font-semibold flex items-center gap-1.5">
          <MonitorSmartphone className="size-4" />
          Host resources
        </h3>
        <Badge tone={vitals.internet.reachable ? "green" : "red"}>
          <Globe className="size-3" />
          {vitals.internet.reachable
            ? `Online${vitals.internet.rtt_ms !== null ? ` • ${vitals.internet.rtt_ms} ms` : ""}`
            : "Offline"}
        </Badge>
        {!vitals.psutil_available && <Badge tone="amber">Limited readings (psutil missing)</Badge>}
        <span className="ml-auto">
          <button
            type="button"
            onClick={() => void refresh(false)}
            disabled={refreshing}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border border-border text-[11px] font-medium hover:bg-muted disabled:opacity-40"
          >
            <RefreshCw className={`size-3 ${refreshing ? "animate-spin" : ""}`} />
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </span>
      </div>

      {/* RAM hero — the primary metric, largest and first. */}
      <div
        className={`rounded-2xl border p-4 sm:p-5 ${
          tone === "red"
            ? "border-red-500/50 bg-red-500/5"
            : tone === "amber"
              ? "border-amber-500/50 bg-amber-500/5"
              : "border-border/60 bg-card"
        }`}
      >
        <div className="flex items-center gap-2 flex-wrap">
          <span className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <MemoryStick className="size-4" />
            RAM — primary
          </span>
          <span className="ml-auto">
            <Badge tone={tone}>{tone === "red" ? "critical" : tone === "amber" ? "elevated" : "healthy"}</Badge>
          </span>
        </div>
        <div className="mt-2 flex items-end gap-2 flex-wrap">
          <span className="text-3xl sm:text-4xl font-bold tabular-nums leading-none">
            {formatGiB(ram.used_mb)}
          </span>
          <span className="text-sm text-muted-foreground tabular-nums pb-0.5">
            of {formatGiB(ram.total_mb)} • {ram.percent.toFixed(1)}%
          </span>
          <span className="text-xs text-muted-foreground tabular-nums pb-1 ml-auto">
            {formatGiB(ram.free_mb)} free
          </span>
        </div>
        <div className="mt-3">
          <RamBar percent={ram.percent} />
        </div>
        {historyError ? (
          <div className="mt-3">
            <ErrorBox
              message={`RAM trend unavailable — history fetch failed: ${historyError}`}
              onRetry={() => void refresh(false)}
            />
          </div>
        ) : (
          trend.length >= 2 && (
            <div className="text-[11px] text-muted-foreground mt-3">
              <span>Last 5 min trend</span>
              <RamSparkline points={trend} />
            </div>
          )
        )}
      </div>

      {/* Alerts — a failed live fetch is surfaced, not hidden behind the vitals snapshot. */}
      {alertsError && (
        <ErrorBox
          message={`Live alerts unavailable — fetch failed: ${alertsError}. Showing the snapshot from vitals instead.`}
          onRetry={() => void refresh(false)}
        />
      )}
      <AlertList alerts={alerts} />

      {/* Secondary metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <StatCard
          label={`CPU • ${vitals.cpu.cores} cores${vitals.cpu.frequency_mhz ? ` • ${Math.round(vitals.cpu.frequency_mhz)} MHz` : ""}`}
          value={`${vitals.cpu.percent.toFixed(1)}%`}
          sub="processor load"
        />
        <StatCard
          label="Network down / up"
          value={`${vitals.network.download_mbps.toFixed(2)} / ${vitals.network.upload_mbps.toFixed(2)} Mbps`}
          sub={`${formatBytes(vitals.network.bytes_recv)} rx • ${formatBytes(vitals.network.bytes_sent)} tx`}
        />
        <StatCard
          label={`Host • ${vitals.system.os}`}
          value={vitals.system.hostname}
          sub={`up ${formatUptime(vitals.system.uptime_seconds)}`}
        />
        <StatCard
          label="GPU"
          value={gpu ? gpu.name : "Not detected"}
          sub={
            gpu?.utilization_percent !== null && gpu?.utilization_percent !== undefined
              ? `${Number(gpu.utilization_percent).toFixed(0)}% load`
              : gpu
                ? `via ${gpu.source}`
                : "gracefully hidden"
          }
        />
      </div>

      {/* Top processes — distinguish (a) failed fetch, (b) host unsupported
          (backend degraded flag), (c) measured empty, never conflating them. */}
      <div>
        <h4 className="text-xs font-semibold flex items-center gap-1.5 mb-2">
          <Activity className="size-3.5" />
          Top processes by CPU
        </h4>
        {procError ? (
          <ErrorBox
            message={`Top processes unavailable — fetch failed: ${procError}`}
            onRetry={() => void refresh(false)}
          />
        ) : procData === null ? (
          <EmptyState title="No process data" hint="Waiting for the first process sample." />
        ) : procData.degraded ? (
          <EmptyState
            title="Process telemetry unsupported"
            hint={procData.reason ?? "Process enumeration is unsupported on this host."}
          />
        ) : procData.processes === null ? (
          <EmptyState title="No process data" hint="Process telemetry is unavailable on this host." />
        ) : procData.processes.length === 0 ? (
          <EmptyState title="No process data" hint="The host reported no processes." />
        ) : (
          <div className="rounded-xl border border-border/60 overflow-hidden">
            {procData.processes.map((p) => (
              <div
                key={p.pid}
                className="flex items-center gap-2 px-3 py-1.5 text-[11px] border-b border-border/40 last:border-0"
              >
                <span className="font-mono text-muted-foreground w-14 shrink-0 tabular-nums">{p.pid}</span>
                <span className="font-medium truncate flex-1">{p.name}</span>
                <span className="tabular-nums text-muted-foreground">{p.memory_mb >= 1024 ? `${(p.memory_mb / 1024).toFixed(1)} GiB` : `${p.memory_mb.toFixed(0)} MiB`}</span>
                <span className="font-bold tabular-nums w-14 text-right">{p.cpu_percent.toFixed(1)}%</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Disks per drive */}
      <div>
        <h4 className="text-xs font-semibold flex items-center gap-1.5 mb-2">
          <HardDrive className="size-3.5" />
          Disk / ROM per drive
        </h4>
        {vitals.disks.length === 0 ? (
          <EmptyState title="No disk data" hint="Disk figures are unavailable on this host." />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {vitals.disks.map((d) => (
              <div key={d.mount} className="rounded-xl border border-border/60 bg-card px-3 py-2.5">
                <div className="flex items-center gap-2 text-xs">
                  <Cpu className="size-3 text-muted-foreground" />
                  <span className="font-semibold">{d.mount}</span>
                  <span className="ml-auto font-bold tabular-nums">{d.percent.toFixed(1)}%</span>
                </div>
                <div className="mt-1 text-[11px] text-muted-foreground tabular-nums">
                  {formatGiB(d.used_mb)} of {formatGiB(d.total_mb)} • {formatGiB(d.free_mb)} free
                </div>
                <div className="mt-2 h-1.5 w-full rounded-full bg-muted overflow-hidden">
                  <div
                    className={`h-full rounded-full ${d.percent >= 95 ? "bg-red-500" : d.percent >= 90 ? "bg-amber-500" : "bg-emerald-500"}`}
                    style={{ width: `${Math.min(100, Math.max(0, d.percent))}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
