"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  HARNESS_APPROVAL_KINDS,
  HarnessBudgets,
  HarnessBundleDetail,
  HarnessBundleList,
  HarnessBundleRow,
  HarnessBudgetRow,
  HarnessCapability,
  HarnessDiffDetail,
  HarnessExperienceList,
  HarnessExperienceRow,
  HarnessPromotionEvent,
  HarnessPromotionHistory,
  HarnessRunDetail,
  HarnessRunList,
  HarnessRunRow,
  HarnessStatus,
  activateHarnessBundle,
  approveHarnessCandidate,
  cancelHarnessRun,
  failureText,
  fetchHarnessBudgets,
  fetchHarnessBundle,
  fetchHarnessBundleDiff,
  fetchHarnessBundles,
  fetchHarnessExperiences,
  fetchHarnessPromotions,
  fetchHarnessRun,
  fetchHarnessRuns,
  fetchHarnessStatus,
  rejectHarnessCandidate,
  resumeHarnessRun,
  rollbackHarnessBundle,
  startHarnessRun,
} from "@/lib/harness-lab";
import { Badge, Block, Btn, DetailRow, EmptyState, ErrorBox, LoadingRows, Notice, Section, SkeletonList, StatCard, UnavailableNotice, inputCls } from "@/components/ui";
import { relTime } from "@/lib/time";
import { FlaskConical, RefreshCw } from "lucide-react";

/**
 * The Harness Lab — the `harness-lab` workspace view.
 *
 * It answers one question: **is this installation's harness able to improve
 * itself right now, and if not, exactly what is missing?** Every word it
 * renders is the server's; the panel adds no verdict of its own.
 *
 * The rules this surface is built around, each with a tempting wrong reading:
 *
 * | Server says | Panel shows |
 * | --- | --- |
 * | `harness_capability: null` | "state is not reported" — never "wiring is complete" |
 * | `executes: false` | *does not execute*, beside every missing wiring point by name |
 * | `store.readable: false` | the store's own reason — **never an empty tree** |
 * | `counts` null / absent list | "not reported"/"no list arrived" — never `0`/`[]` |
 * | `active_bundle: null` | "no active bundle is recorded" — not "the tree is empty" |
 * | `manifest_error` on the pointer | the degraded read named, beside the pointer |
 * | `unreadable[]` on a listing | the unreadable rows named — not silently dropped |
 * | `resumable: null` | "resumability not reported" — never "terminal" |
 * | a refused approve/activate/rollback | the server's sentence, verbatim |
 * | `cost_usd: null` in the ledger | "not reported" — never a spend of `$0` |
 * | a 503/cancel-while-disabled | the server's refusal; the control is never hidden |
 *
 * Four design decisions carry most of the weight:
 *
 * 1. **Six reads, six independent failures.** Posture, runs, bundles,
 *    promotions, experiences and budgets each own their loading, empty and
 *    error state, so one 503 cannot blank the five that answered — they answer
 *    different questions and an all-or-nothing load would hide four answers
 *    behind one failure.
 * 2. **Nothing is painted as promoted from a click.** Every decision re-reads
 *    the authoritative reads it can change, and the receipt is rendered as a
 *    receipt, not as the new state.
 * 3. **The kills switch is never re-checked here.** The client sends the
 *    request and renders whichever answer comes back — a `503
 *    harness_improvement_disabled`, a `409 harness_baseline_absent`, or a
 *    `403` the server owns. A local `enabled` gate would be a second authority
 *    and would hide the refusal that answers.
 * 4. **The promotion ladder is read from the Gateway, never pinned here.**
 *    `GET /promotions` carries the ladder map and this view renders it as
 *    data; no stage list, no transition table and no gate is re-derived in
 *    this file.
 *
 * Coverage: `src/lib/harness-lab.test.mjs` (routes, verbs, the null-preserving
 * mappers, the local refusals) and `src/lib/harness-lab-view.test.mjs` (the
 * pins above, against this file).
 */

type Tone = "green" | "amber" | "gray" | "blue" | "purple" | "cyan" | "red" | "indigo";

/** A count, with its absence named rather than zeroed. */
function countText(count: number | null): string {
  return count === null ? "not reported" : String(count);
}

/** A timestamp, with its absence and its unreadability kept apart. */
function stampText(value: string | null): string {
  if (value === null || value === "") return "no timestamp reported";
  const rel = relTime(value);
  return rel === null ? "timestamp not readable" : rel;
}

