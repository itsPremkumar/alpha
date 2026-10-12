"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  ContractView,
  FinalReport,
  GateView,
  ReconciliationView,
  SCOPE_STATUSES,
  ScopeDetail,
  ScopeList,
  ScopeSummary,
  TaskView,
  approveGate,
  blockTask,
  cancelScope,
  dispatchTask,
  failureText,
  fetchChecks,
  fetchFinalizationPreview,
  fetchHealth,
  fetchReconciliation,
  fetchScope,
  fetchScopes,
  finalizeScope,
  freshnessText,
  gatesPassedText,
  recoverScope,
  reviewGate,
  runGate,
  scheduleScope,
  startScope,
  verifyTask,
} from "@/lib/completion";
import {
  Badge,
  Btn,
  EmptyState,
  ErrorBox,
  Notice,
  Section,
  SkeletonList,
  inputCls,
} from "@/components/ui";
import { relTime } from "@/lib/time";
import { RefreshCw, ShieldCheck, ClipboardCheck } from "lucide-react";

/**
 * The acceptance-contract ledger — the `completion` workspace view.
 *
 * It answers one question a chat transcript cannot: **did the work actually
 * succeed?** Everything it renders is the server's claim; the panel adds no
 * verdict of its own.
 *
 * The rules this surface is built around, each with a tempting wrong reading:
 *
 * | Server says | Panel shows |
 * | --- | --- |
 * | `readable: false` | the ledger's real reason, and *not reported* for every count |
 * | `count: null` | *not reported* — never `0`, which would read as "nothing recorded" |
 * | `scopes: null` | "the Gateway sent no scope list" — not "no scopes" |
 * | `scopes: []` | "no scopes match this filter" |
 * | either read rejected | its own reason, beside the data the *other* read returned |
 * | a gate with `status: passed` and `freshness: current` | the only green |
 * | a gate with `status: waived` | the waiver and its reason — **never** green |
 * | a gate with `freshness: stale` | "a pass from before the last edit cannot answer for work done since" |
 * | a gate with no evidence | "never been checked" — distinct from a failed check |
 * | a task at `returned` | *returned*, never *verified* — only its own gates do that |
 * | `enforced: false` on a report | the decision was computed and **not applied** |
 * | `complete: false` with blockers | the refusal *is* the answer, rendered as a fact |
 * | an unknown status/state string | rendered verbatim in a grey badge |
 * | a 403 on a mutation | the server's own sentence, verbatim |
 *
 * Four design decisions carry most of the weight:
 *
 * 1. **The reads fail independently.** Posture, the list and the detail each
 *    own their own `useCallback` load, so an unreadable ledger cannot blank a
 *    healthy list (or vice versa). There is no `Promise.all` in this file.
 * 2. **Nothing is painted from a click.** Every mutation awaits the route and
 *    then **re-reads** real state. A refused write fires nothing and leaves the
 *    row exactly where it was.
 * 3. **`finalize` is gated on an acknowledgement that starts unchecked**, and
 *    the panel states the safety model beside it: this is the only route to
 *    `complete`, and in enforce mode it moves the scope for good.
 * 4. **Authorisation is never inferred here.** There is no client-side admin
 *    check: the buttons appear for every caller and a member's 403 is rendered
 *    verbatim through `failureText`, because `errMsg` would replace it with a
 *    generic sentence and the refusal *is* the answer.
 *
 * Coverage: `src/lib/completion.test.mjs` (routes, verbs, the null-preserving
 * mappers, the local refusals) and `src/lib/completion-view.test.mjs` (the
 * four-place wiring and every rendering above).
 */

/** Badge tone for a scope status. An unrecognised status is grey, never a known colour. */
function scopeTone(
  status: string,
): "green" | "amber" | "red" | "blue" | "cyan" | "purple" | "gray" {
  switch (status) {
    case "complete":
      return "green";
    case "partial":
      return "amber";
    case "running":
    case "verifying":
      return "blue";
    case "planning":
      return "cyan";
    case "blocked":
    case "handoff_required":
      return "purple";
    case "failed":
    case "cancelled":
      return "red";
    default:
      return "gray";
  }
}

/** Badge tone for a gate status. Only `passed` is green — a waiver is a decision, not a pass. */
function gateTone(
  status: string,
): "green" | "amber" | "red" | "blue" | "purple" | "gray" {
  switch (status) {
    case "passed":
      return "green";
    case "failed":
      return "red";
    case "stale":
      return "amber";
    case "running":
      return "blue";
    case "blocked":
      return "purple";
    // `pending`, `waived`, `abandoned` and anything from a newer Gateway are
    // grey: a waived gate is a *visible owner decision*, and painting it green
    // would be the exact collapse "waived is not passed" exists to stop.
    default:
      return "gray";
  }
}

/** Badge tone for a task state. `verified` is the only green. */
function taskTone(
  state: string,
): "green" | "amber" | "red" | "blue" | "purple" | "cyan" | "gray" {
  switch (state) {
    case "verified":
      return "green";
    case "in_flight":
    case "verifying":
      return "blue";
    case "returned":
      return "cyan";
    case "ready":
      return "cyan";
    case "blocked":
      return "purple";
    case "abandoned":
    case "failed":
      return "red";
    case "waiting":
      return "amber";
    default:
      return "gray";
  }
}

/** A count, with its absence named rather than zeroed. */
function countText(count: number | null): string {
  return count === null ? "not reported" : String(count);
}

/** `met/total`, or *not reported* when either side is missing. */
function ratioText(met: number | null, total: number | null): string {
  if (met === null || total === null) return "not reported";
  return `${met}/${total}`;
}

