"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  RECONCILE_VERDICTS,
  SIDE_EFFECT_LEVELS,
  SIDE_EFFECT_STATUSES,
  ReconcileResult,
  SIDE_EFFECT_LIMIT_DEFAULT,
  SideEffectEntry,
  SideEffectList,
  SideEffectSummary,
  fetchSideEffect,
  fetchSideEffectList,
  fetchSideEffectSummary,
  failureText,
  reconcileSideEffect,
} from "@/lib/side-effects";
import { Badge, Btn, EmptyState, ErrorBox, Notice, Section, SkeletonList, StatCard, inputCls } from "@/components/ui";
import { relTime } from "@/lib/time";
import { RefreshCw, ScrollText } from "lucide-react";

/**
 * The effect journal and reconciliation console — the `effects` workspace view.
 *
 * It answers one question: **which external effects is Alpha unsure about, and
 * what did an operator decide about them?** Everything it renders is the
 * server's claim; the panel adds no verdict of its own.
 *
 * The rules this surface is built around, each with a tempting wrong reading:
 *
 * | Server says | Panel shows |
 * | --- | --- |
 * | `reported: false` | the server's reason, and *not reported* for every count |
 * | `oldest_unknown_age_seconds: null` while reported | *no unknown entry waiting* |
 * | the same `null` while unreported | *not reported* — never `0s` |
 * | `entries: null` | "the Gateway sent no entry list" — not "no effects" |
 * | `entries: []` | "no entries match this filter" |
 * | a rejected read | that read's reason, beside the data the other read returned |
 * | `reconcilable: null` | *reconcilability not reported*, and **no button** |
 * | `reconcilable: false` | the status, and no button implying a pending verdict |
 * | a 403 on reconcile | the server's own sentence, verbatim |
 * | `reopened`/`escalated: null` after a submit | *not reported* — never `false` |
 * | an unknown status/level string | rendered verbatim in a grey badge |
 *
 * Four design decisions carry most of the weight:
 *
 * 1. **The two reads fail independently.** Summary and list go through
 *    `Promise.allSettled`, so a summary that 503s cannot blank a list that
 *    answered — they answer opposite questions ("how much is there" vs "which
 *    rows") and a single all-or-nothing promise would hide one behind the
 *    other.
 * 2. **A reconcile re-reads the entry before it offers a form.** The row in the
 *    table may be seconds stale; offering a verdict on an entry somebody else
 *    already settled is how a 409 gets presented as the operator's mistake. The
 *    fresh `reconcilable` decides, and `null` declines the form rather than
 *    guessing.
 * 3. **The submit is opt-in and the server's answer is the only success.**
 *    An unchecked checkbox gates it, and the panel renders the *response's*
 *    `reopened` / `escalated` — never an inference from which button was
 *    pressed.
 * 4. **Authorisation is never inferred here.** There is no client-side admin
 *    check: the button appears for every caller and a member's 403 is rendered
 *    verbatim through `failureText`, because "you may not" is an answer the
 *    server owns.
 *
 * Coverage: `src/lib/side-effects.test.mjs` (routes, verbs, the null-preserving
 * mappers, the local refusals) and `src/lib/effects-view.test.mjs` (the pins
 * above, against this file).
 */

/** Badge tone for a status. An unrecognised status is grey, never a known colour. */
function statusTone(status: string): "green" | "amber" | "red" | "blue" | "cyan" | "gray" {
  switch (status) {
    case "completed":
    case "reconciled":
      return "green";
    case "unknown":
      return "amber";
    case "failed":
      return "red";
    case "in_flight":
      return "blue";
    case "pending":
      return "cyan";
    default:
      return "gray";
  }
}

/** Badge tone for a risk level. `destructive` is the only red by default. */
function levelTone(level: string): "gray" | "green" | "blue" | "amber" | "red" {
  switch (level) {
    case "read_only":
      return "gray";
    case "low_risk":
      return "green";
    case "moderate":
      return "blue";
    case "high_risk":
      return "amber";
    case "destructive":
      return "red";
    default:
      return "gray";
  }
}

/** A count, with its absence named rather than zeroed. */
function countText(count: number | null): string {
  return count === null ? "not reported" : String(count);
}

