"use client";

// SentinelSection — the autonomous repair loop, finally readable.
//
// Every number on this page comes from the real gateway endpoints in
// backend/app/gateway/routers/autonomy.py (prefix /api/autonomy):
//
//   GET  /sentinel/analytics?limit=N   — the journal folded into one reading
//   GET  /sentinel/kinds               — the declared fault kinds + repair posture
//   GET  /sentinel/escalations?limit=N — human handoffs, oldest first
//   GET  /sentinel/reports?limit=N     — the raw pass journal
//   GET  /sentinel/signals              — observe-only collection
//   POST /sentinel/run                  — one real pass (auto_heal always sent)
//   POST /sentinel/escalations/{id}/acknowledge | /resolve  — admin decisions
//
// The three ground rules this page is built on:
//
//  1. **Six independent reads, six independent failures.** They go through
//     `Promise.allSettled`, so a corrupt journal or an unreadable escalation
//     store blanks exactly one panel and names which read it was. A single
//     `Promise.all` would let one 500 present itself as a whole empty plane.
//  2. **A word is only attached to the counters that earned it.** The health
//     badge renders `analyticsHealthView`, which returns its reason beside its
//     label; the per-kind verdict renders `verdictWords`, and the evidence line
//     under it lists the counts separately. Neither can carry the other's
//     claim, and an unrecognised verdict renders neutral gray, never green.
//  3. **Nothing is painted from a click.** A decision posts, then the list and
//     the escalation itself are re-read; a refused action leaves the row
//     exactly where it was with the server's own sentence under it.
import React, { useCallback, useEffect, useState } from "react";
import {
  acknowledgeSentinelEscalation,
  analyticsHealthView,
  clampLimit,
  durationText,
  failureText,
  getSentinelAnalytics,
  getSentinelEscalations,
  getSentinelKinds,
  getSentinelReports,
  getSentinelSignals,
  kindEvidenceLine,
  resolveSentinelEscalation,
  runSentinelPass,
  scannedText,
  verdictTone,
  verdictWords,
} from "@/lib/sentinel";
import type {
  SentinelAnalytics,
  SentinelEscalation,
  SentinelEscalations,
  SentinelKinds,
  SentinelKindReading,
  SentinelReports,
  SentinelSignals,
} from "@/lib/sentinel";
import type { SentinelRunReport } from "@/lib/sentinel";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, SkeletonList } from "@/components/ui";
import { Activity, GitCompareArrows, ListChecks, RefreshCw, Radar, ScrollText, ShieldAlert, Wrench } from "lucide-react";

/* ══ Section shell ══════════════════════════════════════════════════════ */

export function SentinelSection() {
  const [refreshKey, setRefreshKey] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);

  const flash = useCallback((message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(null), 9000);
  }, []);

  return (
    <Section
      title="Sentinel"
      hint="The autonomous repair loop: what it observed, what it repaired, what it reverted, and what a human still has to decide. Every value below is measured by the gateway, never simulated."
      actions={
        <Btn variant="ghost" onClick={() => setRefreshKey((key) => key + 1)}>
          <RefreshCw className="size-3.5" /> Refresh
        </Btn>
      }
    >
      {notice && <Notice message={notice} />}
      <AnalyticsPanel refreshKey={refreshKey} />
      <KindRegistryPanel refreshKey={refreshKey} />
      <HandoffPanel refreshKey={refreshKey} onNotice={flash} />
      <PassControls onNotice={flash} onRan={() => setRefreshKey((key) => key + 1)} />
      <HistoryPanel refreshKey={refreshKey} />
      <SignalsPanel />
    </Section>
  );
}

/* ══ Aggregate readings ═════════════════════════════════════════════════ */