/** One scope's row in the list. */
function ScopeRow(props: {
  scope: ScopeSummary;
  selected: boolean;
  onOpen: (scopeId: string) => void;
}) {
  const scope = props.scope;
  return (
    <div
      className="px-3 py-2 space-y-1.5"
      data-completion-scope={scope.scope_id}
    >
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[11px] font-medium font-mono">
            {scope.scope_id || "id not reported"}
          </span>
          <Badge
            tone={scopeTone(scope.status)}
            title="The engine's own scope status, verbatim"
          >
            {scope.status === "" ? "status not reported" : scope.status}
          </Badge>
          {scope.mode !== null && scope.mode !== "" && (
            <Badge tone="gray">mode: {scope.mode}</Badge>
          )}
          {scope.contract_valid === false && (
            <Badge
              tone="amber"
              title="A contract with blocking findings refuses dispatch until it is repaired"
            >
              contract invalid
            </Badge>
          )}
          {scope.budget_exhausted === true && (
            <Badge tone="red">budget exhausted</Badge>
          )}
        </span>
        <Btn
          variant={props.selected ? "primary" : "ghost"}
          onClick={() => props.onOpen(scope.scope_id)}
        >
          Inspect
        </Btn>
      </div>
      <p className="text-[11px] text-muted-foreground line-clamp-2">
        {scope.request_snapshot === null
          ? "the original request was not reported"
          : scope.request_snapshot}
      </p>
      <p className="text-[10px] text-muted-foreground">
        requirements{" "}
        {ratioText(scope.requirements_met, scope.requirements_total)} · tasks{" "}
        {ratioText(scope.tasks_verified, scope.tasks_total)} · gates{" "}
        {gatesPassedText(scope)}
        {scope.gates_total !== null ? `/${scope.gates_total}` : ""} · evidence{" "}
        {countText(scope.evidence_total)} · updated{" "}
        {scope.updated_at === null ? "not reported" : relTime(scope.updated_at)}
      </p>
    </div>
  );
}

/** One gate's row inside the detail pane. */
function GateRow(props: {
  gate: GateView;
  onApprove?: () => void;
  onReview?: () => void;
  onRun?: () => void;
}) {
  const gate = props.gate;
  // A gate that has never been checked is not a gate that failed. Both are
  // shown, and neither is allowed to look like a pass. `ev` is bound rather
  // than aliased to a boolean so the null check narrows it directly below.
  const ev = gate.latest_evidence;
  return (
    <div className="px-3 py-2 space-y-1.5" data-completion-gate={gate.gate_id}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[11px] font-medium font-mono">
            {gate.gate_id}
          </span>
          <Badge
            tone={gateTone(gate.status)}
            title="The engine's own gate status, verbatim"
          >
            {gate.status === "" ? "status not reported" : gate.status}
          </Badge>
          {gate.required === true && <Badge tone="gray">required</Badge>}
          {gate.required === false && <Badge tone="gray">optional</Badge>}
          {gate.check_ref !== null && gate.check_ref !== "" && (
            <Badge
              tone="cyan"
              title="A registered check implementation — never free-form shell text from a model"
            >
              {gate.check_ref}
            </Badge>
          )}
          {gate.approval_live === false && (
            <Badge tone="amber">approval stale</Badge>
          )}
          {gate.approval_live === null && (
            <Badge tone="gray">approval liveness not reported</Badge>
          )}
        </span>
        <span className="flex items-center gap-1.5">
          {gate.kind === "manual_review" && props.onReview && (
            <Btn
              onClick={props.onReview}
              title="Record a human verdict, attributed to a named reviewer"
            >
              Review
            </Btn>
          )}
          {gate.kind !== "manual_review" && props.onRun && (
            <Btn
              onClick={props.onRun}
              title="Execute this gate and record its evidence"
            >
              Run
            </Btn>
          )}
          {props.onApprove && (
            <Btn
              onClick={props.onApprove}
              title="Approve this gate's current definition — bound to its digest"
            >
              Approve
            </Btn>
          )}
        </span>
      </div>
      <p className="text-[11px]">
        {gate.title === null ? "title not reported" : gate.title}
      </p>
      <p
        className={`text-[10px] ${ev !== null && ev.freshness === "current" ? "text-muted-foreground" : "text-amber-700 dark:text-amber-300"}`}
      >
        {freshnessText(gate)}
        {ev !== null && ev.outcome !== "" && <> · last outcome: {ev.outcome}</>}
        {ev !== null && ev.worker !== null && ev.worker !== "" && (
          <> · by {ev.worker}</>
        )}
      </p>
      {gate.waiver_reason !== null && gate.waiver_reason !== "" && (
        <p className="text-[10px] text-muted-foreground">
          Waived by an owner decision: {gate.waiver_reason} — a waived gate is a
          visible decision and finalization still treats it as a blocker.
        </p>
      )}
      {gate.last_failure !== null && gate.last_failure !== "" && (
        <p className="text-[10px] text-red-700 dark:text-red-300">
          last failure: {gate.last_failure}
        </p>
      )}
    </div>
  );
}

