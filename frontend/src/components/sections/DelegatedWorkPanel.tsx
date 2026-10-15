"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";

import { Badge, Btn, EmptyState, ErrorBox, SkeletonList, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import {
  fetchThreadDelegations,
  isTerminalDelegation,
  type RunDelegation,
} from "@/lib/runs-inspector";

type ReadState = "loading" | "ready" | "error";

const isActiveDelegation = (status: string | null) => status === "in_progress";

function statusTone(status: string | null): "green" | "blue" | "amber" | "red" | "gray" {
  if (status === "completed") return "green";
  if (status === "in_progress") return "blue";
  if (status === "failed" || status === "timed_out" || status === "polling_timed_out") return "red";
  if (status === "cancelled") return "amber";
  return "gray";
}

function recordedTime(value: string | null): React.ReactNode {
  if (!value) return "Creation time not reported";
  if (!Number.isFinite(Date.parse(value))) return "Creation time was reported in an unreadable format";
  return <time dateTime={value}>{new Date(value).toLocaleString()}</time>;
}

function DelegationCard({ item }: { item: RunDelegation }) {
  return (
    <details className="group rounded-xl border border-border/60 bg-muted/20">
      <summary className="flex cursor-pointer list-none items-start gap-2 px-3 py-3 marker:hidden">
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-semibold">{item.description || "Task description not reported"}</span>
            <Badge tone={statusTone(item.status)}>{item.status ?? "status not reported"}</Badge>
            {item.subagentType && <Badge tone="gray">{item.subagentType}</Badge>}
          </span>
          <span className="mt-1 block text-[10px] text-muted-foreground">
            {item.createdAt ? <>Recorded {recordedTime(item.createdAt)}</> : recordedTime(null)}
            {item.id && <span> · Task {item.id}</span>}
          </span>
        </span>
        <span aria-hidden="true" className="text-xs text-muted-foreground transition-transform group-open:rotate-180">⌄</span>
      </summary>
      <div className="space-y-2 border-t border-border/50 px-3 py-3 text-[11px]">
        <dl className="grid gap-2 sm:grid-cols-2">
          <div><dt className="text-muted-foreground">Delegation ID</dt><dd className="break-all font-mono">{item.id ?? "Not reported"}</dd></div>
          <div><dt className="text-muted-foreground">Source run</dt><dd className="break-all font-mono">{item.runId ?? "Run attribution not reported by the Gateway"}</dd></div>
          <div><dt className="text-muted-foreground">Subagent type</dt><dd>{item.subagentType ?? "Not reported"}</dd></div>
          <div><dt className="text-muted-foreground">Stop reason</dt><dd>{item.stopReason ?? "Not reported"}</dd></div>
        </dl>
        {item.resultBrief ? (
          <div className="rounded-lg bg-card px-3 py-2"><p className="mb-1 font-semibold">Reported result</p><p className="whitespace-pre-wrap text-muted-foreground">{item.resultBrief}</p></div>
        ) : (
          <p className="rounded-lg bg-card px-3 py-2 text-muted-foreground">The Gateway has not included a result brief in this delegation record.</p>
        )}
      </div>
    </details>
  );
}

/** Thread-owned view of `ThreadState.delegations`; never mixes the global control-plane fleet. */
export function DelegatedWorkPanel({ threadId }: { threadId: string | null }) {
  const [items, setItems] = useState<RunDelegation[] | null>(null);
  const [phase, setPhase] = useState<ReadState>(threadId ? "loading" : "ready");
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const requestId = useRef(0);

  const load = useCallback(async (quiet = false) => {
    const currentRequest = ++requestId.current;
    if (!threadId) {
      setItems(null);
      setPhase("ready");
      setError(null);
      return;
    }
    if (!quiet) setPhase("loading");
    try {
      const result = await fetchThreadDelegations(threadId);
      if (currentRequest !== requestId.current) return;
      setItems(result);
      setError(null);
      setPhase("ready");
    } catch (reason) {
      if (currentRequest !== requestId.current) return;
      setError(errMsg(reason));
      setPhase("error");
    }
  }, [threadId]);

  useEffect(() => {
    setItems(null);
    setError(null);
    void load();
  }, [load]);

  const hasActive = items?.some((item) => isActiveDelegation(item.status)) ?? false;
  useEffect(() => {
    if (!hasActive) return;
    const timer = window.setInterval(() => void load(true), 5000);
    return () => window.clearInterval(timer);
  }, [hasActive, load]);

  const visible = useMemo(() => {
    const query = filter.trim().toLowerCase();
    if (!query) return items ?? [];
    return (items ?? []).filter((item) =>
      [item.description, item.subagentType, item.status, item.id, item.runId, item.resultBrief]
        .some((value) => value?.toLowerCase().includes(query)),
    );
  }, [filter, items]);
  const active = visible.filter((item) => isActiveDelegation(item.status));
  const done = visible.filter((item) => isTerminalDelegation(item.status));
  const other = visible.filter((item) => !isActiveDelegation(item.status) && !isTerminalDelegation(item.status));

  return (
    <section className="rounded-2xl border border-border/60 bg-card p-4 space-y-3" aria-labelledby="delegated-work-title" data-testid="thread-delegated-work">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-40 flex-1">
          <h3 id="delegated-work-title" className="text-sm font-semibold">Delegated work in this conversation</h3>
          <p className="mt-0.5 text-[10px] text-muted-foreground">Thread-scoped task history from the Gateway. Active items refresh automatically.</p>
        </div>
        {items && <Badge tone="gray">{items.length} recorded</Badge>}
        <label htmlFor="delegated-work-filter" className="sr-only">Filter delegated work</label>
        <input id="delegated-work-filter" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter delegated tasks…" className={`${inputCls} max-w-xs`} />
        <Btn variant="ghost" onClick={() => void load()} disabled={phase === "loading"}>
          <RefreshCw className={`size-3.5 ${phase === "loading" ? "animate-spin" : ""}`} /> Refresh
        </Btn>
      </div>

      {phase === "loading" && items === null && <SkeletonList rows={2} />}
      {phase === "error" && items === null && <ErrorBox message={`Conversation delegation history is unavailable — ${error ?? "the Gateway did not provide a reason"}`} onRetry={() => void load()} />}
      {phase === "error" && items !== null && <ErrorBox message={`Could not refresh this conversation's delegation history; the last server response remains visible. ${error ?? ""}`} onRetry={() => void load()} />}
      {phase === "ready" && items === null && (threadId === null
        ? <p className="rounded-lg border border-border/60 bg-muted/30 p-3 text-[11px] text-muted-foreground">This draft has no server conversation yet, so delegated work cannot be read.</p>
        : <p className="rounded-lg border border-border/60 bg-muted/30 p-3 text-[11px] text-muted-foreground">The Gateway did not include a delegation ledger for this conversation. That is unknown, not a confirmed empty history.</p>)}
      {phase === "ready" && items?.length === 0 && <EmptyState title="No delegated tasks recorded in this conversation" hint="Subagent tasks from a run appear here after the Gateway records them." />}
      {items !== null && items.length > 0 && visible.length === 0 && <EmptyState title="No matching delegated tasks" hint="Try a different task, role, status, or run ID." />}
      {visible.length > 0 && (
        <div className="space-y-4">
          {[
            { label: `Active · ${active.length}`, rows: active },
            { label: `Done · ${done.length}`, rows: done },
            { label: `Other reported states · ${other.length}`, rows: other },
          ].map((group) => (
            <div key={group.label}>
              <h4 className="mb-2 text-xs font-semibold">{group.label}</h4>
              {group.rows.length > 0 ? <div className="space-y-2">{group.rows.map((item, index) => <DelegationCard key={`${item.runId ?? "unattributed"}:${item.id ?? index}`} item={item} />)}</div> : <p className="rounded-lg border border-dashed border-border/60 px-3 py-2 text-[10px] text-muted-foreground">No records in this group.</p>}
            </div>
          ))}
          <p className="text-[10px] text-muted-foreground">Only delegations with run IDs can be attributed to a specific run. Older untagged records stay in this conversation view and are labeled as unattributed.</p>
        </div>
      )}
    </section>
  );
}