function AnalyticsPanel(props: { refreshKey: number }) {
  const [analytics, setAnalytics] = useState<SentinelAnalytics | null>(null);
  const [window, setWindow] = useState<{ limit: number; total_on_disk: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const envelope = await getSentinelAnalytics();
      setAnalytics(envelope.analytics);
      setWindow({ limit: envelope.limit, total_on_disk: envelope.total_on_disk });
      setError(null);
    } catch (e) {
      // A corrupt journal answers 500 with the file and line. That is a real
      // finding, not an empty plane.
      setError(failureText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, props.refreshKey]);

  if (loading && !analytics) return <SkeletonList rows={5} />;
  if (error && !analytics) {
    return <ErrorBox message={`Sentinel analytics unavailable: ${error}`} onRetry={load} />;
  }
  if (!analytics) return null;

  const health = analyticsHealthView(analytics);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <Badge tone={health.tone} title={health.reason}>
          {health.label}
        </Badge>
        <span className="text-muted-foreground">{health.reason}</span>
      </div>

      {error && <ErrorBox message={`The folded reading could not be refreshed: ${error}`} onRetry={load} />}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatCard label="Passes folded" value={String(analytics.passes)} sub={window ? `of ${window.total_on_disk} on disk` : undefined} />
        <StatCard
          label="Signals scanned"
          value={scannedText(analytics)}
          sub={
            analytics.scanned_total === null && analytics.scanned_reporting_passes > 0
              ? `${analytics.scanned_reporting_passes} of ${analytics.passes} pass(es) reported a count`
              : "0 is a measurement; not reported means nobody measured"
          }
        />
        <StatCard label="Verified repairs" value={String(analytics.fixed_total)} sub="committed after passing the repair gate" />
        <StatCard label="Repairs reverted" value={String(analytics.reverted_total)} sub="failed verification and were rolled back" />
        <StatCard label="Escalations" value={String(analytics.escalated_total)} sub="handed to a strategy or a human" />
        <StatCard label="Fault kinds seen" value={String(analytics.kinds.length)} sub={`${analytics.outcome_count} outcome row(s)`} />
        <StatCard
          label="Mean pass duration"
          value={durationText(analytics.duration_mean_s)}
          sub={`measured on ${analytics.duration_measured_passes} of ${analytics.passes} pass(es)`}
        />
        <StatCard
          label="Repair vs observe passes"
          value={
            analytics.auto_heal_passes === null
              ? "not reported"
              : `${analytics.auto_heal_passes} / ${analytics.observe_passes}`
          }
          sub={analytics.auto_heal_passes === null ? "no pass reported its auto_heal flag" : "repair / observe"}
        />
      </div>

      {/* The window these totals were measured over. Without it a capped total
          reads as the whole journal. */}
      {window && window.total_on_disk > analytics.passes && (
        <p className="text-[11px] text-muted-foreground">
          {analytics.dropped_by_cap} older pass(es) are outside this window of {window.limit}; these totals
          describe the window, not the whole journal.
        </p>
      )}
      {analytics.first_recorded_at && (
        <p className="text-[11px] text-muted-foreground">
          folded window: {analytics.first_recorded_at} to {analytics.last_recorded_at ?? "unknown"}
        </p>
      )}

      <KindTable kinds={analytics.kinds} />
      <RepeatTable repeats={analytics.repeats} />

      {analytics.errors.length > 0 && (
        <div className="space-y-1">
          <div className="text-[11px] font-semibold text-muted-foreground">Errors recorded in the folded window</div>
          <ul className="list-disc pl-4 text-[11px] text-red-600 dark:text-red-400">
            {analytics.errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}

      <ul className="space-y-0.5 text-[10px] text-muted-foreground">
        {analytics.disclosures.map((d) => (
          <li key={d}>{d}</li>
        ))}
      </ul>
    </div>
  );
}

function StatCard(props: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-border/60 bg-card px-3 py-2.5">
      <div className="text-sm font-bold leading-tight break-words">{props.value}</div>
      <div className="text-[10px] text-muted-foreground mt-1">{props.label}</div>
      {props.sub && <div className="text-[10px] text-muted-foreground">{props.sub}</div>}
    </div>
  );
}

/* ══ Fault intelligence ═════════════════════════════════════════════════ */

