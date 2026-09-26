"use client";

// SupervisorSection — the AutonomySupervisor + Sentinel repair loop, finally visible.
//
// Every number on this page comes from the real gateway endpoints in
// backend/app/gateway/routers/autonomy.py:
//   GET  /api/autonomy/status           — live per-loop counters + request-time config flags
//   GET  /api/autonomy/sentinel/reports — durable report journal (fail-closed reads)
//   POST /api/autonomy/sentinel/run     — one real pass; auto_heal defaults to OFF here
//   GET  /api/autonomy/sentinel/signals — observe-only collection (nothing is fixed)
//
// Honesty rules enforced on this page:
//   * A loop that has never run shows "never run" (never epoch 0 or a fake green).
//   * A disabled loop says "disabled — enable in autonomy config"; the plan's
//     default is disabled, so the common case is honest, not a fake "healthy".
//   * An unavailable config block renders the server's real error, never "off".
//   * A corrupt/unreadable report journal shows the gateway's 500 detail
//     (file + line) instead of an empty history.
//   * The repair pass is opt-in per click and labelled with its safety model
//     (checkpoint → verify → revert-on-red, bounded fixes, unknown kinds escalate).
import React, { useCallback, useEffect, useState } from "react";
import {
  getSupervisorStatus,
  getSentinelReports,
  runSentinelPass,
  getSentinelSignals,
} from "@/lib/supervisor";
import type {
  LoopStatus,
  SentinelReportEntry,
  SentinelRunReport,
  SentinelSignals,
  SupervisorStatus,
} from "@/lib/supervisor";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { RefreshCw, Play, ShieldAlert, Radar, ScrollText } from "lucide-react";

type Tone = "green" | "amber" | "gray" | "blue" | "red" | "purple" | "cyan" | "indigo";

/** Tone per observed condition. Unknown values render neutral gray — never green. */
function loopTone(loop: LoopStatus): Tone {
  if (loop.parked) return "red";
  if (loop.failures > 0) return "amber";
  if (loop.running) return "blue";
  if (!loop.enabled) return "gray";
  return "green";
}

function fmtEpoch(sec: number | null): string {
  if (sec === null) return "never";
  return new Date(sec * 1000).toLocaleString();
}

function fmtDuration(sec: number | null): string {
  if (sec === null) return "—";
  if (sec < 1) return `${Math.round(sec * 1000)} ms`;
  return `${sec.toFixed(2)} s`;
}

export function SupervisorSection() {
  const [refreshKey, setRefreshKey] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);

  const flash = useCallback((m: string) => {
    setNotice(m);
    window.setTimeout(() => setNotice(null), 8000);
  }, []);

  return (
    <Section
      title="Supervisor"
      hint="Live autonomy loop counters and the Sentinel repair loop — every value below is measured by the gateway, never simulated."
      actions={
        <Btn variant="ghost" onClick={() => setRefreshKey((k) => k + 1)}>
          <RefreshCw className="size-3.5" /> Refresh
        </Btn>
      }
    >
      {notice && <Notice message={notice} />}
      <StatusPanel refreshKey={refreshKey} />
      <SentinelPanel refreshKey={refreshKey} onNotice={flash} onRan={() => setRefreshKey((k) => k + 1)} />
    </Section>
  );
}

/* ══ Supervisor status ═════════════════════════════════════════════ */

