"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  ChevronDown,
  Coins,
  Cpu,
  FileOutput,
  Wrench,
} from "lucide-react";
import { RunUsage, fetchRunUsage, formatCost, formatTokenCount } from "@/lib/runs";
import {
  Badge,
  Btn,
  LoadingRows,
  MeasuredNumber,
  UnavailableNotice,
} from "@/components/ui";

/** Rows of the per-call table rendered before it is capped. */
const MAX_CALL_ROWS = 50;

/**
 * The numeric HTTP status when the client carried one, else `null`.
 *
 * `errMsg` deliberately paraphrases a 404 into "it may have been deleted",
 * which is right for an incidental read and wrong here: nothing was deleted —
 * the run simply has no usage row. For a deliberate read the operator needs
 * the status, so it is read straight off the error (the same rule
 * `side-effects.ts` calls `failureText`).
 */
function statusOf(err: unknown): number | null {
  const status = (err as { status?: unknown } | null | undefined)?.status;
  return typeof status === "number" ? status : null;
}

/** The message as the Gateway wrote it, not as `errMsg` paraphrases it. */
function rawMessage(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return "The Gateway gave no reason.";
}

/**
 * "What did this answer cost and touch?"
 *
 * A turn report was only available in the Run inspector, which means the one
 * number an operator wants *beside* the answer — what did that cost me, which
 * models, how many calls, did a subagent spend anything — required knowing the
 * run inspector exists and navigating to it. It is now one disclosure on the
 * message itself.
 *
 * Three rules, all of them about not lying:
 *
 * 1. **Opening this performs no read.** The usage route is only hit when the
 *    operator actually asks, so a transcript of 200 messages does not 200
 *    usage requests on open.
 * 2. **Nothing is derived.** Every number comes from the run's own usage
 *    record. An absent number renders "not reported", never `0` — zero is a
 *    measurement that says "none were spent", which is a different claim from
 *    "nobody counted".
 * 3. **A failed read is named.** It says *which* route failed and carries the
 *    Gateway's reason, because "the usage is unavailable" and "there were no
 *    tokens" lead to opposite conclusions.
 */