function KindTable(props: { kinds: SentinelKindReading[] }) {
  if (props.kinds.length === 0) {
    return (
      <EmptyState
        title="No fault kinds in this window"
        hint="The folded passes recorded no per-signal outcomes. That is a measurement of what the engine wrote, not a claim that nothing failed."
      />
    );
  }
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2 text-[11px] font-semibold text-muted-foreground">
        <ListChecks className="size-3.5" /> Fault intelligence ({props.kinds.length} kind(s), worst first)
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[11px] text-left">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 pr-3">Kind</th>
              <th className="py-1 pr-3">Verdict</th>
              <th className="py-1 pr-3">What the engine recorded</th>
              <th className="py-1 pr-3">Passes</th>
              <th className="py-1 pr-3">Distinct faults</th>
              <th className="py-1 pr-3">First seen</th>
              <th className="py-1 pr-3">Last seen</th>
            </tr>
          </thead>
          <tbody>
            {props.kinds.map((kind) => (
              <tr key={kind.kind} className="border-t border-border/60 align-top">
                <td className="py-1.5 pr-3 font-semibold">{kind.kind}</td>
                <td className="py-1.5 pr-3">
                  <Badge tone={verdictTone(kind.verdict)}>{verdictWords(kind.verdict)}</Badge>
                </td>
                <td className="py-1.5 pr-3">
                  <div>{kindEvidenceLine(kind)}</div>
                  <OutcomeBar kind={kind} />
                  {kind.unknown_statuses.length > 0 && (
                    <div className="text-muted-foreground">
                      status words this build does not name: {kind.unknown_statuses.join(", ")}
                    </div>
                  )}
                </td>
                <td className="py-1.5 pr-3 tabular-nums">{kind.passes}</td>
                <td className="py-1.5 pr-3 tabular-nums">{kind.distinct_fingerprints}</td>
                <td className="py-1.5 pr-3">{kind.first_seen ?? "not reported"}</td>
                <td className="py-1.5 pr-3">{kind.last_seen ?? "not reported"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/**
 * The fixed/reverted/escalated split for one kind, as a three-segment bar.
 *
 * The width of each segment is its share of the kind's own occurrences, so a
 * kind with one repair and nine escalations visibly carries the nine — the
 * verdict word alone cannot say that, and the numbers alone are slow to read.
 * A kind with no named status renders no bar rather than an empty one.
 */
function OutcomeBar(props: { kind: SentinelKindReading }) {
  const { kind } = props;
  const named = kind.fixed + kind.reverted + kind.escalated + kind.skipped;
  if (named <= 0) return null;
  const share = (value: number) => `${Math.round((value / named) * 100)}%`;
  return (
    <div className="mt-1 flex h-1.5 w-full max-w-40 overflow-hidden rounded-full bg-muted" aria-hidden="true">
      {kind.fixed > 0 && <div className="bg-emerald-500" style={{ width: share(kind.fixed) }} />}
      {kind.reverted > 0 && <div className="bg-amber-500" style={{ width: share(kind.reverted) }} />}
      {kind.escalated > 0 && <div className="bg-red-500" style={{ width: share(kind.escalated) }} />}
      {kind.skipped > 0 && <div className="bg-muted-foreground/40" style={{ width: share(kind.skipped) }} />}
    </div>
  );
}

function RepeatTable(props: { repeats: SentinelAnalytics["repeats"] }) {
  if (props.repeats.length === 0) return null;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2 text-[11px] font-semibold text-muted-foreground">
        <GitCompareArrows className="size-3.5" /> Faults that came back ({props.repeats.length} fingerprint(s))
      </div>
      <p className="text-[11px] text-muted-foreground">
        A fingerprint recurring is not automatically a failure — a flapping tool spans two passes. It is
        the row that says whether a repair held, so it carries its kind&apos;s verdict beside the count.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-[11px] text-left">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 pr-3">Fingerprint</th>
              <th className="py-1 pr-3">Kind</th>
              <th className="py-1 pr-3">Passes</th>
              <th className="py-1 pr-3">Occurrences</th>
              <th className="py-1 pr-3">Last seen</th>
              <th className="py-1 pr-3">Kind verdict</th>
            </tr>
          </thead>
          <tbody>
            {props.repeats.map((repeat) => (
              <tr key={repeat.fingerprint} className="border-t border-border/60 align-top">
                <td className="py-1.5 pr-3 font-mono">{repeat.fingerprint}</td>
                <td className="py-1.5 pr-3">{repeat.kind}</td>
                <td className="py-1.5 pr-3 tabular-nums">{repeat.passes}</td>
                <td className="py-1.5 pr-3 tabular-nums">{repeat.occurrences}</td>
                <td className="py-1.5 pr-3">{repeat.last_seen ?? "not reported"}</td>
                <td className="py-1.5 pr-3">
                  <Badge tone={verdictTone(repeat.verdict)}>{verdictWords(repeat.verdict)}</Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ══ The declared repair posture ════════════════════════════════════════ */

function KindRegistryPanel(props: { refreshKey: number }) {
  const [kinds, setKinds] = useState<SentinelKinds | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setKinds(await getSentinelKinds());
      setError(null);
    } catch (e) {
      setError(failureText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, props.refreshKey]);

  if (error && !kinds) {
    return <ErrorBox message={`Fault-kind registry unavailable: ${error}`} onRetry={load} />;
  }
  if (!kinds) return <SkeletonList rows={2} />;

  return (
    <div className="space-y-2 border-t border-border/60 pt-3">
      <div className="flex items-center gap-2 text-[11px] font-semibold text-muted-foreground">
        <Wrench className="size-3.5" /> Fault-kind registry ({kinds.kinds.length} declared)
      </div>
      {error && <ErrorBox message={`The registry could not be refreshed: ${error}`} onRetry={load} />}
      <div className="overflow-x-auto">
        <table className="w-full text-[11px] text-left">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 pr-3">Kind</th>
              <th className="py-1 pr-3">Repair strategy</th>
              <th className="py-1 pr-3">What that means</th>
            </tr>
          </thead>
          <tbody>
            {kinds.kinds.map((entry) => (
              <tr key={entry.kind} className="border-t border-border/60 align-top">
                <td className="py-1.5 pr-3 font-semibold">{entry.kind}</td>
                <td className="py-1.5 pr-3">
                  <Badge tone={entry.repair_registered ? "green" : "gray"}>
                    {entry.repair_registered ? "registered" : "none"}
                  </Badge>
                  {!entry.recognised && (
                    <Badge tone="amber" title="This repair target is not in the loop's recognised kinds">
                      unrecognised
                    </Badge>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-muted-foreground">{entry.repair_note ?? "not reported"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {Object.keys(kinds.verification_commands).length > 0 && (
        <p className="text-[11px] text-muted-foreground">
          A repair may only commit after these checks pass:{" "}
          {Object.entries(kinds.verification_commands)
            .map(([name, command]) => `${name} (${command.join(" ")})`)
            .join("; ")}
        </p>
      )}
      <ul className="space-y-0.5 text-[10px] text-muted-foreground">
        {kinds.disclosures.map((d) => (
          <li key={d}>{d}</li>
        ))}
      </ul>
    </div>
  );
}

/* ══ Human handoffs ════════════════════════════════════════════════════ */

function HandoffPanel(props: { refreshKey: number; onNotice: (message: string) => void }) {
  const [rows, setRows] = useState<SentinelEscalations | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ id: string; message: string } | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await getSentinelEscalations());
      setError(null);
    } catch (e) {
      // 503 naming the file: "could not look" must never render as "nothing is
      // waiting on a human".
      setError(failureText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, props.refreshKey]);

  const decide = async (escalation: SentinelEscalation, verb: "acknowledge" | "resolve") => {
    const note = verb === "resolve" ? window.prompt("Resolution note (what did you decide, and why)?") ?? "" : "";
    setPending(`${verb}:${escalation.escalation_id}`);
    setRowError(null);
    try {
      const result =
        verb === "acknowledge"
          ? await acknowledgeSentinelEscalation(escalation.escalation_id, "operator")
          : await resolveSentinelEscalation(escalation.escalation_id, "operator", note);
      props.onNotice(result.note ?? `Recorded the ${verb} decision.`);
      // Re-read the list rather than painting the click: a refused write leaves
      // the row exactly where it was.
      await load();
    } catch (e) {
      // A refusal is the answer, kept in the server's own words.
      setRowError({ id: escalation.escalation_id, message: failureText(e) });
    } finally {
      setPending(null);
    }
  };

  if (error && !rows) {
    return <ErrorBox message={`Sentinel handoffs unavailable: ${error}`} onRetry={load} />;
  }
  if (!rows) return <SkeletonList rows={2} />;

  return (
    <div className="space-y-2 border-t border-border/60 pt-3">
      <div className="flex items-center gap-2 text-[11px] font-semibold text-muted-foreground">
        <ShieldAlert className="size-3.5" /> Human handoffs ({rows.total} on record)
        {rows.truncated && <Badge tone="amber">showing newest {rows.returned}</Badge>}
      </div>
      {error && <ErrorBox message={`The handoff queue could not be refreshed: ${error}`} onRetry={load} />}

      {rows.escalations.length === 0 ? (
        <EmptyState
          title="Nothing is waiting on a human"
          hint="The store was read and held no Sentinel escalation — a measurement, not a success claim."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] text-left">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 pr-3">Opened</th>
                <th className="py-1 pr-3">Fault</th>
                <th className="py-1 pr-3">Attempts</th>
                <th className="py-1 pr-3">Status</th>
                <th className="py-1 pr-3">Decided by</th>
                <th className="py-1 pr-3">Decision</th>
              </tr>
            </thead>
            <tbody>
              {rows.escalations.map((row) => {
                const acknowledgedBy = row.acknowledged_by;
                const decidedBy = row.resolved_by ?? acknowledgedBy;
                return (
                  <tr key={row.escalation_id} className="border-t border-border/60 align-top">
                    <td className="py-1.5 pr-3">{row.created_at_iso ?? "not reported"}</td>
                    <td className="py-1.5 pr-3">
                      <div className="font-mono text-[10px] text-muted-foreground">{row.task_id ?? "no fingerprint"}</div>
                      <div>{row.detail ?? "no detail recorded"}</div>
                      <div className="text-muted-foreground">
                        reason: {row.reason ?? "not reported"}
                        {row.reason_class ? ` (${row.reason_class})` : ""}
                      </div>
                    </td>
                    <td className="py-1.5 pr-3 tabular-nums">
                      {row.attempt ?? "not reported"}
                      {row.max_attempts !== null ? ` / ${row.max_attempts}` : ""}
                    </td>
                    <td className="py-1.5 pr-3">
                      <Badge tone={row.status === "open" ? "amber" : row.status === "resolved" ? "green" : "gray"}>
                        {row.status ?? "status not reported"}
                      </Badge>
                    </td>
                    <td className="py-1.5 pr-3">{decidedBy ?? "nobody yet"}</td>
                    <td className="py-1.5 pr-3">
                      {row.status === "open" ? (
                        <div className="flex flex-wrap gap-1.5">
                          <Btn
                            disabled={pending !== null}
                            onClick={() => void decide(row, "acknowledge")}
                            title="Marks this handoff as seen. It resolves nothing and repairs nothing."
                          >
                            Acknowledge
                          </Btn>
                          <Btn
                            variant="ghost"
                            disabled={pending !== null}
                            onClick={() => void decide(row, "resolve")}
                            title="Records the human decision with a note. It changes no engine state."
                          >
                            Resolve
                          </Btn>
                        </div>
                      ) : (
                        <span className="text-muted-foreground">
                          {row.resolution ? row.resolution : "no note recorded"}
                        </span>
                      )}
                      {pending === `acknowledge:${row.escalation_id}` && <span className="ml-1 text-muted-foreground">recording…</span>}
                      {pending === `resolve:${row.escalation_id}` && <span className="ml-1 text-muted-foreground">recording…</span>}
                      {rowError?.id === row.escalation_id && (
                        <div className="mt-1 text-red-600 dark:text-red-400">{rowError.message}</div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-[11px] text-muted-foreground">
        An escalation is a handoff, not a fault count: one record covers one (fingerprint, reason) while it
        stays open. Recording a decision is admin-only and changes no engine state.
      </p>
      <ul className="space-y-0.5 text-[10px] text-muted-foreground">
        {rows.disclosures.map((d) => (
          <li key={d}>{d}</li>
        ))}
      </ul>
    </div>
  );
}

/* ══ Pass controls ═════════════════════════════════════════════════════ */

function PassControls(props: { onNotice: (message: string) => void; onRan: () => void }) {
  const [autoHeal, setAutoHeal] = useState(false);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [report, setReport] = useState<SentinelRunReport | null>(null);

  const runPass = async () => {
    setRunning(true);
    setRunError(null);
    try {
      // auto_heal is ALWAYS sent explicitly; omission is never a way to
      // request a repair pass.
      const result = await runSentinelPass(autoHeal);
      setReport(result);
      props.onNotice(`Sentinel pass finished: ${result.summary || "no summary returned"}`);
      props.onRan();
    } catch (e) {
      setRunError(failureText(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="space-y-2 border-t border-border/60 pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="text-sm font-semibold">Run one pass</div>
        <label className="flex items-center gap-1.5 text-[11px]">
          <input
            type="checkbox"
            checked={autoHeal}
            onChange={(e) => setAutoHeal(e.target.checked)}
            className="size-3.5"
          />
          <span>
            repair <span className="text-muted-foreground">(off by default — observe-only)</span>
          </span>
        </label>
        <Btn
          disabled={running}
          onClick={runPass}
          title="POST /api/autonomy/sentinel/run — runs on a worker thread and is journaled at the same choke point the supervisor loop uses"
        >
          <Activity className="size-3.5" /> {running ? "Running…" : autoHeal ? "Run repair pass" : "Run observe pass"}
        </Btn>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Safety model: a repair pass checkpoints the files it is about to touch, applies a bounded set of
        verified fixes, re-runs the checks and reverts anything that comes back red before it may commit.
        An unknown signal kind is escalated, never guessed at. An observe pass registers no repair
        strategy at all, so every signal it meets is escalated.
      </p>
      {runError && <ErrorBox message={`Sentinel pass failed: ${runError}`} onRetry={() => setRunError(null)} />}
      {report && (
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          <Badge tone={report.fixed > 0 ? "green" : report.escalated > 0 ? "amber" : "gray"}>
            {report.summary || "no summary returned"}
          </Badge>
          <span className="text-muted-foreground">
            scanned {report.scanned} · fixed {report.fixed} · reverted {report.reverted} · escalated{" "}
            {report.escalated}
            {report.errors.length ? ` · ${report.errors.length} error(s)` : ""}
          </span>
          <span className="text-muted-foreground">{durationText(report.duration_s)}</span>
        </div>
      )}
    </div>
  );
}

/* ══ Pass history ══════════════════════════════════════════════════════ */

function HistoryPanel(props: { refreshKey: number }) {
  const [reports, setReports] = useState<SentinelReports | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setReports(await getSentinelReports());
      setError(null);
    } catch (e) {
      setError(failureText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, props.refreshKey]);

  if (error && !reports) {
    return (
      <div className="border-t border-border/60 pt-3">
        <ErrorBox message={`Sentinel report journal unreadable: ${error}`} onRetry={load} />
      </div>
    );
  }
  if (!reports) return <SkeletonList rows={2} />;

  return (
    <div className="space-y-2 border-t border-border/60 pt-3">
      <div className="flex items-center gap-2 text-[11px] font-semibold text-muted-foreground">
        <ScrollText className="size-3.5" /> Pass history (durable journal)
      </div>
      {error && <ErrorBox message={`The journal could not be refreshed: ${error}`} onRetry={load} />}
      {reports.reports.length === 0 ? (
        <EmptyState
          title="No passes recorded yet"
          hint="The journal is empty. Run a pass above, or enable the sentinel loop in autonomy config — whatever the engine really records will appear here verbatim."
        />
      ) : (
        <>
          <p className="text-[11px] text-muted-foreground">
            {reports.total} pass(es) on disk
            {reports.cap !== null ? ` · showing newest ${reports.reports.length}` : ""} · {reports.source}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] text-left">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-1 pr-3">Recorded</th>
                  <th className="py-1 pr-3">Trigger</th>
                  <th className="py-1 pr-3">Scanned</th>
                  <th className="py-1 pr-3">Fixed</th>
                  <th className="py-1 pr-3">Reverted</th>
                  <th className="py-1 pr-3">Escalated</th>
                  <th className="py-1 pr-3">Duration</th>
                  <th className="py-1 pr-3">Errors</th>
                </tr>
              </thead>
              <tbody>
                {[...reports.reports].reverse().map((entry) => (
                  <React.Fragment key={entry.recorded_at}>
                    <tr className="border-t border-border/60 align-top">
                      <td className="py-1.5 pr-3">{entry.recorded_at}</td>
                      <td className="py-1.5 pr-3">
                        {entry.trigger ?? "not reported"}
                        {entry.auto_heal !== null && (
                          <Badge tone={entry.auto_heal ? "amber" : "gray"}>{entry.auto_heal ? "repair" : "observe"}</Badge>
                        )}
                      </td>
                      <td className="py-1.5 pr-3 tabular-nums">{entry.report.scanned}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{entry.report.fixed}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{entry.report.reverted}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{entry.report.escalated}</td>
                      <td className="py-1.5 pr-3">{durationText(entry.report.duration_s)}</td>
                      <td className="py-1.5 pr-3">
                        {entry.report.errors.length === 0 ? (
                          <span className="text-muted-foreground">none</span>
                        ) : (
                          <ul className="list-disc pl-4 text-red-600 dark:text-red-400">
                            {entry.report.errors.map((e, i) => (
                              <li key={i}>{e}</li>
                            ))}
                          </ul>
                        )}
                      </td>
                    </tr>
                    <tr className="border-t border-border/30">
                      <td colSpan={8} className="py-1.5 pr-3">
                        <p className="text-[11px]">
                          {entry.report.summary || "The pass recorded no summary."}
                        </p>
                        {entry.report.outcomes.length === 0 && (
                          <p className="text-[10px] text-muted-foreground">
                            No per-outcome detail recorded — the counts above are the whole record.
                          </p>
                        )}
                      </td>
                    </tr>
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="space-y-0.5 text-[10px] text-muted-foreground">
            {reports.disclosures.map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/* ══ Observe-only signals ══════════════════════════════════════════════ */

function SignalsPanel() {
  const [signals, setSignals] = useState<SentinelSignals | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const observe = async () => {
    setLoading(true);
    try {
      setSignals(await getSentinelSignals());
      setError(null);
    } catch (e) {
      setError(failureText(e));
    } finally {
      setLoading(false);
    }
  };

  const severityTone = (severity: string) => (severity === "critical" ? "red" : severity === "high" ? "amber" : "gray");

  return (
    <div className="space-y-2 border-t border-border/60 pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="text-sm font-semibold">Live signals</div>
        <Btn variant="ghost" disabled={loading} onClick={observe}>
          <Radar className="size-3.5" /> {loading ? "Observing…" : "Observe now"}
        </Btn>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Collects what the configured sources can see right now. Nothing is diagnosed, fixed or committed.
      </p>
      {error && <ErrorBox message={`Signal collection failed: ${error}`} onRetry={observe} />}
      {signals && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-2 text-[11px] font-semibold text-muted-foreground">
            Observed signals ({signals.count})
            <Badge tone={signals.observe_only && !signals.fixes_applied ? "blue" : "amber"}>
              {signals.observe_only && !signals.fixes_applied ? "observe-only" : "check the report"}
            </Badge>
          </div>
          {signals.signals.length === 0 ? (
            <EmptyState
              title="No signals observed"
              hint="The configured sources produced nothing on this pass — that is a measurement, not a success claim."
            />
          ) : (
            <ul className="space-y-1 text-[11px]">
              {signals.signals.map((s) => (
                <li key={s.fingerprint} className="rounded-lg border border-border/60 px-2 py-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={severityTone(s.severity)}>{s.severity}</Badge>
                    <span className="font-semibold">{s.kind}</span>
                    <span className="text-muted-foreground">{s.source}</span>
                    <span className="text-muted-foreground">{s.detected_at}</span>
                  </div>
                  <div>{s.message}</div>
                  <div className="text-muted-foreground">fingerprint {s.fingerprint}</div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