function StatusPanel(props: { refreshKey: number }) {
  const [status, setStatus] = useState<SupervisorStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await getSupervisorStatus());
      setError(null);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, props.refreshKey]);

  if (loading && !status) return <SkeletonList rows={4} />;
  if (error && !status) {
    return <ErrorBox message={`Autonomy status unavailable: ${error}`} onRetry={load} />;
  }
  if (!status) return null;

  const { config } = status;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <Badge tone={status.supervisor_enabled ? "green" : "gray"}>
          supervisor {status.supervisor_enabled ? "enabled" : "disabled"}
        </Badge>
        <Badge tone={config.available ? (config.autonomy_enabled ? "blue" : "gray") : "amber"}>
          {config.available
            ? `config autonomy ${config.autonomy_enabled ? "on" : "off"}`
            : "config unavailable"}
        </Badge>
        {config.available && (
          <Badge tone={config.bus_enabled ? "blue" : "gray"}>
            event bus {config.bus_enabled ? "on" : "off"}
          </Badge>
        )}
        <span className="text-muted-foreground">
          started {fmtEpoch(status.started_at)}
        </span>
      </div>

      {!config.available && (
        <Notice message={`Request-time config could not be read — ${config.error ?? "no reason given"}`} />
      )}
      {status.notes.map((n) => (
        <p key={n} className="text-[11px] text-muted-foreground">
          {n}
        </p>
      ))}

      {status.loops.length === 0 ? (
        <EmptyState title="No loops registered" hint="The supervisor singleton registered no loops in this process." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] text-left">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 pr-3">Loop</th>
                <th className="py-1 pr-3">State</th>
                <th className="py-1 pr-3">Runs</th>
                <th className="py-1 pr-3">Failures</th>
                <th className="py-1 pr-3">Last run</th>
                <th className="py-1 pr-3">Duration</th>
                <th className="py-1 pr-3">Last result</th>
              </tr>
            </thead>
            <tbody>
              {status.loops.map((loop) => (
                <tr key={loop.loop_id} className="border-t border-border/60 align-top">
                  <td className="py-1.5 pr-3">
                    <div className="font-semibold">{loop.loop_id}</div>
                    <div className="text-muted-foreground">{loop.description}</div>
                    {loop.config_enabled !== null && (
                      <div className="text-muted-foreground">
                        config: {loop.config_enabled ? "enabled" : "disabled"} · interval{" "}
                        {fmtDuration(loop.interval_seconds)}
                        {loop.jitter_seconds !== null ? ` + jitter ${loop.jitter_seconds}s` : ""}
                      </div>
                    )}
                  </td>
                  <td className="py-1.5 pr-3">
                    <div className="flex flex-col gap-1">
                      <Badge tone={loopTone(loop)}>
                        {!loop.enabled
                          ? "disabled"
                          : loop.parked
                            ? "parked"
                            : loop.running
                              ? "running"
                              : "enabled"}
                      </Badge>
                      {!loop.enabled && (
                        <span className="text-muted-foreground">disabled — enable in autonomy config</span>
                      )}
                      {loop.enabled && !loop.task_alive && !loop.parked && (
                        <span className="text-muted-foreground">no supervisor task alive</span>
                      )}
                      {loop.parked && loop.park_reason && (
                        <span className="text-muted-foreground">parked: {loop.park_reason}</span>
                      )}
                    </div>
                  </td>
                  <td className="py-1.5 pr-3 tabular-nums">{loop.runs}</td>
                  <td className="py-1.5 pr-3 tabular-nums">{loop.failures}</td>
                  <td className="py-1.5 pr-3">{fmtEpoch(loop.last_run_at)}</td>
                  <td className="py-1.5 pr-3">{fmtDuration(loop.last_duration_seconds)}</td>
                  <td className="py-1.5 pr-3">
                    {loop.last_error ? (
                      <span className="text-red-600 dark:text-red-400">{loop.last_error}</span>
                    ) : loop.last_summary ? (
                      <span>{loop.last_summary}</span>
                    ) : (
                      <span className="text-muted-foreground">no passes recorded yet</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ══ Sentinel panel ════════════════════════════════════════════════ */

function SentinelPanel(props: { refreshKey: number; onNotice: (m: string) => void; onRan: () => void }) {
  const [reports, setReports] = useState<{
    reports: SentinelReportEntry[];
    total: number;
    cap: number | null;
    source: string;
    disclosures: string[];
  } | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [autoHeal, setAutoHeal] = useState(false);
  const [signals, setSignals] = useState<SentinelSignals | null>(null);
  const [signalsError, setSignalsError] = useState<string | null>(null);
  const [loadingSignals, setLoadingSignals] = useState(false);

  const loadHistory = useCallback(async () => {
    try {
      setReports(await getSentinelReports());
      setHistoryError(null);
    } catch (e) {
      // Fail closed: the gateway answers 500 with the journal path + line for a
      // corrupt store. Show that reason — never an empty "no passes" view.
      setHistoryError(errMsg(e));
    }
  }, []);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory, props.refreshKey]);

  const runPass = async () => {
    setRunning(true);
    setRunError(null);
    try {
      const report: SentinelRunReport = await runSentinelPass(autoHeal);
      props.onNotice(
        `Sentinel pass finished: ${report.summary || "no summary returned"}` +
          (report.errors.length ? ` — ${report.errors.length} error(s)` : ""),
      );
      props.onRan();
      await loadHistory();
    } catch (e) {
      setRunError(errMsg(e));
    } finally {
      setRunning(false);
    }
  };

  const observeSignals = async () => {
    setLoadingSignals(true);
    try {
      setSignals(await getSentinelSignals());
      setSignalsError(null);
    } catch (e) {
      setSignalsError(errMsg(e));
    } finally {
      setLoadingSignals(false);
    }
  };

  return (
    <div className="space-y-3 border-t border-border/60 pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="text-sm font-semibold">Sentinel</div>
        <label className="flex items-center gap-1.5 text-[11px]">
          <input
            type="checkbox"
            checked={autoHeal}
            onChange={(e) => setAutoHeal(e.target.checked)}
            className="size-3.5"
          />
          <span>
            auto-heal <span className="text-muted-foreground">(off by default — observe-only)</span>
          </span>
        </label>
        <Btn disabled={running} onClick={runPass} title="POST /api/autonomy/sentinel/run (asyncio.to_thread; the pass is journaled at the same choke point the supervisor uses)">
          <Play className="size-3.5" /> {running ? "Running…" : autoHeal ? "Run repair pass" : "Run observe pass"}
        </Btn>
        <Btn variant="ghost" disabled={loadingSignals} onClick={observeSignals}>
          <Radar className="size-3.5" /> Observe signals
        </Btn>
      </div>

      <p className="text-[11px] text-muted-foreground">
        Safety model: a repair pass checkpoints, applies a bounded set of verified fixes (5 per run),
        re-runs verification and reverts anything that comes back red; an unknown signal kind is
        escalated, never guessed at.
      </p>

      {runError && <ErrorBox message={`Sentinel pass failed: ${runError}`} onRetry={() => setRunError(null)} />}

      {/* Report history */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-2 text-[11px] font-semibold text-muted-foreground">
          <ScrollText className="size-3.5" /> Pass history (durable journal)
        </div>
        {historyError && (
          <ErrorBox message={`Sentinel report journal unreadable: ${historyError}`} onRetry={loadHistory} />
        )}
        {!historyError && !reports && <SkeletonList rows={2} />}
        {!historyError && reports && reports.reports.length === 0 && (
          <EmptyState
            title="No passes recorded yet"
            hint="The journal is empty. Run an observe pass above, or enable the sentinel loop in autonomy config — whatever the engine really records will appear here verbatim."
          />
        )}
        {!historyError && reports && reports.reports.length > 0 && (
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
                    <tr key={entry.recorded_at} className="border-t border-border/60 align-top">
                      <td className="py-1.5 pr-3">{entry.recorded_at}</td>
                      <td className="py-1.5 pr-3">
                        {entry.trigger ?? "—"}
                        {entry.auto_heal !== null && (
                          <Badge tone={entry.auto_heal ? "amber" : "gray"}>
                            {entry.auto_heal ? "repair" : "observe"}
                          </Badge>
                        )}
                      </td>
                      <td className="py-1.5 pr-3 tabular-nums">{entry.report.scanned}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{entry.report.fixed}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{entry.report.reverted}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{entry.report.escalated}</td>
                      <td className="py-1.5 pr-3">{fmtDuration(entry.report.duration_s)}</td>
                      <td className="py-1.5 pr-3">
                        {entry.report.errors.length === 0 ? (
                          <span className="text-muted-foreground">none</span>
                        ) : (
                          <ul className="list-disc pl-4">
                            {entry.report.errors.map((e, i) => (
                              <li key={i} className="text-red-600 dark:text-red-400">
                                {e}
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                    </tr>
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

      {/* Observe-only signals */}
      {signalsError && <ErrorBox message={`Signal collection failed: ${signalsError}`} onRetry={observeSignals} />}
      {signals && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-2 text-[11px] font-semibold text-muted-foreground">
            <ShieldAlert className="size-3.5" /> Observed signals ({signals.count})
            <Badge tone={signals.observe_only && !signals.fixes_applied ? "blue" : "amber"}>
              {signals.observe_only && !signals.fixes_applied ? "observe-only" : "check the report"}
            </Badge>
          </div>
          <p className="text-[11px] text-muted-foreground">{signals.note}</p>
          {signals.signals.length === 0 ? (
            <EmptyState title="No signals observed" hint="The configured sources produced nothing on this pass — that is a measurement, not a success claim." />
          ) : (
            <ul className="space-y-1 text-[11px]">
              {signals.signals.map((s) => (
                <li key={s.fingerprint} className="rounded-lg border border-border/60 px-2 py-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={s.severity === "critical" ? "red" : s.severity === "high" ? "amber" : "gray"}>
                      {s.severity}
                    </Badge>
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