/**
 * The oldest-unknown age, which has two different `null`s.
 *
 * The server sends `null` when nothing is waiting; it sends nothing at all (or
 * fails to report) when it could not answer. The age is only allowed to say
 * "no unknown entry waiting" in the first case.
 */
function ageText(summary: SideEffectSummary): string {
  if (summary.oldest_unknown_age_seconds !== null) return `${Math.round(summary.oldest_unknown_age_seconds)}s waiting`;
  return summary.reported ? "no unknown entry waiting" : "not reported";
}

/** One entry's row. */
function EntryRow(props: {
  entry: SideEffectEntry;
  selected: boolean;
  onOpen: (toolCallId: string) => void;
}) {
  const entry = props.entry;
  return (
    <div className="px-3 py-2 space-y-1.5" data-effects-entry={entry.tool_call_id}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[11px] font-medium font-mono">{entry.tool_call_id || "id not reported"}</span>
          <Badge tone="gray">{entry.tool_name || "tool not reported"}</Badge>
          <Badge tone={statusTone(entry.status)} title="The ledger's own status string">
            {entry.status === "" ? "status not reported" : entry.status}
          </Badge>
          <Badge tone={levelTone(entry.level)} title="Risk level recorded when the effect was declared">
            {entry.level === "" ? "level not reported" : entry.level}
          </Badge>
          {entry.verdict !== null && entry.verdict !== "" && <Badge tone="purple">verdict: {entry.verdict}</Badge>}
        </span>
        <Btn variant={props.selected ? "primary" : "ghost"} onClick={() => props.onOpen(entry.tool_call_id)}>
          Inspect
        </Btn>
      </div>
      <div className="text-[10px] text-muted-foreground flex items-center gap-2 flex-wrap">
        <span>{entry.created_at === null ? "created not reported" : relTime(entry.created_at)}</span>
        <span>{entry.thread_id === null || entry.thread_id === "" ? "no thread reported" : `thread ${entry.thread_id}`}</span>
        <span>{entry.run_id === null || entry.run_id === "" ? "no run reported" : `run ${entry.run_id}`}</span>
        <span>{entry.user_id === null || entry.user_id === "" ? "no owner reported" : `owner ${entry.user_id}`}</span>
        <span>{entry.needs_reconciliation === true ? "needs reconciliation" : entry.needs_reconciliation === false ? "no reconciliation flagged" : "reconciliation need not reported"}</span>
      </div>
      <div className="text-[10px] text-muted-foreground font-mono break-all">
        args {entry.arguments_digest ?? "digest not reported"} · result {entry.result_digest ?? "digest not reported"}
      </div>
    </div>
  );
}

