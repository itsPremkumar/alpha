"use client";

import React, { useEffect, useMemo, useState } from "react";
import {
  listSubagentCatalog,
  fetchLiveSubagentsStrict,
  spawnSubagent,
  cancelSubagent,
  subagentResult,
  subagentStatusTone,
  isKnownSubagentStatus,
  batchStatusTone,
  batchItemStatusTone,
  SubagentDef,
  LiveSubagent,
} from "@/lib/subagents";
import {
  catalogCounts,
  conflictNote,
  countsSentence,
  enabledView,
  groupBySource,
  isKnownSource,
  limitView,
  listView,
  overridesView,
  promptDisclosure,
  rowCapabilityLine,
  sourceMeta,
  titleOf,
} from "@/lib/subagent-catalog-view";
import {
  listBatches,
  batchItems,
  pauseBatch,
  resumeBatch,
  cancelBatch,
  retryBatchItem,
  Batch,
  BatchItem,
} from "@/lib/batches";
import {
  Section,
  EmptyState,
  ErrorBox,
  Notice,
  Btn,
  Badge,
  Field,
  SkeletonList,
  inputCls,
} from "@/components/ui";
import { errMsg } from "@/lib/http";
import { Play, Ban, RefreshCw, Eye, Pause, RotateCcw } from "lucide-react";