/** One task's row inside the detail pane. */
function TaskRow(props: {
  task: TaskView;
  onBlock?: () => void;
  onVerify?: () => void;
  onDispatch?: () => void;
}) {
  const task = props.task;
  return (
    <div className="px-3 py-2 space-y-1.5" data-completion-task={task.task_id}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[11px] font-medium font-mono">
            {task.task_id}
          </span>
          <Badge
            tone={taskTone(task.state)}
            title="The engine's own task state, verbatim"
          >
            {task.state === "" ? "state not reported" : task.state}
          </Badge>
          {task.depth !== null && task.depth > 0 && (
            <Badge tone="gray">depth {task.depth}</Badge>
          )}
          {task.dispatchable === true && (
            <Badge
              tone="cyan"
              title="Every dependency is verified and capacity remains"
            >
              dispatchable
            </Badge>
          )}
          {task.blocking !== null && task.blocking.length > 0 && (
            <Badge tone="purple" title="Task ids this one is waiting on">
              waiting on {task.blocking.length}
            </Badge>
          )}
        </span>
        <span className="flex items-center gap-1.5">
          {props.onDispatch && (
            <Btn
              onClick={props.onDispatch}
              title="Request a launch — the task stays READY until a host confirms"
            >
              Dispatch
            </Btn>
          )}
          {props.onVerify && (
            <Btn
              onClick={props.onVerify}
              title="Run this task's gates and record the evidence"
            >
              Verify
            </Btn>
          )}
          {props.onBlock && (
            <Btn
              onClick={props.onBlock}
              title="Mark blocked — dependents are parked rather than bypassed"
            >
              Block
            </Btn>
          )}
        </span>
      </div>
      <p className="text-[11px]">
        {task.title === null ? "title not reported" : task.title}
      </p>
      {task.blocked_reason !== null && task.blocked_reason !== "" && (
        <p className="text-[10px] text-amber-700 dark:text-amber-300">
          blocked: {task.blocked_reason}
        </p>
      )}
      {task.readiness_reason !== null && task.readiness_reason !== "" && (
        <p className="text-[10px] text-muted-foreground">
          {task.readiness_reason}
        </p>
      )}
      {task.owns !== null && task.owns.length > 0 && (
        <p className="text-[10px] text-muted-foreground font-mono break-all">
          owns: {task.owns.join(", ")} — claimed before launch so two workers
          cannot share a path.
        </p>
      )}
    </div>
  );
}