/** A scalar from an opaque record; `null` reads "not reported" in `DetailRow`. */
function scalarText(value: unknown): string {
  if (value === null || value === undefined) return "not reported";
  if (typeof value === "string") return value === "" ? "(empty)" : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

/** A byte size, with its absence named. */
function sizeText(size: number | null): string {
  return size === null ? "size not reported" : `${size} B`;
}

/** A measured cost is a number; an unreported one is never `$0`. */
function costText(cost: number | null): string {
  return cost === null ? "not reported" : `$${cost}`;
}

/** The server's own answer to "may this run be resumed"; never re-derived. */
function resumableText(resumable: boolean | null): string {
  if (resumable === true) return "resumable (the server's answer)";
  if (resumable === false) return "not resumable (the server's answer)";
  return "resumability not reported";
}

/** Badge tone for a run stage. The stage string itself is rendered verbatim. */
function stageTone(stage: string): Tone {
  switch (stage) {
    case "completed":
      return "green";
    case "blocked":
      return "amber";
    case "failed":
    case "cancelled":
    case "budget_exhausted":
      return "red";
    default:
      return "gray";
  }
}

/** Badge tone for a bundle's promotion state. An unknown state is grey. */
function promotionTone(state: string): Tone {
  switch (state) {
    case "active":
      return "green";
    case "rejected":
      return "red";
    default:
      return "gray";
  }
}

/** Badge tone for a promotion-log event. The event string is rendered verbatim. */
function eventTone(event: string | null): Tone {
  switch (event) {
    case "activated":
    case "rolled_back":
      return "green";
    case "approved":
      return "blue";
    case "rejected":
      return "red";
    default:
      return "gray";
  }
}

/** Badge tone for an experience's finding. An unknown finding is grey. */
function findingTone(finding: string | null): Tone {
  switch (finding) {
    case "benefit":
      return "green";
    case "regression":
      return "red";
    default:
      return "gray";
  }
}

/* ── Posture: capability, store, active bundle, ceilings ────────────────── */

/** The machine-readable execution plane. `executes: false` is the shipped posture. */
function CapabilityCard({ capability }: { capability: HarnessCapability | null }) {
  if (capability === null) {
    return (
      <div className="rounded-xl border border-border/60 p-3 space-y-1.5" data-harness-capability="not-reported">
        <p className="text-xs font-semibold">Execution plane</p>
        <p className="text-[11px] text-muted-foreground">
          The Gateway sent no capability row, so the execution plane's state is not reported — that is not the same as wiring being complete.
        </p>
      </div>
    );
  }
  const executesLabel = capability.executes === true ? "executes" : capability.executes === false ? "does not execute" : "execution not reported";
  const executesTone: Tone = capability.executes === true ? "green" : capability.executes === false ? "gray" : "amber";
  return (
    <div className="rounded-xl border border-border/60 p-3 space-y-2" data-harness-capability={capability.state ?? "unknown"}>
      <div className="flex items-center gap-1.5 flex-wrap">
        <p className="text-xs font-semibold">Execution plane</p>
        <Badge tone="gray" title="The capability this row describes">
          {capability.capability ?? "capability not reported"}
        </Badge>
        <Badge tone="gray" title="The capability's state, verbatim">
          {capability.state ?? "state not reported"}
        </Badge>
        <Badge tone={executesTone} title="Whether this process can execute a probe at all">
          {executesLabel}
        </Badge>
      </div>
      <p className="text-[11px] text-muted-foreground">
        An improvement run needs an executor, an editor and a baseline provider. This Gateway wires none of them, so a start is refused or parked with the missing collaborator named — never faked.
      </p>
      {capability.missing_wiring === null ? (
        <p className="text-[11px] text-muted-foreground">The Gateway sent no missing-wiring list; nothing here says the wiring is complete.</p>
      ) : capability.missing_wiring.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">The Gateway reports no missing wiring.</p>
      ) : (
        <div className="space-y-1.5">
          <p className="text-[11px] font-medium">Wiring this process would need:</p>
          {capability.missing_wiring.map((row) => (
            <div key={row.wiring_point} className="text-[10px] text-muted-foreground space-y-0.5">
              <p className="font-mono break-all">{row.wiring_point || "wiring point not reported"}</p>
              <p>{row.why_absent || "no reason reported"}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** The store's readability plus its bounded counts. */
function StoreCard({ store }: { store: HarnessStatus["store"] }) {
  if (store === null) {
    return (
      <div className="rounded-xl border border-border/60 p-3 space-y-1.5" data-harness-store="not-reported">
        <p className="text-xs font-semibold">Store</p>
        <p className="text-[11px] text-muted-foreground">The Gateway sent no store block; readability and counts are not reported.</p>
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-border/60 p-3 space-y-2" data-harness-store={store.readable === true ? "readable" : store.readable === false ? "unreadable" : "unknown"}>
      <div className="flex items-center gap-1.5 flex-wrap">
        <p className="text-xs font-semibold">Store</p>
        <Badge tone={store.readable === true ? "green" : store.readable === false ? "red" : "amber"} title="Whether the store listing could be read">
          {store.readable === true ? "readable" : store.readable === false ? "could not be read" : "readability not reported"}
        </Badge>
      </div>
      <p className="text-[10px] text-muted-foreground font-mono break-all">{store.root ?? "store root not reported"}</p>
      {store.readable === false ? (
        <ErrorBox message={`The store could not be read: ${store.reason ?? "no reason reported"}`} />
      ) : store.readable === null ? (
        <p className="text-[11px] text-muted-foreground">Store readability is not reported, so no count below can be read as a measurement.</p>
      ) : (
        <div className="grid grid-cols-3 gap-2">
          <StatCard label="bundles" value={countText(store.counts === null ? null : store.counts.bundles ?? null)} sub="stored manifests" />
          <StatCard label="runs" value={countText(store.counts === null ? null : store.counts.runs ?? null)} sub="run records" />
          <StatCard label="experiences" value={countText(store.counts === null ? null : store.counts.experiences ?? null)} sub="lesson entries" />
        </div>
      )}
      {store.counts === null && store.readable === true && <p className="text-[11px] text-muted-foreground">The Gateway sent no counts block; every count here would read "not reported" rather than 0.</p>}
      {store.unreadable !== null && store.unreadable.length > 0 && (
        <div className="text-[10px] text-muted-foreground space-y-0.5">
          <p className="font-medium">{store.unreadable.length} store row(s) could not be read and are not counted above:</p>
          {store.unreadable.map((row) => (
            <p key={row} className="font-mono break-all">{row}</p>
          ))}
        </div>
      )}
    </div>
  );
}

/** The active pointer: present with a manifest, degraded, absent — three states. */
function ActiveBundleCard({ active }: { active: HarnessStatus["active_bundle"] }) {
  if (active === null) {
    return (
      <div className="rounded-xl border border-border/60 p-3 space-y-1.5" data-harness-active="none">
        <p className="text-xs font-semibold">Active bundle</p>
        <p className="text-[11px] text-muted-foreground">
          No active bundle is recorded. A start is therefore expected to refuse with the absent-baseline reason rather than inventing one.
        </p>
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-border/60 p-3 space-y-2" data-harness-active={active.manifest_error !== null ? "manifest-degraded" : "recorded"}>
      <div className="flex items-center gap-1.5 flex-wrap">
        <p className="text-xs font-semibold">Active bundle</p>
        <span className="text-[11px] font-mono break-all">{active.bundle_id || "id not reported"}</span>
        <Badge tone="gray" title="The promotion state of the active bundle's manifest">
          {active.manifest?.promotion_state || "promotion state not reported"}
        </Badge>
      </div>
      <div>
        <DetailRow label="previous" value={<span className="font-mono">{active.previous_bundle_id}</span>} />
        <DetailRow label="activated" value={active.activated_at === null ? "not reported" : stampText(active.activated_at)} />
        <DetailRow label="actor" value={active.actor} />
        <DetailRow label="reason" value={active.reason} />
        <DetailRow label="components" value={active.manifest?.component_count === null || active.manifest?.component_count === undefined ? null : String(active.manifest.component_count)} />
      </div>
      {active.manifest_error !== null && <ErrorBox message={`The active pointer is recorded, but its manifest could not be read: ${active.manifest_error}`} />}
      {active.manifest_error === null && active.manifest === null && (
        <p className="text-[11px] text-muted-foreground">The active pointer is recorded, but the Gateway sent no manifest for it.</p>
      )}
    </div>
  );
}

/* ── Rows ───────────────────────────────────────────────────────────────── */

/** One bounded run timeline row. */
function RunRow(props: { run: HarnessRunRow; selected: boolean; onOpen: (runId: string) => void }) {
  const run = props.run;
  const counts = run.counts;
  return (
    <div className="px-3 py-2 space-y-1.5" data-harness-run={run.run_id}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[11px] font-medium font-mono">{run.run_id || "id not reported"}</span>
          <Badge tone={stageTone(run.stage)} title="The run record's own stage string">
            {run.stage === "" ? "stage not reported" : run.stage}
          </Badge>
          <Badge tone="gray">{resumableText(run.resumable)}</Badge>
          {run.mode !== null && run.mode !== "" && <Badge tone="purple">mode: {run.mode}</Badge>}
          {run.cancel_requested === true && <Badge tone="amber">cancel requested</Badge>}
        </span>
        <Btn variant={props.selected ? "primary" : "ghost"} onClick={() => props.onOpen(run.run_id)}>
          Open
        </Btn>
      </div>
      <div className="text-[10px] text-muted-foreground flex items-center gap-2 flex-wrap">
        <span>{stampText(run.created_at)}</span>
        <span>{run.task_type !== null && run.task_type !== "" ? `task type ${run.task_type}` : "task type not reported"}</span>
        <span className="font-mono">{run.task_fingerprint === null || run.task_fingerprint === "" ? "fingerprint not reported" : run.task_fingerprint}</span>
      </div>
      <p className="text-[11px] min-w-0 break-words">{run.outcome !== null && run.outcome !== "" ? run.outcome : "outcome not reported"}</p>
      {counts === null ? (
        <p className="text-[10px] text-muted-foreground">counts not reported</p>
      ) : (
        <p className="text-[10px] text-muted-foreground">
          checkpoints {countText(counts.checkpoints)} · proposals {countText(counts.proposals)} · validations {countText(counts.validations)} (accepted {countText(counts.accepted_validations)}) · probes {countText(counts.probes)} · contrasts {countText(counts.contrasts)} · experiences {countText(counts.experiences)}
        </p>
      )}
      {run.blocked_reason !== null && run.blocked_reason !== "" && <p className="text-[10px] text-amber-600 dark:text-amber-400">blocked: {run.blocked_reason}</p>}
      {run.error !== null && run.error !== "" && <p className="text-[10px] text-red-600 dark:text-red-400">error: {run.error}</p>}
    </div>
  );
}

/** The full record for one selected run, plus its resume/cancel controls. */
function RunDetailPanel(props: {
  detail: HarnessRunDetail | null;
  loading: boolean;
  error: string | null;
  cancelReason: string;
  onCancelReasonChange: (value: string) => void;
  onResume: () => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const { detail, loading, error } = props;
  if (loading) {
    return (
      <div className="rounded-xl border border-border/60 p-3" data-harness-run-detail="loading">
        <SkeletonList rows={2} />
      </div>
    );
  }
  if (error !== null) {
    return <ErrorBox message={`The run record read failed: ${error}`} />;
  }
  if (detail === null) return null;
  const run = detail.run;
  if (run === null) {
    return (
      <div className="rounded-xl border border-border/60 p-3 space-y-2" data-harness-run-detail="absent">
        <p className="text-[11px] text-muted-foreground">The Gateway answered with no run record for this id; the journal lines below are all that remain of it.</p>
        <JournalTail detail={detail} />
      </div>
    );
  }
  const history = run.history ?? [];
  const notes = run.notes ?? [];
  return (
    <div className="rounded-xl border border-border/60 p-3 space-y-3" data-harness-run-detail={run.run_id ?? "unknown"}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs font-semibold font-mono">{run.run_id ?? "id not reported"}</span>
          <Badge tone={stageTone(run.stage ?? "")} title="The record's own stage string">
            {run.stage === null || run.stage === "" ? "stage not reported" : run.stage}
          </Badge>
          <Badge tone="gray">{resumableText(detail.resumable)}</Badge>
          {detail.next_stage !== null && detail.next_stage !== "" && <Badge tone="blue">next stage: {detail.next_stage}</Badge>}
        </span>
        <span className="flex items-center gap-1.5 flex-wrap">
          <input className={`${inputCls} !w-64`} value={props.cancelReason} onChange={(e) => props.onCancelReasonChange(e.target.value)} placeholder="Cancellation reason (optional; the server records its own default when empty)" />
          <Btn variant="ghost" onClick={props.onResume} disabled={props.busy}>
            Resume
          </Btn>
          <Btn variant="danger" onClick={props.onCancel} disabled={props.busy}>
            Request cancel
          </Btn>
        </span>
      </div>

      <div>
        <DetailRow label="mode" value={run.mode} />
        <DetailRow label="created by" value={run.created_by} />
        <DetailRow label="created" value={run.created_at === null ? "not reported" : stampText(run.created_at)} />
        <DetailRow label="updated" value={run.updated_at === null ? "not reported" : stampText(run.updated_at)} />
        <DetailRow label="base bundle" value={<span className="font-mono">{run.base_bundle_id}</span>} />
        <DetailRow label="successor bundle" value={<span className="font-mono">{run.successor_bundle_id}</span>} />
        <DetailRow label="task type" value={run.task_type} />
        <DetailRow label="task fingerprint" value={<span className="font-mono">{run.task_fingerprint}</span>} />
        <DetailRow label="cancel requested" value={run.cancel_requested === null ? null : run.cancel_requested ? "yes" : "no"} />
        <DetailRow label="cancel reason" value={run.cancel_reason} />
        <DetailRow label="blocked reason" value={run.blocked_reason} />
        <DetailRow label="error" value={run.error} />
      </div>

      {run.task_text !== null && (
        <div className="space-y-1">
          <p className="text-[11px] font-medium">Task</p>
          <p className="text-[11px] text-muted-foreground break-words">{run.task_text}</p>
        </div>
      )}
      {run.task_outcome !== null && (
        <div className="space-y-1">
          <p className="text-[11px] font-medium">Declared outcome</p>
          <p className="text-[11px] text-muted-foreground break-words">{run.task_outcome}</p>
        </div>
      )}

      <p className="text-[10px] text-muted-foreground">
        measured so far — proposals {countText(run.proposal_count)} · validations {countText(run.validation_count)} · probe outcomes {countText(run.probe_outcome_count)} · contrasts {countText(run.contrast_count)} · experiences {countText(run.experience_count)}
      </p>

      {notes.length > 0 && (
        <div className="space-y-0.5 text-[10px] text-muted-foreground">
          <p className="font-medium text-[11px]">Notes</p>
          {notes.map((note, index) => (
            <p key={`${index}-${note}`}>{note}</p>
          ))}
        </div>
      )}

      {history.length > 0 && (
        <details className="text-[10px] text-muted-foreground">
          <summary className="cursor-pointer text-[11px] font-medium text-foreground">Stage history ({history.length})</summary>
          <div className="mt-1 space-y-0.5">
            {history.slice(-12).map((row, index) => (
              <p key={`${index}-${row.event ?? "event"}`} className="font-mono break-all">
                {row.at ?? "no timestamp reported"} · {row.event ?? "event not reported"} · {row.stage ?? "stage not reported"}
                {row.reason !== null && row.reason !== "" ? ` — ${row.reason}` : ""}
              </p>
            ))}
          </div>
        </details>
      )}

      <JournalTail detail={detail} />
    </div>
  );
}

/** The append-only journal tail, with torn lines disclosed. */
function JournalTail({ detail }: { detail: HarnessRunDetail }) {
  const events = detail.events;
  return (
    <div className="space-y-1" data-harness-run-journal="present">
      <p className="text-[11px] font-medium">Journal</p>
      {events === null ? (
        <p className="text-[10px] text-muted-foreground">The Gateway sent no journal tail for this run; that is not a claim that nothing was recorded.</p>
      ) : events.length === 0 ? (
        <p className="text-[10px] text-muted-foreground">The journal is empty.</p>
      ) : (
        <div className="space-y-0.5">
          {events.slice(-12).map((row, index) => (
            <p key={`${index}-${row.at ?? "at"}`} className="text-[10px] text-muted-foreground font-mono break-all">
              {row.at ?? "no timestamp reported"} · {row.event ?? "event not reported"} · {row.stage ?? "stage not reported"}
              {row.reason !== null && row.reason !== "" ? ` — ${row.reason}` : ""}
              {row.extra !== null ? ` · ${JSON.stringify(row.extra)}` : ""}
            </p>
          ))}
          {events.length > 12 && <p className="text-[10px] text-muted-foreground">… showing the last 12 of {events.length} journal lines.</p>}
        </div>
      )}
      {detail.unreadable_event_lines !== null && detail.unreadable_event_lines.length > 0 && (
        <p className="text-[10px] text-amber-600 dark:text-amber-400">{detail.unreadable_event_lines.length} journal line(s) could not be read and are not shown.</p>
      )}
    </div>
  );
}

/** One bundle manifest row. */
function BundleRow(props: { bundle: HarnessBundleRow; selected: boolean; active: boolean; onOpen: (bundleId: string) => void }) {
  const bundle = props.bundle;
  return (
    <div className="px-3 py-2 space-y-1.5" data-harness-bundle={bundle.bundle_id}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[11px] font-medium font-mono">{bundle.bundle_id || "id not reported"}</span>
          <Badge tone={promotionTone(bundle.promotion_state)} title="The manifest's own promotion state">
            {bundle.promotion_state === "" ? "promotion state not reported" : bundle.promotion_state}
          </Badge>
          {props.active && (
            <Badge tone="green" title="This is the bundle the active pointer names">
              active
            </Badge>
          )}
        </span>
        <Btn variant={props.selected ? "primary" : "ghost"} onClick={() => props.onOpen(bundle.bundle_id)}>
          Open
        </Btn>
      </div>
      <div className="text-[10px] text-muted-foreground flex items-center gap-2 flex-wrap">
        <span>{stampText(bundle.created_at)}</span>
        <span>{bundle.parent_bundle_id === null || bundle.parent_bundle_id === "" ? "no parent recorded (a baseline snapshot)" : `parent ${bundle.parent_bundle_id}`}</span>
        <span>{bundle.component_count === null ? "component count not reported" : `${bundle.component_count} component(s)`}</span>
        <span>{bundle.source_run_id === null || bundle.source_run_id === "" ? "no source run recorded" : `from run ${bundle.source_run_id}`}</span>
      </div>
      {bundle.created_by !== null && bundle.created_by !== "" && <p className="text-[10px] text-muted-foreground">created by {bundle.created_by}</p>}
    </div>
  );
}

/** One manifest plus its (redacted) component bodies. */
function BundleDetailPanel(props: { detail: HarnessBundleDetail | null; loading: boolean; error: string | null }) {
  const { detail, loading, error } = props;
  if (loading) {
    return (
      <div className="rounded-xl border border-border/60 p-3" data-harness-bundle-detail="loading">
        <SkeletonList rows={2} />
      </div>
    );
  }
  if (error !== null) {
    return <ErrorBox message={`The bundle read failed: ${error}`} />;
  }
  if (detail === null) return null;
  const bundle = detail.bundle;
  const components = detail.components;
  return (
    <div className="rounded-xl border border-border/60 p-3 space-y-3" data-harness-bundle-detail={bundle === null ? "absent-record" : bundle.bundle_id}>
      {bundle === null ? (
        <p className="text-[11px] text-muted-foreground">The Gateway answered with no bundle record for this id.</p>
      ) : (
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs font-semibold font-mono">{bundle.bundle_id || "id not reported"}</span>
          <Badge tone={promotionTone(bundle.promotion_state)}>{bundle.promotion_state === "" ? "promotion state not reported" : bundle.promotion_state}</Badge>
        </div>
      )}
      <div>
        <DetailRow label="agent profile" value={bundle?.agent_profile ?? null} />
        <DetailRow label="alpha runtime" value={bundle?.alpha_runtime_version ?? null} />
        <DetailRow label="tool schema" value={bundle?.tool_schema_version ?? null} />
        <DetailRow label="policy" value={bundle?.policy_version ?? null} />
        <DetailRow label="source run" value={<span className="font-mono">{bundle?.source_run_id ?? null}</span>} />
      </div>
      {detail.note !== null && detail.note !== "" && <Notice tone="neutral" message={detail.note} />}
      {components === null ? (
        <p className="text-[11px] text-muted-foreground">The Gateway sent no component list; that is not a claim that the manifest is empty.</p>
      ) : components.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">The manifest declares no components.</p>
      ) : (
        <div className="space-y-2">
          <p className="text-[11px] font-medium">Components ({components.length})</p>
          {components.map((component) => (
            <div key={component.component_id} className="rounded-lg border border-border/50 px-2.5 py-2 space-y-1">
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="text-[11px] font-mono break-all">{component.component_id || "id not reported"}</span>
                <Badge tone="gray">{component.kind || "kind not reported"}</Badge>
                <span className="text-[10px] text-muted-foreground">{sizeText(component.size_bytes)}</span>
                {component.missing === true ? (
                  <Badge tone="amber" title="The manifest references a blob this store does not hold; the manifest itself is still valid">
                    body not stored
                  </Badge>
                ) : component.content === null ? (
                  <Badge tone="gray" title="The body was redacted away or never stored">
                    body not shown
                  </Badge>
                ) : null}
              </div>
              <p className="text-[10px] text-muted-foreground font-mono break-all">sha256 {component.sha256 ?? "digest not reported"}</p>
              {component.content !== null && (
                <details>
                  <summary className="cursor-pointer text-[10px] text-muted-foreground">body ({sizeText(component.size_bytes)})</summary>
                  <pre className="mt-1 max-h-64 overflow-auto rounded bg-muted/40 p-2 text-[10px] whitespace-pre-wrap break-all">{component.content}</pre>
                </details>
              )}
            </div>
          ))}
        </div>
      )}
      {detail.redactions !== null && detail.redactions.length > 0 && (
        <div className="text-[10px] text-muted-foreground space-y-0.5">
          <p className="font-medium">Redacted before this read left the server:</p>
          {detail.redactions.map((finding, index) => (
            <p key={`${index}-${finding}`} className="font-mono break-all">{finding}</p>
          ))}
        </div>
      )}
    </div>
  );
}

/** The component-level diff against the parent, bodies deliberately skipped. */
function BundleDiffPanel(props: { diff: HarnessDiffDetail | null; loading: boolean; error: string | null }) {
  const { diff, loading, error } = props;
  if (loading) {
    return (
      <div className="rounded-xl border border-border/60 p-3" data-harness-bundle-diff="loading">
        <SkeletonList rows={2} />
      </div>
    );
  }
  if (error !== null) {
    return <ErrorBox message={`The bundle diff read failed: ${error}`} />;
  }
  if (diff === null) return null;
  const inner = diff.diff;
  return (
    <div className="rounded-xl border border-border/60 p-3 space-y-2" data-harness-bundle-diff="present">
      <p className="text-[11px] font-medium">Diff against the parent (or the base the server resolved)</p>
      {inner === null ? (
        <p className="text-[11px] text-muted-foreground">The Gateway sent no diff block.</p>
      ) : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <StatCard label="added" value={countText(inner.added === null ? null : inner.added.length)} />
            <StatCard label="removed" value={countText(inner.removed === null ? null : inner.removed.length)} />
            <StatCard label="modified" value={countText(inner.modified === null ? null : inner.modified.length)} />
            <StatCard label="unchanged" value={countText(inner.unchanged === null ? null : inner.unchanged.length)} />
          </div>
          <div className="text-[10px] text-muted-foreground space-y-0.5">
            {inner.added !== null && inner.added.length > 0 && <p className="break-all">added: {inner.added.join(", ")}</p>}
            {inner.removed !== null && inner.removed.length > 0 && <p className="break-all">removed: {inner.removed.join(", ")}</p>}
            {(inner.modified ?? []).map((row) => (
              <p key={row.component_id} className="break-all">
                modified: {row.component_id || "id not reported"} · {row.kind ?? "kind not reported"} · size delta {row.size_delta === null ? "not reported" : String(row.size_delta)}
              </p>
            ))}
            {inner.added === null && inner.removed === null && inner.modified === null && inner.unchanged === null && <p>The diff block arrived with every list absent.</p>}
          </div>
          <p className="text-[10px] text-muted-foreground">
            Bodies are skipped here (the client asks the server for the diff without them); open the bundle above for component bodies. {diff.note ?? ""}
          </p>
          {diff.redactions !== null && diff.redactions.length > 0 && <p className="text-[10px] text-muted-foreground">{diff.redactions.length} redaction finding(s) were recorded while building this diff.</p>}
        </>
      )}
    </div>
  );
}

/** One durable promotion-log row. */
function PromotionEventRow({ event }: { event: HarnessPromotionEvent }) {
  return (
    <div className="px-3 py-1.5 space-y-0.5" data-harness-promotion-event={event.event ?? "unknown"}>
      <div className="flex items-center gap-1.5 flex-wrap">
        <Badge tone={eventTone(event.event)} title="The promotion log's own event string">
          {event.event ?? "event not reported"}
        </Badge>
        <span className="text-[10px] font-mono break-all">{event.bundle_id ?? "bundle not reported"}</span>
        {event.previous_bundle_id !== null && event.previous_bundle_id !== "" && <span className="text-[10px] text-muted-foreground">from {event.previous_bundle_id}</span>}
      </div>
      <div className="text-[10px] text-muted-foreground flex items-center gap-2 flex-wrap">
        <span>{stampText(event.at)}</span>
        <span>{event.actor === null || event.actor === "" ? "actor not reported" : `by ${event.actor}`}</span>
        {event.granted !== null && event.granted.length > 0 && <span>granted: {event.granted.join(", ")}</span>}
      </div>
      {event.reason !== null && event.reason !== "" && <p className="text-[10px] text-muted-foreground break-words">{event.reason}</p>}
    </div>
  );
}

/** One experience-library entry, staleness reported rather than applied. */
function ExperienceRow({ entry }: { entry: HarnessExperienceRow }) {
  return (
    <div className="px-3 py-2 space-y-1" data-harness-experience={entry.experience_id}>
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[11px] font-medium">{entry.title ?? "title not reported"}</span>
        <Badge tone={findingTone(entry.finding)} title="The entry's own finding string">
          {entry.finding ?? "finding not reported"}
        </Badge>
        {entry.status !== null && entry.status !== "" && <Badge tone="gray">status: {entry.status}</Badge>}
        {entry.superseded_by !== null && entry.superseded_by !== "" && <Badge tone="amber">superseded by {entry.superseded_by}</Badge>}
      </div>
      {entry.lesson !== null && entry.lesson !== "" && <p className="text-[11px] text-muted-foreground break-words">{entry.lesson}</p>}
      <div className="text-[10px] text-muted-foreground flex items-center gap-2 flex-wrap">
        <span>{stampText(entry.created_at)}</span>
        <span>{entry.scope === null || entry.scope === "" ? "scope not reported" : `scope ${entry.scope}`}</span>
        <span>{entry.task_type === null || entry.task_type === "" ? "task type not reported" : `task type ${entry.task_type}`}</span>
        {entry.domain_tags !== null && entry.domain_tags.length > 0 && <span>tags: {entry.domain_tags.join(", ")}</span>}
      </div>
      <p className="text-[10px] text-muted-foreground">
        {entry.staleness === null
          ? "staleness not reported"
          : entry.staleness.length === 0
            ? "not stale per the recorded conditions"
            : `stale: ${entry.staleness.join("; ")}`}
      </p>
      {entry.benefit !== null && entry.benefit !== "" && <p className="text-[10px] text-muted-foreground break-words">benefit: {entry.benefit}</p>}
      {entry.counter_evidence !== null && entry.counter_evidence !== "" && <p className="text-[10px] text-muted-foreground break-words">counter-evidence: {entry.counter_evidence}</p>}
    </div>
  );
}

/** One measured budget-ledger row. An unreported dimension stays "not reported". */
function BudgetRow({ row }: { row: HarnessBudgetRow }) {
  return (
    <div className="px-3 py-1.5 space-y-0.5" data-harness-budget-row={row.run_id ?? "unknown"}>
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[10px] font-mono break-all">{row.run_id ?? "run not reported"}</span>
        <Badge tone="gray">{row.stage ?? "stage not reported"}</Badge>
        <span className="text-[10px] text-muted-foreground">{row.model ?? "model not reported"}</span>
      </div>
      <p className="text-[10px] text-muted-foreground">
        input {countText(row.input_tokens)} · output {countText(row.output_tokens)} · cost {costText(row.cost_usd)} · elapsed {row.elapsed_ms === null ? "not reported" : `${row.elapsed_ms} ms`}
      </p>
      <p className="text-[10px] text-muted-foreground">{stampText(row.recorded_at)}</p>
    </div>
  );
}

/* ── The view ───────────────────────────────────────────────────────────── */

export function HarnessLabSection() {
  /* Six independent reads — six independent loading/error/empty states. */
  const [status, setStatus] = useState<HarnessStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);

  const [runs, setRuns] = useState<HarnessRunList | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [runsLoading, setRunsLoading] = useState(true);

  const [bundles, setBundles] = useState<HarnessBundleList | null>(null);
  const [bundlesError, setBundlesError] = useState<string | null>(null);
  const [bundlesLoading, setBundlesLoading] = useState(true);

  const [promotions, setPromotions] = useState<HarnessPromotionHistory | null>(null);
  const [promotionsError, setPromotionsError] = useState<string | null>(null);
  const [promotionsLoading, setPromotionsLoading] = useState(true);

  const [experiences, setExperiences] = useState<HarnessExperienceList | null>(null);
  const [experiencesError, setExperiencesError] = useState<string | null>(null);
  const [experiencesLoading, setExperiencesLoading] = useState(true);

  const [budgets, setBudgets] = useState<HarnessBudgets | null>(null);
  const [budgetsError, setBudgetsError] = useState<string | null>(null);
  const [budgetsLoading, setBudgetsLoading] = useState(true);

  /* Detail reads. */
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [runDetail, setRunDetail] = useState<HarnessRunDetail | null>(null);
  const [runDetailError, setRunDetailError] = useState<string | null>(null);
  const [runDetailLoading, setRunDetailLoading] = useState(false);

  const [selectedBundleId, setSelectedBundleId] = useState<string | null>(null);
  const [bundleDetail, setBundleDetail] = useState<HarnessBundleDetail | null>(null);
  const [bundleDetailError, setBundleDetailError] = useState<string | null>(null);
  const [bundleDetailLoading, setBundleDetailLoading] = useState(false);
  const [diff, setDiff] = useState<HarnessDiffDetail | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);

  /* Writes. */
  const [startTask, setStartTask] = useState("");
  const [startOutcome, setStartOutcome] = useState("");
  const [starting, setStarting] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const [reason, setReason] = useState("");
  const [approvals, setApprovals] = useState<Record<string, boolean>>({});
  const [acting, setActing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    setStatusLoading(true);
    setStatusError(null);
    try {
      setStatus(await fetchHarnessStatus());
    } catch (err) {
      setStatusError(failureText(err));
    } finally {
      setStatusLoading(false);
    }
  }, []);

  const loadRuns = useCallback(async () => {
    setRunsLoading(true);
    setRunsError(null);
    try {
      setRuns(await fetchHarnessRuns());
    } catch (err) {
      setRunsError(failureText(err));
    } finally {
      setRunsLoading(false);
    }
  }, []);

  const loadBundles = useCallback(async () => {
    setBundlesLoading(true);
    setBundlesError(null);
    try {
      setBundles(await fetchHarnessBundles());
    } catch (err) {
      setBundlesError(failureText(err));
    } finally {
      setBundlesLoading(false);
    }
  }, []);

  const loadPromotions = useCallback(async () => {
    setPromotionsLoading(true);
    setPromotionsError(null);
    try {
      setPromotions(await fetchHarnessPromotions());
    } catch (err) {
      setPromotionsError(failureText(err));
    } finally {
      setPromotionsLoading(false);
    }
  }, []);

  const loadExperiences = useCallback(async () => {
    setExperiencesLoading(true);
    setExperiencesError(null);
    try {
      setExperiences(await fetchHarnessExperiences());
    } catch (err) {
      setExperiencesError(failureText(err));
    } finally {
      setExperiencesLoading(false);
    }
  }, []);

  const loadBudgets = useCallback(async () => {
    setBudgetsLoading(true);
    setBudgetsError(null);
    try {
      setBudgets(await fetchHarnessBudgets());
    } catch (err) {
      setBudgetsError(failureText(err));
    } finally {
      setBudgetsLoading(false);
    }
  }, []);

  const loadRunDetail = useCallback(async (runId: string) => {
    setRunDetailLoading(true);
    setRunDetailError(null);
    try {
      setRunDetail(await fetchHarnessRun(runId));
    } catch (err) {
      setRunDetailError(failureText(err));
    } finally {
      setRunDetailLoading(false);
    }
  }, []);

  const loadBundleDetail = useCallback(async (bundleId: string) => {
    setBundleDetailLoading(true);
    setBundleDetailError(null);
    try {
      setBundleDetail(await fetchHarnessBundle(bundleId));
    } catch (err) {
      setBundleDetailError(failureText(err));
    } finally {
      setBundleDetailLoading(false);
    }
    setDiffLoading(true);
    setDiffError(null);
    try {
      // Bodies are skipped on purpose: what an operator decides on is the
      // shape of the change, and the bundle read above already carries bodies.
      setDiff(await fetchHarnessBundleDiff(bundleId, { includeContents: false }));
    } catch (err) {
      setDiffError(failureText(err));
    } finally {
      setDiffLoading(false);
    }
  }, []);

  const reloadAll = useCallback(() => {
    void Promise.allSettled([loadStatus(), loadRuns(), loadBundles(), loadPromotions(), loadExperiences(), loadBudgets()]);
  }, [loadStatus, loadRuns, loadBundles, loadPromotions, loadExperiences, loadBudgets]);

  useEffect(() => {
    reloadAll();
  }, [reloadAll]);

  const openRun = useCallback(
    (runId: string) => {
      setSelectedRunId(runId);
      void loadRunDetail(runId);
    },
    [loadRunDetail],
  );

  const openBundle = useCallback(
    (bundleId: string) => {
      setSelectedBundleId(bundleId);
      void loadBundleDetail(bundleId);
    },
    [loadBundleDetail],
  );

  const submitStart = useCallback(async () => {
    setStarting(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const receipt = await startHarnessRun({ task: startTask, outcome: startOutcome });
      setActionNotice(
        `Start answered: run ${receipt.run_id ?? "id not reported"} is at stage ${receipt.stage ?? "stage not reported"}. The list below is re-read from the store rather than built from this receipt.`,
      );
      if (receipt.run_id !== null && receipt.run_id !== "") setSelectedRunId(receipt.run_id);
      await Promise.all([loadRuns(), loadStatus()]);
    } catch (err) {
      // The expected answer in a production posture is the server's own
      // refusal naming the absent baseline; it is rendered verbatim.
      setActionError(failureText(err));
    } finally {
      setStarting(false);
    }
  }, [startTask, startOutcome, loadRuns, loadStatus]);

  const submitResume = useCallback(
    async (runId: string) => {
      setActing(true);
      setActionError(null);
      setActionNotice(null);
      try {
        const receipt = await resumeHarnessRun(runId);
        setActionNotice(`Resume answered: run ${receipt.run_id ?? runId} is at stage ${receipt.stage ?? "stage not reported"}. Re-read below.`);
        await Promise.all([loadRuns(), loadRunDetail(runId), loadStatus()]);
      } catch (err) {
        setActionError(failureText(err));
      } finally {
        setActing(false);
      }
    },
    [loadRuns, loadRunDetail, loadStatus],
  );

  const submitCancel = useCallback(
    async (runId: string) => {
      setActing(true);
      setActionError(null);
      setActionNotice(null);
      try {
        const receipt = await cancelHarnessRun(runId, cancelReason.trim() === "" ? undefined : cancelReason);
        setActionNotice(
          [
            `Cancellation request recorded for ${receipt.run_id ?? runId}.`,
            `cancel requested: ${receipt.cancel_requested === null ? "not reported" : receipt.cancel_requested ? "yes" : "no"}.`,
            `applied at next boundary: ${receipt.applied_at_next_boundary === null ? "not reported" : receipt.applied_at_next_boundary ? "yes" : "no"}.`,
            receipt.note ?? "",
          ]
            .join(" ")
            .trim(),
        );
        await Promise.all([loadRuns(), loadRunDetail(runId)]);
      } catch (err) {
        setActionError(failureText(err));
      } finally {
        setActing(false);
      }
    },
    [cancelReason, loadRuns, loadRunDetail],
  );

  const selectedApprovals = HARNESS_APPROVAL_KINDS.filter((kind) => approvals[kind] === true);

  const activeBundleId = promotions?.pointer?.bundle_id ?? status?.active_bundle?.bundle_id ?? bundles?.active_bundle?.bundle_id ?? null;

  const submitApprove = useCallback(async () => {
    if (selectedBundleId === null) return;
    setActing(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const receipt = await approveHarnessCandidate(selectedBundleId, { reason, approvals: [...selectedApprovals] });
      setActionNotice(
        `Approval recorded for ${receipt.bundle_id ?? selectedBundleId}. ${receipt.note ?? "Approval is recorded, not applied: activation is a separate step."} The promotion log below is re-read.`,
      );
      await loadPromotions();
    } catch (err) {
      setActionError(failureText(err));
    } finally {
      setActing(false);
    }
  }, [selectedBundleId, reason, selectedApprovals, loadPromotions]);

  const submitReject = useCallback(async () => {
    if (selectedBundleId === null) return;
    setActing(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const receipt = await rejectHarnessCandidate(selectedBundleId, reason);
      setActionNotice(
        `Rejection recorded for ${receipt.bundle_id ?? selectedBundleId}. promotion state now: ${receipt.promotion_state ?? "not reported"}. The log and bundle tree below are re-read.`,
      );
      await Promise.all([loadPromotions(), loadBundles(), loadBundleDetail(selectedBundleId)]);
    } catch (err) {
      setActionError(failureText(err));
    } finally {
      setActing(false);
    }
  }, [selectedBundleId, reason, loadPromotions, loadBundles, loadBundleDetail]);

  const submitActivate = useCallback(async () => {
    if (selectedBundleId === null) return;
    setActing(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const receipt = await activateHarnessBundle(selectedBundleId, reason);
      setActionNotice(
        `Activation answered for ${receipt.bundle_id ?? selectedBundleId}: activated ${receipt.activated === null ? "not reported" : receipt.activated ? "yes" : "no"}; previous ${receipt.previous_bundle_id ?? "not reported"}. ${receipt.note ?? ""} Re-read below.`,
      );
      await Promise.all([loadStatus(), loadBundles(), loadPromotions(), loadBundleDetail(selectedBundleId)]);
    } catch (err) {
      setActionError(failureText(err));
    } finally {
      setActing(false);
    }
  }, [selectedBundleId, reason, loadStatus, loadBundles, loadPromotions, loadBundleDetail]);

  const submitRollback = useCallback(async () => {
    if (activeBundleId === null) return;
    setActing(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const receipt = await rollbackHarnessBundle(activeBundleId, reason);
      setActionNotice(
        `Rollback answered: restored ${receipt.restored_bundle_id ?? "not reported"} from ${receipt.from_bundle_id ?? "not reported"}. ${receipt.note ?? ""} Re-read below.`,
      );
      await Promise.all([loadStatus(), loadBundles(), loadPromotions()]);
    } catch (err) {
      setActionError(failureText(err));
    } finally {
      setActing(false);
    }
  }, [activeBundleId, reason, loadStatus, loadBundles, loadPromotions]);

  const busy = acting || starting;

  return (
    <Section
      title="Harness Lab"
      hint="Recursive harness improvement: probe candidate harness edits against the baseline, contrast the evidence, and promote a successor only through the gated ladder. This view executes nothing and promotes nothing by itself — every gate is the server's answer."
      actions={
        <Btn variant="ghost" onClick={reloadAll} disabled={statusLoading && runsLoading && bundlesLoading}>
          <RefreshCw className="size-3.5" />
          Refresh
        </Btn>
      }
    >
      {actionError !== null && <Notice tone="warn" message={actionError} />}
      {actionNotice !== null && <Notice tone="success" message={actionNotice} />}

      {/* ── Posture ─────────────────────────────────────────────────────── */}
      <Block title="Posture" hint="What this installation can do right now: the feature flag, the execution plane, the store and the active bundle.">
        {statusLoading && status === null ? (
          <LoadingRows what="the harness posture (/api/harness/status)" rows={2} />
        ) : statusError !== null ? (
          <UnavailableNotice what="the harness posture" reason={statusError} onRetry={loadStatus} retrying={statusLoading} />
        ) : status === null ? null : (
          <div className="space-y-3">
            <div className="flex items-center gap-1.5 flex-wrap">
              <Badge tone={status.enabled === true ? "green" : status.enabled === false ? "gray" : "amber"} title="The feature flag's value as the Gateway reports it">
                {status.enabled === true ? "enabled" : status.enabled === false ? "disabled" : "enabled state not reported"}
              </Badge>
              <Badge tone="gray">mode: {status.mode ?? "not reported"}</Badge>
              {status.config_hash !== null && status.config_hash !== "" && <Badge tone="gray" title="The hash of the settings the engine resolved">config {status.config_hash}</Badge>}
            </div>
            {status.enabled_note !== null && status.enabled_note !== "" && <Notice tone={status.enabled === false ? "warn" : "neutral"} message={status.enabled_note} />}
            <p className="text-[10px] text-muted-foreground">
              While the feature is disabled the run/advance writes answer <span className="font-mono">503</span> naming the setting, while cancelling, rejecting and rolling back stay reachable — the kill switch must not trap work in flight. The kill switch is never re-checked here; whichever answer the server gives is rendered.
            </p>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
              <CapabilityCard capability={status.capability} />
              <StoreCard store={status.store} />
              <ActiveBundleCard active={status.active_bundle} />
              <div className="rounded-xl border border-border/60 p-3 space-y-2" data-harness-ceilings="present">
                <p className="text-xs font-semibold">Ceilings & promotion policy</p>
                {status.ceilings === null ? (
                  <p className="text-[11px] text-muted-foreground">The Gateway sent no ceilings; no cap can be read as measured here.</p>
                ) : (
                  <div>
                    {Object.entries(status.ceilings).map(([key, value]) => (
                      <DetailRow key={key} label={key} value={<span className="font-mono">{scalarText(value)}</span>} />
                    ))}
                  </div>
                )}
                {status.promotion_policy === null ? (
                  <p className="text-[11px] text-muted-foreground">The Gateway sent no promotion policy.</p>
                ) : (
                  <details>
                    <summary className="cursor-pointer text-[11px] font-medium">Promotion policy</summary>
                    <div className="mt-1">
                      {Object.entries(status.promotion_policy).map(([key, value]) => (
                        <DetailRow key={key} label={key} value={<span className="font-mono">{scalarText(value)}</span>} />
                      ))}
                    </div>
                  </details>
                )}
              </div>
            </div>
            {status.durability !== null && status.durability !== "" && <p className="text-[10px] text-muted-foreground">durability: {status.durability}</p>}
          </div>
        )}
      </Block>

      {/* ── Start ───────────────────────────────────────────────────────── */}
      <Block
        title="Start an improvement run"
        hint="One task contract, driven to its first stopping point. Expected to be refused while no baseline bundle exists and no executor is wired — the refusal is the honest answer, not a bug to hide the form for."
      >
        <div className="space-y-2">
          <textarea
            className={`${inputCls} min-h-20`}
            value={startTask}
            onChange={(e) => setStartTask(e.target.value)}
            placeholder="Task: what should run under the current harness, and then under candidates?"
          />
          <textarea
            className={`${inputCls} min-h-16`}
            value={startOutcome}
            onChange={(e) => setStartOutcome(e.target.value)}
            placeholder="Outcome: the measurable result that tells whether the task succeeded"
          />
          <div className="flex items-center gap-2 flex-wrap">
            <Btn onClick={submitStart} disabled={busy}>
              <FlaskConical className="size-3.5" />
              Start run
            </Btn>
            <span className="text-[10px] text-muted-foreground">
              Both fields are required; the client refuses an empty field locally with the same bound the server would 422 on. A refusal such as{" "}
              <span className="font-mono">409 harness_baseline_absent</span> is rendered verbatim above.
            </span>
          </div>
        </div>
      </Block>

      {/* ── Runs ────────────────────────────────────────────────────────── */}
      <Block
        title="Improvement runs"
        hint="Newest-first timeline rows. Open one for the full record, its journal tail, and the resume/cancellation controls."
        actions={bundles === null && runs === null ? undefined : <Btn variant="ghost" onClick={loadRuns} disabled={runsLoading}>Reload runs</Btn>}
      >
        <div className="space-y-2">
          {runsLoading && runs === null ? (
            <LoadingRows what="the run timeline (/api/harness/runs)" rows={2} />
          ) : runsError !== null ? (
            <UnavailableNotice what="the run timeline" reason={runsError} onRetry={loadRuns} retrying={runsLoading} />
          ) : runs === null ? null : (
            <>
              {runs.complete === false && <Notice tone="warn" message="The run list is partial: the store reported that it could not be read completely, so absent run ids below are not a claim that no run exists." />}
              {runs.unreadable !== null && runs.unreadable.length > 0 && (
                <Notice tone="warn" message={`${runs.unreadable.length} run file(s) could not be read and are not shown: ${runs.unreadable.join(", ")}`} />
              )}
              {runs.runs === null ? (
                <EmptyState title="The Gateway sent no run list" hint="That is not a claim that no run exists — the list itself did not arrive." />
              ) : runs.runs.length === 0 ? (
                <EmptyState title="No improvement runs yet" hint="A run appears here after the Gateway accepts a task contract." />
              ) : (
                <div className="divide-y divide-border/50">
                  {runs.runs.map((run) => (
                    <RunRow key={run.run_id} run={run} selected={run.run_id === selectedRunId} onOpen={openRun} />
                  ))}
                </div>
              )}
              {runDetailLoading || runDetail !== null || runDetailError !== null ? (
                <RunDetailPanel
                  detail={runDetail}
                  loading={runDetailLoading}
                  error={runDetailError}
                  cancelReason={cancelReason}
                  onCancelReasonChange={setCancelReason}
                  onResume={() => {
                    if (selectedRunId !== null) void submitResume(selectedRunId);
                  }}
                  onCancel={() => {
                    if (selectedRunId !== null) void submitCancel(selectedRunId);
                  }}
                  busy={busy}
                />
              ) : null}
            </>
          )}
        </div>
      </Block>

      {/* ── Bundles ─────────────────────────────────────────────────────── */}
      <Block
        title="Bundle tree"
        hint="Immutable content-addressed snapshots. A candidate is a new bundle whose parent is the baseline — never an edit to the active one."
      >
        <div className="space-y-2">
          {bundlesLoading && bundles === null ? (
            <LoadingRows what="the bundle tree (/api/harness/bundles)" rows={2} />
          ) : bundlesError !== null ? (
            <UnavailableNotice what="the bundle tree" reason={bundlesError} onRetry={loadBundles} retrying={bundlesLoading} />
          ) : bundles === null ? null : (
            <>
              <div className="text-[11px] text-muted-foreground" data-harness-active-pointer>
                {bundles.active_bundle === null
                  ? "No active bundle is recorded."
                  : `Active bundle: ${bundles.active_bundle.bundle_id || "id not reported"}${bundles.active_bundle.previous_bundle_id !== null ? ` (previous ${bundles.active_bundle.previous_bundle_id})` : " (no previous bundle recorded)"}.`}
              </div>
              {bundles.complete === false && <Notice tone="warn" message="The bundle listing is partial: the store reported that it could not be read completely." />}
              {bundles.unreadable !== null && bundles.unreadable.length > 0 && (
                <Notice tone="warn" message={`${bundles.unreadable.length} bundle file(s) could not be read and are not shown: ${bundles.unreadable.join(", ")}`} />
              )}
              {bundles.bundles === null ? (
                <EmptyState title="The Gateway sent no bundle list" hint="That is not a claim that the tree is empty — the list itself did not arrive." />
              ) : bundles.bundles.length === 0 ? (
                <EmptyState title="No bundles are stored yet" hint="A baseline snapshot appears here once an improvement run takes one." />
              ) : (
                <div className="divide-y divide-border/50">
                  {bundles.bundles.map((bundle) => (
                    <BundleRow key={bundle.bundle_id} bundle={bundle} selected={bundle.bundle_id === selectedBundleId} active={bundle.bundle_id === activeBundleId} onOpen={openBundle} />
                  ))}
                </div>
              )}
              {bundleDetailLoading || bundleDetail !== null || bundleDetailError !== null ? (
                <>
                  <BundleDetailPanel detail={bundleDetail} loading={bundleDetailLoading} error={bundleDetailError} />
                  <BundleDiffPanel diff={diff} loading={diffLoading} error={diffError} />
                </>
              ) : null}
            </>
          )}
        </div>
      </Block>

      {/* ── Promotion ───────────────────────────────────────────────────── */}
      <Block
        title="Promotion ladder"
        hint="The ladder is read from the Gateway, never pinned here. Approving records a decision; activating swaps the pointer; rolling back restores the prior known-good bundle. Every refusal is rendered with the server's own words."
      >
        <div className="space-y-2" data-harness-promotions>
          {promotionsLoading && promotions === null ? (
            <LoadingRows what="the promotion history (/api/harness/promotions)" rows={2} />
          ) : promotionsError !== null ? (
            <UnavailableNotice what="the promotion history" reason={promotionsError} onRetry={loadPromotions} retrying={promotionsLoading} />
          ) : promotions === null ? null : (
            <>
              {promotions.ladder === null ? (
                <p className="text-[11px] text-muted-foreground">The Gateway sent no ladder; the stage transitions are not reported here.</p>
              ) : (
                <div className="flex items-center gap-1.5 flex-wrap text-[10px]" data-harness-ladder>
                  {Object.entries(promotions.ladder).map(([from, to]) => (
                    <Badge key={from} tone="gray" title="One forward edge of the promotion ladder, as the Gateway reports it">
                      {from} → {to}
                    </Badge>
                  ))}
                </div>
              )}

              {promotions.pointer_error !== null && (
                <ErrorBox message={`The promotion pointer could not be read: ${promotions.pointer_error}`} />
              )}
              {promotions.pointer_error === null && promotions.pointer === null && (
                <p className="text-[11px] text-muted-foreground">No active bundle is recorded, so there is nothing to roll back.</p>
              )}

              <div className="rounded-xl border border-border/60 p-3 space-y-2">
                <p className="text-[11px] font-medium">
                  Actions {selectedBundleId === null ? "— select a bundle above first" : `on ${selectedBundleId}`}
                </p>
                <input
                  className={inputCls}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Reason (optional; the server records its own default when empty)"
                />
                <div className="flex items-center gap-1.5 flex-wrap">
                  {HARNESS_APPROVAL_KINDS.map((kind) => (
                    <label key={kind} className="flex items-center gap-1 text-[10px] text-muted-foreground">
                      <input
                        type="checkbox"
                        checked={approvals[kind] === true}
                        onChange={(e) => setApprovals((prev) => ({ ...prev, [kind]: e.target.checked }))}
                      />
                      grant approval: {kind}
                    </label>
                  ))}
                </div>
                <p className="text-[10px] text-muted-foreground">
                  An approval is recorded, not applied — it can never satisfy a substantive gate, and a granted kind the server does not consider pending is refused by name. The server stays the authority on which approvals are pending.
                </p>
                <div className="flex items-center gap-1.5 flex-wrap">
                  <Btn onClick={submitApprove} disabled={busy || selectedBundleId === null}>
                    Approve candidate
                  </Btn>
                  <Btn variant="ghost" onClick={submitReject} disabled={busy || selectedBundleId === null}>
                    Reject candidate
                  </Btn>
                  <Btn onClick={submitActivate} disabled={busy || selectedBundleId === null}>
                    Activate
                  </Btn>
                  <Btn variant="danger" onClick={submitRollback} disabled={busy || activeBundleId === null} title="Restores the active pointer's previous known-good bundle">
                    Roll back the active bundle
                  </Btn>
                </div>
                <p className="text-[10px] text-muted-foreground">
                  Nothing is painted as promoted from a click: every decision re-reads the promotion log, the bundle tree and the posture, and a refusal is shown exactly as the server wrote it.
                </p>
              </div>

              {promotions.unreadable !== null && promotions.unreadable.length > 0 && (
                <Notice tone="warn" message={`${promotions.unreadable.length} promotion log file(s) could not be read and are not shown: ${promotions.unreadable.join(", ")}`} />
              )}
              {promotions.events === null ? (
                <EmptyState title="The Gateway sent no promotion log" hint="That is not a claim that nothing was ever promoted — the log itself did not arrive." />
              ) : promotions.events.length === 0 ? (
                <EmptyState title="No promotion decisions recorded yet" hint="Approvals, activations and rollbacks appear here in the durable log." />
              ) : (
                <div className="divide-y divide-border/50">
                  {promotions.events.map((event, index) => (
                    <PromotionEventRow key={`${index}-${event.at ?? "at"}-${event.bundle_id ?? "bundle"}`} event={event} />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </Block>

      {/* ── Experiences ─────────────────────────────────────────────────── */}
      <Block
        title="Experience library"
        hint="Lessons with their evidence and their computed staleness. Staleness is reported, not applied: a stale entry is shown here, never silently hidden."
      >
        <div className="space-y-2">
          {experiencesLoading && experiences === null ? (
            <LoadingRows what="the experience library (/api/harness/experiences)" rows={2} />
          ) : experiencesError !== null ? (
            <UnavailableNotice what="the experience library" reason={experiencesError} onRetry={loadExperiences} retrying={experiencesLoading} />
          ) : experiences === null ? null : (
            <>
              {experiences.complete === false && <Notice tone="warn" message="The experience listing is partial: the store reported that it could not be read completely." />}
              {experiences.unreadable !== null && experiences.unreadable.length > 0 && (
                <Notice tone="warn" message={`${experiences.unreadable.length} experience file(s) could not be read and are not shown: ${experiences.unreadable.join(", ")}`} />
              )}
              <p className="text-[10px] text-muted-foreground">
                total reported: {countText(experiences.total)} · limit {countText(experiences.limit)}
              </p>
              {experiences.experiences === null ? (
                <EmptyState title="The Gateway sent no experience list" hint="That is not a claim that the library is empty — the list itself did not arrive." />
              ) : experiences.experiences.length === 0 ? (
                <EmptyState title="No experiences recorded yet" hint="A lesson is recorded only when a contrast supports it or records an explicit regression." />
              ) : (
                <div className="divide-y divide-border/50">
                  {experiences.experiences.map((entry) => (
                    <ExperienceRow key={entry.experience_id} entry={entry} />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </Block>

      {/* ── Budgets ─────────────────────────────────────────────────────── */}
      <Block
        title="Budget ledger"
        hint="Ceilings are the declared limits; ledger rows are what was measured. An unreported dimension stays null rather than reading as a spend of zero."
      >
        <div className="space-y-2">
          {budgetsLoading && budgets === null ? (
            <LoadingRows what="the budget ledger (/api/harness/budgets)" rows={2} />
          ) : budgetsError !== null ? (
            <UnavailableNotice what="the budget ledger" reason={budgetsError} onRetry={loadBudgets} retrying={budgetsLoading} />
          ) : budgets === null ? null : (
            <>
              <div className="flex items-center gap-2 flex-wrap">
                <StatCard label="measured model calls" value={countText(budgets.measured_model_calls)} sub="rows in the ledger" />
                {budgets.ceilings === null ? (
                  <StatCard label="ceilings" value="not reported" sub="no cap was read" />
                ) : (
                  Object.entries(budgets.ceilings).map(([key, value]) => <StatCard key={key} label={key} value={scalarText(value)} />)
                )}
              </div>
              {budgets.note !== null && budgets.note !== "" && <p className="text-[10px] text-muted-foreground">{budgets.note}</p>}
              {budgets.complete === false && <Notice tone="warn" message="The ledger is partial: the store reported that it could not be read completely." />}
              {budgets.unreadable !== null && budgets.unreadable.length > 0 && (
                <Notice tone="warn" message={`${budgets.unreadable.length} ledger file(s) could not be read and are not shown: ${budgets.unreadable.join(", ")}`} />
              )}
              {budgets.ledger === null ? (
                <EmptyState title="The Gateway sent no budget ledger" hint="That is not a claim that nothing was spent — the ledger itself did not arrive." />
              ) : budgets.ledger.length === 0 ? (
                <EmptyState title="The ledger is empty" hint="Nothing has been measured yet; the next run records its stage charges here." />
              ) : (
                <div className="divide-y divide-border/50">
                  {budgets.ledger.map((row, index) => (
                    <BudgetRow key={`${index}-${row.run_id ?? "run"}-${row.stage ?? "stage"}`} row={row} />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </Block>
    </Section>
  );
}