export function SubagentsSection(props: { threadId: string | null }) {
  const [catalog, setCatalog] = useState<SubagentDef[]>([]);
  const [live, setLive] = useState<LiveSubagent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [objective, setObjective] = useState("");
  const [result, setResult] = useState<{ id: string; text: string } | null>(
    null,
  );
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
    const [c, l] = await Promise.allSettled([
      listSubagentCatalog(),
      fetchLiveSubagentsStrict(),
    ]);
    if (c.status === "fulfilled") {
      setCatalog(c.value);
      setCatalogError(null);
    } else {
      setCatalog([]);
      setCatalogError(errMsg(c.reason));
    }
    if (l.status === "fulfilled") {
      setLive(l.value);
      setLiveError(null);
    } else {
      setLive([]);
      setLiveError(errMsg(l.reason));
    }
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
        <Field
          label="Start a helper"
          hint="Describe a self-contained job, e.g. “Research three competitors and summarize pricing”. Needs admin rights on the server."
        >
          <div className="flex gap-2">
            <input
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onSpawn()}
              placeholder="What should the helper do?…"
              className={inputCls}
              aria-label="Helper objective"
            />
            <Btn
              onClick={onSpawn}
              disabled={!objective.trim() || spawning}
              title={
                spawning ? "A spawn request is still in flight" : undefined
              }
            >
              <Play className="size-3.5" aria-hidden="true" />{" "}
              {spawning ? "Starting…" : "Start"}
            </Btn>
          </div>
        </Field>
      </div>

      <div>
        {/* The count is a measurement. While the read is in flight, or after it
            failed, it is not zero — nothing was measured. */}
        <h3 className="text-xs font-semibold mb-2">
          Running now (
          {loading ? "…" : liveError ? "count unavailable" : live.length})
        </h3>
        {loading ? (
          <SkeletonList rows={2} />
        ) : liveError ? (
          <ErrorBox
            message={`The helper fleet could not be read — this is a fetch failure, not an empty fleet. (${liveError})`}
            onRetry={load}
          />
        ) : live.length === 0 ? (
          <EmptyState
            title="Nothing running"
            hint="Helpers appear here while the agent works on multi-step tasks."
          />
        ) : (
          <div className="space-y-2">
            {live.map((s) => {
              const busy = busyId === s.id;
              return (
                <div
                  key={s.id}
                  className="rounded-xl border border-border/60 bg-card px-4 py-2.5"
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <p
                      className="text-xs font-semibold font-mono flex-1 min-w-32 break-all"
                      title={s.id || undefined}
                    >
                      {s.id || "id not reported by the server"}
                    </p>
                    {/* The server's status, verbatim, with a tone that never
                      asserts success for a state this build does not know. */}
                    <Badge tone={subagentStatusTone(s.status)}>
                      {s.status || "status not reported"}
                    </Badge>
                    {s.status && !isKnownSubagentStatus(s.status) && (
                      <span className="text-[10px] text-muted-foreground">
                        not a status this build knows — shown exactly as the
                        server reported it
                      </span>
                    )}
                  </div>
                  {s.objective ? (
                    <p className="text-[11px] text-muted-foreground mt-1 line-clamp-2">
                      {s.objective}
                    </p>
                  ) : (
                    <p className="text-[11px] text-muted-foreground mt-1">
                      Objective not reported by the server.
                    </p>
                  )}
                  <div className="flex gap-2 mt-2">
                    <Btn
                      variant="ghost"
                      onClick={() => onViewResult(s)}
                      disabled={readingId === s.id}
                    >
                      <Eye className="size-3.5" aria-hidden="true" />{" "}
                      {readingId === s.id ? "Reading…" : "Result"}
                    </Btn>
                    <Btn
                      variant="danger"
                      onClick={() => onCancel(s)}
                      disabled={busy}
                    >
                      <Ban className="size-3.5" aria-hidden="true" />{" "}
                      {busy ? "Stopping…" : "Stop"}
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
            <p className="text-xs font-semibold flex-1 font-mono break-all">
              Result: {result.id || "unknown helper"}
            </p>
            <Btn variant="ghost" onClick={() => setResult(null)}>
              Close
            </Btn>
          </div>
          <pre className="text-[11px] font-mono whitespace-pre-wrap max-h-80 overflow-y-auto rounded-xl bg-muted/40 p-3">
            {result.text}
          </pre>
        </div>
      )}

      <BatchesBlock threadId={props.threadId} onError={setError} />

      <CatalogPanel
        catalog={catalog}
        loading={loading}
        error={catalogError}
        onRetry={load}
      />
    </Section>
  );
}

/* ------------------------------------------------------------------ *
 * Catalog panel — every field the server sends, per definition.
 *
 * It was a two-column grid of cards showing four things: name, an on/off
 * badge, a two-line description clamp, and `model · source`. The other eleven
 * fields of `SubagentResponse` were mapped by the client and then never
 * rendered — the tool allowlist, the deny-list, granted skills, the turn and
 * timeout ceilings, `display_name`, `conflict`, `config_overrides`, and the
 * system prompt.
 *
 * That last one is why this is a detail pane rather than a longer card. An
 * operator choosing a subagent needs to answer "what may it call, and what is
 * it told to do", and both answers are the fields that were being dropped. All
 * sentences come from `lib/subagent-catalog-view.ts` so this markup holds no
 * claim of its own.
 * ------------------------------------------------------------------ */

function CatalogPanel(props: {
  catalog: SubagentDef[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  const { catalog, loading, error, onRetry } = props;
  const groups = useMemo(() => groupBySource(catalog), [catalog]);
  const counts = useMemo(() => catalogCounts(catalog), [catalog]);

  /**
   * Selection defaults to the first definition and is *reset* when the catalog
   * changes, so a deleted row cannot leave the pane showing a name the server
   * no longer lists. Keyed on the measured total plus the first name: two
   * catalogs of the same size really can differ (a managed definition replacing
   * a builtin), and a pane left on the old row would be claiming a definition
   * the read did not return.
   */
  const [selected, setSelected] = useState<string>("");
  const catalogKey = useMemo(
    () => `${counts.total}:${catalog.map((c) => c.name).join("|")}`,
    [counts.total, catalog],
  );
  const selectedDef = useMemo(
    () =>
      catalog.find((c) => `${c.source}::${c.name}` === selected) ??
      catalog[0] ??
      null,
    [catalog, selected],
  );

  useEffect(() => {
    if (!catalog.length) {
      setSelected("");
      return;
    }
    setSelected((cur) =>
      catalog.some((c) => `${c.source}::${c.name}` === cur)
        ? cur
        : `${catalog[0].source}::${catalog[0].name}`,
    );
  }, [catalogKey, catalog]);

  const [query, setQuery] = useState("");
  const visibleGroups = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return groups;
    return groups
      .map((g) => ({
        ...g,
        items: g.items.filter((c) =>
          `${c.name} ${c.description}`.toLowerCase().includes(q),
        ),
      }))
      .filter((g) => g.items.length > 0);
  }, [groups, query]);
  /** How many definitions the filter hid, so a short list is never read as the whole catalog. */
  const hidden =
    counts.total - visibleGroups.reduce((n, g) => n + g.items.length, 0);

  return (
    <div>
      <div className="flex items-center gap-2 flex-wrap mb-2">
        <h3 className="text-xs font-semibold">Available subagents</h3>
        {/* The count is a measurement. While the read is in flight, or after it
            failed, it is not zero — nothing was measured. */}
        <span className="text-[10px] text-muted-foreground">
          {loading
            ? "reading…"
            : error
              ? "count unavailable"
              : countsSentence(counts)}
        </span>
      </div>

      {error ? (
        <ErrorBox
          message={`The subagent catalog could not be read — this is a fetch failure, not an empty catalog. (${error})`}
          onRetry={onRetry}
        />
      ) : catalog.length === 0 && !loading ? (
        <EmptyState
          title="No catalog"
          hint="The server did not return a subagent catalog."
        />
      ) : (
        <>
          <div className="mb-2 flex items-center gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter by name or purpose…"
              className={inputCls}
              aria-label="Filter subagent definitions"
            />
            {/* A filtered list that reads as the catalog is the same failure as a
                count with no evidence: the reader cannot tell "this one matched"
                from "this is everything". */}
            {hidden > 0 && (
              <span className="text-[10px] text-muted-foreground whitespace-nowrap">
                {hidden} of {counts.total} hidden by the filter
              </span>
            )}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)] gap-3">
            {/* ---- the list: names are the evidence ---- */}
            <div className="rounded-2xl border border-border/60 bg-card p-2 max-h-[32rem] overflow-y-auto">
              {loading && catalog.length === 0 ? (
                <SkeletonList rows={3} />
              ) : visibleGroups.length === 0 ? (
                <p className="text-[11px] text-muted-foreground px-2 py-3">
                  No definition matches “{query.trim()}”. {counts.total} are in
                  the catalog.
                </p>
              ) : (
                visibleGroups.map((g) => (
                  <div key={g.source} className="mb-2 last:mb-0">
                    <div className="flex items-center gap-1.5 px-2 py-1">
                      <Badge tone={g.tone}>{g.label}</Badge>
                      <span className="text-[10px] text-muted-foreground">
                        {g.items.length}
                      </span>
                    </div>
                    <div className="space-y-1">
                      {g.items.map((c) => {
                        const key = `${c.source}::${c.name}`;
                        const en = enabledView(c.enabled);
                        const on =
                          selectedDef !== null &&
                          `${selectedDef.source}::${selectedDef.name}` === key;
                        return (
                          <button
                            key={key}
                            type="button"
                            onClick={() => setSelected(key)}
                            aria-pressed={on}
                            className={`w-full text-left rounded-xl px-2.5 py-1.5 border transition-colors ${
                              on
                                ? "border-primary/60 bg-primary/5"
                                : "border-transparent hover:border-border/60"
                            }`}
                          >
                            <div className="flex items-center gap-1.5">
                              <span className="text-[11px] font-mono font-semibold truncate flex-1">
                                {c.name || "(unnamed)"}
                              </span>
                              {/* An unreported flag gets a written label, never the
                                  green "on" this badge used to draw for absent. */}
                              {en.tone === "muted" ? (
                                <span className="text-[9px] text-muted-foreground px-1.5 py-0.5 rounded-full bg-muted whitespace-nowrap">
                                  {en.label}
                                </span>
                              ) : (
                                <Badge
                                  tone={en.tone === "green" ? "green" : "gray"}
                                >
                                  {en.label}
                                </Badge>
                              )}
                              {c.conflict && (
                                <span
                                  className="text-[9px] text-amber-600 dark:text-amber-400"
                                  title="Another definition uses this name"
                                >
                                  ⚠
                                </span>
                              )}
                            </div>
                            <p className="text-[10px] text-muted-foreground truncate mt-0.5">
                              {rowCapabilityLine(c)}
                            </p>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ))
              )}
            </div>

            {/* ---- the detail: everything the list could not carry ---- */}
            <div className="rounded-2xl border border-border/60 bg-card p-4 min-h-64">
              {selectedDef ? (
                <SubagentDetail def={selectedDef} />
              ) : (
                <p className="text-[11px] text-muted-foreground">
                  Select a subagent to see its full definition.
                </p>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Chips(props: { values: string[]; tone?: "gray" | "amber" | "blue" }) {
  if (!props.values.length) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-1.5">
      {props.values.map((v) => (
        <span
          key={v}
          className={`text-[10px] font-mono px-1.5 py-0.5 rounded-md ${
            props.tone === "amber"
              ? "bg-amber-500/10 text-amber-700 dark:text-amber-400"
              : props.tone === "blue"
                ? "bg-primary/10 text-primary"
                : "bg-muted text-muted-foreground"
          }`}
        >
          {v}
        </span>
      ))}
    </div>
  );
}

function SubagentDetail({ def }: { def: SubagentDef }) {
  const [showPrompt, setShowPrompt] = useState(false);
  const { title, fromDisplayName } = titleOf(def);
  const src = sourceMeta(def.source);
  const en = enabledView(def.enabled);
  const tools = listView(def.tools, "allowlist");
  const denied = listView(def.disallowedTools, "deny-list");
  const skills = listView(def.skills, "skills");
  const prompt = promptDisclosure(def.systemPrompt);
  const overrides = overridesView(def.configOverrides);
  const conflict = conflictNote(def.conflict);

  return (
    <div>
      <div className="flex items-start gap-2 flex-wrap">
        <div className="min-w-0 flex-1">
          <h4 className="text-sm font-semibold font-mono break-all">{title}</h4>
          {fromDisplayName && (
            <p className="text-[10px] text-muted-foreground font-mono">
              display_name · name {def.name}
            </p>
          )}
        </div>
        <div className="flex items-center gap-1.5 flex-wrap">
          <Badge tone={src.tone}>{src.label}</Badge>
          {en.tone === "muted" ? (
            <span
              className="text-[10px] text-muted-foreground px-2 py-0.5 rounded-full bg-muted"
              title={en.reason}
            >
              {en.label}
            </span>
          ) : (
            <Badge
              tone={en.tone === "green" ? "green" : "gray"}
              title={en.reason}
            >
              {en.label}
            </Badge>
          )}
        </div>
      </div>

      <p className="text-[10px] text-muted-foreground mt-1">{src.blurb}</p>
      {en.tone === "muted" && (
        <p className="text-[10px] text-muted-foreground mt-1">{en.reason}</p>
      )}
      {conflict && (
        <p className="text-[11px] text-amber-700 dark:text-amber-400 mt-2 rounded-lg bg-amber-500/10 px-2.5 py-1.5">
          {conflict}
        </p>
      )}

      {/* Purpose, in full. The old card clamped this to two lines, which hid
          the "when NOT to use this" half of every builtin description — the
          part that decides whether a delegation is worth its context cost. */}
      <p className="text-[11px] mt-3 whitespace-pre-wrap">
        {def.description ||
          "The server reported no description for this definition."}
      </p>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-4">
        <Detail
          label="Model"
          value={def.model || "not reported"}
          note={
            def.model === "inherit"
              ? "the dispatching agent's model"
              : undefined
          }
        />
        <Detail label="Turn ceiling" value={limitView(def.maxTurns, "turns")} />
        <Detail label="Timeout" value={limitView(def.timeoutSeconds, "s")} />
        <Detail
          label="Editable here"
          value={def.editable ? "yes" : "no"}
          note={def.editable ? undefined : "read-only source"}
        />
      </div>

      <div className="mt-4 space-y-3">
        <div>
          <p className="text-[11px] font-semibold">
            Tools{" "}
            <span className="font-normal text-muted-foreground">
              · {tools.summary}
            </span>
          </p>
          <Chips values={tools.chips} />
        </div>
        {denied.state !== "absent" && (
          <div>
            <p className="text-[11px] font-semibold">
              Blocked tools{" "}
              <span className="font-normal text-muted-foreground">
                · {denied.summary}
              </span>
            </p>
            <Chips values={denied.chips} tone="amber" />
          </div>
        )}
        <div>
          <p className="text-[11px] font-semibold">
            Skills{" "}
            <span className="font-normal text-muted-foreground">
              · {skills.summary}
            </span>
          </p>
          <Chips values={skills.chips} tone="blue" />
        </div>
        <div>
          <p className="text-[11px] font-semibold">
            config.yaml overrides{" "}
            <span className="font-normal text-muted-foreground">
              · {overrides.rows.length} set
            </span>
          </p>
          {overrides.rows.length > 0 && (
            <div className="mt-1.5 space-y-0.5">
              {overrides.rows.map((r) => (
                <p key={r.key} className="text-[10px] font-mono">
                  {r.key}:{" "}
                  <span className="text-muted-foreground">{r.value}</span>
                </p>
              ))}
            </div>
          )}
          <p className="text-[10px] text-muted-foreground mt-1">
            {overrides.note}
          </p>
        </div>
        <div>
          <p className="text-[11px] font-semibold">System prompt</p>
          {prompt.present ? (
            <>
              <div className="flex items-center gap-2 mt-1">
                <Btn variant="ghost" onClick={() => setShowPrompt((v) => !v)}>
                  {showPrompt
                    ? "Hide prompt"
                    : `Show prompt (${def.systemPrompt?.length ?? 0} chars)`}
                </Btn>
                <span className="text-[10px] text-muted-foreground">
                  admin-visible
                </span>
              </div>
              {showPrompt && (
                <pre className="text-[10px] font-mono whitespace-pre-wrap max-h-72 overflow-y-auto rounded-xl bg-muted/40 p-3 mt-2">
                  {def.systemPrompt}
                </pre>
              )}
            </>
          ) : (
            <p className="text-[10px] text-muted-foreground mt-1">
              {prompt.reason}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function Detail(props: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-xl bg-muted/40 px-2.5 py-2">
      <p className="text-[9px] uppercase tracking-wide text-muted-foreground">
        {props.label}
      </p>
      <p className="text-[11px] font-mono font-semibold break-all mt-0.5">
        {props.value}
      </p>
      {props.note && (
        <p className="text-[9px] text-muted-foreground mt-0.5">{props.note}</p>
      )}
    </div>
  );
}

function BatchesBlock(props: {
  threadId: string | null;
  onError: (m: string) => void;
}) {
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
  const [itemErrors, setItemErrors] = useState<Record<string, string | null>>(
    {},
  );
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
        <EmptyState
          title="Pick a chat first"
          hint="Batches belong to a conversation — select one to see its parallel work packages."
        />
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <h3 className="text-xs font-semibold flex-1">
          Work batches (
          {loading ? "…" : listError ? "count unavailable" : batches.length})
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
        <EmptyState
          title="No batches in this chat"
          hint="When the agent splits big work into parallel packages, they appear here with per-item controls."
        />
      ) : (
        <div className="space-y-2">
          {batches.map((b) => {
            const busy = busyBatch === b.id;
            return (
              <div
                key={b.id}
                className="rounded-xl border border-border/60 bg-card"
              >
                <div
                  className="flex items-center gap-2 px-4 py-2.5 cursor-pointer"
                  onClick={() => openBatch(b)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === "Enter" && openBatch(b)}
                >
                  <p
                    className="text-xs font-mono flex-1 min-w-24 break-all"
                    title={b.id || undefined}
                  >
                    {b.id || "id not reported by the server"}
                  </p>
                  {/* Same rule as a subagent: a status this build does not name
                    must not be painted with the success colour. */}
                  <Badge tone={batchStatusTone(b.status)}>
                    {b.status || "status not reported"}
                  </Badge>
                  <span className="text-[11px] text-muted-foreground">
                    {open === b.id ? "Hide" : "Items"}
                  </span>
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
                      <p className="text-[11px] text-muted-foreground">
                        Reading items…
                      </p>
                    ) : items[b.id].length === 0 ? (
                      <p className="text-[11px] text-muted-foreground">
                        The server reported no items in this batch.
                      </p>
                    ) : (
                      items[b.id].map((it) => (
                        <div
                          key={it.id}
                          className="flex items-center gap-2 rounded-lg bg-muted/40 px-2.5 py-1.5"
                        >
                          <span className="text-[11px] flex-1 min-w-0 truncate">
                            {it.label || it.id || "(unnamed item)"}
                          </span>
                          <Badge tone={batchItemStatusTone(it.status)}>
                            {it.status || "status not reported"}
                          </Badge>
                          {(it.status === "failed" || it.status === "error") &&
                            props.threadId && (
                              <button
                                type="button"
                                onClick={() =>
                                  act(b, () =>
                                    retryBatchItem(
                                      props.threadId as string,
                                      b.id,
                                      it.id,
                                    ),
                                  )
                                }
                                disabled={busy}
                                className="p-1 rounded hover:bg-muted disabled:opacity-30"
                                title={
                                  busy
                                    ? "A request for this batch is still in flight"
                                    : "Retry this item"
                                }
                                aria-label={
                                  busy
                                    ? "Retry in progress"
                                    : `Retry item ${it.label || it.id}`
                                }
                              >
                                <RotateCcw
                                  className="size-3.5"
                                  aria-hidden="true"
                                />
                              </button>
                            )}
                        </div>
                      ))
                    )}
                    {props.threadId && (
                      <div className="flex gap-2 pt-1 flex-wrap">
                        <Btn
                          variant="ghost"
                          disabled={busy}
                          onClick={() =>
                            act(b, () =>
                              pauseBatch(props.threadId as string, b.id),
                            )
                          }
                        >
                          <Pause className="size-3.5" aria-hidden="true" />{" "}
                          Pause
                        </Btn>
                        <Btn
                          variant="ghost"
                          disabled={busy}
                          onClick={() =>
                            act(b, () =>
                              resumeBatch(props.threadId as string, b.id),
                            )
                          }
                        >
                          <Play className="size-3.5" aria-hidden="true" />{" "}
                          Resume
                        </Btn>
                        <Btn
                          variant="danger"
                          disabled={busy}
                          onClick={() =>
                            window.confirm("Cancel this whole batch?") &&
                            act(b, () =>
                              cancelBatch(props.threadId as string, b.id),
                            )
                          }
                        >
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
