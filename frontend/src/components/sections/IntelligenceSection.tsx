"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  ControlPlane,
  FabricData,
  GoalsData,
  JournalData,
  LedgerData,
  LoopHealthData,
  ModeData,
  ReplayData,
  SECTION_LABELS,
  SOURCE_SECTIONS,
  SourceEnvelope,
  SourceSectionId,
  CONTROL_PLANE_SCHEMA,
  fetchControlPlane,
  healthStateText,
  healthStateTone,
  metricBasisLabel,
  metricBasisTone,
  metricValueText,
  sectionDisclosure,
  summaryCountsLine,
  unavailableSourcesLine,
} from "@/lib/intelligence";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { Activity, Blocks, BookOpen, History, ListChecks, RefreshCw, Settings2, Target } from "lucide-react";

const SECTION_ICONS: Record<SourceSectionId, React.ReactNode> = {
  mode: <Settings2 className="size-3.5" />,
  loop_health: <Activity className="size-3.5" />,
  ledger: <ListChecks className="size-3.5" />,
  journal: <BookOpen className="size-3.5" />,
  capability_fabric: <Blocks className="size-3.5" />,
  replay: <History className="size-3.5" />,
  goals: <Target className="size-3.5" />,
};

/**
 * The intelligence control plane — `GET /api/intelligence/control-plane`.
 *
 * This panel answers "Is Alpha actually becoming more capable?" from one
 * composed read, and its whole design is the refusal to guess: a source that
 * could not be read renders `unavailable` with the server's own reason, a
 * metric whose basis is not `measured` renders words instead of a number, and
 * a count the Gateway never sent renders *not reported* instead of `0`.
 *
 * The panel is **read-only** — it imports `fetchControlPlane` (a `get`) and
 * `errMsg`, and offers no control that writes. Coverage for every claim it
 * renders lives in `src/lib/intelligence.test.mjs`.
 */

