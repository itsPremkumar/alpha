"use client";

import React, { useEffect, useState } from "react";
import { listThreadRuns, fetchRunMessages, fetchWorkspaceChanges, cancelRun, formatTokenCount, RunInfo, WorkspaceChange } from "@/lib/runs";
import { Section, EmptyState, ErrorBox, Badge, Btn, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { Ban, RefreshCw, FileDiff, MessagesSquare, Cpu } from "lucide-react";
import { RunInspectorSection } from "./RunInspectorSection";
import { RunReplayControls } from "./RunReplayControls";
import { RunUsagePanel } from "./RunUsagePanel";

export function RunsSection(props: { threadId: string | null }) {
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<RunInfo | null>(null);
  // The list's own glance summary for the selected run. This is NOT the run's
  // story — that is <RunInspectorSection>'s job — but a failed read here must
  // still never render as "0 messages / 0 file changes".
  const [detail, setDetail] = useState<{ messages: number; changes: WorkspaceChange[] } | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

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
    setSelected(null);
    setDetail(null);
    setDetailError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.threadId]);

  const inspect = async (run: RunInfo) => {
    if (!props.threadId) return;
    setSelected(run);
    setDetailLoading(true);
    setDetailError(null);
    try {
      const [msgs, changes] = await Promise.all([
        fetchRunMessages(props.threadId, run.run_id),
        fetchWorkspaceChanges(props.threadId, run.run_id),
      ]);
      setDetail({ messages: msgs.length, changes });
    } catch (e) {
      // A failed detail fetch must never render as real 0/0/empty counts —
      // clear the detail and surface an explicit "failed to load" state.
      setDetail(null);
      setDetailError(errMsg(e));
    } finally {
      setDetailLoading(false);
    }
  };

  const onCancel = async (run: RunInfo) => {
    if (!props.threadId) return;
    if (!window.confirm(`Stop run ${run.run_id.slice(0, 8)}…? The agent will halt safely.`)) return;
    try {
      await cancelRun(props.threadId, run.run_id);
      await load();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const statusTone = (s: string) => (s === "success" || s === "completed" ? "green" : s === "running" || s === "pending" ? "blue" : s === "error" || s === "failed" ? undefined : "gray") as "green" | "blue" | "gray" | undefined;

  return (
    <Section
      title="Run history"
      hint="Every answer the agent gives is a run. Pick one to see its measured tokens and cost, replay it step by step (including an errors-only view), and the files it changed — or stop a run that is still going."
      actions={
        <Btn variant="ghost" onClick={load} disabled={!props.threadId || loading}>
          <RefreshCw className="size-3.5" /> Refresh
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
              <div
                key={r.run_id}
                className={`rounded-xl border bg-card p-3 cursor-pointer transition-colors ${selected?.run_id === r.run_id ? "border-primary ring-1 ring-primary/30" : "border-border/60 hover:border-primary/40"}`}
                onClick={() => inspect(r)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === "Enter" && inspect(r)}
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge tone={statusTone(r.status)}>{r.status}</Badge>
                  <span className="text-[11px] font-mono text-muted-foreground">{r.run_id.slice(0, 12)}…</span>
                  <span className="text-[11px] text-muted-foreground ml-auto">
                    {r.created_at ? new Date(r.created_at).toLocaleString() : ""}
                  </span>
                </div>
                <div className="text-[11px] text-muted-foreground mt-1.5">
                  {r.assistant_id || "agent"} • {r.model}
                  {r.error && <span className="text-destructive"> • {r.error.slice(0, 120)}</span>}
                </div>
                <div className="text-[11px] text-muted-foreground mt-1 flex items-center gap-1.5 flex-wrap">
                  <Cpu className="size-3 text-primary" />
                  <span className="font-mono">{formatTokenCount(r.total_tokens)} tokens</span>
                  {r.llm_call_count !== null && <span className="font-mono">• {formatTokenCount(r.llm_call_count)} LLM calls</span>}
                  {r.total_tokens === null && <span className="italic">token totals not reported by this Gateway</span>}
                </div>
                {(r.status === "running" || r.status === "pending") && (
                  <div className="mt-2">
                    <Btn
                      variant="danger"
                      onClick={(e) => {
                        e.stopPropagation();
                        onCancel(r);
                      }}
                    >
                      <Ban className="size-3.5" /> Stop this run
                    </Btn>
                  </div>
                )}
              </div>
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
                      <div className="rounded-xl bg-muted/40 p-2.5 text-center">
                        <MessagesSquare className="size-4 mx-auto text-primary" />
                        <div className="text-sm font-bold mt-1">{detail.messages}</div>
                        <div className="text-[10px] text-muted-foreground">messages</div>
                      </div>
                      <div className="rounded-xl bg-muted/40 p-2.5 text-center">
                        <FileDiff className="size-4 mx-auto text-primary" />
                        <div className="text-sm font-bold mt-1">{detail.changes.length}</div>
                        <div className="text-[10px] text-muted-foreground">file changes</div>
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
          </div>
        </div>
      )}
    </Section>
  );
}
