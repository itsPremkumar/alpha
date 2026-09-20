"use client";

import React, { useEffect, useState } from "react";
import { fetchIntegrationHealth, IntegrationHealth, ClassCoverage } from "@/lib/integration";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { RefreshCw, Plug, Blocks, Route, Layers, Radio } from "lucide-react";

const SECTION_ICONS: Record<string, React.ReactNode> = {
  tools: <Blocks className="size-3.5" />,
  routers: <Route className="size-3.5" />,
  middlewares: <Layers className="size-3.5" />,
  loops: <Radio className="size-3.5" />,
};

function CoverageCard(props: { name: string; cov: ClassCoverage }) {
  const { name, cov } = props;
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
        {complete && <Badge tone="green">complete</Badge>}
      </div>
      <div className="mt-2 h-1.5 rounded-full bg-muted overflow-hidden">
        <div className={`h-full rounded-full ${complete ? "bg-emerald-500" : "bg-primary"}`} style={{ width: `${pct}%` }} />
      </div>
      {(cov.intentionally_unwired > 0 || cov.excluded > 0) && (
        <div className="mt-1.5 text-[10px] text-muted-foreground">
          {cov.intentionally_unwired > 0 && <span>{cov.intentionally_unwired} intentionally unwired </span>}
          {cov.excluded > 0 && <span>{cov.excluded} excluded</span>}
        </div>
      )}
    </div>
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
          <CoverageCard key={name} name={name} cov={cov} />
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        {health.manifest_found ? <Badge tone="green">manifest present</Badge> : <Badge tone="amber">manifest missing</Badge>}
        {totalUnwired === 0 ? <Badge tone="green">no unwired entries</Badge> : <Badge tone="amber">{totalUnwired} unwired</Badge>}
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