/** Facts for one source's `data`, once its envelope says the data is there. */
function SectionFacts(props: { id: SourceSectionId; data: unknown }): React.ReactNode {
  switch (props.id) {
    case "mode": {
      const d = props.data as ModeData;
      return (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5 flex-wrap">
            <Badge tone={d.enabled === true ? "blue" : d.enabled === false ? "gray" : "amber"}>
              {d.enabled === true ? "learning enabled" : d.enabled === false ? "learning disabled" : "enabled not reported"}
            </Badge>
            {d.mode !== null && <Badge tone="gray">{d.mode}</Badge>}
          </div>
          {d.permits === null ? (
            <p className="text-[10px] text-muted-foreground">permits not reported</p>
          ) : (
            <ul className="text-[10px] text-muted-foreground space-y-0.5">
              {Object.entries(d.permits).map(([mode, permitted]) => (
                <li key={mode}>
                  {mode}: {permitted === true ? "permitted" : permitted === false ? "not permitted" : "not reported"}
                </li>
              ))}
            </ul>
          )}
        </div>
      );
    }
    case "loop_health": {
      const d = props.data as LoopHealthData;
      const report = d.report;
      if (!report) return <p className="text-[11px] text-muted-foreground">The composition returned no report.</p>;
      return (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5 flex-wrap">
            <Badge tone={healthStateTone(report.regime)} title="Verdict from alpha.intelligence.loop_health">
              {healthStateText(report.regime)}
            </Badge>
            <span className="text-[11px] text-muted-foreground">
              {report.scored_attempts === null ? "scored attempts not reported" : `${report.scored_attempts} scored attempts`}
            </span>
          </div>
          {report.reason !== null && <p className="text-[11px] text-amber-700 dark:text-amber-300">{report.reason}</p>}
          {report.bottleneck !== null && report.bottleneck !== "" && (
            <p className="text-[11px]">
              <span className="font-semibold">Bottleneck:</span> {report.bottleneck}
            </p>
          )}
          {report.recommended_action !== null && report.recommended_action !== "" && (
            <p className="text-[11px]">
              <span className="font-semibold">Recommended:</span> {report.recommended_action}
            </p>
          )}
          {report.reasons !== null && report.reasons.length > 0 && (
            <ul className="text-[10px] text-muted-foreground list-disc pl-4 space-y-0.5">
              {report.reasons.map((reason, index) => (
                <li key={index}>{reason}</li>
              ))}
            </ul>
          )}
          <p className="text-[10px] text-muted-foreground">
            {report.gain_per_attempt_trend === null ? "gain trend not reported" : `gain trend ${report.gain_per_attempt_trend}`} ·{" "}
            {report.subsystems_agreeing === null || report.subsystems_total === null
              ? "subsystem agreement not reported"
              : `${report.subsystems_agreeing}/${report.subsystems_total} subsystems agree`}{" "}
            · {d.observed_events === null ? "observed events not reported" : `${d.observed_events} observed events`}
          </p>
          {d.required_subsystems !== null && d.required_subsystems.length > 0 && (
            <p className="text-[10px] text-muted-foreground">Requires: {d.required_subsystems.join(", ")}</p>
          )}
        </div>
      );
    }
    case "ledger": {
      const d = props.data as LedgerData;
      return (
        <div className="space-y-1.5 text-[11px]">
          <p>
            {d.candidates === null ? "candidates not reported" : `${d.candidates} candidates`} ·{" "}
            {d.records === null ? "records not reported" : `${d.records} records`}
          </p>
          {d.enabled_subsystems === null ? (
            <p className="text-[10px] text-muted-foreground">enabled subsystems not reported</p>
          ) : d.enabled_subsystems.length === 0 ? (
            <p className="text-[10px] text-muted-foreground">no subsystems enabled for the quorum</p>
          ) : (
            <p className="text-[10px] text-muted-foreground">Quorum: {d.enabled_subsystems.join(", ")}</p>
          )}
        </div>
      );
    }
    case "journal": {
      const d = props.data as JournalData;
      const integrity = d.integrity;
      return (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5 flex-wrap">
            {integrity === null ? (
              <Badge tone="amber">chain integrity not verified</Badge>
            ) : integrity.ok === true ? (
              <Badge tone="green">chain verified</Badge>
            ) : integrity.ok === false ? (
              <Badge tone="red" title={integrity.reason ?? undefined}>
                chain broken{integrity.broken_at !== null ? ` at entry ${integrity.broken_at}` : ""}
              </Badge>
            ) : (
              <Badge tone="amber">chain integrity not reported</Badge>
            )}
            <span className="text-[11px] text-muted-foreground">
              {integrity !== null && integrity.checked !== null ? `${integrity.checked} entries checked` : "entries checked not reported"}
            </span>
          </div>
          <p className="text-[10px] text-muted-foreground">
            {d.entry_count === null ? "recent entries not reported" : `${d.entry_count} recent entries`} ·{" "}
            {d.corrupt_lines === null ? "corrupt lines not reported" : `${d.corrupt_lines} corrupt lines`}
          </p>
          {integrity !== null && integrity.ok === false && integrity.reason !== null && integrity.reason !== "" && (
            <p className="text-[11px] text-red-600 dark:text-red-400">{integrity.reason}</p>
          )}
        </div>
      );
    }
    case "capability_fabric": {
      const d = props.data as FabricData;
      const counts = d.status_counts === null ? null : Object.entries(d.status_counts).sort(([a], [b]) => a.localeCompare(b));
      return (
        <div className="space-y-1.5 text-[11px]">
          <p>{d.active === null ? "active experts not reported" : `${d.active} active experts`}</p>
          {counts === null ? (
            <p className="text-[10px] text-muted-foreground">status counts not reported</p>
          ) : counts.length === 0 ? (
            <p className="text-[10px] text-muted-foreground">no records by status</p>
          ) : (
            <div className="flex flex-wrap gap-1">
              {counts.map(([status, count]) => (
                <Badge key={status} tone="gray">
                  {status}: {typeof count === "number" ? count : "not reported"}
                </Badge>
              ))}
            </div>
          )}
          {d.note !== null && <p className="text-[10px] text-muted-foreground">{d.note}</p>}
        </div>
      );
    }
    case "replay": {
      const d = props.data as ReplayData;
      const strata = d.strata === null ? null : Object.entries(d.strata).sort(([a], [b]) => a.localeCompare(b));
      return (
        <div className="space-y-1.5 text-[11px]">
          <p>
            {d.size === null ? "size not reported" : `${d.size} items`}
            {d.capacity === null ? " (capacity not reported)" : ` of ${d.capacity} capacity`}
          </p>
          <p>
            {d.utilisation === null ? "utilisation not reported" : `${Math.round(d.utilisation * 100)}% full`} ·{" "}
            {d.oldest_age_seconds === null ? "oldest-item age not reported" : `oldest item ${Math.round(d.oldest_age_seconds)}s old`}
          </p>
          {strata !== null && strata.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {strata.map(([stratum, count]) => (
                <Badge key={stratum} tone="gray">
                  {stratum}: {typeof count === "number" ? count : "not reported"}
                </Badge>
              ))}
            </div>
          )}
        </div>
      );
    }
    case "goals": {
      const d = props.data as GoalsData;
      return (
        <div className="space-y-1.5 text-[11px]">
          <p>{d.tracked === null ? "tracked goals not reported" : `${d.tracked} goals tracked`}</p>
          {d.note !== null && <p className="text-[10px] text-muted-foreground">{d.note}</p>}
        </div>
      );
    }
    default:
      // A section id this build does not know how to render: say so rather
      // than dropping its data silently.
      return <p className="text-[11px] text-muted-foreground">No facts are declared for this section in this build.</p>;
  }
}

