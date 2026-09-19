"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Brain,
  Blocks,
  CalendarClock,
  Coins,
  Cpu,
  Database,
  Plug,
  Radio,
  Server,
} from "lucide-react";

import { Badge } from "@/components/ui";
import { Probe, probeAll } from "@/lib/system";
import { ConsoleStats, fetchConsoleStats, fetchOpsVersion } from "@/lib/workspace";

/**
 * Live vitals for the whole workspace, sourced from the Gateway.
 *
 * The main screen should never leave the user guessing whether the backend is
 * actually there, so this strip consolidates the highest-signal numbers
 * (connectivity, version, usage, and subsystem readiness) in one place instead
 * of scattering them across settings pages.
 */

interface Vitals {
  online: boolean;
  version: string;
  stats: ConsoleStats | null;
  probes: Probe[];
}

function compactNumber(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatCost(cost: number | null, currency: string | null): string {
  if (cost === null || !Number.isFinite(cost)) return "—";
  const symbol = currency ? ` ${currency}` : "";
  return `$${cost.toFixed(cost < 1 ? 4 : 2)}${symbol}`;
}

async function load(): Promise<Vitals> {
  const [probes, statsRes, version] = await Promise.all([
    probeAll().catch(() => [] as Probe[]),
    fetchConsoleStats().catch(() => null),
    fetchOpsVersion().catch(() => "unknown"),
  ]);
  const gateway = probes.find((p) => p.key === "gateway");
  return {
    online: Boolean(gateway?.ok),
    version,
    stats: statsRes,
    probes,
  };
}

function Metric({
  icon,
  label,
  value,
  title,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  title?: string;
}) {
  return (
    <span
      title={title ?? `${label}: ${value}`}
      className="inline-flex items-center gap-1 text-[11px] text-muted-foreground whitespace-nowrap"
    >
      <span className="text-muted-foreground/70">{icon}</span>
      <span className="font-semibold text-foreground tabular-nums">{value}</span>
      <span className="hidden lg:inline">{label}</span>
    </span>
  );
}

export function WorkspaceVitals({ className = "" }: { className?: string }) {
  const [vitals, setVitals] = useState<Vitals | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setVitals(await load());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (loading && !vitals) {
    return (
      <div className={`flex items-center gap-3 text-[11px] text-muted-foreground ${className}`}>
        <Activity className="size-3.5 animate-pulse" />
        <span>Checking backend…</span>
      </div>
    );
  }

  if (!vitals) return null;

  const s = vitals.stats;
  const subsystems = vitals.probes.filter((p) =>
    ["memory", "skills", "scheduled", "channels", "mcp", "watchdog", "company"].includes(p.key),
  );
  const readyCount = subsystems.filter((p) => p.ok).length;

  return (
    <div className={`flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11px] ${className}`}>
      <Badge tone={vitals.online ? "green" : "red"}>
        <Server className="size-3" />
        {vitals.online ? "Gateway online" : "Gateway offline"}
      </Badge>

      <span title={`Agent Workspace version ${vitals.version}`} className="inline-flex items-center gap-1 text-muted-foreground whitespace-nowrap">
        <Cpu className="size-3" />
        <span className="font-medium tabular-nums">v{vitals.version}</span>
      </span>

      {s && (
        <>
          <Metric icon={<Activity className="size-3" />} label="runs" value={compactNumber(s.runs)} />
          <Metric icon={<Database className="size-3" />} label="chats" value={compactNumber(s.threads)} />
          <Metric icon={<Blocks className="size-3" />} label="agents" value={compactNumber(s.agents)} />
          <Metric icon={<Coins className="size-3" />} label="tokens" value={compactNumber(s.tokens)} />
          <Metric icon={<Coins className="size-3" />} label="cost" value={formatCost(s.cost, s.currency)} />
        </>
      )}

      {subsystems.length > 0 && (
        <span className="inline-flex items-center gap-1 text-muted-foreground whitespace-nowrap">
          <Radio className="size-3" />
          <span className="font-semibold text-foreground tabular-nums">
            {readyCount}/{subsystems.length}
          </span>
          <span className="hidden lg:inline">subsystems ready</span>
        </span>
      )}

      {subsystems.map((p) => (
        <span
          key={p.key}
          title={`${p.label} — ${p.detail}`}
          className="inline-flex items-center gap-1 text-muted-foreground whitespace-nowrap"
        >
          <span
            className={`size-1.5 rounded-full ${p.ok ? "bg-emerald-500" : "bg-muted-foreground/40"}`}
            aria-hidden="true"
          />
          {p.key === "memory" && <Brain className="size-3" />}
          {p.key === "skills" && <Blocks className="size-3" />}
          {p.key === "scheduled" && <CalendarClock className="size-3" />}
          {p.key === "channels" && <Radio className="size-3" />}
          {p.key === "mcp" && <Plug className="size-3" />}
          <span className="hidden xl:inline">{p.label}</span>
          <span className="sr-only">
            {p.label}: {p.detail}
          </span>
        </span>
      ))}
    </div>
  );
}
