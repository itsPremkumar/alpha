"use client";

import React, { useCallback, useEffect, useState } from "react";
import { RunInfo, RunUsage, fetchRunUsage, formatCost, formatTokenCount } from "@/lib/runs";
import { Badge, Btn, ErrorBox, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { Coins, Cpu } from "lucide-react";

/** Rows of the per-call table rendered before it is capped. */
const MAX_CALL_ROWS = 200;

function costLabel(usage: RunUsage, cost: number | null): string {
  const formatted = formatCost(cost, usage.currency);
  if (formatted !== null) return formatted;
  if (!usage.pricing_configured) return "not priced — no models[*].pricing configured";
  return "no price configured for this run's models";
}

/**
 * Real token and cost data for one run.
 *
 * Run totals come from the run's own record (also carried on the list row);
 * the per-model split and the per-call rows come from
 * `GET /threads/{id}/runs/{id}/usage`. An absent number is rendered as unknown
 * and an unpriced model says why, rather than showing a zero.
 */
export function RunUsagePanel(props: { threadId: string; runId: string; run: RunInfo }) {
  const { threadId, runId, run } = props;
  const [usage, setUsage] = useState<RunUsage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setUsage(await fetchRunUsage(threadId, runId));
    } catch (e) {
      setUsage(null);
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }, [threadId, runId]);

  useEffect(() => {
    void load();
  }, [load]);

  const totalTokens = usage ? usage.total_tokens : run.total_tokens;
  const totalCost = usage ? usage.total_cost : null;
  const callCount = usage ? usage.llm_call_count : run.llm_call_count;
  const costSource = usage ? costLabel(usage, totalCost) : "unavailable until the run's usage is read";

  return (
    <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-3">
      <div className="flex items-start justify-between gap-2 flex-wrap">
        <div>
          <h4 className="text-sm font-semibold flex items-center gap-1.5">
            <Cpu className="size-4 text-primary" /> Tokens &amp; cost
          </h4>
          <p className="text-[11px] text-muted-foreground mt-0.5">
            {error
              ? "This run's measured usage could not be read — the tiles above fall back to the run record, and cost stays unknown."
              : loading || !usage
                ? "Reading this run's measured usage…"
                : "Measured by the Gateway for this run."}
          </p>
        </div>
        <Btn variant="ghost" onClick={() => void load()} disabled={loading}>
          Reload
        </Btn>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <div className="rounded-xl bg-muted/40 p-2.5 text-center">
          <div className="text-sm font-bold">{formatTokenCount(totalTokens)}</div>
          <div className="text-[10px] text-muted-foreground">total tokens</div>
        </div>
        <div className="rounded-xl bg-muted/40 p-2.5 text-center">
          <div className="text-sm font-bold">
            {formatTokenCount(usage ? usage.total_input_tokens : run.total_input_tokens)}
            <span className="text-[10px] font-normal text-muted-foreground"> in</span>
          </div>
          <div className="text-[10px] text-muted-foreground">
            {formatTokenCount(usage ? usage.total_output_tokens : run.total_output_tokens)} out
          </div>
        </div>
        <div className="rounded-xl bg-muted/40 p-2.5 text-center">
          <div className="text-sm font-bold">{formatTokenCount(callCount)}</div>
          <div className="text-[10px] text-muted-foreground">LLM calls</div>
        </div>
        <div className="rounded-xl bg-muted/40 p-2.5 text-center">
          <div className="text-sm font-bold flex items-center justify-center gap-1">
            <Coins className="size-3.5 text-primary" /> {formatCost(totalCost, usage?.currency ?? null) ?? "—"}
          </div>
          <div className="text-[10px] text-muted-foreground">{usage?.pricing_configured ? "estimated cost" : "cost unavailable"}</div>
        </div>
      </div>

      <p className="text-[11px] text-muted-foreground">
        {error ? `Cost/tokens unreadable: ${error}.` : costSource}
        {usage && usage.llm_call_count !== null && (
          <>
            {" · "}
            {formatTokenCount(usage.lead_agent_tokens)} lead · {formatTokenCount(usage.subagent_tokens)} subagent ·{" "}
            {formatTokenCount(usage.middleware_tokens)} middleware
          </>
        )}
      </p>

      {error ? (
        <ErrorBox message={`Couldn't read this run's usage — token counts and cost are unavailable, not zero. (${error})`} onRetry={() => void load()} />
      ) : loading || !usage ? (
        <SkeletonList rows={2} />
      ) : (
        <>
          <div className="space-y-1">
            <p className="text-[11px] font-semibold">
              Per model
              {usage.by_model_source === "run_totals" && (
                <span className="font-normal text-muted-foreground"> — no per-model split was reported; this is the run total priced at its model</span>
              )}
              {usage.by_model_source === "unavailable" && <span className="font-normal text-muted-foreground"> — this run reported no token usage</span>}
            </p>
            {usage.by_model.map((row) => (
              <div key={row.model ?? "unknown"} className="rounded-lg bg-muted/30 px-2 py-1.5 text-[11px] flex items-center gap-2 flex-wrap">
                <span className="font-mono font-semibold">{row.model ?? "unknown"}</span>
                <span className="text-muted-foreground font-mono">
                  {formatTokenCount(row.input_tokens)} in / {formatTokenCount(row.output_tokens)} out
                </span>
                {row.cache_read_tokens !== null && <span className="text-muted-foreground">· {formatTokenCount(row.cache_read_tokens)} cache hit</span>}
                <span className="ml-auto font-mono">{formatCost(row.cost, usage.currency) ?? "—"}</span>
              </div>
            ))}
          </div>

          <div className="space-y-1">
            <p className="text-[11px] font-semibold flex items-center gap-2 flex-wrap">
              Per LLM call
              <span className="font-normal text-muted-foreground">
                one row per model call; a delegated subagent reports its cumulative usage for the whole execution
              </span>
              {!usage.calls_complete && (
                <Badge tone="amber">
                  partial — the server-side walk stopped before the end of this run's usage events
                </Badge>
              )}
            </p>
            {usage.calls.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">
                No per-call usage events are persisted for this run (older runs only kept run-level totals).
              </p>
            ) : (
              <div className="space-y-1 max-h-72 overflow-y-auto">
                {usage.calls.slice(0, MAX_CALL_ROWS).map((row, i) => (
                  <div key={`${row.seq ?? "?"}-${row.source}-${i}`} className="rounded-lg bg-muted/30 px-2 py-1.5 text-[11px] flex items-center gap-2 flex-wrap">
                    <span className="font-mono text-[10px] text-muted-foreground w-8 shrink-0">{row.seq ?? "—"}</span>
                    <span className="font-mono font-semibold">{row.model ?? "model unknown"}</span>
                    <Badge tone={row.source === "subagent" ? "purple" : "blue"}>{row.caller ?? (row.source === "subagent" ? "subagent" : "llm")}</Badge>
                    {row.status && <Badge tone={row.status === "completed" ? "green" : "red"}>{row.status}</Badge>}
                    {row.call_index !== null && <span className="text-muted-foreground font-mono">call {row.call_index}</span>}
                    {row.latency_ms !== null && <span className="text-muted-foreground font-mono">{Math.round(row.latency_ms / 100) / 10}s</span>}
                    <span className="text-muted-foreground font-mono ml-auto">
                      {formatTokenCount(row.input_tokens)} in / {formatTokenCount(row.output_tokens)} out ·{" "}
                      {formatCost(row.cost, usage.currency) ?? "—"}
                    </span>
                  </div>
                ))}
                {usage.calls.length > MAX_CALL_ROWS && (
                  <p className="text-[10px] text-muted-foreground">
                    Showing the first {MAX_CALL_ROWS} of {usage.calls.length} usage rows.
                  </p>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