/** One source's card: availability badge, the disclosure, then its facts. */
function SourceCard(props: { id: SourceSectionId; envelope: SourceEnvelope<unknown> }) {
  const disclosure = sectionDisclosure(props.envelope);
  return (
    <div className="rounded-xl border border-border/60 p-3 space-y-2" data-intelligence-section={props.id}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold flex items-center gap-1.5">
          {SECTION_ICONS[props.id]}
          {SECTION_LABELS[props.id]}
        </span>
        <Badge tone={props.envelope.available ? "blue" : "red"}>{props.envelope.available ? "read" : "unavailable"}</Badge>
      </div>
      {disclosure !== null && <p className="text-[11px] text-muted-foreground">{disclosure}</p>}
      {props.envelope.data !== null && <SectionFacts id={props.id} data={props.envelope.data} />}
    </div>
  );
}

export function IntelligenceSection() {
  const [plane, setPlane] = useState<ControlPlane | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      setPlane(await fetchControlPlane());
      setError(null);
    } catch (err) {
      // The previous plane (if any) stays on screen: a refresh that failed
      // must not blank figures the server already confirmed.
      setError(errMsg(err));
    } finally {
      setBusy(false);
      setInitialLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (initialLoading) return <SkeletonList rows={6} />;
  if (plane === null) return <ErrorBox message={error ?? "the control plane read failed"} onRetry={() => void refresh()} />;

  const summary = plane.summary;
  const unavailableLine = unavailableSourcesLine(summary.sources_unavailable);

  return (
    <Section
      title="Intelligence control plane"
      hint="One composed read of every intelligence source: mode, loop health, evidence, journal, capability fabric, replay and goals. Read-only — this view performs no mutations."
      actions={
        <>
          <Badge tone={healthStateTone(summary.health_state)} title="The loop-health regime, mirrored from the Gateway">
            {healthStateText(summary.health_state)}
          </Badge>
          <Btn onClick={() => void refresh()} disabled={busy} title="Re-read every source">
            <RefreshCw className="size-3.5" />
            {busy ? "Refreshing…" : "Refresh"}
          </Btn>
        </>
      }
    >
      <div className="space-y-4">
        {error !== null && <Notice tone="warn" message={`The last control-plane read failed: ${error}`} />}
        {plane.schema_version !== CONTROL_PLANE_SCHEMA && (
          <Notice
            tone="warn"
            message={`The Gateway reports payload schema ${plane.schema_version ?? "not reported"}; this build renders ${CONTROL_PLANE_SCHEMA}, so fields above may be missing.`}
          />
        )}
        {unavailableLine !== null && <Notice tone="warn" message={unavailableLine} />}
        <p className="text-[11px] text-muted-foreground">{summaryCountsLine(summary)}</p>
        {summary.unowned !== null && summary.unowned > 0 && (
          <Notice
            tone="neutral"
            message={`${summary.unowned} dashboard metrics have no aggregate owner. Each is declared with the subsystem that would own it and renders its reason instead of a number — wiring one later is a local change; a fabricated zero is not.`}
          />
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="md:col-span-2">
            <SourceCard id="loop_health" envelope={plane.loop_health} />
          </div>
          {SOURCE_SECTIONS.filter((id) => id !== "loop_health").map((id) => (
            <SourceCard key={id} id={id} envelope={plane[id]} />
          ))}
        </div>

        <div className="rounded-xl border border-border/60">
          <div className="px-3 py-2 border-b border-border/60 flex items-center justify-between gap-2 flex-wrap">
            <span className="text-xs font-semibold">Metrics</span>
            <span className="text-[10px] text-muted-foreground">the basis decides what renders — only a measured metric shows a number</span>
          </div>
          {plane.metrics === null ? (
            <div className="p-3">
              <EmptyState title="The Gateway sent no metric list" hint="This is not a claim that there are zero metrics — no list arrived at all." />
            </div>
          ) : plane.metrics.length === 0 ? (
            <div className="p-3">
              <EmptyState title="The Gateway reported no metrics" hint="The composition answered with an empty list." />
            </div>
          ) : (
            <div className="divide-y divide-border/40">
              {plane.metrics.map((metric) => (
                <div key={metric.name} className="px-3 py-2 space-y-1" data-metric={metric.name}>
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <span className="text-[11px] font-medium">{metric.name}</span>
                    <span className="flex items-center gap-1.5">
                      <span className="text-[11px] font-mono">{metricValueText(metric)}</span>
                      <Badge tone={metricBasisTone(metric.basis)} title={metric.reason || undefined}>
                        {metricBasisLabel(metric.basis)}
                      </Badge>
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-2 flex-wrap text-[10px] text-muted-foreground">
                    <span>{metric.reason || (metric.basis === "measured" ? "" : "no reason reported")}</span>
                    <span>{[metric.unit, metric.source].filter(Boolean).join(" · ")}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <p className="text-[10px] text-muted-foreground">
          schema {plane.schema_version ?? "not reported"} · read-only route GET /api/intelligence/control-plane · every figure is read from the
          subsystem that owns it; nothing here is recomputed.
        </p>
      </div>
    </Section>
  );
}
