"use client";

import React, { useEffect, useRef, useState } from "react";
import { listThreadRuns, cancelRun, formatTokenCount, RunInfo } from "@/lib/runs";
import {
  fetchRunMessageCount,
  fetchWorkspaceChangesForRun,
  workspaceChangeCount,
  type RunMessageCount,
  type WorkspaceChanges,
} from "@/lib/runs-inspector";
import { Section, EmptyState, ErrorBox, Badge, Btn, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { absoluteStamp } from "@/lib/time";
import { AlertTriangle, Ban, Check, RefreshCw, FileDiff, MessagesSquare, Cpu } from "lucide-react";
import { RunInspectorSection } from "./RunInspectorSection";
import { RunReplayControls } from "./RunReplayControls";
import { RunUsagePanel } from "./RunUsagePanel";

/**
 * A tone for a run's own status word in the list.
 *
 * A failure is RED, and an unrecognised status is grey. These used to be the
 * same colour: the failure branch returned `undefined`, which the shared
 * `Badge` renders in its muted default — identical to the catch-all `gray` — so
 * a run that had errored and a status from a newer Gateway were
 * indistinguishable in the list an operator scans to find the failure.
 */
export function runListStatusTone(status: string): "green" | "blue" | "gray" | "red" {
  if (status === "success" || status === "completed") return "green";
  if (status === "running" || status === "pending") return "blue";
  if (status === "error" || status === "failed") return "red";
  return "gray";
}

/**
 * One run's row, as a pure render of a record the Gateway already returned.
 *
 * Extracted from the container so the honesty rules below can be asserted
 * against real markup rather than against the source of a component whose data
 * only arrives in an effect.
 *
 *  - **A missing creation time says so.** The row used to render `""` — a
 *    blank where a time belongs, which reads as "no delay" rather than "the
 *    Gateway sent no time".
 *  - **A missing token total says so.** `formatTokenCount(null)` is the `—`
 *    glyph, so the row used to print `— tokens` *and* "token totals not
 *    reported" in the same tile: two answers to one question, one of them a
 *    bare dash wearing a unit.
 *  - **The configured model is named as such.** `run.model` is the Alpha model
 *    name the run resolved to, not the provider that answered; saying only
 *    "model" let an operator read it as the latter.
 *  - **The card is a named control.** It carries an `aria-label` naming the run,
 *    its status and when it started, and answers both Enter and Space.
 */
export function RunListCard(props: {
  run: RunInfo;
  selected: boolean;
  /** The run whose stop request is in flight, so its control can disable. */
  cancelling: boolean;
  onSelect: (run: RunInfo) => void;
  onCancel: (run: RunInfo) => void;
}) {
  const r = props.run;
  return (
    <div
      className={`rounded-xl border bg-card p-3 cursor-pointer transition-colors ${props.selected ? "border-primary ring-1 ring-primary/30" : "border-border/60 hover:border-primary/40"}`}
      onClick={() => props.onSelect(r)}
      role="button"
      tabIndex={0}
      aria-pressed={props.selected}
      aria-label={`Inspect run ${r.run_id}, status ${r.status}, created ${absoluteStamp(r.created_at) ?? "time not reported"}`}
      onKeyDown={(e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        // Space would otherwise scroll the page; this control is a button.
        e.preventDefault();
        props.onSelect(r);
      }}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone={runListStatusTone(r.status)}>{r.status}</Badge>
        <span className="text-[11px] font-mono text-muted-foreground" title={r.run_id}>
          {r.run_id.slice(0, 12)}…
        </span>
        <span
          className="text-[11px] text-muted-foreground ml-auto"
          title={absoluteStamp(r.created_at) ?? "the Gateway reported no creation time for this run"}
        >
          {absoluteStamp(r.created_at) ?? "created time not reported"}
        </span>
      </div>
      <div className="text-[11px] text-muted-foreground mt-1.5">
        {r.assistant_id || "agent"} • configured model{" "}
        <span
          className="font-mono"
          title="the Alpha model name this run resolved to, not the provider that answered"
        >
          {r.model}
        </span>
        {r.error && <span className="text-destructive"> • {r.error.slice(0, 120)}</span>}
      </div>
      <div className="text-[11px] text-muted-foreground mt-1 flex items-center gap-1.5 flex-wrap">
        <Cpu className="size-3 text-primary" aria-hidden="true" />
        {r.total_tokens === null ? (
          <span className="italic">token totals not reported by this Gateway</span>
        ) : (
          <span className="font-mono" title={`${r.total_tokens} tokens, as reported by the Gateway on this run's record`}>
            {formatTokenCount(r.total_tokens)} tokens
          </span>
        )}
        {r.llm_call_count !== null && (
          <span className="font-mono" title="how many model responses this run recorded">
            • {r.llm_call_count} model calls
          </span>
        )}
      </div>
      {(r.status === "running" || r.status === "pending") && (
        <div className="mt-2">
          <Btn
            variant="danger"
            onClick={(e) => {
              e.stopPropagation();
              props.onCancel(r);
            }}
            disabled={props.cancelling}
            title={props.cancelling ? "The cancellation request is in flight" : `Ask the Gateway to stop run ${r.run_id}`}
          >
            <Ban className="size-3.5" /> {props.cancelling ? "Stopping…" : "Stop this run"}
          </Btn>
        </div>
      )}
    </div>
  );
}

export function RunsSection(props: { threadId: string | null }) {
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<RunInfo | null>(null);
  // The list's own glance summary for the selected run. This is NOT the run's
  // story — that is <RunInspectorSection>'s job — but a failed read here must
  // still never render as "0 messages / 0 file changes", and neither must an
  // *unavailable* comparison.
  const [detail, setDetail] = useState<{ messages: RunMessageCount; workspace: WorkspaceChanges } | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  // The run whose stop request is in flight, so exactly one Stop control is
  // disabled at a time and a second click cannot send a second cancel.
  const [cancelling, setCancelling] = useState<string | null>(null);
  // What the Gateway actually said about the stop. Rendered in words; never
  // folded into the run's status, which only a re-read can change.
  const [cancelNotice, setCancelNotice] = useState<{ runId: string; message: string; ok: boolean } | null>(null);
  // A run claims a generation, exactly as the chat transcript does. Clicking run
  // A and then run B let A's slower `Promise.all` land after B's, which painted
  // A's message and file counts under B's heading — two reads, one
  // confident-looking answer about the wrong run.
  const inspectGeneration = useRef(0);

  const load = async () => {
    if (!props.threadId) return;
    setLoading(true);
    setError(null);
    try {
      setRuns(await listThreadRuns(props.threadId));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // Everything below is per-conversation, so it is dropped on a switch rather
    // than carried across: the previous conversation's rows staying on screen
    // under the new one's header is a claim about runs nobody is looking at.
    inspectGeneration.current += 1;
    setSelected(null);
    setRuns([]);
    setDetail(null);
    setDetailError(null);
    setCancelNotice(null);
    setCancelling(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.threadId]);

  const inspect = async (run: RunInfo) => {
    if (!props.threadId) return;
    const token = ++inspectGeneration.current;
    setSelected(run);
    setDetailLoading(true);
    setDetailError(null);
    try {
      const [messages, workspace] = await Promise.all([
        fetchRunMessageCount(props.threadId, run.run_id),
        fetchWorkspaceChangesForRun(props.threadId, run.run_id),
      ]);
      if (inspectGeneration.current !== token) return;
      setDetail({ messages, workspace });
    } catch (e) {
      // A failed detail fetch must never render as real 0/0/empty counts —
      // clear the detail and surface an explicit "failed to load" state.
      if (inspectGeneration.current !== token) return;
      setDetail(null);
      setDetailError(errMsg(e));
    } finally {
      if (inspectGeneration.current === token) setDetailLoading(false);
    }
  };

  /**
   * Ask the Gateway to stop a run, then re-read the list.
   *
   * No optimistic state: the run's status on screen is whatever the Gateway
   * last reported, and only a fresh list read changes it. The control is
   * disabled for the duration — a double-click must not send two cancels — and
   * a refusal is shown in words, never as a run that quietly kept going under a
   * stopped-looking button.
   */
  const onCancel = async (run: RunInfo) => {
    if (!props.threadId || cancelling === run.run_id) return;
    if (!window.confirm(`Stop run ${run.run_id.slice(0, 8)}…? The agent will halt safely.`)) return;
    setCancelling(run.run_id);
    setCancelNotice(null);
    try {
      await cancelRun(props.threadId, run.run_id);
      await load();
      setCancelNotice({
        runId: run.run_id,
        message: `The Gateway accepted the stop request for ${run.run_id.slice(0, 8)}…. Its status above is what the Gateway reported on the re-read that followed.`,
        ok: true,
      });
    } catch (e) {
      setCancelNotice({
        runId: run.run_id,
        message: `The Gateway refused to stop ${run.run_id.slice(0, 8)}…: ${errMsg(e)} The run's status is unchanged.`,
        ok: false,
      });
    } finally {
      setCancelling(null);
    }
  };

  const statusTone = (s: string) => runListStatusTone(s);

  /** The honest file-change count, or `null` when the Gateway could not say. */
  const changeCount = detail ? workspaceChangeCount(detail.workspace) : null;

  return (
    <Section
      title="Run history"
      hint="Every answer the agent gives is a run. Pick one to see its measured tokens and cost, replay it step by step (including an errors-only view), and the files it changed — or stop a run that is still going."
      actions={
        <Btn variant="ghost" onClick={load} disabled={!props.threadId || loading} title="Re-read this conversation's run list from the Gateway">
          <RefreshCw className={`size-3.5 ${loading ? "animate-spin" : ""}`} /> {loading ? "Refreshing…" : "Refresh"}
        </Btn>
      }
    >
      {!props.threadId ? (
        <EmptyState title="No conversation selected" hint="Start or pick a chat first — its runs will appear here." />
      ) : loading ? (
        <SkeletonList rows={5} />
      ) : error ? (
        <ErrorBox message={error} onRetry={load} />
      ) : runs.length === 0 ? (
        <EmptyState title="No runs yet" hint="Send a message in Chat and each agent response will be listed here." />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div className="space-y-2">
            {runs.map((r) => (
              <RunListCard
                key={r.run_id}
                run={r}
                selected={selected?.run_id === r.run_id}
                cancelling={cancelling === r.run_id}
                onSelect={inspect}
                onCancel={onCancel}
              />
            ))}
          </div>
          <div className="space-y-3">
            {!selected ? (
              <EmptyState title="Select a run" hint="Click any run on the left to inspect it." />
            ) : (
              <>
                {detailError ? (
                  <ErrorBox
                    message={`Couldn't load this run's detail — messages/file changes are unavailable, not zero. (${detailError})`}
                    onRetry={() => inspect(selected)}
                  />
                ) : detailLoading || !detail ? (
                  <SkeletonList rows={2} />
                ) : (
                  <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-2">
                    <p className="text-[11px] font-semibold text-muted-foreground">At a glance — the full story is below</p>
                    {selected.stop_reason && (
                      <p className="text-[11px] text-muted-foreground">
                        Stop reason: <span className="font-mono">{selected.stop_reason}</span>
                      </p>
                    )}
                    <div className="grid grid-cols-2 gap-2">
                      <div
                        className="rounded-xl bg-muted/40 p-2.5 text-center"
                        role="group"
                        aria-label={
                          detail.messages.partial
                            ? `At least ${detail.messages.count} messages: the read stopped at its own cap, so this is a floor, not a total.`
                            : `${detail.messages.count} messages recorded by the Gateway for this run.`
                        }
                        title={
                          detail.messages.partial
                            ? `At least ${detail.messages.count} messages — this read stopped at its own cap, and the ordered transcript is in the run inspector below.`
                            : `${detail.messages.count} messages the Gateway recorded for this run`
                        }
                      >
                        <MessagesSquare className="size-4 mx-auto text-primary" aria-hidden="true" />
                        {/*
                          A partial read is a floor, not a total: this endpoint
                          pages at 50 by default, so a bare read used to render
                          "50 messages" for a run that recorded far more. The
                          full, ordered transcript is in the inspector below.
                        */}
                        <div className="text-sm font-bold mt-1">
                          {detail.messages.partial ? `${detail.messages.count}+` : detail.messages.count}
                        </div>
                        <div className="text-[10px] text-muted-foreground">
                          messages
                          {detail.messages.partial ? " (at least)" : ""}
                        </div>
                      </div>
                      <div
                        className="rounded-xl bg-muted/40 p-2.5 text-center"
                        role="group"
                        aria-label={
                          changeCount === null
                            ? detail.workspace.available === false
                              ? "File changes unknown: the Gateway could not compare this run's workspace snapshots, so the count is not zero."
                              : "File changes not reported by the Gateway."
                            : `${changeCount} file changes recorded by the Gateway for this run.`
                        }
                        title={
                          changeCount === null
                            ? detail.workspace.available === false
                              ? "The Gateway could not compare this run's workspace snapshots. That is not a measurement of zero changes."
                              : "The Gateway reported no file-change count for this run."
                            : `${changeCount} files changed, from the Gateway's before/after comparison of this run's workspace`
                        }
                      >
                        <FileDiff className="size-4 mx-auto text-primary" aria-hidden="true" />
                        {/*
                          The Gateway answers a workspace comparison with
                          `available: false` when it could not make the
                          comparison at all, and that is NOT a measurement of
                          zero changes — this tile used to read "0 file changes"
                          in that case. `workspaceChangeCount` keeps the honest
                          unknown out of the number slot, and the words under it
                          say which of the two unknowns it is rather than
                          printing a bare dash that could be either.
                        */}
                        {changeCount === null ? (
                          <>
                            <div className="text-sm font-bold mt-1 text-amber-600 dark:text-amber-400">unknown</div>
                            <div className="text-[10px] text-muted-foreground">
                              {detail.workspace.available === false
                                ? "comparison not available for this run"
                                : "file changes not reported"}
                            </div>
                          </>
                        ) : (
                          <>
                            <div className="text-sm font-bold mt-1">{changeCount}</div>
                            <div className="text-[10px] text-muted-foreground">file changes</div>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                )}
                {/* The run's full story: the prompt, every tool call with its
                    resolved status, the event timeline, the delivery receipt,
                    and the tokens. */}
                <RunInspectorSection threadId={props.threadId} runId={selected.run_id} showPicker={false} />
                <RunUsagePanel threadId={props.threadId} runId={selected.run_id} run={selected} />
                <RunReplayControls threadId={props.threadId} runId={selected.run_id} />
              </>
            )}
            {cancelNotice ? (
              <p
                className={`text-[11px] flex items-start gap-1.5 ${cancelNotice.ok ? "text-emerald-700 dark:text-emerald-400" : "text-destructive"}`}
              >
                {cancelNotice.ok ? (
                  <Check className="size-3 mt-0.5 shrink-0" aria-hidden="true" />
                ) : (
                  <AlertTriangle className="size-3 mt-0.5 shrink-0" aria-hidden="true" />
                )}
                {cancelNotice.message}
              </p>
            ) : null}
          </div>
        </div>
      )}
    </Section>
  );
}
