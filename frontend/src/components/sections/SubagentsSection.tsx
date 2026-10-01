"use client";

import React, { useEffect, useState } from "react";
import {
  listSubagentCatalog, fetchLiveSubagentsStrict, spawnSubagent, cancelSubagent, subagentResult,
  subagentStatusTone, isKnownSubagentStatus, batchStatusTone, batchItemStatusTone,
  SubagentDef, LiveSubagent,
} from "@/lib/subagents";
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
  /**
   * Per-read failure flags.
   *
   * `load()` wrapped the catalog and the live fleet in one `try` over
   * `listLiveSubagents()`, which caught and returned `[]`. Measured against a
   * down Gateway that produced `[]`, so the section painted "Running now (0)"
   * and the `EmptyState` "Nothing running" — the UI asserting that no helper
   * is working when nothing had been read. The reader now rejects (see
   * `fetchLiveSubagentsStrict`), and each list carries its own flag so a
   * catalog failure cannot blank the fleet and vice versa.
   */
  const [liveError, setLiveError] = useState<string | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  /** True while a spawn is in flight, so a double-click cannot spawn two. */
  const [spawning, setSpawning] = useState(false);
  /** Subagent ids with a lifecycle request in flight. */
  const [busyId, setBusyId] = useState<string | null>(null);
  /** True while a result read is in flight, so its own error is attributable. */
  const [readingId, setReadingId] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    // Settle independently: one failed fetch must not blank the other list.
    const [c, l] = await Promise.allSettled([listSubagentCatalog(), fetchLiveSubagentsStrict()]);
    if (c.status === "fulfilled") { setCatalog(c.value); setCatalogError(null); }
    else { setCatalog([]); setCatalogError(errMsg(c.reason)); }
    if (l.status === "fulfilled") { setLive(l.value); setLiveError(null); }
    else { setLive([]); setLiveError(errMsg(l.reason)); }
    setLoading(false);
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const flash = (m: string) => {
    setNotice(m);
    window.setTimeout(() => setNotice(null), 4000);
  };

  /**
   * Spawn one helper, with the control disabled for the round trip.
   *
   * The guard is not cosmetic: `POST /api/subagents/control/spawn`
   * (subagent_control.py:78) provisions a real subagent with a lease, and the
   * objective was cleared only inside the success path, so a second click
   * inside the request window spawned a SECOND helper from the same draft —
   * duplicate token spend, on the surface whose purpose is spawning helpers.
   */
  const onSpawn = async () => {
    const text = objective.trim();
    if (!text || spawning) return;
    setSpawning(true);
    setError(null);
    try {
      await spawnSubagent(text);
      // Only clear the draft once the server has accepted the spawn.
      setObjective("");
      flash("Helper started — watch it below.");
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setSpawning(false);
    }
  };

  /**
   * Show one subagent's deliverable, or the reason there is none.
   *
   * `subagentResult` used to `catch { return null }`, so a 500 or a 404 rendered
   * "No result yet — it may still be working." — an invented explanation for a
   * read that never completed. `null` now means the server answered and
   * reported no result; a failure keeps the server's `detail`.
   */
  const onViewResult = async (s: LiveSubagent) => {
    setReadingId(s.id);
    setResult(null);
    try {
      const r = await subagentResult(s.id);
      setResult({
        id: s.id,
        text: r
          ? JSON.stringify(r, null, 2).slice(0, 8000)
          : "The server reported no result for this helper. That is an empty answer, not a failure to read.",
      });
    } catch (e) {
      setResult({
        id: s.id,
        text: `Could not read this helper's result — this is a fetch failure, not a missing result. The server said: ${errMsg(e)}`,
      });
    } finally {
      setReadingId(null);
    }
  };

  /** Stop one helper, locked for the round trip. */
  const onCancel = async (s: LiveSubagent) => {
    if (busyId) return;
    if (!window.confirm("Stop this helper?")) return;
    setBusyId(s.id);
    setError(null);
    try {
      await cancelSubagent(s.id);
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusyId(null);
    }
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
            <Btn onClick={onSpawn} disabled={!objective.trim() || spawning} title={spawning ? "A spawn request is still in flight" : undefined}>
              <Play className="size-3.5" aria-hidden="true" /> {spawning ? "Starting…" : "Start"}
            </Btn>
          </div>
        </Field>
      </div>

      <div>
        {/* The count is a measurement. While the read is in flight, or after it
            failed, it is not zero — nothing was measured. */}
        <h3 className="text-xs font-semibold mb-2">
          Running now ({loading ? "…" : liveError ? "count unavailable" : live.length})
        </h3>
        {loading ? (
          <SkeletonList rows={2} />
        ) : liveError ? (
          <ErrorBox
            message={`The helper fleet could not be read — this is a fetch failure, not an empty fleet. (${liveError})`}
            onRetry={load}
          />
        ) : live.length === 0 ? (
          <EmptyState title="Nothing running" hint="Helpers appear here while the agent works on multi-step tasks." />
        ) : (
          <div className="space-y-2">
            {live.map((s) => {
              const busy = busyId === s.id;
              return (
              <div key={s.id} className="rounded-xl border border-border/60 bg-card px-4 py-2.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <p className="text-xs font-semibold font-mono flex-1 min-w-32 break-all" title={s.id || undefined}>
                    {s.id || "id not reported by the server"}
                  </p>
                  {/* The server's status, verbatim, with a tone that never
                      asserts success for a state this build does not know. */}
                  <Badge tone={subagentStatusTone(s.status)}>
                    {s.status || "status not reported"}
                  </Badge>
                  {s.status && !isKnownSubagentStatus(s.status) && (
                    <span className="text-[10px] text-muted-foreground">
                      not a status this build knows — shown exactly as the server reported it
                    </span>
                  )}
                </div>
                {s.objective
                  ? <p className="text-[11px] text-muted-foreground mt-1 line-clamp-2">{s.objective}</p>
                  : <p className="text-[11px] text-muted-foreground mt-1">Objective not reported by the server.</p>}
                <div className="flex gap-2 mt-2">
                  <Btn variant="ghost" onClick={() => onViewResult(s)} disabled={readingId === s.id}>
                    <Eye className="size-3.5" aria-hidden="true" /> {readingId === s.id ? "Reading…" : "Result"}
                  </Btn>
                  <Btn variant="danger" onClick={() => onCancel(s)} disabled={busy}>
                    <Ban className="size-3.5" aria-hidden="true" /> {busy ? "Stopping…" : "Stop"}
                  </Btn>
                </div>
              </div>
              );
            })}
          </div>
        )}
      </div>

      {result && (
        <div className="rounded-2xl border border-border/60 bg-card p-4">
          <div className="flex items-center gap-2 mb-2">
            <p className="text-xs font-semibold flex-1 font-mono break-all">Result: {result.id || "unknown helper"}</p>
            <Btn variant="ghost" onClick={() => setResult(null)}>Close</Btn>
          </div>
          <pre className="text-[11px] font-mono whitespace-pre-wrap max-h-80 overflow-y-auto rounded-xl bg-muted/40 p-3">{result.text}</pre>
        </div>
      )}

      <BatchesBlock threadId={props.threadId} onError={setError} />

      <div>
        <h3 className="text-xs font-semibold mb-2">
          Available types ({loading ? "…" : catalogError ? "count unavailable" : catalog.length})
        </h3>
        {catalogError ? (
          <ErrorBox
            message={`The helper catalog could not be read — this is a fetch failure, not an empty catalog. (${catalogError})`}
            onRetry={load}
          />
        ) : catalog.length === 0 && !loading ? (
          <EmptyState title="No catalog" hint="The server did not return a helper catalog." />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {catalog.map((c) => (
              <div key={`${c.source}-${c.name}`} className="rounded-xl border border-border/60 bg-card p-3">
                <div className="flex items-center gap-2">
                  <p className="text-xs font-semibold font-mono flex-1 truncate">{c.name || "(unnamed type)"}</p>
                  {/* `enabled` is now `boolean | null`. It was
                      `Boolean(pick(…, true))`, so an absent flag became TRUE and
                      drew this green "on" badge — a catalog entry the server
                      never enabled, displayed as enabled. Unknown says so. */}
                  {c.enabled === null ? (
                    <span className="text-[10px] text-muted-foreground px-2 py-0.5 rounded-full bg-muted">
                      enabled state not reported
                    </span>
                  ) : (
                    <Badge tone={c.enabled ? "green" : "gray"}>{c.enabled ? "on" : "off"}</Badge>
                  )}
                </div>
                <p className="text-[11px] text-muted-foreground mt-1 line-clamp-2">{c.description || "No description."}</p>
                <p className="text-[10px] font-mono text-muted-foreground mt-1">
                  {c.model ? `model: ${c.model}` : "model not reported"}
                  {c.source ? ` • ${c.source}` : " • source not reported"}
                </p>
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
  /**
   * Per-batch item-read failure, and the batch list's own failure.
   *
   * `(items[b.id] || []).length === 0` rendered "No items listed." for a
   * batch whose item read FAILED — a failed fetch and a genuinely empty batch
   * were the same screen. `itemErrors` keeps them apart, and `listError` does
   * the same for the batch list, which otherwise showed "No batches in this
   * chat" on a 500.
   */
  const [itemErrors, setItemErrors] = useState<Record<string, string | null>>({});
  const [listError, setListError] = useState<string | null>(null);
  /** Batch with a lifecycle request in flight — a double-click must not double-post. */
  const [busyBatch, setBusyBatch] = useState<string | null>(null);

  const load = async () => {
    if (!props.threadId) return;
    setLoading(true);
    try {
      setBatches(await listBatches(props.threadId));
      setListError(null);
    } catch (e) {
      setBatches([]);
      setListError(errMsg(e));
      props.onError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setOpen(null);
    setItems({});
    setItemErrors({});
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.threadId]);

  const openBatch = async (b: Batch) => {
    if (!props.threadId) return;
    const isOpen = open === b.id;
    setOpen(isOpen ? null : b.id);
    if (!isOpen && !items[b.id] && !itemErrors[b.id]) {
      try {
        const list = await batchItems(props.threadId, b.id);
        setItems((prev) => ({ ...prev, [b.id]: list }));
        setItemErrors((prev) => ({ ...prev, [b.id]: null }));
      } catch (e) {
        // A failed read is NOT an empty batch. Retry is offered.
        setItemErrors((prev) => ({ ...prev, [b.id]: errMsg(e) }));
        props.onError(errMsg(e));
      }
    }
  };

  /** One batch verb, with the control disabled for the round trip. */
  const act = async (b: Batch, fn: () => Promise<void>) => {
    if (busyBatch) return;
    setBusyBatch(b.id);
    try {
      await fn();
      await load();
      setOpen(null);
    } catch (e) {
      props.onError(errMsg(e));
    } finally {
      setBusyBatch(null);
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
        <h3 className="text-xs font-semibold flex-1">
          Work batches ({loading ? "…" : listError ? "count unavailable" : batches.length})
        </h3>
        <Btn variant="ghost" onClick={load}>
          <RefreshCw className="size-3.5" aria-hidden="true" /> Refresh
        </Btn>
      </div>
      {loading ? (
        <SkeletonList rows={2} />
      ) : listError ? (
        <ErrorBox
          message={`Work batches could not be read — this is a fetch failure, not an empty list. (${listError})`}
          onRetry={load}
        />
      ) : batches.length === 0 ? (
        <EmptyState title="No batches in this chat" hint="When the agent splits big work into parallel packages, they appear here with per-item controls." />
      ) : (
        <div className="space-y-2">
          {batches.map((b) => {
            const busy = busyBatch === b.id;
            return (
            <div key={b.id} className="rounded-xl border border-border/60 bg-card">
              <div className="flex items-center gap-2 px-4 py-2.5 cursor-pointer" onClick={() => openBatch(b)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && openBatch(b)}>
                <p className="text-xs font-mono flex-1 min-w-24 break-all" title={b.id || undefined}>
                  {b.id || "id not reported by the server"}
                </p>
                {/* Same rule as a subagent: a status this build does not name
                    must not be painted with the success colour. */}
                <Badge tone={batchStatusTone(b.status)}>{b.status || "status not reported"}</Badge>
                <span className="text-[11px] text-muted-foreground">{open === b.id ? "Hide" : "Items"}</span>
              </div>
              {open === b.id && (
                <div className="px-4 pb-3 border-t border-border/50 pt-2 space-y-1.5">
                  {/* Three states, only one of which is an empty batch. */}
                  {itemErrors[b.id] ? (
                    <ErrorBox
                      message={`Items could not be read — this is a fetch failure, not an empty batch. (${itemErrors[b.id]})`}
                      onRetry={() => openBatch(b)}
                    />
                  ) : !items[b.id] ? (
                    <p className="text-[11px] text-muted-foreground">Reading items…</p>
                  ) : items[b.id].length === 0 ? (
                    <p className="text-[11px] text-muted-foreground">The server reported no items in this batch.</p>
                  ) : (
                    items[b.id].map((it) => (
                      <div key={it.id} className="flex items-center gap-2 rounded-lg bg-muted/40 px-2.5 py-1.5">
                        <span className="text-[11px] flex-1 min-w-0 truncate">{it.label || it.id || "(unnamed item)"}</span>
                        <Badge tone={batchItemStatusTone(it.status)}>{it.status || "status not reported"}</Badge>
                        {(it.status === "failed" || it.status === "error") && props.threadId && (
                          <button
                            type="button"
                            onClick={() => act(b, () => retryBatchItem(props.threadId as string, b.id, it.id))}
                            disabled={busy}
                            className="p-1 rounded hover:bg-muted disabled:opacity-30"
                            title={busy ? "A request for this batch is still in flight" : "Retry this item"}
                            aria-label={busy ? "Retry in progress" : `Retry item ${it.label || it.id}`}
                          >
                            <RotateCcw className="size-3.5" aria-hidden="true" />
                          </button>
                        )}
                      </div>
                    ))
                  )}
                  {props.threadId && (
                    <div className="flex gap-2 pt-1 flex-wrap">
                      <Btn variant="ghost" disabled={busy} onClick={() => act(b, () => pauseBatch(props.threadId as string, b.id))}>
                        <Pause className="size-3.5" aria-hidden="true" /> Pause
                      </Btn>
                      <Btn variant="ghost" disabled={busy} onClick={() => act(b, () => resumeBatch(props.threadId as string, b.id))}>
                        <Play className="size-3.5" aria-hidden="true" /> Resume
                      </Btn>
                      <Btn variant="danger" disabled={busy} onClick={() => window.confirm("Cancel this whole batch?") && act(b, () => cancelBatch(props.threadId as string, b.id))}>
                        <Ban className="size-3.5" aria-hidden="true" /> Cancel
                      </Btn>
                    </div>
                  )}
                </div>
              )}
            </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
