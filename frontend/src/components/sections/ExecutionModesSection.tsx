"use client";

// ExecutionModesSection - one catalogue of the execution and delegation modes
// this system has, where each is used, and whether its value is MEASURED.
//
// It exists because Arena and the dynamic workflow were not findable: both were
// already implemented (`ArenaPanel` in Team Ops, the council strategy picker in
// Messages, `WorkflowsSection`) but nothing collected them, so each was only
// reachable if you already knew which surface owned it.
//
// What makes this a reference rather than a control panel is deliberate. Most of
// these settings are NOT readable from the Gateway - there is no GET route for
// council strategies, execution tiers, or the effective subagent ceilings, and
// no PUT/PATCH for any of them. So a row either shows what the Gateway measured
// (Arena, staged deliberation) or states its own state and links to the surface
// that owns it. No row invents a value, and no row offers a write the Gateway
// would reject.
//
// `ModeReadiness` is the whole contract:
//   live         the Gateway answered; these numbers are measured
//   auto-routed  the engine decides per prompt; observable on the run it chose
//   config-only  real, set in config.yaml, unreadable through the API

import React, { useCallback, useEffect, useState } from "react";
import { Badge, Btn, ErrorBox } from "@/components/ui";
import { getArenaCatalogue, getArenaConfig, type ArenaCatalogue, type ArenaCeiling } from "@/lib/arena";
import {
  buildModeFamilies,
  summarySentence,
  type ModeFamily,
  type ModeReadiness,
} from "@/lib/execution-modes-view";
import { errMsg } from "@/lib/http";
import { ArrowUpRight, RefreshCw } from "lucide-react";

const READINESS_META: Record<ModeReadiness, { label: string; tone: "green" | "blue" | "gray" }> = {
  live: { label: "Live from Gateway", tone: "green" },
  "auto-routed": { label: "Auto-routed", tone: "blue" },
  "config-only": { label: "Config only", tone: "gray" },
};

/**
 * A ceiling the Gateway did not send.
 *
 * `null` renders as an em dash, never as `0`. Zero would not read as unknown;
 * it would read as an instruction ("you may run zero competitors"), which is a
 * claim about the deployment that nothing measured.
 */
function factValue(value: string | number | null): string {
  if (value === null) return "—";
  if (typeof value === "number") return value.toLocaleString();
  return value;
}

function FamilyCard(props: { family: ModeFamily; onOpenView: (view: string) => void }) {
  const { family, onOpenView } = props;
  const meta = READINESS_META[family.readiness];
  return (
    <section
      data-execution-mode={family.id}
      data-readiness={family.readiness}
      className="rounded-xl border border-border/60 bg-card/60 p-4 flex flex-col gap-3"
    >
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-foreground">{family.name}</h3>
          <p className="text-[11px] text-muted-foreground mt-0.5">{family.blurb}</p>
        </div>
        <Badge tone={meta.tone} title={family.readinessNote || undefined}>
          {meta.label}
        </Badge>
      </header>

      {/*
        The reason a row is not live sits under the badge rather than replacing
        it. A grey chip alone would be indistinguishable from "this feature is
        off", which is a different and much more actionable claim.
      */}
      {family.readinessNote && (
        <p className="text-[11px] text-muted-foreground border-l-2 border-border/60 pl-2">
          {family.readinessNote}
        </p>
      )}

      {family.facts.length > 0 && (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px]">
          {family.facts.map((f) => (
            <div key={f.label} className="flex justify-between gap-2 min-w-0">
              <dt className="text-muted-foreground truncate">{f.label}</dt>
              <dd
                className={`shrink-0 tabular-nums ${f.value === null ? "text-muted-foreground/60" : "text-foreground"}`}
                title={f.value === null ? "The Gateway did not report this value" : undefined}
              >
                {factValue(f.value)}
              </dd>
            </div>
          ))}
        </dl>
      )}

      <Btn
        variant="ghost"
        className="self-start"
        onClick={() => onOpenView(family.view)}
        title={family.action}
      >
        {family.action}
        <ArrowUpRight className="size-3" />
      </Btn>
    </section>
  );
}

export function ExecutionModesSection(props: {
  /** Navigate to the workspace view that owns a mode. */
  onOpenView?: (view: string) => void;
}) {
  const { onOpenView } = props;
  const [config, setConfig] = useState<ArenaCeiling | null>(null);
  const [catalogue, setCatalogue] = useState<ArenaCatalogue | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    // Two independent reads, reported independently: a Gateway that answers
    // `/arena/config` but not `/arena/profiles` still shows the ceilings, and
    // `Promise.all` would have thrown that half away.
    const [configResult, catalogueResult] = await Promise.allSettled([
      getArenaConfig(),
      getArenaCatalogue(),
    ]);
    setConfig(configResult.status === "fulfilled" ? configResult.value : null);
    setCatalogue(catalogueResult.status === "fulfilled" ? catalogueResult.value : null);
    // The reason is kept so the Arena row can say WHICH read failed.
    const failure = [configResult, catalogueResult].find((r) => r.status === "rejected");
    setError(failure && failure.status === "rejected" ? errMsg(failure.reason) : null);
    setLoading(false);
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const families = buildModeFamilies({ config, catalogue, arenaError: error });

  return (
    <div className="p-4 space-y-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-foreground">Execution modes</h2>
          {/* A measured claim, not a marketing one: it says how many rows below
              are backed by a real read. */}
          <p className="text-[11px] text-muted-foreground mt-0.5">
            {loading ? "Reading the Gateway…" : summarySentence(families)}
          </p>
        </div>
        <Btn variant="ghost" onClick={() => void refresh()} disabled={loading} ariaLabel="Refresh mode catalogue">
          <RefreshCw className={`size-3.5 ${loading ? "animate-spin" : ""}`} />
        </Btn>
      </header>

      {error && (
        <ErrorBox
          message={`Arena reads did not answer: ${error}`}
          onRetry={() => void refresh()}
        />
      )}

      <div className="grid gap-3 md:grid-cols-2">
        {families.map((family) => (
          <FamilyCard
            key={family.id}
            family={family}
            onOpenView={(view) => onOpenView?.(view)}
          />
        ))}
      </div>
    </div>
  );
}