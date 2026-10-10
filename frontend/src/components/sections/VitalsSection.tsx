"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Cpu,
  Database,
  Gauge,
  Globe,
  HardDrive,
  Layers,
  MemoryStick,
  Monitor,
  RefreshCw,
  Server,
  TriangleAlert,
  Wifi,
} from "lucide-react";

import {
  Badge,
  Btn,
  DetailRow,
  LoadingRows,
  MeasuredNumber,
  NullDisclosure,
  UnavailableNotice,
  ViewPage,
  Block,
} from "@/components/ui";
import { errMsg } from "@/lib/http";
import { Probe, probeAll } from "@/lib/system";
import {
  Connectivity,
  connectivityView,
  fetchConnectivity,
  recheckConnectivity,
} from "@/lib/network";
import {
  SystemProcessesResponse,
  SystemNetworkInterfacesResponse,
  SystemVitals,
  fetchNetworkInterfaces,
  fetchSystemHistory,
  fetchSystemProcesses,
  fetchSystemVitals,
  SystemHistoryPoint,
} from "@/lib/systemMonitor";
import { ConsoleStats, fetchConsoleStats, fetchOpsVersion } from "@/lib/workspace";

/* -------------------------------------------------------------------------- *
 * Formatting helpers. Each one states its own absence in words, because the
 * defect this page exists to prevent is a bare dash that reads as a value.
 * -------------------------------------------------------------------------- */

function formatGiB(mb: number | null): string {
  if (mb === null || !Number.isFinite(mb) || mb < 0) return "—";
  if (mb === 0) return "0 GiB";
  return `${(mb / 1024).toFixed(1)} GiB`;
}

function formatMB(mb: number | null): string {
  if (mb === null || !Number.isFinite(mb) || mb < 0) return "—";
  return `${Math.round(mb).toLocaleString()} MB`;
}

function formatBytes(n: number | null): string {
  if (n === null || !Number.isFinite(n) || n < 0) return "—";
  if (n === 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let v = n;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u += 1;
  }
  return `${v.toFixed(1)} ${units[u]}`;
}

function formatMbps(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  return `${n.toFixed(1)} Mbps`;
}