/** The fresh detail + reconcile form for one entry. */
function ReconcilePanel(props: {
  entry: SideEffectEntry | null;
  loading: boolean;
  error: string | null;
  verdict: string;
  onVerdictChange: (value: string) => void;
  reason: string;
  onReasonChange: (value: string) => void;
  acknowledged: boolean;
  onAcknowledgeChange: (value: boolean) => void;
  submitting: boolean;
  submitError: string | null;
  result: ReconcileResult | null;
  onSubmit: () => void;
  onClose: () => void;
}) {
  const { entry, loading, error } = props;

  if (loading) {
    return (
      <div className="rounded-xl border border-border/60 p-3" data-effects-detail="loading">
        <SkeletonList rows={2} />
      </div>
    );
  }
  if (error !== null) {
    // The server's own sentence: a 404 "not recorded in this ledger", a 403
    // "reconciliation requires an administrator …", or a 503 store reason.
    return <ErrorBox message={error} />;
  }
  if (entry === null) return null;

  const canReconcile = entry.reconcilable === true;
  const reasonTooLong = props.reason.length > 2000;
  const reasonEmpty = props.reason.length < 1;
  const submitDisabled = props.submitting || !props.acknowledged || reasonEmpty;

  return (
    <div className="rounded-xl border border-border/60 p-3 space-y-3" data-effects-detail={entry.tool_call_id}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="text-xs font-semibold flex items-center gap-1.5">
          <ScrollText className="size-3.5" />
          {entry.tool_call_id || "entry"}
        </span>
        <Btn variant="ghost" onClick={props.onClose}>
          Close
        </Btn>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
        <p>
          <span className="text-muted-foreground">status:</span> {entry.status === "" ? "not reported" : entry.status}
        </p>
        <p>
          <span className="text-muted-foreground">level:</span> {entry.level === "" ? "not reported" : entry.level}
        </p>
        <p>
          <span className="text-muted-foreground">tool:</span> {entry.tool_name || "not reported"}
        </p>
        <p>
          <span className="text-muted-foreground">attempt:</span> {countText(entry.attempt)}
        </p>
        <p>
          <span className="text-muted-foreground">owner:</span> {entry.user_id === null || entry.user_id === "" ? "not reported" : entry.user_id}
        </p>
        <p>
          <span className="text-muted-foreground">verdict:</span> {entry.verdict ?? "not reported"}
        </p>
        <p>
          <span className="text-muted-foreground">lease expires:</span> {entry.lease_expires_at ?? "not reported"}
        </p>
        <p>
          <span className="text-muted-foreground">updated:</span> {entry.updated_at === null ? "not reported" : relTime(entry.updated_at)}
        </p>
      </div>
      <p className="text-[11px]">{entry.detail === null || entry.detail === "" ? "no reason recorded on this entry" : entry.detail}</p>
      <p className="text-[10px] text-muted-foreground font-mono break-all">
        args {entry.arguments_digest ?? "digest not reported"} · result {entry.result_digest ?? "digest not reported"} — this API carries digests
        only; arguments and results never cross it.
      </p>

      {props.result !== null && (
        <Notice
          tone="success"
          message={[
            `Recorded ${props.result.verdict ?? "a verdict"} on ${props.result.tool_call_id || entry.tool_call_id}.`,
            `status now: ${props.result.status ?? "not reported"}.`,
            `reopened: ${props.result.reopened === null ? "not reported" : props.result.reopened ? "yes — the entry is back to unknown" : "no"}.`,
            `escalated: ${props.result.escalated === null ? "not reported" : props.result.escalated ? "yes" : "no"}.`,
          ].join(" ")}
        />
      )}
      {props.submitError !== null && <Notice tone="warn" message={props.submitError} />}

      {entry.reconcilable === false ? (
        <Notice
          tone="neutral"
          message={`The server reports this entry cannot take a verdict (status: ${entry.status === "" ? "not reported" : entry.status}). A settled or in-flight entry is refused with 409; this panel does not offer a form the ledger would reject.`}
        />
      ) : !canReconcile ? (
        <p className="text-[11px] text-muted-foreground">reconcilability not reported — the Gateway sent no `reconcilable` claim for this entry, so no verdict is offered.</p>
      ) : (
        <div className="space-y-2 border-t border-border/50 pt-3">
          <p className="text-[11px] text-muted-foreground">
            Record what the external system actually did. This writes a verdict to the ledger; it never cancels, resumes or replays a run.
            The server decides whether you may — a refusal is shown as it was written.
          </p>
          <label className="block space-y-1">
            <span className="text-[11px] font-semibold">Verdict</span>
            <select className={inputCls} value={props.verdict} onChange={(e) => props.onVerdictChange(e.target.value)}>
              {RECONCILE_VERDICTS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-[11px] font-semibold">Why this verdict — what was checked to establish it</span>
            <textarea className={`${inputCls} min-h-20`} value={props.reason} onChange={(e) => props.onReasonChange(e.target.value)} rows={3} />
            <span className={`block text-[11px] font-normal ${reasonTooLong || reasonEmpty ? "text-amber-700 dark:text-amber-300" : "text-muted-foreground"}`}>
              {props.reason.length}/2000 characters — the server requires 1–2000 and stores this as the entry's reason.
            </span>
          </label>
          <label className="flex items-start gap-2 text-[11px]">
            <input type="checkbox" className="mt-0.5" checked={props.acknowledged} onChange={(e) => props.onAcknowledgeChange(e.target.checked)} />
            <span>
              I understand this records a permanent verdict on a ledger row. An <code>undetermined</code> verdict reopens the entry rather than
              settling it.
            </span>
          </label>
          <Btn onClick={props.onSubmit} disabled={submitDisabled} title="Record the verdict">
            {props.submitting ? "Recording…" : "Record verdict"}
          </Btn>
        </div>
      )}
    </div>
  );
}

export function EffectsSection() {
  const [summary, setSummary] = useState<SideEffectSummary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [list, setList] = useState<SideEffectList | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // The default is the reconciliation queue: the one question this view exists
  // to answer is "what is unaccounted for", and `unknown` is that set.
  const [statusFilter, setStatusFilter] = useState<string>("unknown");
  const [levelFilter, setLevelFilter] = useState<string>("");
  const [limit, setLimit] = useState<number>(SIDE_EFFECT_LIMIT_DEFAULT);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [freshEntry, setFreshEntry] = useState<SideEffectEntry | null>(null);
  const [freshError, setFreshError] = useState<string | null>(null);
  const [freshLoading, setFreshLoading] = useState(false);

  const [verdict, setVerdict] = useState<string>("confirmed_success");
  const [reason, setReason] = useState<string>("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [result, setResult] = useState<ReconcileResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    // Two reads, two outcomes: a summary that cannot be produced must not
    // blank a list that answered, or vice versa.
    const [summaryResult, listResult] = await Promise.allSettled([
      fetchSideEffectSummary(),
      fetchSideEffectList({ status: statusFilter === "" ? undefined : statusFilter, level: levelFilter === "" ? undefined : levelFilter, limit }),
    ]);
    if (summaryResult.status === "fulfilled") {
      setSummary(summaryResult.value);
      setSummaryError(null);
    } else {
      setSummaryError(failureText(summaryResult.reason));
    }
    if (listResult.status === "fulfilled") {
      setList(listResult.value);
      setListError(null);
    } else {
      setListError(failureText(listResult.reason));
    }
    setLoading(false);
  }, [statusFilter, levelFilter, limit]);

  useEffect(() => {
    void load();
  }, [load]);

  const openEntry = useCallback(async (toolCallId: string) => {
    setSelectedId(toolCallId);
    setFreshEntry(null);
    setFreshError(null);
    setResult(null);
    setSubmitError(null);
    setReason("");
    setAcknowledged(false);
    setFreshLoading(true);
    try {
      // A *fresh* read: the table row may be seconds old, and offering a
      // verdict on an entry somebody already settled is how a 409 gets
      // blamed on the operator.
      setFreshEntry(await fetchSideEffect(toolCallId));
    } catch (err) {
      setFreshError(failureText(err));
    } finally {
      setFreshLoading(false);
    }
  }, []);

  const closeEntry = useCallback(() => {
    setSelectedId(null);
    setFreshEntry(null);
    setFreshError(null);
    setResult(null);
    setSubmitError(null);
  }, []);

  const submit = useCallback(async () => {
    if (selectedId === null) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const confirmed = await reconcileSideEffect(selectedId, verdict, reason);
      setResult(confirmed);
      setReason("");
      setAcknowledged(false);
      // Re-read everything the response cannot speak for: the response is one
      // row's claim, the queue and the counts are the rest of the ledger.
      try {
        setFreshEntry(await fetchSideEffect(selectedId));
      } catch (err) {
        setFreshError(failureText(err));
      }
      void load();
    } catch (err) {
      // A 403 or a 409 lands here with the server's own words; `errMsg` would
      // replace both with a generic sentence and lose the answer.
      setSubmitError(failureText(err));
    } finally {
      setSubmitting(false);
    }
  }, [selectedId, verdict, reason, load]);

  const entries = list?.entries ?? null;

  return (
    <Section
      title="Effect journal"
      hint="Every external effect Alpha recorded, and the verdict an operator gave the ones it could not account for. Digests only — arguments and results never cross this API. Recording a verdict never cancels, resumes or replays a run."
      actions={
        <Btn onClick={() => void load()} disabled={loading} title="Re-read the summary and the list">
          <RefreshCw className="size-3.5" />
          {loading ? "Refreshing…" : "Refresh"}
        </Btn>
      }
    >
      <div className="space-y-4" data-effects-section="journal">
        {summaryError !== null && <Notice tone="warn" message={`The summary read failed: ${summaryError}. The list below is unaffected.`} />}
        {listError !== null && <Notice tone="warn" message={`The entry list read failed: ${listError}. The summary above is unaffected.`} />}

        {summary === null && summaryError === null ? (
          <SkeletonList rows={2} />
        ) : summary === null ? (
          <ErrorBox message={summaryError ?? "the summary read failed"} onRetry={() => void load()} />
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard label={`recorded effects · scope ${summary.scope ?? "not reported"}`} value={countText(summary.total)} />
            <StatCard label="awaiting a verdict" value={countText(summary.unknown)} />
            <StatCard label="oldest unknown" value={ageText(summary)} />
            <StatCard label="counts by level" value={summary.by_level === null ? "not reported" : `${Object.keys(summary.by_level).length} levels`} />
          </div>
        )}

        {summary !== null && !summary.reported && (
          <Notice tone="warn" message="The Gateway did not confirm this summary was reported, so every count above reads 'not reported' rather than 0." />
        )}

        <div className="flex items-end gap-2 flex-wrap">
          <label className="block space-y-1">
            <span className="text-[11px] font-semibold">Status</span>
            <select className={`${inputCls} w-44`} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">all statuses</option>
              {SIDE_EFFECT_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-[11px] font-semibold">Level</span>
            <select className={`${inputCls} w-44`} value={levelFilter} onChange={(e) => setLevelFilter(e.target.value)}>
              <option value="">all levels</option>
              {SIDE_EFFECT_LEVELS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-[11px] font-semibold">Page size</span>
            <select className={`${inputCls} w-28`} value={String(limit)} onChange={(e) => setLimit(Number(e.target.value))}>
              {[25, 50, 100, 250, 500].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        </div>

        {selectedId !== null && (
          <ReconcilePanel
            entry={freshEntry}
            loading={freshLoading}
            error={freshError}
            verdict={verdict}
            onVerdictChange={setVerdict}
            reason={reason}
            onReasonChange={setReason}
            acknowledged={acknowledged}
            onAcknowledgeChange={setAcknowledged}
            submitting={submitting}
            submitError={submitError}
            result={result}
            onSubmit={() => void submit()}
            onClose={closeEntry}
          />
        )}

        <div className="rounded-xl border border-border/60">
          <div className="px-3 py-2 border-b border-border/60 flex items-center justify-between gap-2 flex-wrap">
            <span className="text-xs font-semibold">Entries</span>
            <span className="text-[10px] text-muted-foreground">
              {list === null
                ? loading
                  ? "reading…"
                  : "not read"
                : `${countText(list.count)} shown · ${list.order ?? "order not reported"} · up to ${limit} per page`}
            </span>
          </div>
          {list === null && listError === null ? (
            <div className="p-3">
              <SkeletonList rows={3} />
            </div>
          ) : list === null ? (
            <div className="p-3">
              <EmptyState
                title="The entry list could not be read"
                hint="This is a failed read, not an empty ledger. The reason is in the notice above."
              />
            </div>
          ) : entries === null ? (
            <div className="p-3">
              <EmptyState title="The Gateway sent no entry list" hint="This is not a claim that no effects were recorded — no list arrived at all." />
            </div>
          ) : entries.length === 0 ? (
            <div className="p-3">
              <EmptyState
                title="No entries match this filter"
                hint="The Gateway answered with an empty list. Widen the status or level filter to see the rest of the journal."
              />
            </div>
          ) : (
            <div className="divide-y divide-border/40">
              {entries.map((entry) => (
                <EntryRow key={entry.tool_call_id} entry={entry} selected={entry.tool_call_id === selectedId} onOpen={(id) => void openEntry(id)} />
              ))}
            </div>
          )}
          {entries !== null && entries.length > 0 && list !== null && list.count !== null && list.count > entries.length && (
            <p className="px-3 py-2 text-[10px] text-muted-foreground border-t border-border/40">
              Showing {entries.length} of {list.count} — the page is bounded by the limit above, not by the ledger's total.
            </p>
          )}
        </div>

        <p className="text-[10px] text-muted-foreground">
          read-only routes GET /side-effects/summary and GET /side-effects · POST /side-effects/&#123;tool_call_id&#125;/reconcile · scope shown
          above comes from the server: a member sees their own entries, an admin sees every scope. Nothing on this screen decides who you are.
        </p>
      </div>
    </Section>
  );
}