export function TurnCostStrip(props: {
  threadId: string | null;
  runId: string | null;
  /** Counts taken straight off the message, so the summary is free and honest. */
  toolCount: number;
  artifactCount: number;
}) {
  const [open, setOpen] = useState(false);
  const [usage, setUsage] = useState<RunUsage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  // A local draft thread has no server row, so there is no run to read. That
  // is a fact about the thread, not a failure — it is disclosed rather than
  // presenting as an error.
  const runnable =
    Boolean(props.threadId && props.runId) &&
    !String(props.threadId).startsWith("local-");

  const load = useCallback(async () => {
    if (!runnable) return;
    setLoading(true);
    setError(null);
    try {
      setUsage(await fetchRunUsage(props.threadId!, props.runId!));
    } catch (e) {
      setUsage(null);
      // The raw error is kept, not its paraphrase: the number of rows it
      // produces is zero in both cases, so only the status tells the two
      // apart, and the status is read off the stored object.
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setLoading(false);
    }
  }, [runnable, props.threadId, props.runId]);

  // Read once, on first open. Cached afterwards: re-reading on every render
  // would be a request per keystroke of the transcript scroll.
  useEffect(() => {
    if (open && !usage && !loading && !error) void load();
  }, [open, usage, loading, error, load]);

  if (!props.runId) return null;

  return (
    <div className="rounded-lg border border-border/60 bg-muted/20 text-xs overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={open ? "Hide this turn's cost and detail" : "Show this turn's cost and detail"}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/60 transition-colors"
      >
        <Coins className="size-3.5 shrink-0 text-primary" aria-hidden="true" />
        <span className="shrink-0 font-semibold text-foreground/90">
          What did this answer cost and touch?
        </span>
        {/* Free, already-known counts. They describe the turn, not the
            Gateway's accounting, and are shown before any read happens. */}
        {props.toolCount > 0 && (
          <span className="shrink-0 inline-flex items-center gap-1 text-[10px] text-muted-foreground">
            <Wrench className="size-3" aria-hidden="true" />
            {props.toolCount} tool{props.toolCount === 1 ? "" : "s"}
          </span>
        )}
        {props.artifactCount > 0 && (
          <span className="shrink-0 inline-flex items-center gap-1 text-[10px] text-muted-foreground">
            <FileOutput className="size-3" aria-hidden="true" />
            {props.artifactCount} artifact{props.artifactCount === 1 ? "" : "s"}
          </span>
        )}
        <ChevronDown
          className={`ml-auto size-3.5 shrink-0 text-muted-foreground transition-transform ${open ? "" : "-rotate-90"}`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="space-y-2 border-t border-border/50 p-2.5">
          {!runnable ? (
            <p className="text-[11px] text-muted-foreground">
              This conversation has not been saved to the Gateway yet, so no run
              record — and therefore no measured usage — exists for it. Send a
              message and the accounting attaches to that run.
            </p>
          ) : loading && !usage ? (
            <LoadingRows what="this run's usage" rows={2} />
          ) : error ? (
            // A missing usage record and an unreadable one are different
            // facts: the first says the run never persisted usage (or
            // predates the per-run ledger), the second says a read failed.
            // Rendering both as an error would claim the first is a fault.
            statusOf(error) === 404 ? (
              <p className="text-[11px] text-muted-foreground">
                No usage record exists for this run. That is not zero tokens —
                this run never persisted one, which is the case for seeded
                runs and for runs that produced no model calls. The rest of the
                turn above is still measured.
              </p>
            ) : (
              <UnavailableNotice what="this run's usage" reason={rawMessage(error)} onRetry={() => void load()} retrying={loading} />
            )
          ) : usage ? (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <div className="rounded-xl bg-muted/40 p-2.5 text-center">
                  <div className="text-sm font-bold">{formatTokenCount(usage.total_tokens)}</div>
                  <div className="text-[10px] text-muted-foreground">total tokens</div>
                </div>
                <div className="rounded-xl bg-muted/40 p-2.5 text-center">
                  <div className="text-sm font-bold">
                    {formatTokenCount(usage.total_input_tokens)}
                    <span className="text-[10px] font-normal text-muted-foreground"> in</span>
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    {formatTokenCount(usage.total_output_tokens)} out
                  </div>
                </div>
                <div className="rounded-xl bg-muted/40 p-2.5 text-center">
                  <div className="text-sm font-bold">{formatTokenCount(usage.llm_call_count)}</div>
                  <div className="text-[10px] text-muted-foreground">LLM calls</div>
                </div>
                <div className="rounded-xl bg-muted/40 p-2.5 text-center">
                  <div className="flex items-center justify-center gap-1 text-sm font-bold">
                    <Cpu className="size-3.5 text-primary" aria-hidden="true" />
                    {formatCost(usage.total_cost, usage.currency) ?? "—"}
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    {usage.pricing_configured ? "estimated cost" : "cost unavailable"}
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-1.5 flex-wrap">
                <MeasuredNumber value={usage.lead_agent_tokens} noun="lead tokens" />
                <MeasuredNumber value={usage.subagent_tokens} noun="subagent tokens" />
                <MeasuredNumber value={usage.middleware_tokens} noun="middleware tokens" />
                {!usage.calls_complete && (
                  <Badge
                    tone="amber"
                    title="The server-side walk over this run's usage events stopped before the end, so the per-call table is a partial view of a real total."
                  >
                    per-call list partial
                  </Badge>
                )}
              </div>

              {usage.by_model_source === "run_totals" && (
                <p className="text-[11px] text-muted-foreground">
                  No per-model split was reported for this run; the row below is
                  the run total priced at its single model.
                </p>
              )}
              {usage.by_model_source === "unavailable" && (
                <p className="text-[11px] text-muted-foreground">
                  This run reported no token usage, so there is no per-model row
                  to show.
                </p>
              )}

              {usage.by_model.length > 0 && (
                <div className="space-y-1">
                  {usage.by_model.map((row) => (
                    <div
                      key={row.model ?? "unknown"}
                      className="rounded-lg bg-muted/30 px-2 py-1.5 text-[11px] flex items-center gap-2 flex-wrap"
                    >
                      <span className="font-mono font-semibold">{row.model ?? "model unknown"}</span>
                      <span className="text-muted-foreground font-mono">
                        {formatTokenCount(row.input_tokens)} in /{" "}
                        {formatTokenCount(row.output_tokens)} out
                      </span>
                      {row.cache_read_tokens !== null && (
                        <span className="text-muted-foreground">
                          · {formatTokenCount(row.cache_read_tokens)} cache hit
                        </span>
                      )}
                      <span className="ml-auto font-mono">
                        {formatCost(row.cost, usage.currency) ?? "—"}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              <div className="space-y-1">
                <p className="text-[11px] font-semibold">Per LLM call</p>
                {usage.calls.length === 0 ? (
                  <p className="text-[11px] text-muted-foreground">
                    No per-call usage events are persisted for this run; older
                    runs kept run-level totals only.
                  </p>
                ) : (
                  <>
                    <div className="space-y-1 max-h-60 overflow-y-auto">
                      {usage.calls.slice(0, MAX_CALL_ROWS).map((row, i) => (
                        <div
                          key={`${row.seq ?? "?"}-${row.source}-${i}`}
                          className="rounded-lg bg-muted/30 px-2 py-1.5 text-[11px] flex items-center gap-2 flex-wrap"
                        >
                          <span className="font-mono text-[10px] text-muted-foreground w-8 shrink-0">
                            {row.seq ?? "—"}
                          </span>
                          <span className="font-mono font-semibold">{row.model ?? "model unknown"}</span>
                          <Badge tone={row.source === "subagent" ? "purple" : "blue"}>
                            {row.caller ?? (row.source === "subagent" ? "subagent" : "llm")}
                          </Badge>
                          {row.status && (
                            <Badge tone={row.status === "completed" ? "green" : "red"}>{row.status}</Badge>
                          )}
                          {row.call_index !== null && (
                            <span className="text-muted-foreground font-mono">call {row.call_index}</span>
                          )}
                          {row.latency_ms !== null && (
                            <span className="text-muted-foreground font-mono">
                              {Math.round(row.latency_ms / 100) / 10}s
                            </span>
                          )}
                          <span className="text-muted-foreground font-mono ml-auto">
                            {formatTokenCount(row.input_tokens)} in /{" "}
                            {formatTokenCount(row.output_tokens)} out
                          </span>
                        </div>
                      ))}
                    </div>
                    {usage.calls.length > MAX_CALL_ROWS && (
                      <p className="text-[10px] text-muted-foreground">
                        Showing the first {MAX_CALL_ROWS} of {usage.calls.length} usage rows.
                      </p>
                    )}
                  </>
                )}
              </div>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