/** The finalization report block, which is where "enforced" and "complete" part ways. */
function FinalReportPanel(props: { report: FinalReport; label: string }) {
  const report = props.report;
  const blockers = report.blockers ?? [];
  return (
    <div
      className="rounded-xl border border-border/60 p-3 space-y-2"
      data-completion-final-report="1"
    >
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="text-xs font-semibold">{props.label}</span>
        <span className="flex items-center gap-1.5 flex-wrap">
          {report.complete === true ? (
            <Badge tone="green">complete</Badge>
          ) : report.complete === false ? (
            <Badge tone="amber">not complete</Badge>
          ) : (
            <Badge tone="gray">complete not reported</Badge>
          )}
          {report.enforced === false ? (
            <Badge
              tone="blue"
              title="Observe mode computes the decision and records it without moving the scope"
            >
              enforced: false — decision recorded, status unchanged
            </Badge>
          ) : report.enforced === true ? (
            <Badge tone="gray">enforced</Badge>
          ) : (
            <Badge tone="gray">enforcement not reported</Badge>
          )}
        </span>
      </div>
      {report.summary !== null && report.summary !== "" && (
        <p className="text-[11px]">{report.summary}</p>
      )}
      <p className="text-[10px] text-muted-foreground">
        checks ran {countText(report.checks_ran)} · passed{" "}
        {countText(report.checks_passed)} · failed{" "}
        {countText(report.checks_failed)} · stale{" "}
        {countText(report.checks_stale)} · not run{" "}
        {countText(report.checks_not_run)} · requirements{" "}
        {ratioText(report.requirements_met, report.requirements_total)} ·
        attempt {countText(report.attempt)}
      </p>
      {report.not_rerun !== null && report.not_rerun.length > 0 && (
        <p className="text-[10px] text-amber-700 dark:text-amber-300">
          Not re-executed at this boundary: {report.not_rerun.join(", ")} — a
          required gate that was not re-run cannot prove the world is unchanged.
        </p>
      )}
      {report.blockers === null ? (
        <p className="text-[10px] text-muted-foreground">
          blockers not reported — the Gateway sent no blocker list.
        </p>
      ) : blockers.length === 0 ? (
        <p className="text-[10px] text-muted-foreground">
          no blockers recorded.
        </p>
      ) : (
        <ul className="space-y-1">
          {blockers.map((blocker, index) => (
            <li
              key={`${blocker.code}-${blocker.subject_id}-${index}`}
              className="text-[10px] text-amber-700 dark:text-amber-300"
            >
              <span className="font-mono">{blocker.code}</span>
              {blocker.kind !== null && blocker.kind !== "" && (
                <> ({blocker.kind})</>
              )}
              {blocker.subject_id !== null && blocker.subject_id !== "" && (
                <> on {blocker.subject_id}</>
              )}
              : {blocker.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The reconciliation audit, which names its own basis because it is a heuristic. */
function ReconciliationPanel(props: { report: ReconciliationView }) {
  const report = props.report;
  const unrepresented = report.unrepresented ?? [];
  const unmet = report.unmet ?? [];
  const unverifiable = report.unverifiable ?? [];
  return (
    <div
      className="rounded-xl border border-border/60 p-3 space-y-2"
      data-completion-reconciliation="1"
    >
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="text-xs font-semibold">Request → contract audit</span>
        {report.clean === true ? (
          <Badge tone="green">clean</Badge>
        ) : report.clean === false ? (
          <Badge tone="amber">gaps found</Badge>
        ) : (
          <Badge tone="gray">clean not reported</Badge>
        )}
      </div>
      <p className="text-[10px] text-muted-foreground">
        basis: {report.basis ?? "not reported"} — this matching is a heuristic,
        and it says so. clauses seen {countText(report.clauses_seen)}.
      </p>
      {unrepresented.length > 0 && (
        <p className="text-[10px] text-amber-700 dark:text-amber-300">
          clauses with no requirement: {unrepresented.join("; ")}
        </p>
      )}
      {unmet.length > 0 && (
        <p className="text-[10px] text-amber-700 dark:text-amber-300">
          mandatory requirements without current evidence: {unmet.join("; ")}
        </p>
      )}
      {unverifiable.length > 0 && (
        <p className="text-[10px] text-amber-700 dark:text-amber-300">
          bound to no observable at all: {unverifiable.join("; ")}
        </p>
      )}
      {report.notes !== null && report.notes !== "" && (
        <p className="text-[10px] text-muted-foreground">{report.notes}</p>
      )}
    </div>
  );
}

export function CompletionSection() {
  const [health, setHealth] = useState<unknown>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [checks, setChecks] = useState<unknown>(null);
  const [checksError, setChecksError] = useState<string | null>(null);
  const [list, setList] = useState<ScopeList | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [statusFilter, setStatusFilter] = useState<string>("");
  const [limit, setLimit] = useState<number>(50);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ScopeDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [preview, setPreview] = useState<FinalReport | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [reconciliation, setReconciliation] =
    useState<ReconciliationView | null>(null);
  const [reconciliationError, setReconciliationError] = useState<string | null>(
    null,
  );

  const [acknowledged, setAcknowledged] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionNote, setActionNote] = useState<string | null>(null);
  const [acting, setActing] = useState(false);

  // The engine's posture: enabled/mode/enforcing, and whether the ledger is even
  // readable. An unreadable ledger is a 503-shaped fact, not an empty panel.
  const loadHealth = useCallback(async () => {
    try {
      setHealth(await fetchHealth());
      setHealthError(null);
    } catch (err) {
      setHealthError(failureText(err));
    }
  }, []);

  const loadChecks = useCallback(async () => {
    try {
      setChecks(await fetchChecks());
      setChecksError(null);
    } catch (err) {
      setChecksError(failureText(err));
    }
  }, []);

  const loadList = useCallback(async () => {
    try {
      setList(
        await fetchScopes({
          status: statusFilter === "" ? undefined : statusFilter,
          limit,
        }),
      );
      setListError(null);
    } catch (err) {
      setListError(failureText(err));
    }
  }, [statusFilter, limit]);

  const loadDetail = useCallback(async (scopeId: string) => {
    try {
      setDetail(await fetchScope(scopeId));
      setDetailError(null);
    } catch (err) {
      setDetailError(failureText(err));
      setDetail(null);
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    await Promise.all([loadHealth(), loadChecks(), loadList()]);
    setLoading(false);
  }, [loadHealth, loadChecks, loadList]);

  useEffect(() => {
    void load();
  }, [load]);

  const openScope = useCallback(
    async (scopeId: string) => {
      setSelectedId(scopeId);
      setDetail(null);
      setPreview(null);
      setReconciliation(null);
      setPreviewError(null);
      setReconciliationError(null);
      setActionError(null);
      setActionNote(null);
      setAcknowledged(false);
      setDetailLoading(true);
      // Three reads, three outcomes: the projection, the decision it would
      // earn, and the request audit. A preview that 404s must not blank the
      // tasks the projection returned.
      await Promise.all([
        loadDetail(scopeId),
        (async () => {
          try {
            setPreview(await fetchFinalizationPreview(scopeId));
            setPreviewError(null);
          } catch (err) {
            setPreviewError(failureText(err));
          }
        })(),
        (async () => {
          try {
            setReconciliation(await fetchReconciliation(scopeId));
            setReconciliationError(null);
          } catch (err) {
            setReconciliationError(failureText(err));
          }
        })(),
      ]);
      setDetailLoading(false);
    },
    [loadDetail],
  );

  const closeScope = useCallback(() => {
    setSelectedId(null);
    setDetail(null);
    setPreview(null);
    setReconciliation(null);
    setDetailError(null);
    setPreviewError(null);
    setReconciliationError(null);
    setActionError(null);
    setActionNote(null);
    setAcknowledged(false);
  }, []);

  /**
   * Run one mutation, then re-read everything it could have moved.
   *
   * Nothing is painted from the click: the route's own response is discarded
   * except for an error, and the panel re-reads the projection, the preview and
   * the list. A refused write fires no re-read and leaves the row where it was.
   */
  const act = useCallback(
    async (note: string, fn: () => Promise<unknown>) => {
      if (selectedId === null) return;
      setActing(true);
      setActionError(null);
      setActionNote(null);
      try {
        await fn();
        setActionNote(note);
        setAcknowledged(false);
        await Promise.all([loadDetail(selectedId), loadList()]);
        try {
          setPreview(await fetchFinalizationPreview(selectedId));
        } catch (err) {
          setPreviewError(failureText(err));
        }
      } catch (err) {
        // A 403 or a 409 lands here with the server's own words; `errMsg`
        // would replace both with a generic sentence and lose the answer.
        setActionError(failureText(err));
      } finally {
        setActing(false);
      }
    },
    [selectedId, loadDetail, loadList],
  );

  const scopes = list?.scopes ?? null;
  const contract: ContractView | null = detail?.contract ?? null;
  const tasks: TaskView[] | null = detail?.tasks ?? null;
  const gates: GateView[] | null = detail?.gates ?? null;
  const summary: ScopeSummary | null = detail?.scope ?? null;
  const report: FinalReport | null = detail?.final_report ?? null;

  return (
    <Section
      title="Completion engine"
      hint="The acceptance contract fixed before work started, the gates that prove it, and the guard that is the only route to 'complete'. 'Returned' is not 'verified', 'not run' is not 'passed', and observe mode never claims a completion it did not apply. Nothing here starts a run."
      actions={
        <Btn
          onClick={() => void load()}
          disabled={loading}
          title="Re-read the posture, the catalog and the scope list"
        >
          <RefreshCw className="size-3.5" />
          {loading ? "Refreshing…" : "Refresh"}
        </Btn>
      }
    >
      <div className="space-y-4" data-completion-section="ledger">
        {/* ── Posture ─────────────────────────────────────────────────── */}
        {healthError !== null && (
          <Notice
            tone="warn"
            message={`The engine posture read failed: ${healthError}. The scope list below is unaffected.`}
          />
        )}
        {checksError !== null && (
          <Notice
            tone="warn"
            message={`The check catalog read failed: ${checksError}. The scope list below is unaffected.`}
          />
        )}

        {health === null && healthError === null ? (
          <SkeletonList rows={2} />
        ) : health === null ? (
          <ErrorBox
            message={healthError ?? "the posture read failed"}
            onRetry={() => void loadHealth()}
          />
        ) : (
          <PostureCard health={health} checks={checks} />
        )}

        {/* ── The list ────────────────────────────────────────────────── */}
        {listError !== null && (
          <Notice
            tone="warn"
            message={`The scope list read failed: ${listError}. The posture above is unaffected.`}
          />
        )}

        <div className="flex items-end gap-2 flex-wrap">
          <label className="block space-y-1">
            <span className="text-[11px] font-semibold">Status</span>
            <select
              className={`${inputCls} w-44`}
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="">all statuses</option>
              {SCOPE_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-[11px] font-semibold">Page size</span>
            <select
              className={`${inputCls} w-28`}
              value={String(limit)}
              onChange={(e) => setLimit(Number(e.target.value))}
            >
              {[25, 50, 100, 250, 500].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        </div>

        {selectedId !== null && (
          <DetailPane
            scopeId={selectedId}
            summary={summary}
            contract={contract}
            tasks={tasks}
            gates={gates}
            detail={detail}
            detailError={detailError}
            detailLoading={detailLoading}
            preview={preview}
            previewError={previewError}
            reconciliation={reconciliation}
            reconciliationError={reconciliationError}
            report={report}
            acknowledged={acknowledged}
            onAcknowledgeChange={setAcknowledged}
            actionError={actionError}
            actionNote={actionNote}
            acting={acting}
            onAct={(note, fn) => void act(note, fn)}
            onClose={closeScope}
          />
        )}

        <div className="rounded-xl border border-border/60">
          <div className="px-3 py-2 border-b border-border/60 flex items-center justify-between gap-2 flex-wrap">
            <span className="text-xs font-semibold">Scopes</span>
            <span className="text-[10px] text-muted-foreground">
              {list === null
                ? loading
                  ? "reading…"
                  : "not read"
                : `${countText(list.count)} shown · scope ${list.scope ?? "not reported"} · newest first · up to ${limit} per page`}
            </span>
          </div>
          {list === null && listError === null ? (
            <div className="p-3">
              <SkeletonList rows={3} />
            </div>
          ) : list === null ? (
            <div className="p-3">
              <EmptyState
                title="The scope list could not be read"
                hint="This is a failed read, not an empty ledger. The reason is in the notice above."
              />
            </div>
          ) : scopes === null ? (
            <div className="p-3">
              <EmptyState
                title="The Gateway sent no scope list"
                hint="This is not a claim that no completion scope was ever opened — no list arrived at all."
              />
            </div>
          ) : scopes.length === 0 ? (
            <div className="p-3">
              <EmptyState
                title="No scopes match this filter"
                hint="The Gateway answered with an empty list. Completion scopes are opt-in: nothing in the normal chat, run, bot or scheduler paths opens one."
              />
            </div>
          ) : (
            <div className="divide-y divide-border/40">
              {scopes.map((scope) => (
                <ScopeRow
                  key={scope.scope_id}
                  scope={scope}
                  selected={scope.scope_id === selectedId}
                  onOpen={(id) => void openScope(id)}
                />
              ))}
            </div>
          )}
          {scopes !== null &&
            scopes.length > 0 &&
            list !== null &&
            list.count !== null &&
            list.count > scopes.length && (
              <p className="px-3 py-2 text-[10px] text-muted-foreground border-t border-border/40">
                Showing {scopes.length} of {list.count} — the page is bounded by
                the limit above, not by the ledger's total.
              </p>
            )}
        </div>

        <p className="text-[10px] text-muted-foreground">
          routes GET /completion/health, /checks, /scopes,
          /scopes/&#123;id&#125; (+ /contract, /events, /reconciliation,
          /finalization/preview) · the lifecycle verbs are POST and every one is
          admin-gated server-side. This panel decides nothing about who you are;
          a refusal is rendered as it was written. The ledger is an advisory
          single-process JSON file — restart-recoverable, never cross-process
          exactly-once.
        </p>
      </div>
    </Section>
  );
}

/** The posture card: enabled/mode/enforcing, ledger readability, and the check allowlist. */
function PostureCard(props: { health: unknown; checks: unknown }) {
  const h = props.health as Record<string, unknown>;
  const readable = h.readable === true;
  const enforcing = h.enforcing === true;
  const mode = typeof h.mode === "string" ? h.mode : null;
  const enabled = h.enabled === true;
  const checkList =
    props.checks !== null &&
    typeof props.checks === "object" &&
    Array.isArray((props.checks as Record<string, unknown>).checks)
      ? ((props.checks as Record<string, unknown>).checks as string[])
      : null;

  return (
    <div
      className="rounded-xl border border-border/60 p-3 space-y-2"
      data-completion-posture="1"
    >
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="flex items-center gap-1.5 flex-wrap">
          <ShieldCheck className="size-4" />
          <span className="text-xs font-semibold">Engine posture</span>
          {enabled ? (
            <Badge tone="green">enabled</Badge>
          ) : enabled === false ? (
            <Badge tone="gray">disabled</Badge>
          ) : (
            <Badge tone="gray">enabled not reported</Badge>
          )}
          {mode !== null && (
            <Badge tone={mode === "enforce" ? "blue" : "cyan"}>
              mode: {mode}
            </Badge>
          )}
          {enforcing ? (
            <Badge
              tone="blue"
              title="Enforcing is enabled *and* enforce mode, so a disabled engine never reads as a live gate"
            >
              enforcing
            </Badge>
          ) : (
            <Badge
              tone="gray"
              title="The engine is not currently gating completion"
            >
              not enforcing
            </Badge>
          )}
        </span>
      </div>
      {readable ? (
        <p className="text-[10px] text-muted-foreground">
          ledger readable at{" "}
          <span className="font-mono break-all">
            {typeof h.path === "string" ? h.path : "path not reported"}
          </span>{" "}
          · scopes {countText(typeof h.count === "number" ? h.count : null)} ·
          events {countText(typeof h.events === "number" ? h.events : null)} ·
          policy{" "}
          {typeof h.policy_id === "string" ? h.policy_id : "not reported"}
        </p>
      ) : (
        <p className="text-[10px] text-amber-700 dark:text-amber-300">
          The ledger could not be read:{" "}
          {typeof h.error === "string" ? h.error : "no reason reported"} — every
          count reads *not reported* rather than 0, because "nothing recorded"
          and "could not look" are opposite claims.
        </p>
      )}
      {checkList === null ? (
        <p className="text-[10px] text-muted-foreground">
          check catalog not reported — the Gateway sent no allowlist, so no gate
          name can be verified here.
        </p>
      ) : checkList.length === 0 ? (
        <p className="text-[10px] text-amber-700 dark:text-amber-300">
          the Gateway reported an empty check allowlist — no gate could be
          satisfied at all.
        </p>
      ) : (
        <p className="text-[10px] text-muted-foreground">
          {checkList.length} registered checks — every gate names one of these
          rather than free-form shell text from a model:{" "}
          <span className="font-mono">{checkList.join(", ")}</span>
        </p>
      )}
    </div>
  );
}

/** Everything about one selected scope: contract, tasks, gates, the audit and the guard. */
function DetailPane(props: {
  scopeId: string;
  summary: ScopeSummary | null;
  contract: ContractView | null;
  tasks: TaskView[] | null;
  gates: GateView[] | null;
  detail: ScopeDetail | null;
  detailError: string | null;
  detailLoading: boolean;
  preview: FinalReport | null;
  previewError: string | null;
  reconciliation: ReconciliationView | null;
  reconciliationError: string | null;
  report: FinalReport | null;
  acknowledged: boolean;
  onAcknowledgeChange: (value: boolean) => void;
  actionError: string | null;
  actionNote: string | null;
  acting: boolean;
  onAct: (note: string, fn: () => Promise<unknown>) => void;
  onClose: () => void;
}) {
  const p = props;
  const [reason, setReason] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [verdict, setVerdict] = useState<"pass" | "fail">("pass");

  const summary = p.summary;
  const gates = p.gates;

  return (
    <div
      className="rounded-xl border border-border/60 p-3 space-y-3"
      data-completion-detail={p.scopeId}
    >
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="flex items-center gap-1.5 flex-wrap">
          <ClipboardCheck className="size-4" />
          <span className="text-xs font-semibold font-mono">{p.scopeId}</span>
          {summary !== null && (
            <Badge
              tone={scopeTone(summary.status)}
              title="The engine's own scope status, verbatim"
            >
              {summary.status}
            </Badge>
          )}
          {p.detail !== null && p.detail.enforcing === true && (
            <Badge tone="blue">enforcing</Badge>
          )}
          {p.detail !== null && p.detail.enforcing === false && (
            <Badge tone="gray">
              observe / disabled — decisions are recorded, statuses are left
              alone
            </Badge>
          )}
        </span>
        <Btn onClick={p.onClose} variant="ghost">
          Close
        </Btn>
      </div>

      {summary !== null && (
        <p className="text-[10px] text-muted-foreground">
          owner {summary.owner_id ?? "not reported"} · thread{" "}
          {summary.thread_id ?? "not reported"} · requirements{" "}
          {ratioText(summary.requirements_met, summary.requirements_total)} ·
          tasks verified {countText(summary.tasks_verified)} of{" "}
          {countText(summary.tasks_total)} · gates passed{" "}
          {gatesPassedText(summary)} · finalize attempts{" "}
          {countText(summary.finalize_attempts)} · budget{" "}
          {summary.budget_exhausted === true
            ? "exhausted"
            : summary.budget_exhausted === false
              ? "ok"
              : "not reported"}
        </p>
      )}

      {p.actionNote !== null && (
        <Notice
          tone="success"
          message={`${p.actionNote} The panel re-read the ledger rather than trusting this response.`}
        />
      )}
      {p.actionError !== null && <Notice tone="warn" message={p.actionError} />}

      {/* ── Lifecycle controls ─────────────────────────────────────── */}
      <div className="flex items-end gap-2 flex-wrap border-t border-border/50 pt-3">
        <Btn
          disabled={p.acting}
          onClick={() =>
            p.onAct("Started the scope.", () => startScope(p.scopeId))
          }
          title="Start the work, or resume a parked scope with its reason recorded"
        >
          {p.acting ? "Working…" : "Start"}
        </Btn>
        <Btn
          disabled={p.acting}
          onClick={() =>
            p.onAct("Recomputed readiness.", () => scheduleScope(p.scopeId))
          }
          title="Recompute readiness and report what may start under the ceiling"
        >
          Schedule
        </Btn>
        <Btn
          disabled={p.acting}
          onClick={() =>
            p.onAct("Folded the host's report into the ledger.", () =>
              recoverScope(p.scopeId, {}),
            )
          }
          title="Reconnect reconciliation — folds what a host reports back in, rather than assuming a wave completed"
        >
          Recover
        </Btn>
        <label className="block space-y-1">
          <span className="text-[11px] font-semibold">
            Reason (cancel/block)
          </span>
          <input
            className={`${inputCls} w-56`}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="why this stops"
          />
        </label>
        <Btn
          variant="danger"
          disabled={p.acting}
          onClick={() =>
            p.onAct("Cancelled the scope with the recorded reason.", () =>
              cancelScope(p.scopeId, reason),
            )
          }
          title="Cancel with a recorded reason — live tasks are abandoned, not verified"
        >
          Cancel
        </Btn>
      </div>

      {/* ── Contract ────────────────────────────────────────────────── */}
      {p.contract === null ? (
        p.detailError !== null ? null : (
          <p className="text-[10px] text-muted-foreground">
            contract not reported — the projection carried no contract block.
          </p>
        )
      ) : (
        <div
          className="rounded-xl border border-border/60 p-3 space-y-2"
          data-completion-contract="1"
        >
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <span className="text-xs font-semibold">Acceptance contract</span>
            <span className="flex items-center gap-1.5 flex-wrap">
              <Badge tone="gray">v{p.contract.version ?? "?"}</Badge>
              {p.contract.valid === true ? (
                <Badge tone="green">valid</Badge>
              ) : p.contract.valid === false ? (
                <Badge
                  tone="amber"
                  title="A contract with blocking findings refuses dispatch until it is repaired"
                >
                  invalid — dispatch refused
                </Badge>
              ) : (
                <Badge tone="gray">validity not reported</Badge>
              )}
            </span>
          </div>
          <p className="text-[10px] text-muted-foreground font-mono break-all">
            digest {p.contract.digest ?? "not reported"}
          </p>
          {p.contract.findings !== null && p.contract.findings.length > 0 && (
            <ul className="space-y-1">
              {p.contract.findings.map((finding, index) => (
                <li
                  key={`${finding.code}-${index}`}
                  className={`text-[10px] ${finding.severity === "error" ? "text-amber-700 dark:text-amber-300" : "text-muted-foreground"}`}
                >
                  <span className="font-mono">{finding.code}</span> (
                  {finding.severity})
                  {finding.subject_id !== "" && <> on {finding.subject_id}</>}:{" "}
                  {finding.message}
                </li>
              ))}
            </ul>
          )}
          {p.contract.requirements !== null &&
            p.contract.requirements.length > 0 && (
              <ul className="space-y-1">
                {p.contract.requirements.map((req) => (
                  <li key={req.requirement_id} className="text-[10px]">
                    <Badge
                      tone={
                        req.status === "met"
                          ? "green"
                          : req.status === "not_met"
                            ? "red"
                            : "gray"
                      }
                      title="An unreviewed requirement is unverified, never met"
                    >
                      {req.status ?? "not reported"}
                    </Badge>{" "}
                    {req.intent ?? "intent not reported"}
                    {req.observable === "" && req.observable !== null && (
                      <> — bound to no observable yet</>
                    )}
                  </li>
                ))}
              </ul>
            )}
        </div>
      )}

      {/* ── Tasks ───────────────────────────────────────────────────── */}
      <div className="rounded-xl border border-border/60">
        <div className="px-3 py-2 border-b border-border/60 flex items-center justify-between gap-2">
          <span className="text-xs font-semibold">Task DAG</span>
          <span className="text-[10px] text-muted-foreground">
            {p.tasks === null ? "not reported" : `${p.tasks.length} tasks`}
          </span>
        </div>
        {p.tasks === null ? (
          <div className="p-3">
            <EmptyState
              title="The Gateway sent no task list"
              hint="This is not a claim that the plan has no tasks — no list arrived at all."
            />
          </div>
        ) : p.tasks.length === 0 ? (
          <div className="p-3">
            <EmptyState
              title="This scope has no tasks"
              hint="A scope without tasks cannot be dispatched, and finalization will report it as unverified work."
            />
          </div>
        ) : (
          <div className="divide-y divide-border/40">
            {p.tasks.map((task) => (
              <TaskRow
                key={task.task_id}
                task={task}
                onDispatch={() =>
                  p.onAct(`Requested a launch for ${task.task_id}.`, () =>
                    dispatchTask(p.scopeId, task.task_id),
                  )
                }
                onVerify={() =>
                  p.onAct(`Ran ${task.task_id}'s gates.`, () =>
                    verifyTask(p.scopeId, task.task_id, "verify"),
                  )
                }
                onBlock={() =>
                  p.onAct(`Marked ${task.task_id} blocked.`, () =>
                    blockTask(
                      p.scopeId,
                      task.task_id,
                      reason || "blocked from the completion panel",
                    ),
                  )
                }
              />
            ))}
          </div>
        )}
      </div>

      {/* ── Gates ───────────────────────────────────────────────────── */}
      <div className="rounded-xl border border-border/60">
        <div className="px-3 py-2 border-b border-border/60 flex items-center justify-between gap-2">
          <span className="text-xs font-semibold">Gates</span>
          <span className="text-[10px] text-muted-foreground">
            {gates === null
              ? "not reported"
              : `${gates.length} gates · only a \`passed\` gate with \`current\` evidence satisfies a requirement`}
          </span>
        </div>
        {gates === null ? (
          <div className="p-3">
            <EmptyState
              title="The Gateway sent no gate list"
              hint="This is not a claim that no proof obligation exists — no list arrived at all."
            />
          </div>
        ) : gates.length === 0 ? (
          <div className="p-3">
            <EmptyState
              title="This scope has no gates"
              hint="Without gates nothing can be verified: a task would move to 'returned' and stay there, because returned is not verified."
            />
          </div>
        ) : (
          <div className="divide-y divide-border/40">
            {gates.map((gate) => (
              <GateRow
                key={gate.gate_id}
                gate={gate}
                onRun={() =>
                  p.onAct(`Executed ${gate.gate_id}.`, () =>
                    runGate(p.scopeId, gate.gate_id),
                  )
                }
                onApprove={() =>
                  p.onAct(
                    `Approved ${gate.gate_id}'s current definition.`,
                    () =>
                      approveGate(
                        p.scopeId,
                        gate.gate_id,
                        reviewer || "panel",
                        reason,
                      ),
                  )
                }
                onReview={
                  gate.kind === "manual_review"
                    ? () =>
                        p.onAct(
                          `Recorded a ${verdict} review on ${gate.gate_id}.`,
                          () =>
                            reviewGate(
                              p.scopeId,
                              gate.gate_id,
                              verdict,
                              reviewer || "panel",
                              reason,
                            ),
                        )
                    : undefined
                }
              />
            ))}
          </div>
        )}
      </div>

      {/* ── Review inputs (only when a manual-review gate exists) ───── */}
      {gates !== null && gates.some((g) => g.kind === "manual_review") && (
        <div className="flex items-end gap-2 flex-wrap border-t border-border/50 pt-3">
          <label className="block space-y-1">
            <span className="text-[11px] font-semibold">Reviewer name</span>
            <input
              className={`${inputCls} w-44`}
              value={reviewer}
              onChange={(e) => setReviewer(e.target.value)}
              placeholder="who is reviewing"
            />
            <span className="block text-[10px] text-muted-foreground">
              A review lands as evidence attributed to this name — never as an
              automated pass.
            </span>
          </label>
          <label className="block space-y-1">
            <span className="text-[11px] font-semibold">Verdict</span>
            <select
              className={`${inputCls} w-28`}
              value={verdict}
              onChange={(e) =>
                setVerdict(e.target.value === "fail" ? "fail" : "pass")
              }
            >
              <option value="pass">pass</option>
              <option value="fail">fail</option>
            </select>
          </label>
        </div>
      )}

      {/* ── The audit ───────────────────────────────────────────────── */}
      {p.reconciliationError !== null ? (
        <Notice
          tone="warn"
          message={`The request audit read failed: ${p.reconciliationError}. The tasks and gates above are unaffected.`}
        />
      ) : p.reconciliation !== null ? (
        <ReconciliationPanel report={p.reconciliation} />
      ) : p.detailLoading ? null : (
        <p className="text-[10px] text-muted-foreground">
          request audit not reported.
        </p>
      )}

      {/* ── The guard ───────────────────────────────────────────────── */}
      {p.report !== null && (
        <FinalReportPanel report={p.report} label="Last finalization" />
      )}

      {p.previewError !== null ? (
        <Notice
          tone="warn"
          message={`The finalization preview read failed: ${p.previewError}. Nothing on this screen was changed by that read.`}
        />
      ) : p.preview !== null ? (
        <div className="space-y-2">
          <FinalReportPanel
            report={p.preview}
            label="What finalize would decide right now"
          />
          <p className="text-[10px] text-muted-foreground">
            This preview is a read. It never moves a scope, and its report is
            labelled <span className="font-mono">enforced: false</span> — the
            decision is computed from the evidence recorded now, not applied.
          </p>
        </div>
      ) : null}

      <div className="space-y-2 border-t border-border/50 pt-3">
        <label className="flex items-start gap-2 text-[11px]">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={p.acknowledged}
            onChange={(e) => p.onAcknowledgeChange(e.target.checked)}
          />
          <span>
            I understand this is the <strong>only</strong> route to{" "}
            <code>complete</code>. In enforce mode it moves the scope for good;
            in observe mode it records the same decision with{" "}
            <code>enforced: false</code> and leaves the status alone. It re-runs
            required gates first, so a stale pass cannot answer for work done
            since.
          </span>
        </label>
        <div className="flex items-center gap-2 flex-wrap">
          <Btn
            disabled={!p.acknowledged || p.acting}
            onClick={() =>
              p.onAct("Ran the finalization guard.", () =>
                finalizeScope(p.scopeId, true),
              )
            }
            title="The completion guard — the only route to complete"
          >
            Finalize
          </Btn>
          <span className="text-[10px] text-muted-foreground">
            {p.acknowledged
              ? "The guard will evaluate the contract, every required gate's freshness, the task DAG and the original request."
              : "Tick the box to enable Finalize."}
          </span>
        </div>
      </div>
    </div>
  );
}
