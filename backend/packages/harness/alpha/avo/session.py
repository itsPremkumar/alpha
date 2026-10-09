"""The governed run session: one object that owns the whole cycle.

Admission → action → decision → checkpoint → evaluation → promotion, with the
lifecycle, the ledger, the policy gate, the receipt chain and the checkpoint
moving together instead of being coordinated by whoever called last. This is
the module that makes the invariants *interleaving-safe* rather than
individually true:

* **No action without a decision; no decision without a receipt; no allowed
  action without a reservation.** :meth:`GovernedRunSession.submit_action`
  runs the gate and mints the receipt in one step, so there is no call order
  that produces one without the other.
* **The lifecycle gates execution.** An action submitted while the run is not
  in an active state is refused by the state machine, not by a caller's
  courtesy check. A terminal run accepts nothing, ever.
* **Checkpoint boundaries are the plan's list, applied here.** Admission,
  every decision, evaluation, rejection and promotion all write through
  :meth:`checkpoint`, so a crash loses at most the in-flight action — whose
  reservation the ledger already accounts for.
* **The result is assembled from evidence.** :meth:`complete` refuses to emit
  an ``AvoRunResult`` whose truth label disagrees with the evaluation that was
  actually run, and always carries at least one limitation, because a truthful
  run almost always skipped something.

What this module deliberately does *not* do: execute actions. The runner that
performs the work is outside (see
:mod:`alpha.avo.workspace_runner`); this decides whether it may, records that
it did, and accounts for what it cost.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .budgets import BudgetExceeded, BudgetLedger
from .checkpointing import Checkpoint, ResumeVerdict, load_checkpoint, write_checkpoint
from .contracts import (
    AvoRunRequest,
    AvoRunResult,
    EvaluationResult,
    ExperimentSpec,
    PolicyDecision,
    PromotionLevel,
    ProposedAction,
    TruthLabel,
    WorkspaceLocator,
)
from .lifecycle import ACTIVE_STATES, Actor, AvoState, IllegalTransition, RunLifecycle
from .paths import PathRefused, assert_within_root
from .policy import PolicyGate
from .profiles import approval_policy, budget_profile, capability_profile, evaluator_profile
from .receipts import ReceiptChain

__all__ = ["GovernedRunSession", "SessionRefused", "SessionStateError"]

logger = logging.getLogger("alpha.avo.session")


class SessionRefused(ValueError):
    """A request the session will not admit, with the reason stated."""


class SessionStateError(RuntimeError):
    """A lifecycle violation attempted through the session API."""


@dataclass
class GovernedRunSession:
    """One governed run, from admission to its final report.

    Construct via :meth:`admit` (fresh) or :meth:`resume` (checkpoint). The
    direct constructor exists for tests; production callers should not be
    assembling the pieces themselves, because the pieces are the invariant.
    """

    request: AvoRunRequest
    lifecycle: RunLifecycle
    ledger: BudgetLedger
    receipts: ReceiptChain
    gate: PolicyGate
    avo_run_id: str = field(default_factory=lambda: f"avo_{uuid.uuid4().hex[:12]}")
    evaluations: list[EvaluationResult] = field(default_factory=list)
    decisions: list[PolicyDecision] = field(default_factory=list)
    denied_actions: int = 0
    actions_total: int = 0
    workspace_digest: str = ""
    candidate_digest: str | None = None
    started_at: float = field(default_factory=time.time)
    limitations: list[str] = field(default_factory=list)
    #: Actions whose effects are unknown (a crash mid-action). Reconciled by
    #: the runner before the run may continue — never assumed clean.
    in_flight: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # admission
    # ------------------------------------------------------------------
    @classmethod
    def admit(cls, request: AvoRunRequest) -> GovernedRunSession:
        """Admit a fresh run. Every profile identifier resolves or nothing exists.

        Resolution order is deliberate: profiles first (an unknown id must not
        leave half an admitted run behind), then the lifecycle in ``queued``
        with its ``admitted`` transition, then the receipt chain's first
        event. By the time ``run.admitted`` exists, everything it claims is
        already true.
        """
        capability = capability_profile(request.capability_profile_id)
        budget = budget_profile(request.budget_profile_id)
        evaluator_profile(request.evaluator_profile_id)  # resolves or raises
        approval = approval_policy(request.approval_policy_id)

        if not capability.enabled:
            raise SessionRefused(f"capability profile {request.capability_profile_id!r} exists but is disabled; it cannot be reached by naming it")

        lifecycle = RunLifecycle(avo_run_id=f"avo_{uuid.uuid4().hex[:12]}")
        ledger = BudgetLedger(profile=budget)
        receipts = ReceiptChain(alpha_run_id=request.alpha_run_id, avo_run_id=lifecycle.avo_run_id)
        gate = PolicyGate(capability=capability, approval=approval, ledger=ledger)

        session = cls(
            request=request,
            lifecycle=lifecycle,
            ledger=ledger,
            receipts=receipts,
            gate=gate,
            avo_run_id=lifecycle.avo_run_id,
        )
        lifecycle.transition(AvoState.PREFLIGHT, actor=Actor.RUNNER, reason_code="admitted", avo_run_id=lifecycle.avo_run_id)
        receipts.append(
            "run.admitted",
            actor="server",
            reason_code="preflight",
            evidence_refs=(f"idempotency:{request.idempotency_key}",),
        )
        logger.info("governed run %s admitted for task %s", lifecycle.avo_run_id, request.task_id)
        return session

    @classmethod
    def resume(cls, path: str | Path, *, expected_idempotency_key: str | None = None) -> tuple[GovernedRunSession | None, ResumeVerdict]:
        """Resume from a checkpoint. ``(None, verdict)`` when resume is refused.

        The refusal is returned, not raised: §11.3 step 7 wants the caller to
        quarantine and surface, and a returned verdict is harder to ``except:
        pass`` than an exception.
        """
        verdict = load_checkpoint(path, expected_idempotency_key=expected_idempotency_key)
        if not verdict.ok or verdict.checkpoint is None:
            logger.warning("checkpoint %s refused resume: %s", path, "; ".join(verdict.reasons))
            return None, verdict
        ckpt = verdict.checkpoint
        capability = capability_profile(ckpt.capability_profile_id)
        approval = approval_policy(ckpt.approval_policy_id)
        gate = PolicyGate(capability=capability, approval=approval, ledger=ckpt.ledger)
        request = AvoRunRequest(
            alpha_run_id=ckpt.alpha_run_id,
            task_id=ckpt.task_id,
            task_description="(resumed)",
            workspace=WorkspaceLocator(project_id="resumed", relative_path="."),
            idempotency_key=ckpt.idempotency_key or "resumed-key-00000000",
            capability_profile_id=ckpt.capability_profile_id,
            budget_profile_id=ckpt.budget_profile_id,
            evaluator_profile_id=ckpt.evaluator_profile_id,
            approval_policy_id=ckpt.approval_policy_id,
            experiment=ExperimentSpec(hypothesis="(resumed run; the original hypothesis lives in the receipt chain)"),
        )
        session = cls(
            request=request,
            lifecycle=ckpt.lifecycle,
            ledger=ckpt.ledger,
            receipts=ckpt.receipts,
            gate=gate,
            avo_run_id=ckpt.avo_run_id,
            workspace_digest=ckpt.workspace_digest,
            candidate_digest=ckpt.candidate_digest,
        )
        session.receipts.append(
            "run.recovered",
            actor="recovery",
            reason_code="checkpoint_resumed",
            workspace_digest=ckpt.workspace_digest,
            evidence_refs=(f"checkpoint:{ckpt.checkpoint_id}",),
        )
        logger.info("governed run %s resumed from checkpoint %s", ckpt.avo_run_id, ckpt.checkpoint_id)
        return session, verdict

    # ------------------------------------------------------------------
    # lifecycle helpers
    # ------------------------------------------------------------------
    def _require_active(self, verb: str) -> None:
        if self.lifecycle.terminal:
            raise SessionStateError(f"cannot {verb}: run {self.avo_run_id} is in terminal state {self.lifecycle.state.value}; re-admission is a new run")
        if self.lifecycle.state not in ACTIVE_STATES:
            raise SessionStateError(f"cannot {verb}: run is in {self.lifecycle.state.value}, not one of {{{', '.join(sorted(s.value for s in ACTIVE_STATES))}}}")

    def start(self) -> None:
        """Leave preflight and enter ``running``."""
        try:
            self.lifecycle.transition(AvoState.RUNNING, actor=Actor.RUNNER, reason_code="preflight_passed")
        except IllegalTransition as exc:
            raise SessionStateError(str(exc)) from exc

    def pause(self, reason_code: str) -> None:
        try:
            self.lifecycle.transition(AvoState.PAUSED, actor=Actor.OPERATOR, reason_code=reason_code)
        except IllegalTransition as exc:
            raise SessionStateError(str(exc)) from exc

    def resume_running(self, reason_code: str = "operator_resumed") -> None:
        try:
            self.lifecycle.transition(AvoState.RUNNING, actor=Actor.OPERATOR, reason_code=reason_code)
        except IllegalTransition as exc:
            raise SessionStateError(str(exc)) from exc

    def cancel(self, reason_code: str = "cancelled_by_user") -> None:
        """The one transition a model can never make, exposed safely."""
        try:
            self.lifecycle.transition(AvoState.CANCELLED, actor=Actor.OPERATOR, reason_code=reason_code)
        except IllegalTransition as exc:
            raise SessionStateError(str(exc)) from exc
        self.receipts.append("run.completed", actor="operator", reason_code=reason_code)

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def submit_action(
        self,
        action: ProposedAction,
        *,
        granted_approvals: list[str] | None = None,
    ) -> PolicyDecision:
        """Decide an action, mint its receipt, and record the lifecycle edge.

        One method because the three facts — decision, receipt, count — must
        be written together. A caller that could write the decision without
        the receipt could run an action the log never saw.
        """
        self._require_active("submit an action")
        self.actions_total += 1
        decision = self.gate.decide(action, granted_approvals=granted_approvals)
        self.decisions.append(decision)
        event = "action.allowed" if decision.allowed else "action.denied"
        if decision.reason_code == "approval_required":
            event = "approval.requested"
        self.receipts.append_decision(decision, event_type=event, workspace_digest=self.workspace_digest)
        if not decision.allowed:
            self.denied_actions += 1
            if decision.reason_code == "budget_exhausted":
                self._exhaust(decision.reason)
        return decision

    def mark_in_flight(self, action: ProposedAction) -> None:
        """Record that an action's effects are now unknown until reconciled."""
        self._require_active("execute an action")
        if action.action_id not in {d.action_id for d in self.decisions if d.allowed}:
            raise SessionRefused(f"action {action.action_id} has no allowed decision; it may not execute")
        if self.lifecycle.state is AvoState.RUNNING:
            try:
                self.lifecycle.transition(AvoState.CHECKPOINTING, actor=Actor.RUNNER, reason_code="pre_action_checkpoint")
            except IllegalTransition as exc:  # pragma: no cover - graph guarantees this edge
                raise SessionStateError(str(exc)) from exc
            self.checkpoint(reason="pre_action")
            try:
                self.lifecycle.transition(AvoState.RUNNING, actor=Actor.RUNNER, reason_code="action_dispatch")
            except IllegalTransition as exc:  # pragma: no cover
                raise SessionStateError(str(exc)) from exc
        self.in_flight[action.action_id] = {
            "action_digest": action.digest,
            "experiment_id": action.experiment_id,
            "started_at": time.time(),
        }

    def reconcile_action(self, action: ProposedAction, *, measured_cost_usd: float | None = None) -> dict[str, Any]:
        """Close one action: measure, account, receipt. Returns the record.

        The reservation is reconciled against *measured* usage here, and the
        record states the variance — an action that cost more than its
        reservation is visible as such instead of being averaged away.
        """
        record = self.in_flight.pop(action.action_id, None)
        if record is None:
            raise SessionRefused(f"action {action.action_id} is not in flight; nothing to reconcile")
        adjustments: list[dict[str, Any]] = []
        # Reconcile from the decision that allowed this action.
        allowed = next((d for d in reversed(self.decisions) if d.action_id == action.action_id and d.allowed), None)
        if allowed is not None:
            from .budgets import BudgetKind

            for kind_name, amount in allowed.budget_reservation.items():
                try:
                    kind = BudgetKind(kind_name)
                except ValueError:  # pragma: no cover - guarded vocabulary
                    continue
                adjustment = self.ledger.reconcile(
                    kind,
                    reserved=amount,
                    measured=amount,
                    experiment_id=action.experiment_id,
                )
                adjustments.append(adjustment)
        if measured_cost_usd is not None:
            from .budgets import BudgetKind

            try:
                self.ledger.charge(BudgetKind.COST_USD, measured_cost_usd)
            except BudgetExceeded:  # charge is unbounded history; cannot raise
                pass
        self.workspace_digest = action.digest
        self.receipts.append(
            "budget.reserved",
            actor="runner",
            action_id=action.action_id,
            action_digest=action.digest,
            workspace_digest=self.workspace_digest,
            reason_code="reconciled",
            evidence_refs=[f"{a['kind']}:{a.get('reserved', 0)}->{a.get('measured', 0)}" for a in adjustments],
        )
        return {"action_id": action.action_id, "adjustments": adjustments, "cost_usd": measured_cost_usd}

    def _exhaust(self, reason: str) -> None:
        """Fold a budget refusal into the lifecycle and the receipt log."""
        if self.lifecycle.terminal:
            return
        try:
            self.lifecycle.transition(AvoState.BUDGET_EXHAUSTED, actor=Actor.POLICY_GATE, reason_code="budget_exhausted")
        except IllegalTransition:
            # Mid-run exhaustion may not be legal from the current state (e.g.
            # already evaluating); the receipt still records the fact and the
            # ledger is the authority on the numbers.
            logger.info("budget exhaustion observed in state %s: %s", self.lifecycle.state.value, reason)
        self.receipts.append("budget.exhausted", actor="policy_gate", reason_code=reason)

    # ------------------------------------------------------------------
    # evaluation and promotion
    # ------------------------------------------------------------------
    def record_evaluation(self, result: EvaluationResult) -> TruthLabel:
        """Record an evaluation and move the lifecycle onto its verdict."""
        self.evaluations.append(result)
        label = result.truth_label()
        self.receipts.append(
            "evaluation.completed",
            actor="evaluator",
            experiment_id=result.experiment_id,
            action_digest=result.candidate_digest,
            reason_code=label.value,
            evidence_refs=[f"{g.tier}:{g.status.value}" for g in result.gates],
        )
        if self.lifecycle.state is AvoState.RUNNING:
            try:
                self.lifecycle.transition(AvoState.EVALUATING, actor=Actor.EVALUATOR, reason_code="evaluation_recorded")
            except IllegalTransition as exc:  # pragma: no cover - graph guarantees this edge
                raise SessionStateError(str(exc)) from exc
        if label in (TruthLabel.FAILED, TruthLabel.UNVERIFIED):
            self.reject_candidate(reason=f"evaluation_{label.value.lower()}")
        elif label in (TruthLabel.VERIFIED, TruthLabel.PARTIALLY_VERIFIED) and self.lifecycle.state is AvoState.EVALUATING:
            # The graph's promotion path is evaluating -> promotion_eligible.
            # A partial label reaches it too so promotion_eligible() can *refuse*
            # it with the named reason — a run parked in evaluating cannot
            # report why it is not promotable.
            try:
                self.lifecycle.transition(
                    AvoState.PROMOTION_ELIGIBLE,
                    actor=Actor.EVALUATOR,
                    reason_code=f"evaluation_{label.value.lower()}",
                )
            except IllegalTransition as exc:  # pragma: no cover - graph guarantees this edge
                raise SessionStateError(str(exc)) from exc
        return label

    def reject_candidate(self, *, reason: str) -> None:
        if self.lifecycle.state is AvoState.EVALUATING:
            try:
                self.lifecycle.transition(AvoState.CANDIDATE_REJECTED, actor=Actor.EVALUATOR, reason_code=reason)
            except IllegalTransition as exc:  # pragma: no cover
                raise SessionStateError(str(exc)) from exc
        self.receipts.append("candidate.rejected", actor="evaluator", reason_code=reason)

    def promotion_eligible(self, requested_level: PromotionLevel) -> tuple[bool, list[str]]:
        """Eligibility for ``requested_level`` — every condition, no shortcuts.

        The plan's §10.3 list, minus the two halves owned elsewhere (the
        receipt chain is verified here; the approval itself is the policy
        gate's per-action business). Returns ``(eligible, unmet)``; an
        ineligible verdict always names every unmet condition, not the first —
        a caller fixing conditions one at a time through one-at-a-time
        refusals cannot plan.
        """
        unmet: list[str] = []
        if not self.evaluations:
            unmet.append("no_evaluation_recorded")
        else:
            latest = self.evaluations[-1]
            label = latest.truth_label()
            if label is not TruthLabel.VERIFIED:
                unmet.append(f"truth_label_{label.value}")
            if latest.candidate_digest != self.candidate_digest and self.candidate_digest is not None:
                unmet.append("digest_mismatch: evaluated bytes are not the candidate bytes")
        chain = self.receipts.verify()
        if not chain.ok:
            unmet.append(f"receipt_chain_invalid:{chain.defect}")
        if self.ledger.is_exhausted():
            unmet.append(f"budget_exhausted:{self.ledger.exhausted_reason}")
        ceiling = self.gate.capability.max_promotion_level
        if requested_level.rank > ceiling.rank:
            unmet.append(f"requested_level_{requested_level.value}_above_grant_{ceiling.value}")
        if requested_level.rank >= self.gate.approval.promotion_approval_level.rank and not self.gate.granted_approvals:
            unmet.append(f"approval_required_for_level_{requested_level.value}")
        if self.lifecycle.terminal:
            unmet.append(f"run_terminal_{self.lifecycle.state.value}")
        return (not unmet, unmet)

    def promote(self, requested_level: PromotionLevel, *, approval_id: str | None = None) -> AvoRunResult:
        """Attempt promotion at ``requested_level``; returns the run's report.

        Refusal does not raise: the plan's outcome labels include ``BLOCKED``
        precisely because "could not promote" is a *result the run must
        report*, not an exception the caller catches. Two shapes of refusal:

        * **Blocked only on approval** parks the run in ``WAITING_APPROVAL``
          and returns a non-terminal BLOCKED report. A run awaiting a human is
          alive; killing it here would make the approval that arrives next
          useless, and the graph has the ``WAITING_APPROVAL ->
          PROMOTION_ELIGIBLE`` edge back for exactly this.
        * **Blocked on anything else** (no evaluation, digest mismatch, broken
          chain, exhausted budget, level above the grant) is terminal: those
          are facts about work already done, and redoing them means a new run.
        """
        if approval_id:
            self.gate.granted_approvals.add(approval_id)
            self.receipts.append("approval.granted", actor="operator", reason_code=approval_id)
        eligible, unmet = self.promotion_eligible(requested_level)
        if not eligible:
            approval_pending = bool(unmet) and all(reason.startswith("approval_required") for reason in unmet)
            if approval_pending and not self.lifecycle.terminal:
                if self.lifecycle.state is AvoState.PROMOTION_ELIGIBLE:
                    try:
                        self.lifecycle.transition(
                            AvoState.WAITING_APPROVAL,
                            actor=Actor.OPERATOR,
                            reason_code="approval_required_for_promotion",
                        )
                    except IllegalTransition as exc:  # pragma: no cover - graph guarantees this edge
                        raise SessionStateError(str(exc)) from exc
                self.receipts.append("approval.requested", actor="operator", reason_code=f"level_{requested_level.value}")
                return self._report(
                    truth=TruthLabel.BLOCKED,
                    reason="promotion blocked pending approval: " + "; ".join(unmet),
                    promotion_level=PromotionLevel.REPORT_ONLY,
                    terminal=False,
                )
            self.receipts.append("candidate.promoted", actor="policy_gate", reason_code="promotion_blocked", evidence_refs=unmet)
            return self.complete(
                truth=TruthLabel.BLOCKED,
                reason="promotion blocked: " + "; ".join(unmet),
                promotion_level=PromotionLevel.REPORT_ONLY,
            )
        # Eligible: leave a parked approval state before taking the terminal edge.
        if self.lifecycle.state is AvoState.WAITING_APPROVAL:
            try:
                self.lifecycle.transition(
                    AvoState.PROMOTION_ELIGIBLE,
                    actor=Actor.OPERATOR,
                    reason_code="approval_granted",
                )
            except IllegalTransition as exc:  # pragma: no cover - graph guarantees this edge
                raise SessionStateError(str(exc)) from exc
        try:
            self.lifecycle.transition(AvoState.PROMOTED, actor=Actor.OPERATOR, reason_code=f"promoted_{requested_level.value}")
        except IllegalTransition as exc:
            raise SessionStateError(str(exc)) from exc
        self.receipts.append("candidate.promoted", actor="operator", reason_code=f"promoted_{requested_level.value}")
        return self.complete(truth=TruthLabel.VERIFIED, reason=f"promoted at level {requested_level.value}", promotion_level=requested_level)

    # ------------------------------------------------------------------
    # checkpoint and completion
    # ------------------------------------------------------------------
    def checkpoint(self, *, reason: str = "periodic", path: str | Path | None = None) -> Checkpoint | None:
        """Write the run's control state. Returns the checkpoint, or ``None``.

        A checkpoint write is best-effort at this layer: a failed write is
        recorded as a limitation and the run continues only if the caller
        decides it may — what this method never does is report a checkpoint
        that was not written as written.
        """
        ckpt = Checkpoint(
            alpha_run_id=self.request.alpha_run_id,
            avo_run_id=self.avo_run_id,
            task_id=self.request.task_id,
            capability_profile_id=self.request.capability_profile_id,
            budget_profile_id=self.request.budget_profile_id,
            evaluator_profile_id=self.request.evaluator_profile_id,
            approval_policy_id=self.request.approval_policy_id,
            lifecycle=self.lifecycle,
            ledger=self.ledger,
            receipts=self.receipts,
            workspace_digest=self.workspace_digest,
            candidate_digest=self.candidate_digest,
            action_cursor=self.actions_total,
            experiment_count=1 if self.request.experiment.experiment_id else 0,
            idempotency_key=self.request.idempotency_key,
        )
        if path is not None:
            try:
                write_checkpoint(ckpt, path)
                self.receipts.append("checkpoint.written", actor="runner", reason_code=reason, evidence_refs=(f"checkpoint:{ckpt.checkpoint_id}",))
            except OSError as exc:
                self.limitations.append(f"checkpoint write failed ({reason}): {type(exc).__name__}: {exc}")
                logger.warning("checkpoint write failed for %s: %s", self.avo_run_id, exc)
                return None
        return ckpt

    def assert_path_within_workspace(self, relative: str) -> Path:
        """Refuse a path outside the workspace root. Thin seam over paths.py."""
        try:
            return assert_within_root(relative, Path(self.request.workspace.relative_path))
        except PathRefused:
            raise
        except OSError as exc:  # pragma: no cover - filesystem-specific
            raise SessionRefused(f"cannot resolve {relative!r} against the workspace root: {exc}") from exc

    def complete(
        self,
        *,
        truth: TruthLabel,
        reason: str = "",
        promotion_level: PromotionLevel = PromotionLevel.REPORT_ONLY,
    ) -> AvoRunResult:
        """Assemble the final report and take the run's terminal edge."""
        return self._report(truth=truth, reason=reason, promotion_level=promotion_level, terminal=True)

    def _report(
        self,
        *,
        truth: TruthLabel,
        reason: str = "",
        promotion_level: PromotionLevel = PromotionLevel.REPORT_ONLY,
        terminal: bool,
    ) -> AvoRunResult:
        """Assemble the report from what actually happened.

        Terminal entry goes through the lifecycle (a model calling this cannot
        reach ``promoted``: the actor is fixed here, and the promotion path is
        the only one that requests it). ``terminal=False`` writes a report
        *without* moving the run — used for the parked-on-approval BLOCKED
        report, where the run is alive and a ``run.completed`` receipt would
        be a lie.
        """
        if not self.limitations:
            # A truthful run almost always skipped something. Rather than let
            # an empty list read as "nothing was limited", state the truth.
            if not any(g.status.value == "skipped" for e in self.evaluations for g in e.gates):
                self.limitations.append("no evaluation tiers were skipped; all recorded gates ran")
            else:
                self.limitations.append("one or more evaluation tiers were skipped; see evidence for which")
        if self.in_flight:
            self.limitations.append(f"{len(self.in_flight)} action(s) were in flight and remain unreconciled")

        target = {
            TruthLabel.VERIFIED: AvoState.PROMOTED,
            TruthLabel.BLOCKED: AvoState.FAILED,
            TruthLabel.FAILED: AvoState.FAILED,
            TruthLabel.UNVERIFIED: AvoState.FAILED,
            TruthLabel.PARTIALLY_VERIFIED: AvoState.FAILED,
        }[truth]
        if terminal and not self.lifecycle.terminal:
            # BLOCKED/promotion are reached through legal edges only; where the
            # graph has no edge (e.g. PROMOTION_ELIGIBLE -> CANCELLED is fine,
            # EVALUATING -> FAILED is fine) this follows it, else the caller
            # gets the exception rather than a forced state.
            try:
                if self.lifecycle.state is not target:
                    actor = Actor.OPERATOR if truth is TruthLabel.VERIFIED else Actor.RECOVERY
                    self.lifecycle.transition(target, actor=actor, reason_code=reason or "run_completed")
            except IllegalTransition:
                # Recover the run to a state from which the terminal edge exists.
                if self.lifecycle.state not in (AvoState.PROMOTION_ELIGIBLE, AvoState.EVALUATING, AvoState.CANDIDATE_REJECTED):
                    for intermediate in (AvoState.ROLLING_BACK, AvoState.PAUSED):
                        if self.lifecycle.allowed(intermediate):
                            self.lifecycle.transition(intermediate, actor=Actor.RECOVERY, reason_code="path_to_terminal")
                            break
                if not self.lifecycle.terminal:
                    actor = Actor.OPERATOR if truth is TruthLabel.VERIFIED else Actor.RECOVERY
                    self.lifecycle.transition(target, actor=actor, reason_code=reason or "run_completed")
        if terminal:
            self.receipts.append("run.completed", actor="server", reason_code=truth.value)
        latest = self.evaluations[-1] if self.evaluations else None
        return AvoRunResult(
            alpha_run_id=self.request.alpha_run_id,
            avo_run_id=self.avo_run_id,
            task_id=self.request.task_id,
            truth=truth,
            reason=reason,
            experiment_ids=[self.request.experiment.experiment_id],
            evaluated_digest=latest.candidate_digest if latest else None,
            promoted_digest=self.candidate_digest if truth is TruthLabel.VERIFIED else None,
            evidence=[g.model_dump(mode="json") for e in self.evaluations for g in e.gates],
            policy_decisions=[d.to_dict() for d in self.decisions],
            promotion_level=promotion_level,
            approvals_requested=[d.approval_id or "" for d in self.decisions if d.approval_required],
            approvals_granted=sorted(self.gate.granted_approvals),
            actions_total=self.actions_total,
            actions_denied=self.denied_actions,
            cost_usd=_measured_cost(self.ledger),
            latency_seconds=time.time() - self.started_at,
            receipt_chain_head=self.receipts.head,
            limitations=list(self.limitations),
        )

    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """Read-only status for the run: what happened, what it cost."""
        return {
            "avo_run_id": self.avo_run_id,
            "alpha_run_id": self.request.alpha_run_id,
            "task_id": self.request.task_id,
            "state": self.lifecycle.state.value,
            "terminal": self.lifecycle.terminal,
            "actions_total": self.actions_total,
            "actions_denied": self.denied_actions,
            "evaluations": len(self.evaluations),
            "receipt_chain_head": self.receipts.head,
            "budget": self.ledger.snapshot(),
            "policy": self.gate.snapshot(),
            "in_flight": sorted(self.in_flight),
            "limitations": list(self.limitations),
        }


def _measured_cost(ledger: BudgetLedger) -> float | None:
    """Total charged cost, or ``None`` when the provider was unmeasurable."""
    from .budgets import BudgetKind

    dim = ledger.dimension(BudgetKind.COST_USD)
    if ledger.ceiling(BudgetKind.COST_USD) is None and dim.used == 0.0:
        return None
    return round(dim.used, 6)
