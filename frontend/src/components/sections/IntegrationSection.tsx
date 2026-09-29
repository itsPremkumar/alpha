"use client";

import React, { useEffect, useState } from "react";
import { fetchIntegrationHealth, IntegrationHealth, ClassCoverage, AutonomyLoopStatus } from "@/lib/integration";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { RefreshCw, Plug, Blocks, Route, Layers, Radio, Cpu, Bus } from "lucide-react";

const SECTION_ICONS: Record<string, React.ReactNode> = {
  tools: <Blocks className="size-3.5" />,
  routers: <Route className="size-3.5" />,
  middlewares: <Layers className="size-3.5" />,
  loops: <Radio className="size-3.5" />,
};

/**
 * "Wired" is a manifest fact, not a health claim.
 *
 * The card used to render a green "complete" badge whenever `wired === total`,
 * which for `loops` reads as "all eight background loops are running". On the
 * measured deployment all eight are `enabled: false` — the supervisor has
 * started, registered every loop, and switched every one of them off. The badge
 * therefore names the claim it can actually support, and the caller adds the
 * runtime cross-reference.
 */
function CoverageCard(props: { name: string; cov: ClassCoverage; runtimeNote?: React.ReactNode }) {
  const { name, cov, runtimeNote } = props;
  const pct = cov.total > 0 ? Math.round((cov.wired / cov.total) * 100) : 0;
  const complete = cov.total > 0 && cov.wired === cov.total;
  return (
    <div className="rounded-xl border border-border bg-card p-3">
      <div className="flex items-center gap-1.5 text-xs font-semibold capitalize">
        {SECTION_ICONS[name] ?? <Plug className="size-3.5" />}
        {name}
      </div>
      <div className="mt-2 flex items-baseline gap-1.5">
        <span className={`text-lg font-bold ${complete ? "text-emerald-600" : "text-foreground"}`}>
          {cov.wired}
        </span>
        <span className="text-xs text-muted-foreground">/ {cov.total} wired</span>
        {complete && <Badge tone="green">fully wired</Badge>}
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar"
        aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}
        aria-label={`${cov.wired} of ${cov.total} ${name} declared by the manifest are wired`}>
        <div className={`h-full rounded-full ${complete ? "bg-emerald-500" : "bg-primary"}`} style={{ width: `${pct}%` }} />
      </div>
      {runtimeNote && <div className="mt-1.5 text-[10px] text-muted-foreground">{runtimeNote}</div>}
      {(cov.intentionally_unwired > 0 || cov.excluded > 0) && (
        <div className="mt-1.5 text-[10px] text-muted-foreground">
          {cov.intentionally_unwired > 0 && <span>{cov.intentionally_unwired} intentionally unwired </span>}
          {cov.excluded > 0 && <span>{cov.excluded} excluded</span>}
        </div>
      )}
    </div>
  );
}

/**
 * How a loop's counters are worded.
 *
 * `null` never renders as `0`. A loop that has genuinely never run reports
 * `runs: 0` and `last_run_at: 0` from the server, and those read as "0 runs /
 * never" rather than as a missing reading, because the server did measure them.
 * A `null` means the build did not report the field, which is a different claim.
 */
function counter(value: number | null, unit: string): string {
  if (value === null) return `no ${unit} reported`;
  if (value === 0) return `0 ${unit}`;
  return `${value.toLocaleString()} ${unit}`;
}

function secondsSince(epochSeconds: number | null): string {
  if (epochSeconds === null) return "last run not reported";
  if (epochSeconds <= 0) return "never run";
  const delta = Math.max(0, Math.round(Date.now() / 1000 - epochSeconds));
  if (delta < 60) return `last run ${delta}s ago`;
  if (delta < 3600) return `last run ${Math.round(delta / 60)}m ago`;
  if (delta < 86400) return `last run ${Math.round(delta / 3600)}h ago`;
  return `last run ${Math.round(delta / 86400)}d ago`;
}

export function AutonomyLoopRow({ loop }: { loop: AutonomyLoopStatus }) {
  // A loop that is switched off must read as off, with the reason it is off.
  // "Enabled in config" is a different fact from "has run since start".
  const state = !loop.enabled
    ? "disabled in config"
    : loop.parked
      ? `parked${loop.park_reason ? `: ${loop.park_reason}` : ""}`
      : loop.running
        ? "running now"
        : loop.task_alive
          ? "idle, task alive"
          : "idle";
  const tone = !loop.enabled || loop.parked ? "gray" : loop.running ? "green" : undefined;
  return (
    <li className="rounded-xl border border-border/60 bg-card p-2.5" data-autonomy-loop={loop.id}>
      <div className="flex items-center gap-1.5 flex-wrap">
        <Cpu className="size-3.5 text-muted-foreground" />
        <span className="text-xs font-semibold font-mono">{loop.id}</span>
        <Badge tone={tone}>{state}</Badge>
        <span className="text-[10px] text-muted-foreground ml-auto">
          {counter(loop.runs, "runs")} · {counter(loop.failures, "failures")} · {secondsSince(loop.last_run_at)}
        </span>
      </div>
      {loop.description && <div className="mt-1 text-[11px] text-muted-foreground">{loop.description}</div>}
      {loop.last_error && (
        <div className="mt-1 text-[10px] text-destructive break-all">last error: {loop.last_error}</div>
      )}
      {loop.last_summary && (
        <div className="mt-1 text-[10px] text-muted-foreground break-all">last summary: {loop.last_summary}</div>
      )}
    </li>
  );
}

