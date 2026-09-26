"""The commit rule.

NVIDIA AVO's rule, in substance: *"we persist a new committed version only when
it passes correctness checks and matches or improves the benchmark score
relative to the best committed version so far; unsuccessful intermediate attempts
remain part of the agent's internal search trajectory but are not added to the
committed lineage."*

:func:`CommitGate.evaluate` implements that rule, and adds the half AVO does not
have. AVO's gate is **score-based only**: a change that improves the score while
violating some unmeasured behavioural property would be committed by AVO. alpha
owns a differential invariant fuzzer that is better than AVO's gate on that axis
and was not wired to any promotion decision. So the gate here is the
**composition**:

    correctness  AND  invariants  AND  score >= best committed

and each of the three is a hard conjunction, not a weighted term. A candidate
failing any one of them scores zero and is rejected, no matter how good its other
numbers are.

**Why this is strictly stronger than AVO's gate.** AVO has two conjuncts
(correctness, score). This has three, and the third is the one neither project
had. It is also *ordered* so that the cheap, decisive checks run first: a
candidate that fails correctness is never scored at all, so there is no
arithmetic in which a correctness failure could be averaged into a pass.

**Why the gate is server-owned.** The gate evaluates itself against its own
fingerprint. A candidate carrying a different fingerprint -- which is what a
change to ``f`` looks like from the inside -- cannot be evaluated under that
different fingerprint. It is evaluated under the *previous* gate, which is the
only arrangement in which a rewrite of the scorer cannot be used in the same step
that introduces it.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .evidence import (
    ChangeKind,
    InvariantEvidence,
    RejectionCategory,
    RejectionReason,
    VerificationReceipt,
    code_digest,
    record_score,
)
from .scorer_authority import GateRuleSet

__all__ = ["CommitGate", "PromotionDecision", "InvariantOracle"]


@dataclass(frozen=True)
class PromotionDecision:
    """The verdict for one candidate, with everything needed to audit it later."""

    committed: bool
    candidate_id: str
    reason: str
    category: RejectionCategory | None
    reason_code: str
    candidate_score: float
    best_committed_id: str | None
    best_committed_score: float
    gate_version: int
    gate_fingerprint: str
    evaluated_under_previous_gate: bool = False
    change_kind: ChangeKind = ChangeKind.REFINEMENT
    author: str = "unknown"
    invariant_scope: str = "not_run"
    correctness_oracle: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    decided_at: float = field(default_factory=time.time)

    @property
    def rejection_reason(self) -> str:
        """Backwards-compatible alias for callers that read ``rejection_reason``."""
        return self.reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "committed": self.committed,
            "candidate_id": self.candidate_id,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "category": self.category.value if self.category else None,
            "candidate_score": self.candidate_score,
            "best_committed_id": self.best_committed_id,
            "best_committed_score": self.best_committed_score,
            "gate_version": self.gate_version,
            "gate_fingerprint": self.gate_fingerprint,
            "evaluated_under_previous_gate": self.evaluated_under_previous_gate,
            "change_kind": self.change_kind.value,
            "author": self.author,
            "invariant_scope": self.invariant_scope,
            "correctness_oracle": self.correctness_oracle,
            "detail": self.detail,
            "decided_at": self.decided_at,
        }


class InvariantOracle:
    """Produces the invariant half of the gate, from the fuzzer alpha already owns.

    This is a thin, *server-side* wrapper around
    :class:`alpha.testing.differential_invariant_fuzzer.DifferentialInvariantFuzzer`.
    Its only job is to run that fuzzer and shape the answer into
    :class:`~alpha.avo.evidence.InvariantEvidence`. It takes no instructions from
    the candidate about what to check.

    The honesty of the ``scope`` field is load-bearing. When the target does not
    parse as Python the suite genuinely cannot run, and this returns
    ``scope="not_applicable"`` with the reason recorded. It does not return a
    pass. Commits made under a non-measured scope are counted separately in
    :mod:`alpha.avo.honesty` rather than being quietly presented as verified.
    """

    def __init__(self, num_trials: int = 24) -> None:
        # 24 >= the largest curated boundary pool (13 ints, 12 strings), so the
        # generator's exhaustive sweep completes and boundary coverage is
        # deterministic rather than sampled with replacement.
        self.num_trials = num_trials
        self.source = "differential_invariant_fuzzer"

    def check(
        self,
        *,
        baseline_code: str,
        candidate_code: str,
        entrypoint: str,
        target_digest: str,
        input_schema: dict[str, str] | None = None,
    ) -> InvariantEvidence:
        """Run the differential suite over one candidate.

        ``input_schema`` matters more than it looks. The fuzzer's boundary
        generator picks its inputs from a type-specific pool (``STR_BOUNDARIES``,
        ``INT_BOUNDARIES``, ...) and falls back to a small generic pool
        ``[0, 1, -1, "", "test", ...]`` for any parameter whose type it was not
        told. A divergence that only manifests on a value outside that generic
        pool is therefore invisible unless the schema is supplied. The caller
        infers it from the target's own annotations -- the server decides, not the
        candidate -- and the resolved schema is recorded in the evidence, so the
        coverage claim is auditable rather than assumed.
        """
        from alpha.testing.differential_invariant_fuzzer import DifferentialInvariantFuzzer

        schema = input_schema or {}
        try:
            result = DifferentialInvariantFuzzer().evaluate_differential(
                baseline_code=baseline_code,
                modified_code=candidate_code,
                entrypoint_function=entrypoint,
                input_schema=schema or None,
                num_trials=self.num_trials,
            )
        except Exception as exc:  # noqa: BLE001 - an oracle that blows up has not passed
            return InvariantEvidence(
                source=self.source,
                target_digest=target_digest,
                scope="error",
                detail=f"invariant suite raised {type(exc).__name__}: {exc}",
            )

        if not result.get("success", False) and "error" in result:
            return InvariantEvidence(
                source=self.source,
                target_digest=target_digest,
                scope="not_applicable",
                detail=str(result.get("error")),
            )

        regressions = int(result.get("regressions_detected_count", 0) or 0)
        return InvariantEvidence(
            source=self.source,
            target_digest=target_digest,
            scope="measured",
            trials=int(result.get("total_trials", 0) or 0),
            regressions=regressions,
            consistency_score=float(result.get("consistency_score", 0.0) or 0.0),
            findings=tuple(str(r.get("discrepancy_details", ""))[:200] for r in (result.get("regressions") or [])[:5]),
            detail=(
                f"entrypoint={entrypoint} input_schema={schema or 'unresolved'} "
                f"unmutated_paths_checked={result.get('unmutated_paths_checked', 0)}"
            ),
        )


class CommitGate:
    """The server-owned promotion gate. Decides whether a candidate ships.

    It is constructed around an :class:`~alpha.avo.lineage.AVOLineage` and a
    :class:`~alpha.avo.scorer_authority.GateRuleSet`, and it is the only thing in
    the package permitted to move a candidate from the trajectory into the
    committed set when a rule set requires evidence.
    """

    def __init__(
        self,
        lineage: Any,
        *,
        rules: GateRuleSet | None = None,
        invariant_oracle: InvariantOracle | None = None,
    ) -> None:
        self.lineage = lineage
        self.rules = rules or GateRuleSet()
        self.invariant_oracle = invariant_oracle or InvariantOracle()
        self.history: list[PromotionDecision] = []

    @property
    def fingerprint(self) -> str:
        return self.rules.fingerprint

    def best_committed(self, task_id: str | None = None) -> Any | None:
        """The best-scoring committed version on ``task_id``, or ``None``.

        This is the comparison target AVO's rule names. It is deliberately not
        the parent: a candidate may beat its immediate parent and still be worse
        than the best committed version, and in that case it is rejected.

        Scoped to one benchmark, because scores from different benchmarks are not
        commensurable.
        """
        return self.lineage.best_committed(task_id)

    # ------------------------------------------------------------------
    # evaluation
    # ------------------------------------------------------------------
    def evaluate(
        self,
        candidate: Any,
        *,
        receipt: VerificationReceipt | None = None,
        invariant: InvariantEvidence | None = None,
        target_digest: str | None = None,
        evaluating_fingerprint: str | None = None,
        author: str = "unknown",
        change_kind: ChangeKind | None = None,
    ) -> PromotionDecision:
        """Decide one candidate. Pure with respect to the committed set."""
        candidate_id = getattr(candidate, "version_id", None) or f"c_{uuid.uuid4().hex[:8]}"
        digest = target_digest or code_digest(str(getattr(candidate, "metadata", {}).get("code", "")))
        task_id = str(getattr(candidate, "task_id", "default"))

        best = self.best_committed(task_id)
        best_id = getattr(best, "version_id", None) if best is not None else None
        best_score = record_score(best) if best is not None else 0.0
        candidate_score = record_score(candidate)
        kind = change_kind or _kind_from_metadata(candidate)

        def verdict(
            committed: bool,
            reason: RejectionReason | None,
            message: str,
            *,
            under_previous: bool | None = None,
            invariant_scope: str = "not_run",
            oracle: str = "",
            detail: dict[str, Any] | None = None,
        ) -> PromotionDecision:
            return PromotionDecision(
                committed=committed,
                candidate_id=candidate_id,
                reason=message,
                category=None if committed else (reason.category if reason else RejectionCategory.ABANDONED),
                reason_code="committed" if committed or reason is None else reason.value,
                candidate_score=candidate_score,
                best_committed_id=best_id,
                best_committed_score=best_score,
                gate_version=self.rules.version,
                gate_fingerprint=self.fingerprint,
                evaluated_under_previous_gate=is_scorer_change if under_previous is None else under_previous,
                change_kind=kind,
                author=author,
                invariant_scope=invariant_scope,
                correctness_oracle=oracle,
                detail=detail or {},
            )

        # ---- conjunct 0: which gate is speaking? -------------------------
        # A candidate that changed the scorer arrives carrying the new
        # fingerprint. Evaluating it under that fingerprint would let the gate be
        # rewritten and used in the same step. So the mismatch does not change
        # how it is scored -- it records that this candidate IS the scorer, and
        # the decision proceeds under THIS gate, which is the previous one.
        is_scorer_change = evaluating_fingerprint is not None and evaluating_fingerprint != self.fingerprint
        if evaluating_fingerprint is not None and not is_scorer_change and evaluating_fingerprint != self.fingerprint:
            return verdict(False, RejectionReason.GATE_FINGERPRINT_MISMATCH, "gate fingerprint mismatch")

        # ---- conjunct 1: correctness, a hard zero -------------------------
        if not getattr(candidate, "correctness", False):
            return verdict(
                False,
                RejectionReason.CORRECTNESS_FAILURE,
                "correctness failed: candidate is assigned score 0.0 regardless of any other measurement",
                invariant_scope=invariant.scope if invariant else "not_run",
                oracle=receipt.oracle if receipt else "",
            )
        if self.rules.require_correctness_receipt:
            if receipt is None:
                return verdict(
                    False,
                    RejectionReason.CORRECTNESS_UNVERIFIED,
                    "no server-issued verification receipt: correctness asserted by the candidate is not evidence",
                )
            if not receipt.passed:
                return verdict(
                    False,
                    RejectionReason.CORRECTNESS_FAILURE,
                    f"verification receipt reports failure (exit {receipt.exit_code})",
                    oracle=receipt.oracle,
                )
            if receipt.target_digest != digest:
                return verdict(
                    False,
                    RejectionReason.CORRECTNESS_RECEIPT_STALE,
                    "verification receipt was minted for a different revision than the candidate",
                    oracle=receipt.oracle,
                )

        # ---- conjunct 2: invariants --------------------------------------
        invariant_scope = invariant.scope if invariant else "not_run"
        behaviour_changed = False
        if self.rules.require_invariants:
            if invariant is None:
                return verdict(
                    False,
                    RejectionReason.INVARIANT_EVIDENCE_MISSING,
                    "no invariant evidence: a score improvement is not sufficient to commit",
                )
            if invariant.target_digest != digest:
                return verdict(
                    False,
                    RejectionReason.INVARIANT_EVIDENCE_STALE,
                    "invariant evidence was produced for a different revision than the candidate",
                    invariant_scope=invariant_scope,
                )
            if invariant.scope == "measured" and invariant.regressions > 0:
                if self.rules.allow_declared_behaviour_change:
                    # The candidate changed behaviour on purpose and the supplied
                    # oracle certified the new behaviour. That is a legitimate
                    # change, but it is NOT invariant-verified, and it is recorded
                    # as such rather than being quietly passed.
                    behaviour_changed = True
                else:
                    return verdict(
                        False,
                        RejectionReason.INVARIANT_VIOLATED,
                        f"invariant violation: {invariant.regressions} behavioural regression(s) on unmutated paths",
                        invariant_scope=invariant_scope,
                        detail={"findings": list(invariant.findings)},
                    )
            if invariant.scope == "error":
                return verdict(
                    False,
                    RejectionReason.INVARIANT_EVIDENCE_MISSING,
                    f"invariant suite did not produce a verdict: {invariant.detail}",
                    invariant_scope=invariant_scope,
                )
            if behaviour_changed:
                invariant_scope = "behaviour_changed_by_candidate"

        # ---- conjunct 3: score against the BEST COMMITTED, not the parent -
        if best is not None:
            tolerance = self.rules.regress_tolerance
            if candidate_score < best_score * (1.0 - tolerance) - 1e-12:
                return verdict(
                    False,
                    RejectionReason.SCORE_REGRESSION,
                    (
                        f"score {candidate_score:.6g} is below the best committed {best_score:.6g} "
                        f"(version {best_id}); the committed lineage never regresses"
                    ),
                    invariant_scope=invariant_scope,
                    oracle=receipt.oracle if receipt else "",
                    detail={"compared_against": "best_committed", "task_id": task_id, "parent_id": getattr(candidate, "parent_id", None)},
                )

        return verdict(
            True,
            None,
            "committed: correctness and invariants hold and the score matches or improves the best committed version",
            under_previous=is_scorer_change,
            invariant_scope=invariant_scope,
            oracle=receipt.oracle if receipt else "",
            detail={"compared_against": "best_committed" if best is not None else "empty_lineage"},
        )

    # ------------------------------------------------------------------
    # promotion
    # ------------------------------------------------------------------
    def promote(self, candidate: Any, **kwargs: Any) -> PromotionDecision:
        """Evaluate, and on a pass seal the candidate into the committed lineage.

        A rejection still records the candidate in the trajectory, with its
        reason -- a loop that only remembers successes cannot learn from its
        failures.
        """
        decision = self.evaluate(candidate, **kwargs)
        if decision.committed:
            self.lineage.commit_record(candidate, decision)
        else:
            self.lineage.record_trajectory(candidate, decision)
        self.history.append(decision)
        return decision

    def abandon(self, candidate: Any, *, author: str = "unknown", detail: str = "") -> PromotionDecision:
        """Record a candidate the agent walked away from, distinct from a failure."""
        best = self.best_committed(str(getattr(candidate, "task_id", "default")))
        decision = PromotionDecision(
            committed=False,
            candidate_id=getattr(candidate, "version_id", "unknown"),
            reason=detail or "abandoned by agent before evaluation completed",
            category=RejectionCategory.ABANDONED,
            reason_code=RejectionReason.ABANDONED_BY_AGENT.value,
            candidate_score=record_score(candidate),
            best_committed_id=getattr(best, "version_id", None) if best is not None else None,
            best_committed_score=record_score(best) if best is not None else 0.0,
            gate_version=self.rules.version,
            gate_fingerprint=self.fingerprint,
            author=author,
        )
        self.lineage.record_trajectory(candidate, decision)
        self.history.append(decision)
        return decision

    # ------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        committed = sum(1 for d in self.history if d.committed)
        return {
            "gate_version": self.rules.version,
            "gate_fingerprint": self.fingerprint,
            "decisions": len(self.history),
            "committed": committed,
            "refused": len(self.history) - committed,
            "rules": self.rules.to_dict(),
        }


def _kind_from_metadata(candidate: Any) -> ChangeKind:
    """Read a pre-computed change kind off a candidate's metadata, if it has one."""
    metadata = getattr(candidate, "metadata", None)
    value = metadata.get("change_kind") if isinstance(metadata, dict) else None
    try:
        return ChangeKind(value) if value else ChangeKind.REFINEMENT
    except ValueError:
        return ChangeKind.REFINEMENT
