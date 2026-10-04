"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Section, EmptyState, ErrorBox, Notice, Badge, SkeletonList, StatCard } from "@/components/ui";
import { fetchReliability, matrixHeadline, verdictTone, type ReliabilityMatrix } from "@/lib/reliability";
import { errMsg } from "@/lib/http";
import { RefreshCw, ShieldCheck, AlertTriangle } from "lucide-react";

/**
 * Live progress for the real-work validation matrix.
 *
 * The panel polls because the question it answers is a *changing* fact: a wave
 * of workloads is running somewhere else and the operator is watching it here.
 * A ten-second cadence matches the workspace vitals strip; the read is cheap and
 * bounded (a ledger, not a run log).
 *
 * Every state here has to stay distinguishable from a healthy one, because the
 * three failures this surface can hit all look like success if collapsed:
 *
 *   - the Gateway could not read the ledger → a notice with the server's own
 *     reason, never an empty table that reads as "nothing to report";
 *   - the ledger is readable but holds nothing → an empty state that says
 *     nothing has been validated yet;
 *   - a workload passed → green, and only then.
 */

/** Refresh cadence in ms. The matrix changes on a wave, not per frame. */
const POLL_MS = 10_000;

/** An absent measurement renders as this, never as `0`. */
const NOT_REPORTED = "—";

function when(iso: string | null): string {
  if (!iso) return NOT_REPORTED;
  const parsed = Date.parse(iso);
  // `new Date("")` is the epoch, which would claim the check happened in 1970.
  if (Number.isNaN(parsed)) return iso;
  return new Date(parsed).toLocaleString();
}

function seconds(value: number | null): string {
  if (value === null) return NOT_REPORTED;
  if (value < 60) return `${Math.round(value)}s`;
  const minutes = Math.floor(value / 60);
  return `${minutes}m ${Math.round(value % 60)}s`;
}

function WorkloadRow({ workload }: { workload: ReliabilityMatrix["workloads"][number] }) {
  const tone = verdictTone(workload.verdict);
  return (
    <div className={`rounded-xl border border-border/60 bg-card px-3 py-2.5 ${tone.broken ? "border-l-2 border-l-amber-500/70" : ""}`}>
      <div className="flex items-start gap-2.5 flex-wrap">
        <Badge tone={tone.tone} title={workload.detail ?? undefined}>
          {tone.label}
        </Badge>
        <span className="font-mono text-[11px] text-muted-foreground shrink-0">{workload.key}</span>
        <div className="flex-1 min-w-40">
          <p className="text-xs font-medium leading-snug">{workload.title ?? "Untitled workload"}</p>
          {workload.detail && <p className="text-[11px] text-muted-foreground mt-0.5 break-words">{workload.detail}</p>}
        </div>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-[10px] text-muted-foreground">
        {workload.kind && <span>kind: {workload.kind}</span>}
        <span>checked: {when(workload.checkedAt)}</span>
        <span>took: {seconds(workload.elapsedSeconds)}</span>
        {workload.model && <span>model: {workload.model}</span>}
        {workload.runId && <span>run: {workload.runId.slice(0, 8)}</span>}
      </div>
      {workload.serverError && (
        <p className="text-[11px] text-destructive/90 mt-1.5 break-words">
          <span className="font-medium">Gateway reported:</span> {workload.serverError}
        </p>
      )}
    </div>
  );
}

export function ReliabilitySection() {
  const [matrix, setMatrix] = useState<ReliabilityMatrix | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (initial = false) => {
    // Only the first read blanks the panel: a poll that failed must not wipe
    // the last known matrix and replace it with a spinner, which would read as
    // "state unknown" for a surface whose whole job is showing known state.
    if (initial) setLoading(true);
    try {
      setMatrix(await fetchReliability());
      setError(null);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(true);
    const id = window.setInterval(() => void load(false), POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const headline = matrix ? matrixHeadline(matrix) : null;
  const brokenRows = matrix ? matrix.workloads.filter((row) => verdictTone(row.verdict).broken) : [];

  return (
    <Section
      title="Real-work validation"
      hint="Every row is a real task submitted to this Gateway, judged by whether its side effect actually happened — not by the status code it returned."
      actions={
        <button
          type="button"
          onClick={() => void load(false)}
          className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 px-2.5 py-1 text-[11px] font-medium hover:bg-accent"
        >
          <RefreshCw className="size-3.5" />
          Refresh
        </button>
      }
    >
      <div className="space-y-4">
        {error && (
          <ErrorBox
            message={`The validation matrix could not be read: ${error} This is a failed read, not an empty result.`}
            onRetry={() => void load(true)}
          />
        )}

        {loading && !matrix && <SkeletonList rows={3} />}

        {matrix && (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <StatCard
                label="workloads passed"
                value={headline?.value ?? NOT_REPORTED}
                sub={headline?.tone === "gray" ? "not a passing matrix" : "evidence checks, not status codes"}
              />
              <StatCard
                label="recorded"
                value={matrix.total === null ? "not reported" : String(matrix.total)}
                sub={matrix.truncated ? `showing ${matrix.returned} of ${matrix.total}` : "workloads in the ledger"}
              />
              <StatCard
                label="not passing"
                value={matrix.broken === null ? "not reported" : String(matrix.broken)}
                sub="failed, errored, or unverified"
              />
              <StatCard label="last ledger read" value={matrix.reported ? "reported" : "failed"} sub={matrix.reason} />
            </div>

            {!matrix.reported && <Notice message={matrix.reasonText} />}

            {matrix.reported && headline && <p className="text-xs text-muted-foreground">{headline.detail}</p>}

            {matrix.reported && matrix.workloads.length === 0 && (
              <EmptyState
                title="No workloads have run yet"
                hint="The ledger is readable but empty. Nothing has been validated, so nothing can be reported as passing."
              />
            )}

            {brokenRows.length > 0 && (
              <div className="rounded-xl border border-amber-500/40 bg-amber-500/5 px-3 py-2.5">
                <p className="text-xs font-medium flex items-center gap-1.5">
                  <AlertTriangle className="size-3.5 text-amber-600" />
                  {brokenRows.length} workload{brokenRows.length === 1 ? "" : "s"} did not pass
                </p>
                <ul className="mt-1.5 space-y-1 text-[11px] text-muted-foreground">
                  {brokenRows.map((row) => (
                    <li key={row.key} className="break-words">
                      <span className="font-mono">{row.key}</span> {verdictTone(row.verdict).label}
                      {row.detail ? ` — ${row.detail}` : ""}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {matrix.workloads.length > 0 && (
              <div className="space-y-2">
                {matrix.workloads.map((row) => (
                  <WorkloadRow key={row.key} workload={row} />
                ))}
              </div>
            )}

            {matrix.truncated && (
              <p className="text-[11px] text-muted-foreground">
                Showing {matrix.returned} of {matrix.total} recorded workloads; the rest are not displayed.
              </p>
            )}

            <p className="text-[10px] text-muted-foreground flex items-center gap-1.5 break-all">
              <ShieldCheck className="size-3 shrink-0" />
              Ledger: {matrix.ledgerPath ?? NOT_REPORTED}
            </p>
          </>
        )}
      </div>
    </Section>
  );
}
