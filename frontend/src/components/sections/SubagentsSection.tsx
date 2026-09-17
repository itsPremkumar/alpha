"use client";

import React, { useEffect, useState } from "react";
import { listSubagentCatalog, listLiveSubagents, spawnSubagent, cancelSubagent, subagentResult, SubagentDef, LiveSubagent } from "@/lib/subagents";
import { listBatches, batchItems, pauseBatch, resumeBatch, cancelBatch, retryBatchItem, Batch, BatchItem } from "@/lib/batches";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, Field, SkeletonList, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { Play, Ban, RefreshCw, Eye, Pause, RotateCcw } from "lucide-react";

export function SubagentsSection(props: { threadId: string | null }) {
  const [catalog, setCatalog] = useState<SubagentDef[]>([]);
  const [live, setLive] = useState<LiveSubagent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [objective, setObjective] = useState("");
  const [result, setResult] = useState<{ id: string; text: string } | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [c, l] = await Promise.all([listSubagentCatalog(), listLiveSubagents()]);
      setCatalog(c);
      setLive(l);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const flash = (m: string) => {
    setNotice(m);
    window.setTimeout(() => setNotice(null), 4000);
  };

  const onSpawn = async () => {
    if (!objective.trim()) return;
    try {
      await spawnSubagent(objective.trim());
      setObjective("");
      flash("Helper started ” watch it below.");
      await load();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const onViewResult = async (s: LiveSubagent) => {
    const r = await subagentResult(s.id);
    setResult({ id: s.id, text: r ? JSON.stringify(r, null, 2).slice(0, 8000) : "No result yet ” it may still be working." });
  };

  return (
    <Section
      title="Subagents"
      hint="Big jobs get split into helpers that work in the background. See what's running, stop a stuck one, or start one yourself."
      actions={
        <Btn variant="ghost" onClick={load}>
          <RefreshCw className="size-3.5" /> Refresh
        </Btn>
      }
    >
      {error && <ErrorBox message={error} onRetry={load} />}
      {notice && <Notice message={notice} />}

      <div className="rounded-2xl border border-border/60 bg-card p-4">
        <Field label="Start a helper" hint="Describe a self-contained job, e.g. “Research three competitors and summarize pricing”. Needs admin rights on the server.">
          <div className="flex gap-2">
            <input value={objective} onChange={(e) => setObjective(e.target.value)} onKeyDown={(e) => e.key === "Enter" && onSpawn()} placeholder="What should the helper do?…" className={inputCls} aria-label="Helper objective" />
            <Btn onClick={onSpawn} disabled={!objective.trim()}>
              <Play className="size-3.5" /> Start
            </Btn>
          </div>
        </Field>
      </div>

      <div>
        <h3 className="text-xs font-semibold mb-2">Running now ({live.length})</h3>
        {loading ? (
          <SkeletonList rows={2} />
        ) : live.length === 0 ? (
          <EmptyState title="Nothing running" hint="Helpers appear here while the agent works on multi-step tasks." />
        ) : (
          <div className="space-y-2">
            {live.map((s) => (
              <div key={s.id} className="rounded-xl border border-border/60 bg-card px-4 py-2.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <p className="text-xs font-semibold font-mono flex-1 min-w-32 break-all">{s.id.slice(0, 24)}</p>
                  <Badge tone={s.status === "running" ? "blue" : s.status === "failed" || s.status === "error" ? undefined : "green"}>{s.status}</Badge>
                </div>
                {s.objective && <p className="text-[11px] text-muted-foreground mt-1 line-clamp-2">{s.objective}</p>}
                <div className="flex gap-2 mt-2">
                  <Btn variant="ghost" onClick={() => onViewResult(s)}>
                    <Eye className="size-3.5" /> Result
                  </Btn>
                  <Btn variant="danger" onClick={() => window.confirm("Stop this helper?") && cancelSubagent(s.id).then(load).catch((e) => setError(errMsg(e)))}>
                    <Ban className="size-3.5" /> Stop
                  </Btn>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {result && (
        <div className="rounded-2xl border border-border/60 bg-card p-4">
          <div className="flex items-center gap-2 mb-2">
            <p className="text-xs font-semibold flex-1 font-mono break-all">Result: {result.id.slice(0, 24)}</p>
            <Btn variant="ghost" onClick={() => setResult(null)}>Close</Btn>
          </div>
          <pre className="text-[11px] font-mono whitespace-pre-wrap max-h-80 overflow-y-auto rounded-xl bg-muted/40 p-3">{result.text}</pre>
        </div>
      )}

      <BatchesBlock threadId={props.threadId} onError={setError} />

      <div>
        <h3 className="text-xs font-semibold mb-2">Available types ({catalog.length})</h3>
        {catalog.length === 0 && !loading ? (
          <EmptyState title="No catalog" hint="The server did not return a helper catalog." />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {catalog.map((c) => (
              <div key={`${c.source}-${c.name}`} className="rounded-xl border border-border/60 bg-card p-3">
                <div className="flex items-center gap-2">
                  <p className="text-xs font-semibold font-mono flex-1 truncate">{c.name}</p>
                  <Badge tone={c.enabled ? "green" : "gray"}>{c.enabled ? "on" : "off"}</Badge>
                </div>
                <p className="text-[11px] text-muted-foreground mt-1 line-clamp-2">{c.description || "No description."}</p>
                <p className="text-[10px] font-mono text-muted-foreground mt-1">{c.model}{c.source ? ` • ${c.source}` : ""}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </Section>
  );
}

function BatchesBlock(props: { threadId: string | null; onError: (m: string) => void }) {
  const [batches, setBatches] = useState<Batch[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [items, setItems] = useState<Record<string, BatchItem[]>>({});
  const [loading, setLoading] = useState(false);

  const load = async () => {
    if (!props.threadId) return;
    setLoading(true);
    try {
      setBatches(await listBatches(props.threadId));
    } catch (e) {
      props.onError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setOpen(null);
    setItems({});
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.threadId]);

  const openBatch = async (b: Batch) => {
    if (!props.threadId) return;
    const isOpen = open === b.id;
    setOpen(isOpen ? null : b.id);
    if (!isOpen && !items[b.id]) {
      try {
        const list = await batchItems(props.threadId, b.id);
        setItems((prev) => ({ ...prev, [b.id]: list }));
      } catch (e) {
        props.onError(errMsg(e));
      }
    }
  };

  const act = async (fn: () => Promise<void>) => {
    try {
      await fn();
      await load();
      setOpen(null);
    } catch (e) {
      props.onError(errMsg(e));
    }
  };

  if (!props.threadId) {
    return (
      <div>
        <h3 className="text-xs font-semibold mb-2">Work batches</h3>
        <EmptyState title="Pick a chat first" hint="Batches belong to a conversation — select one to see its parallel work packages." />
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <h3 className="text-xs font-semibold flex-1">Work batches ({batches.length})</h3>
        <Btn variant="ghost" onClick={load}>
          <RefreshCw className="size-3.5" /> Refresh
        </Btn>
      </div>
      {loading ? (
        <SkeletonList rows={2} />
      ) : batches.length === 0 ? (
        <EmptyState title="No batches in this chat" hint="When the agent splits big work into parallel packages, they appear here with per-item controls." />
      ) : (
        <div className="space-y-2">
          {batches.map((b) => (
            <div key={b.id} className="rounded-xl border border-border/60 bg-card">
              <div className="flex items-center gap-2 px-4 py-2.5 cursor-pointer" onClick={() => openBatch(b)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && openBatch(b)}>
                <p className="text-xs font-mono flex-1 min-w-24 break-all">{b.id.slice(0, 28)}</p>
                <Badge tone={b.status === "running" ? "blue" : b.status === "failed" ? undefined : "green"}>{b.status}</Badge>
                <span className="text-[11px] text-muted-foreground">{open === b.id ? "Hide" : "Items"}</span>
              </div>
              {open === b.id && (
                <div className="px-4 pb-3 border-t border-border/50 pt-2 space-y-1.5">
                  {(items[b.id] || []).length === 0 ? (
                    <p className="text-[11px] text-muted-foreground">No items listed.</p>
                  ) : (
                    (items[b.id] || []).map((it) => (
                      <div key={it.id} className="flex items-center gap-2 rounded-lg bg-muted/40 px-2.5 py-1.5">
                        <span className="text-[11px] flex-1 min-w-0 truncate">{it.label || it.id.slice(0, 20)}</span>
                        <Badge tone={it.status === "failed" ? undefined : it.status === "done" || it.status === "success" ? "green" : "gray"}>{it.status}</Badge>
                        {(it.status === "failed" || it.status === "error") && props.threadId && (
                          <button type="button" onClick={() => props.threadId && act(() => retryBatchItem(props.threadId as string, b.id, it.id))} className="p-1 rounded hover:bg-muted" title="Retry this item">
                            <RotateCcw className="size-3.5" />
                          </button>
                        )}
                      </div>
                    ))
                  )}
                  {props.threadId && (
                    <div className="flex gap-2 pt-1 flex-wrap">
                      <Btn variant="ghost" onClick={() => props.threadId && act(() => pauseBatch(props.threadId as string, b.id))}>
                        <Pause className="size-3.5" /> Pause
                      </Btn>
                      <Btn variant="ghost" onClick={() => props.threadId && act(() => resumeBatch(props.threadId as string, b.id))}>
                        <Play className="size-3.5" /> Resume
                      </Btn>
                      <Btn variant="danger" onClick={() => window.confirm("Cancel this whole batch?") && props.threadId && act(() => cancelBatch(props.threadId as string, b.id))}>
                        <Ban className="size-3.5" /> Cancel
                      </Btn>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