export function AutonomyPanel({ health }: { health: IntegrationHealth }) {
  const { autonomy, event_bus: bus, peer_network: peers } = health;
  if (!autonomy.reported) {
    return (
      <div className="rounded-xl border border-border/60 p-3 text-[11px] text-muted-foreground">
        Autonomy supervisor state was not reported by <code>GET /api/ops/integration-health</code> — this is an
        unmeasured reading, not an idle supervisor. The manifest coverage above is a separate, declared fact.
      </div>
    );
  }
  const enabled = autonomy.loops.filter((l) => l.enabled).length;
  const parked = autonomy.loops.filter((l) => l.parked);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <Badge tone={autonomy.enabled ? "green" : "gray"}>
          supervisor {autonomy.enabled === null ? "state not reported" : autonomy.enabled ? "enabled" : "disabled"}
        </Badge>
        <Badge tone={enabled === autonomy.loops.length && enabled > 0 ? "green" : "gray"}>
          {autonomy.loops.length === 0
            ? "no loops reported"
            : `${enabled}/${autonomy.loops.length} loops enabled`}
        </Badge>
        {parked.length > 0 && <Badge tone="amber">{parked.length} parked</Badge>}
        <span className="text-muted-foreground">
          started {autonomy.started_at === null ? "not reported" : secondsSince(autonomy.started_at).replace("last run ", "")}
        </span>
      </div>
      {autonomy.loops.length === 0 ? (
        <EmptyState
          title="No autonomy loops reported"
          hint="The supervisor answered and listed no loops — a real answer, not a missing measurement."
        />
      ) : (
        <ul className="space-y-1.5">
          {autonomy.loops.map((loop) => (
            <AutonomyLoopRow key={loop.id} loop={loop} />
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border/60 p-2.5 text-[11px]">
        <Bus className="size-3.5 text-muted-foreground" />
        <span className="font-semibold">Event bus</span>
        {!bus.reported ? (
          <span className="text-muted-foreground">state not reported</span>
        ) : (
          <>
            <Badge tone={bus.enabled ? "green" : "gray"}>{bus.enabled ? "enabled" : "disabled"}</Badge>
            <span className="text-muted-foreground">
              {counter(bus.published, "events published")} · {counter(bus.dropped_total, "dropped")} ·{" "}
              {counter(bus.subscribers, "subscribers")}
            </span>
          </>
        )}
        <span className="text-muted-foreground">
          peer network {peers.reported ? (peers.enabled ? "enabled" : "disabled") : "state not reported"}
        </span>
      </div>
    </div>
  );
}

export function UnwiredBadge(props: { manifestFound: boolean; count: number }) {
  const { manifestFound, count } = props;
  // With no readable manifest the Gateway reports `coverage: {}` and
  // `unwired: []` (backend/Dockerfile:80; backend/tests/
  // test_feature_manifest_deployment.py:9), so an empty list here means
  // "nothing was diffed", not "everything is wired". Green on that is a
  // fabricated all-clear for a measurement that was never taken.
  if (!manifestFound) return <Badge tone="gray">unwired unknown</Badge>;
  return count === 0 ? (
    <Badge tone="green">no unwired entries</Badge>
  ) : (
    <Badge tone="amber">{count} unwired</Badge>
  );
}

export function IntegrationSection() {
  const [health, setHealth] = useState<IntegrationHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [kindFilter, setKindFilter] = useState<string>("all");

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setHealth(await fetchIntegrationHealth());
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const flash = (m: string) => {
    setNotice(m);
    window.setTimeout(() => setNotice(null), 4000);
  };

  if (loading && !health) {
    return (
      <Section title="Integration" hint="Wiring status across the whole stack.">
        <SkeletonList rows={6} />
      </Section>
    );
  }

  if (error) {
    return (
      <Section title="Integration" hint="Wiring status across the whole stack.">
        <ErrorBox message={error} />
        <Btn onClick={load}>
          <RefreshCw className="size-3.5" /> Retry
        </Btn>
      </Section>
    );
  }

  if (!health) return <EmptyState title="No integration data" />;

  const caps = health.capabilities;
  const kinds = Array.from(new Set(caps.map((c) => c.kind))).sort();
  const shown = kindFilter === "all" ? caps : caps.filter((c) => c.kind === kindFilter);
  const enabledCount = caps.filter((c) => c.enabled).length;
  const totalUnwired = health.unwired.length;
  // `coverage.loops` counts what the manifest declares. Whether any of them is
  // actually switched on is `autonomy.loops.<id>.enabled`, a different fact that
  // used to reach the client and be dropped. Naming both on the same card is the
  // point: "8/8 wired" and "0/8 enabled" are not in tension, they are two
  // questions, and only the second one tells an operator whether anything runs.
  const loopRuntimeNote = !health.autonomy.reported
    ? "runtime state not reported by the Gateway"
    : health.autonomy.loops.length === 0
      ? "no loops reported at runtime"
      : `${health.autonomy.loops.filter((l) => l.enabled).length}/${health.autonomy.loops.length} enabled at runtime`;

  return (
    <Section
      title="Integration"
      hint="Every capability Alpha ships and whether it is actually wired: manifest coverage, autonomy loops, the event bus, and opt-in subsystems."
      actions={
        <Btn onClick={() => { load(); flash("Refreshed."); }}>
          <RefreshCw className="size-3.5" /> Refresh
        </Btn>
      }
    >
      {notice && <Notice message={notice} />}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {Object.entries(health.coverage).map(([name, cov]) => (
          <CoverageCard key={name} name={name} cov={cov} runtimeNote={name === "loops" ? loopRuntimeNote : undefined} />
        ))}
      </div>

      <div className="mt-4">
        <h3 className="text-xs font-semibold">Autonomy supervisor &amp; event bus</h3>
        <p className="mb-2 text-[11px] text-muted-foreground">
          Measured runtime state from <code>GET /api/ops/integration-health</code>. Wiring above is what the
          manifest declares; this is what the process is actually doing.
        </p>
        <AutonomyPanel health={health} />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        {health.manifest_found ? <Badge tone="green">manifest present</Badge> : <Badge tone="amber">manifest missing</Badge>}
        <UnwiredBadge manifestFound={health.manifest_found} count={totalUnwired} />
        <span className="text-muted-foreground">
          capabilities: {enabledCount}/{caps.length} enabled
        </span>
        {health.generated_at && <span className="text-muted-foreground">generated {health.generated_at}</span>}
      </div>

      {totalUnwired > 0 && (
        <div className="mt-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-[11px]">
          <strong className="font-semibold">Unwired:</strong> {health.unwired.slice(0, 10).join(", ")}
          {health.unwired.length > 10 && ` … +${health.unwired.length - 10} more`}
        </div>
      )}

      <div className="mt-4 flex items-center gap-2 flex-wrap">
        <span className="text-xs font-semibold">Opt-in capabilities</span>
        <div className="flex gap-1 flex-wrap">
          <button
            type="button"
            onClick={() => setKindFilter("all")}
            className={`px-2 py-0.5 rounded-lg text-[11px] font-semibold ${
              kindFilter === "all" ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:text-foreground"
            }`}
          >
            all
          </button>
          {kinds.map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => setKindFilter(k)}
              className={`px-2 py-0.5 rounded-lg text-[11px] font-semibold capitalize ${
                kindFilter === k ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:text-foreground"
              }`}
            >
              {k}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-2 space-y-1.5">
        {shown.length === 0 && <EmptyState title="No capabilities in this group" />}
        {shown.map((c) => (
          <div key={c.id} className="rounded-xl border border-border bg-card p-2.5 flex items-start gap-2.5">
            <span
              className={`mt-0.5 size-2.5 rounded-full shrink-0 ${
                !c.enabled ? "bg-muted-foreground/40" : c.loadable ? "bg-emerald-500" : "bg-destructive"
              }`}
              title={!c.enabled ? "disabled" : c.loadable ? "enabled and loadable" : "enabled but failed to load"}
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="text-xs font-semibold">{c.id}</span>
                <Badge tone={c.enabled ? "green" : "gray"}>{c.enabled ? "on" : "off"}</Badge>
                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground capitalize">{c.kind}</span>
              </div>
              <div className="text-[11px] text-muted-foreground">{c.description}</div>
              <code className="text-[10px] text-muted-foreground break-all">
                {c.module}:{c.target}
              </code>
              {c.error && <div className="text-[10px] text-destructive break-all">{c.error}</div>}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-4 text-[11px] text-muted-foreground">
        Enable a capability under <code>capabilities:</code> in <code>config.yaml</code>, then restart the gateway.
      </div>
    </Section>
  );
}