function formatUptime(totalSeconds: number): string {
  if (!Number.isFinite(totalSeconds) || totalSeconds < 0) return "unknown";
  const d = Math.floor(totalSeconds / 86400);
  const h = Math.floor((totalSeconds % 86400) / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatAge(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "not reported";
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${(seconds / 3600).toFixed(1)}h ago`;
}

function pct(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "not reported";
  return `${value.toFixed(1)}%`;
}

function num(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "not reported";
  return value.toLocaleString();
}

/** Tone from a load percentage. Never green for an unreported value. */
function loadTone(percent: number | null): "green" | "amber" | "red" | "gray" {
  if (percent === null) return "gray";
  if (percent >= 90) return "red";
  if (percent >= 70) return "amber";
  return "green";
}

function toneClass(tone: "green" | "amber" | "red" | "gray"): string {
  if (tone === "red") return "bg-red-500";
  if (tone === "amber") return "bg-amber-500";
  if (tone === "green") return "bg-emerald-500";
  return "bg-muted-foreground/40";
}

/** A labelled load bar. `null` renders as an unmeasured bar, never as 0%. */
function LoadBar({ percent, label }: { percent: number | null; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <div
        className="h-2 w-full min-w-16 rounded-full bg-muted overflow-hidden"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuetext={percent === null ? `${label}: not reported` : `${label}: ${percent.toFixed(1)}%`}
      >
        <div
          className={`h-full rounded-full ${toneClass(loadTone(percent))}`}
          style={{ width: percent === null ? "0%" : `${Math.min(100, Math.max(0, percent))}%` }}
        />
      </div>
      <span className="w-14 shrink-0 text-right text-[11px] font-medium tabular-nums">
        {percent === null ? "not reported" : `${percent.toFixed(1)}%`}
      </span>
    </div>
  );
}

/**
 * A one-metric tile.
 *
 * The glyph says which thing this is and the noun is always present, so a
 * number is never left to speak for itself.
 */
function Tile({
  icon,
  value,
  label,
  title,
  tone = "gray",
}: {
  icon: React.ReactNode;
  value: string;
  label: string;
  title: string;
  tone?: "green" | "amber" | "red" | "gray";
}) {
  return (
    <div
      title={title}
      className="rounded-xl border border-border/60 bg-card/60 px-3 py-2.5"
      data-tile={label}
    >
      <div className="flex items-center gap-1.5 text-muted-foreground">
        <span className={tone === "red" ? "text-red-500" : tone === "amber" ? "text-amber-500" : tone === "green" ? "text-emerald-500" : ""} aria-hidden="true">
          {icon}
        </span>
        <span className="text-[10px] font-medium uppercase tracking-wide">{label}</span>
      </div>
      <div className="mt-1 text-sm font-bold leading-tight break-words">{value}</div>
    </div>
  );
}

/**
 * A measured time series as a sparkline.
 *
 * The window is stated in the caption, and a series with no samples says so
 * rather than drawing an empty box that reads as a flat line at zero.
 */
function Sparkline({
  points,
  pick,
  label,
  unit,
  tone,
}: {
  points: SystemHistoryPoint[];
  pick: (p: SystemHistoryPoint) => number | null;
  label: string;
  unit: string;
  tone: string;
}) {
  const values = points.map(pick);
  const finite = values.filter((v): v is number => v !== null && Number.isFinite(v));

  if (finite.length < 2) {
    return (
      <div className="space-y-1">
        <div className="text-[11px] font-semibold">{label}</div>
        <p className="text-[11px] text-muted-foreground">
          Not enough measured samples to draw a trend, so no line is drawn rather than a
          fabricated one.
        </p>
      </div>
    );
  }

  const width = 100;
  const height = 28;
  const max = Math.max(...finite);
  const min = Math.min(...finite);
  const span = max - min || 1;
  const step = width / (finite.length - 1);

  const path = finite
    .map((v, i) => {
      const x = i * step;
      const y = height - ((v - min) / span) * (height - 3) - 1.5;
      return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");

  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-semibold">{label}</span>
        <span className="text-[10px] text-muted-foreground tabular-nums">
          {min.toFixed(1)}–{max.toFixed(1)} {unit}
        </span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className="h-7 w-full"
        role="img"
        aria-label={`${label}: ${finite.length} measured samples, ranging ${min.toFixed(1)} to ${max.toFixed(1)} ${unit}`}
      >
        <path d={path} fill="none" stroke={tone} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}

/* -------------------------------------------------------------------------- *
 * Reading shape. Every field is optional because every read can fail alone,
 * and a failed read is stated as such rather than defaulted to a zero.
 * -------------------------------------------------------------------------- */

interface VitalsData {
  version: string;
  stats: ConsoleStats | null;
  statsError: string | null;
  host: SystemVitals | null;
  hostError: string | null;
  connectivity: Connectivity | null;
  connectivityError: string | null;
  probes: Probe[];
  probesFailed: boolean;
  history: SystemHistoryPoint[];
  historyError: string | null;
  processes: SystemProcessesResponse | null;
  processesError: string | null;
  interfaces: SystemNetworkInterfacesResponse | null;
  interfacesError: string | null;
}

/**
 * The full monitor behind the header's vitals strip.
 *
 * The strip answers "is the backend there"; this page answers everything else
 * an operator asks once that is settled — how fast is the link, per endpoint;
 * how loaded is the box; which subsystems are answering; what has this
 * workspace done. Each read is independent and names itself when it fails, so
 * a Gateway that answers the host monitor but not the processes route shows
 * the host and states the gap, rather than blanking the page.
 */
export function VitalsSection() {
  const [data, setData] = useState<VitalsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [rechecking, setRechecking] = useState(false);
  const [recheckError, setRecheckError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [statsRes, hostRes, connRes, probesRes, historyRes, procRes, ifaceRes] = await Promise.allSettled([
      fetchConsoleStats(),
      fetchSystemVitals(),
      fetchConnectivity(),
      probeAll(),
      fetchSystemHistory(5),
      fetchSystemProcesses(12, "memory"),
      fetchNetworkInterfaces(),
    ]);

    const hostError = hostRes.status === "rejected" ? errMsg(hostRes.reason) : null;
    const connError = connRes.status === "rejected" ? errMsg(connRes.reason) : null;
    const statsError = statsRes.status === "rejected" ? errMsg(statsRes.reason) : null;
    const probesFailed = probesRes.status === "rejected";
    const historyError = historyRes.status === "rejected" ? errMsg(historyRes.reason) : null;
    const processesError = procRes.status === "rejected" ? errMsg(procRes.reason) : null;
    const interfacesError = ifaceRes.status === "rejected" ? errMsg(ifaceRes.reason) : null;

    let version = "unknown";
    try {
      version = await fetchOpsVersion();
    } catch {
      version = "unknown";
    }

    const procValue = procRes.status === "fulfilled" ? procRes.value : null;
    const ifaceValue = ifaceRes.status === "fulfilled" ? ifaceRes.value : null;

    setData({
      version,
      stats: statsRes.status === "fulfilled" ? statsRes.value : null,
      statsError,
      host: hostRes.status === "fulfilled" ? hostRes.value : null,
      hostError,
      connectivity: connRes.status === "fulfilled" ? connRes.value : null,
      connectivityError: connError,
      probes: probesRes.status === "fulfilled" ? probesRes.value : [],
      probesFailed,
      history: historyRes.status === "fulfilled" ? historyRes.value.points : [],
      historyError,
      processes: procValue,
      processesError,
      interfaces: ifaceValue,
      interfacesError,
    });
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onRecheck = useCallback(async () => {
    if (rechecking) return;
    setRechecking(true);
    setRecheckError(null);
    try {
      // The recheck's response is deliberately not painted: the page re-reads
      // the whole connectivity record so nothing is shown that the server has
      // not just confirmed.
      await recheckConnectivity();
      await load();
    } catch (e) {
      setRecheckError(errMsg(e));
      await load();
    } finally {
      setRechecking(false);
    }
  }, [rechecking, load]);

  if (loading || !data) {
    return (
      <ViewPage title="Vitals" purpose="Reading every live measurement from this Gateway…">
        <LoadingRows what="the system, network and subsystem readings" rows={4} />
      </ViewPage>
    );
  }

  return (
    <VitalsBody data={data} onRecheck={onRecheck} rechecking={rechecking} recheckError={recheckError} onRefresh={load} />
  );
}

/** The render half, split out so the data half stays readable. */
function VitalsBody({
  data,
  onRecheck,
  rechecking,
  recheckError,
  onRefresh,
}: {
  data: VitalsData;
  onRecheck: () => void;
  rechecking: boolean;
  recheckError: string | null;
  onRefresh: () => void;
}) {
  const link = connectivityView(data.connectivity ?? null, data.connectivityError !== null);
  const host = data.host;
  const s = data.stats;

  return (
    <ViewPage
      title="Vitals"
      purpose="Every live measurement this Gateway reports: the internet link per endpoint, how loaded the machine is, which subsystems are answering, and what this workspace has done. Each block names the route it came from, and a reading that never arrived says so instead of showing a zero."
      facts={
        <>
          <Badge tone={data.connectivityError ? "gray" : link.tone}>
            <Globe className="size-3" />
            {link.value} {link.label}
          </Badge>
          <MeasuredNumber value={host && host.ram.percent !== null ? Math.round(host.ram.percent) : null} noun="% RAM" />
          <MeasuredNumber value={host ? Math.round(host.cpu.percent) : null} noun="% CPU" />
          {data.connectivity ? (
            <MeasuredNumber value={data.connectivity.latency_ms === null ? null : Math.round(data.connectivity.latency_ms)} noun="ms link" />
          ) : (
            <Badge tone="amber" title="GET /api/ops/network did not answer.">
              link not reported
            </Badge>
          )}
          <MeasuredNumber value={s ? s.runs : null} noun="runs" />
        </>
      }
      actions={
        <Btn variant="ghost" onClick={onRefresh} title="Re-read every measurement on this page">
          <RefreshCw className="size-3.5" />
          Refresh
        </Btn>
      }
    >
      {/* ── Internet: per-endpoint, because "the link is up" hides which half ── */}
      <Block
        title="Internet link"
        hint="The runtime's own four-state connectivity reading with per-endpoint round-trips, from GET /api/ops/network — not the host monitor's single TCP probe."
      >
        {data.connectivityError ? (
          <UnavailableNotice what="the internet connectivity reading" reason={data.connectivityError} />
        ) : !data.connectivity ? (
          <NullDisclosure what="connectivity reading" why="the route has not answered for this view yet" />
        ) : (
          <div className="space-y-3">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge tone={link.tone}>{link.value}</Badge>
              <span className="text-[11px] text-muted-foreground">{link.title}</span>
              {link.canRetry && (
                <Btn variant="ghost" onClick={onRecheck} disabled={rechecking} className="!px-2 !py-1 !text-[11px]">
                  <RefreshCw className={`size-3 ${rechecking ? "animate-spin" : ""}`} />
                  {rechecking ? "Re-checking…" : "Measure now"}
                </Btn>
              )}
            </div>
            {recheckError && (
              <p className="text-[11px] text-amber-600 dark:text-amber-400">Last re-check did not run: {recheckError}</p>
            )}

            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              <Tile
                icon={<Wifi className="size-3.5" />}
                value={data.connectivity.latency_ms === null ? "not reported" : `${Math.round(data.connectivity.latency_ms)} ms`}
                label="mean round-trip"
                tone={link.tone}
                title={
                  data.connectivity.latency_ms === null
                    ? "Mean connect round-trip: NOT REPORTED. No reachable endpoint produced a latency in the last probe, so this is not 0 ms."
                    : `Mean measured TCP connect round-trip across the reachable endpoints. It is a TCP connect only — no HTTP request and no payload — so it measures the route, not any provider's health. From GET /api/ops/network (latency_ms).`
                }
              />
              <Tile
                icon={<Activity className="size-3.5" />}
                value={formatAge(data.connectivity.observed_age_seconds)}
                label="reading age"
                title="Seconds since the last completed probe. The route carries no wall-clock timestamp because the reading is stamped from a monotonic clock, so an age is the only honest figure."
              />
              <Tile
                icon={<Server className="size-3.5" />}
                value={data.connectivity.monitoring ? "measuring" : "not measuring"}
                label="monitor state"
                title={`Whether this Gateway process is measuring connectivity at all. ${data.connectivity.reason ? `Reason: ${data.connectivity.reason}.` : ""}`}
              />
            </div>

            {data.connectivity.targets.length > 0 && (
              <div className="space-y-1">
                <p className="text-[11px] font-semibold">Per endpoint</p>
                <div className="overflow-x-auto">
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="text-left text-muted-foreground">
                        <th className="py-1 pr-3 font-medium">Endpoint</th>
                        <th className="py-1 pr-3 font-medium">Reachable</th>
                        <th className="py-1 pr-3 font-medium">Round-trip</th>
                        <th className="py-1 pr-3 font-medium">Failure</th>
                        <th className="py-1 font-medium">Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.connectivity.targets.map((t) => (
                        <tr key={t.name} className="border-t border-border/40">
                          <td className="py-1 pr-3 font-mono">{t.name}</td>
                          <td className="py-1 pr-3">
                            <Badge tone={t.reachable ? "green" : "red"}>{t.reachable ? "yes" : "no"}</Badge>
                          </td>
                          <td className="py-1 pr-3 font-mono tabular-nums">
                            {t.latency_ms === null ? "not reported" : `${t.latency_ms.toFixed(1)} ms`}
                          </td>
                          <td className="py-1 pr-3">{t.failure_kind || "—"}</td>
                          <td className="py-1 text-muted-foreground">{t.detail || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
              <DetailRow
                label="Automatic re-probing"
                value={
                  data.connectivity.retry.automatic
                    ? data.connectivity.retry.retrying
                      ? `running — retrying until the link returns`
                      : `running`
                    : "switched off in this process"
                }
              />
              <DetailRow
                label="Next probe in"
                value={data.connectivity.retry.next_probe_seconds === null ? "not reported" : `~${Math.round(data.connectivity.retry.next_probe_seconds)}s`}
              />
              <DetailRow
                label="Poll interval"
                value={data.connectivity.retry.poll_interval_seconds === null ? "not reported" : `${data.connectivity.retry.poll_interval_seconds}s`}
              />
              <DetailRow
                label="Backoff ceiling"
                value={data.connectivity.retry.backoff_max_seconds === null ? "not reported" : `${data.connectivity.retry.backoff_max_seconds}s`}
              />
              <DetailRow
                label="Network attempt allowed"
                value={data.connectivity.allows_network_attempt === null ? "not reported" : data.connectivity.allows_network_attempt ? "yes" : "no"}
              />
              <DetailRow label="Parked-work durability" value={data.connectivity.parked_durability || "not reported"} />
            </div>
          </div>
        )}
      </Block>

      {/* ── Speed: the throughput readings, which the strip never showed ─────── */}
      <Block
        title="Throughput"
        hint="Live send/receive rate and total bytes moved, from GET /api/system/vitals (network)."
      >
        {!host ? (
          <UnavailableNotice what="host throughput readings" reason={data.hostError} />
        ) : (
          <div className="space-y-3">
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <Tile icon={<Activity className="size-3.5" />} value={formatMbps(host.network.upload_mbps)} label="upload" title="Current upload rate measured on the host, from GET /api/system/vitals (network.upload_mbps)." />
              <Tile icon={<Activity className="size-3.5" />} value={formatMbps(host.network.download_mbps)} label="download" title="Current download rate measured on the host, from GET /api/system/vitals (network.download_mbps)." />
              <Tile icon={<Database className="size-3.5" />} value={formatBytes(host.network.bytes_sent)} label="total sent" title="Lifetime bytes sent on the host, from GET /api/system/vitals (network.bytes_sent)." />
              <Tile icon={<Database className="size-3.5" />} value={formatBytes(host.network.bytes_recv)} label="total received" title="Lifetime bytes received on the host, from GET /api/system/vitals (network.bytes_recv)." />
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <Sparkline points={data.history} pick={(p) => p.download_mbps} label="Download, last 5 minutes" unit="Mbps" tone="#38bdf8" />
              <Sparkline points={data.history} pick={(p) => p.upload_mbps} label="Upload, last 5 minutes" unit="Mbps" tone="#a78bfa" />
            </div>
            {data.historyError && (
              <p className="text-[11px] text-muted-foreground">
                Trend not drawn — the history read failed: {data.historyError}.
              </p>
            )}
          </div>
        )}
      </Block>

      {/* ── Memory ──────────────────────────────────────────────────────────── */}
      <Block title="Memory" hint="Host RAM and swap, from GET /api/system/vitals (memory).">
        {!host ? (
          <UnavailableNotice what="host memory readings" reason={data.hostError} />
        ) : (
          <div className="space-y-3">
            <LoadBar percent={host.ram.percent} label="RAM used" />
            <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
              <DetailRow label="Used" value={formatGiB(host.ram.used_mb)} />
              <DetailRow label="Total installed" value={formatGiB(host.ram.total_mb)} />
              <DetailRow label="Available" value={formatGiB(host.ram.available_mb)} />
              <DetailRow label="Free" value={formatGiB(host.ram.free_mb)} />
              <DetailRow label="Used percentage" value={pct(host.ram.percent)} />
              <DetailRow label="Used, in MB" value={formatMB(host.ram.used_mb)} />
            </div>
            <p className="text-[11px] text-muted-foreground">
              Swap: {formatGiB(host.swap.used_mb)} used of {formatGiB(host.swap.total_mb)} ({pct(host.swap.percent)}),{" "}
              {formatGiB(host.swap.free_mb)} free.
            </p>
            <Sparkline points={data.history} pick={(p) => p.ram_percent} label="RAM load, last 5 minutes" unit="%" tone="#f472b6" />
          </div>
        )}
      </Block>

      {/* ── CPU ─────────────────────────────────────────────────────────────── */}
      <Block title="Processor" hint="Host CPU, from GET /api/system/vitals (cpu).">
        {!host ? (
          <UnavailableNotice what="host CPU readings" reason={data.hostError} />
        ) : (
          <div className="space-y-3">
            <LoadBar percent={host.cpu.percent} label="CPU used" />
            <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
              <DetailRow label="Cores" value={host.cpu.cores ? num(host.cpu.cores) : "not reported"} />
              <DetailRow label="Physical cores" value={host.cpu.physical_cores === null ? "not reported" : num(host.cpu.physical_cores)} />
              <DetailRow label="Current frequency" value={host.cpu.frequency_mhz === null ? "not reported" : `${Math.round(host.cpu.frequency_mhz)} MHz`} />
              <DetailRow label="Maximum frequency" value={host.cpu.max_frequency_mhz === null ? "not reported" : `${Math.round(host.cpu.max_frequency_mhz)} MHz`} />
            </div>
            <Sparkline points={data.history} pick={(p) => p.cpu_percent} label="CPU load, last 5 minutes" unit="%" tone="#4ade80" />
          </div>
        )}
      </Block>

      {/* ── Storage ─────────────────────────────────────────────────────────── */}
      <Block title="Storage" hint="Per-mount disk usage, from GET /api/system/vitals (disks).">
        {!host ? (
          <UnavailableNotice what="host disk readings" reason={data.hostError} />
        ) : host.disks.length === 0 ? (
          <NullDisclosure what="disk mounts" why="the Gateway reported no mounts for this host" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="py-1 pr-3 font-medium">Mount</th>
                  <th className="py-1 pr-3 font-medium">Device</th>
                  <th className="py-1 pr-3 font-medium">Filesystem</th>
                  <th className="py-1 pr-3 font-medium">Used</th>
                  <th className="py-1 pr-3 font-medium">Total</th>
                  <th className="py-1 pr-3 font-medium">Free</th>
                  <th className="py-1 font-medium">Load</th>
                </tr>
              </thead>
              <tbody>
                {host.disks.map((d) => (
                  <tr key={`${d.mount}:${d.device}`} className="border-t border-border/40">
                    <td className="py-1 pr-3 font-mono">{d.mount}</td>
                    <td className="py-1 pr-3 font-mono text-muted-foreground">{d.device || "—"}</td>
                    <td className="py-1 pr-3 text-muted-foreground">{d.filesystem || "—"}</td>
                    <td className="py-1 pr-3 tabular-nums">{formatGiB(d.used_mb)}</td>
                    <td className="py-1 pr-3 tabular-nums">{formatGiB(d.total_mb)}</td>
                    <td className="py-1 pr-3 tabular-nums">{formatGiB(d.free_mb)}</td>
                    <td className="py-1">
                      <LoadBar percent={d.percent} label={`${d.mount} used`} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Block>

      {/* ── GPUs ────────────────────────────────────────────────────────────── */}
      {host && host.gpus.length > 0 && (
        <Block title="Graphics" hint="Per-GPU utilisation, memory and temperature, from GET /api/system/vitals (gpus).">
          <div className="grid gap-2 sm:grid-cols-2">
            {host.gpus.map((g) => (
              <div key={g.index} className="rounded-xl border border-border/60 bg-card/60 p-3 space-y-2">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-xs font-semibold">{g.name}</span>
                  <span className="text-[10px] text-muted-foreground">{g.vendor || "vendor not reported"}</span>
                </div>
                <LoadBar percent={g.utilization_percent} label={`${g.name} utilisation`} />
                <div className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
                  <DetailRow label="Memory" value={g.memory_total_mb === null ? "not reported" : `${formatGiB(g.memory_used_mb)} of ${formatGiB(g.memory_total_mb)}`} />
                  <DetailRow label="Memory load" value={pct(g.memory_percent)} />
                  <DetailRow label="Temperature" value={g.temperature_c === null ? "not reported" : `${g.temperature_c.toFixed(0)} °C`} />
                  <DetailRow label="Reported by" value={g.source || "not reported"} />
                </div>
              </div>
            ))}
          </div>
        </Block>
      )}

      {/* ── Top processes ───────────────────────────────────────────────────── */}
      <Block title="Busiest processes" hint="Top processes by memory, from GET /api/system/processes. Safe telemetry only — no command lines and no environment.">
        {data.processesError ? (
          <UnavailableNotice what="host process readings" reason={data.processesError} />
        ) : !data.processes ? (
          <NullDisclosure what="process list" why="the route has not answered for this view yet" />
        ) : data.processes.processes === null ? (
          // The route succeeded and told us it could not enumerate. That is a
          // THIRD state, distinct from "no processes" and from a failed read.
          <p className="text-[11px] text-muted-foreground">
            Process telemetry is not available on this host
            {data.processes.reason ? ` — ${data.processes.reason}` : ""}. An absent list here is not a host
            running no processes.
          </p>
        ) : data.processes.processes.length === 0 ? (
          <NullDisclosure what="processes" why="the Gateway reported none" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="py-1 pr-3 font-medium">PID</th>
                  <th className="py-1 pr-3 font-medium">Name</th>
                  <th className="py-1 pr-3 font-medium">Memory</th>
                  <th className="py-1 pr-3 font-medium">Memory %</th>
                  <th className="py-1 pr-3 font-medium">CPU %</th>
                  <th className="py-1 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {data.processes.processes.map((p) => (
                  <tr key={`${p.pid}-${p.name}`} className="border-t border-border/40">
                    <td className="py-1 pr-3 font-mono text-muted-foreground">{p.pid}</td>
                    <td className="py-1 pr-3 font-medium">{p.name}</td>
                    <td className="py-1 pr-3 tabular-nums">{formatMB(p.memory_mb)}</td>
                    <td className="py-1 pr-3 tabular-nums">{pct(p.memory_percent)}</td>
                    <td className="py-1 pr-3 tabular-nums">{pct(p.cpu_percent)}</td>
                    <td className="py-1 text-muted-foreground">{p.status || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Block>

      {/* ── Network interfaces ──────────────────────────────────────────────── */}
      <Block title="Network interfaces" hint="Per-interface state and counters, from GET /api/system/network/interfaces.">
        {data.interfacesError ? (
          <UnavailableNotice what="network interface readings" reason={data.interfacesError} />
        ) : !data.interfaces ? (
          <NullDisclosure what="interface list" why="the route has not answered for this view yet" />
        ) : data.interfaces.interfaces === null ? (
          // A disclosed host limitation: null, never a fabricated empty list.
          <p className="text-[11px] text-muted-foreground">
            Interface enumeration is not supported on this host
            {data.interfaces.reason ? ` — ${data.interfaces.reason}` : ""}. No interface is claimed to exist.
          </p>
        ) : data.interfaces.interfaces.length === 0 ? (
          <NullDisclosure what="interfaces" why="the Gateway reported none" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="py-1 pr-3 font-medium">Interface</th>
                  <th className="py-1 pr-3 font-medium">State</th>
                  <th className="py-1 pr-3 font-medium">Link speed</th>
                  <th className="py-1 pr-3 font-medium">IPv4</th>
                  <th className="py-1 pr-3 font-medium">Sent</th>
                  <th className="py-1 font-medium">Received</th>
                </tr>
              </thead>
              <tbody>
                {data.interfaces.interfaces.map((i) => (
                  <tr key={i.name} className="border-t border-border/40">
                    <td className="py-1 pr-3 font-mono font-medium">{i.name}</td>
                    <td className="py-1 pr-3">
                      <Badge tone={i.is_up ? "green" : "gray"}>{i.is_up ? "up" : "down"}</Badge>
                    </td>
                    <td className="py-1 pr-3 tabular-nums">{i.speed_mbps === null ? "not reported" : `${i.speed_mbps} Mbps`}</td>
                    <td className="py-1 pr-3 font-mono text-muted-foreground">{i.ipv4.length ? i.ipv4.join(", ") : "none"}</td>
                    <td className="py-1 pr-3 tabular-nums">{formatBytes(i.bytes_sent)}</td>
                    <td className="py-1 tabular-nums">{formatBytes(i.bytes_recv)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Block>

      {/* ── Host ────────────────────────────────────────────────────────────── */}
      <Block title="Host" hint="The machine this Gateway runs on, from GET /api/system/vitals (system).">
        {!host ? (
          <UnavailableNotice what="host information" reason={data.hostError} />
        ) : (
          <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
            <DetailRow label="Hostname" value={host.system.hostname || "not reported"} />
            <DetailRow label="Operating system" value={[host.system.os, host.system.os_version].filter(Boolean).join(" ") || "not reported"} />
            <DetailRow label="Uptime" value={formatUptime(host.system.uptime_seconds)} />
            <DetailRow label="Health" value={host.health || "not reported"} />
            <DetailRow label="Detailed telemetry" value={host.psutil_available ? "available (psutil)" : "not available on this host"} />
            <DetailRow label="Alpha version" value={`v${data.version}`} />
          </div>
        )}
      </Block>

      {/* ── Alerts ──────────────────────────────────────────────────────────── */}
      <Block title="Active alerts" hint="Conditions the host monitor has raised, from GET /api/system/vitals (alerts).">
        {!host ? (
          <UnavailableNotice what="host alerts" reason={data.hostError} />
        ) : host.alerts.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">
            No active alerts. The Gateway measured and raised none.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {host.alerts.map((a, i) => (
              <li
                key={`${a.key}-${i}`}
                className="flex items-start gap-2 rounded-lg border border-border/50 bg-card/40 px-2.5 py-1.5 text-[11px]"
              >
                <TriangleAlert
                  className={`mt-0.5 size-3.5 shrink-0 ${a.severity === "critical" ? "text-red-500" : "text-amber-500"}`}
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <span className="font-semibold">{a.category}</span>
                    <Badge tone={a.severity === "critical" ? "red" : "amber"}>{a.severity || "unknown severity"}</Badge>
                  </div>
                  <p className="text-muted-foreground">{a.message}</p>
                  {a.value !== null && a.threshold !== null && (
                    <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                      measured {a.value} against a threshold of {a.threshold}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Block>

      {/* ── Workspace totals ──────────────────────────────────────────────── */}
      <Block title="Workspace totals" hint="Lifetime counts from GET /api/console/stats. These are the server's own counts, not this UI's.">
        {data.statsError ? (
          <UnavailableNotice what="workspace totals" reason={data.statsError} />
        ) : !s ? (
          <NullDisclosure what="totals" why="the route has not answered for this view yet" />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <Tile icon={<Activity className="size-3.5" />} value={num(s.runs)} label="runs" title="Recorded runs on this Gateway, from GET /api/console/stats (total_runs)." />
            <Tile icon={<Database className="size-3.5" />} value={num(s.threads)} label="chats" title="Conversations stored on this Gateway, from GET /api/console/stats (total_threads)." />
            <Tile
              icon={<Monitor className="size-3.5" />}
              value={s.agents === null ? "not reported" : num(s.agents)}
              label="agent profiles"
              title={
                s.agents === null
                  ? `Agent profiles: NOT REPORTED. ${s.agentsReason ?? "GET /api/console/stats returned total_agents: null"}. This is a failed read, not zero profiles.`
                  : "Custom agent profiles, from GET /api/console/stats (total_agents). Zero means none are defined."
              }
            />
            <Tile icon={<Gauge className="size-3.5" />} value={num(s.tokens)} label="tokens" title="Model tokens billed to this workspace, from GET /api/console/stats (total_tokens)." />
          </div>
        )}
      </Block>

      {/* ── Subsystem readiness ───────────────────────────────────────────── */}
      <Block title="Subsystem readiness" hint="Every subsystem the Gateway probes, from GET /api/features.">
        {data.probesFailed ? (
          <UnavailableNotice what="subsystem probes" reason="The probe request itself failed, so no subsystem was measured. This is neither 0 of 7 nor 7 of 7 — nobody measured anything." />
        ) : data.probes.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">
            The Gateway reported no subsystems to probe, which is its answer rather than a failed measurement.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {data.probes.map((p) => (
              <li key={p.key} className="flex items-start gap-2 text-[11px]">
                <span className={`mt-1 size-2 shrink-0 rounded-full ${p.ok ? "bg-emerald-500" : "bg-red-500"}`} aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <span className="font-medium">{p.label}</span>
                    <Badge tone={p.ok ? "green" : "red"}>{p.ok ? "ready" : "not ready"}</Badge>
                  </div>
                  {!p.ok && p.detail && <p className="text-muted-foreground">{p.detail}</p>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Block>
    </ViewPage>
  );
}




