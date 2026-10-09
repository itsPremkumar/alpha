"""The tiered evaluator pipeline: structured evidence, never a summary.

The pipeline runs the tiers an
:class:`~alpha.avo.contracts.EvaluatorProfile` declares, in order, and shapes
each tier's outcome into an :class:`~alpha.avo.contracts.EvaluatorGate`. Four
rules the whole module leans on:

1. **A tier that did not run is not a tier that passed.** A tier whose runner
   reports "skipped" says so with the reason; a tier whose runner raises
   reports ``inconclusive`` with the exception text; a required tier with no
   runner at all fails :meth:`EvaluatorPipeline.validate_bindings` *before any
   tier runs*. The distinction survives to
   :meth:`EvaluationResult.truth_label`, so "we could not check" can never be
   read as "we checked and it was fine".
2. **Order is cheapest-and-most-decisive first**, and the profile owns the
   order. A candidate failing ``patch_sanity`` is never run through the full
   suite: the pipeline stops at the first ``failed`` tier, and the remaining
   tiers are ``skipped`` with ``blocked_by`` naming the tier that stopped them.
3. **The digest binds the evidence.** The caller supplies the candidate digest
   measured *before* evaluation; the pipeline records it, and
   :meth:`EvaluationResult.truth_label` is only ever compared against a digest
   the promotion gate re-checks. Evidence minted for other bytes is caught
   there, not trusted here.
4. **Runners are server-registered, per tier.** A runner arrives as a callable
   the server binds; the candidate cannot register one, and an unknown tier in
   the profile (a typo in a table nobody validated) is an error at pipeline
   construction rather than a tier silently never running.

The pipeline does not know how to run tests. It knows what a verdict is, what
"not run" means, and that the two must never compare equal.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .contracts import EvaluationResult, EvaluatorGate, EvaluatorProfile, GateStatus

__all__ = ["TierRunner", "TierOutcome", "EvaluatorPipeline", "UnknownTier"]

logger = logging.getLogger("alpha.avo.evaluation")


class UnknownTier(KeyError):
    """A profile declares a tier no runner and no skip rule knows about."""

    def __init__(self, tier: str, profile_id: str) -> None:
        self.tier = tier
        self.profile_id = profile_id
        super().__init__(f"evaluator profile {profile_id!r} declares unknown tier {tier!r}; declare a runner or add it to allow_skip")


@dataclass
class TierOutcome:
    """What one runner observed. The raw form; the pipeline shapes it."""

    status: GateStatus
    reason: str = ""
    command: str = ""
    exit_code: int | None = None
    duration_seconds: float | None = None
    artifact_refs: list[str] = field(default_factory=list)


#: A runner takes the evaluation context and returns what it observed. It may
#: not return a verdict for a different digest — the context carries the digest
#: for the runner to bind its artifacts against.
TierRunner = Callable[[dict[str, Any]], TierOutcome]


@dataclass
class EvaluatorPipeline:
    """Runs one candidate through an evaluator profile's tiers.

    ``runners`` maps tier name to runner. A profile tier with no runner is
    ``skipped`` (with the reason) when it is in ``profile.allow_skip``; any
    other missing runner raises :class:`UnknownTier` at
    :meth:`validate_bindings`, which every :meth:`evaluate` call runs first —
    a required tier that nobody can run must fail loudly *before* any tier
    runs, not quietly in the middle of producing evidence.
    """

    profile: EvaluatorProfile
    runners: dict[str, TierRunner] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def validate_bindings(self) -> None:
        """Every required tier must have a runner *before* any tier runs.

        Fail here rather than mid-pipeline: a pipeline that runs three tiers
        and then discovers the fourth cannot be run has already produced
        evidence it must now throw away, and a partial EvaluationResult is
        worse than no EvaluationResult.
        """
        for tier in self.profile.tiers:
            if tier not in self.runners and tier not in self.profile.allow_skip:
                raise UnknownTier(tier, self.profile.profile_id)

    # ------------------------------------------------------------------
    def evaluate(
        self,
        *,
        candidate_digest: str,
        experiment_id: str,
        context: dict[str, Any] | None = None,
        evaluator_version: str = "1",
        baseline_digest: str | None = None,
    ) -> EvaluationResult:
        """Run every tier in profile order, stopping at the first failure."""
        self.validate_bindings()
        base_context = dict(context or {})
        base_context["candidate_digest"] = candidate_digest
        base_context["experiment_id"] = experiment_id
        base_context["profile_id"] = self.profile.profile_id

        gates: list[EvaluatorGate] = []
        blocked_by: str | None = None
        started = time.monotonic()

        for tier in self.profile.tiers:
            if blocked_by is not None:
                gates.append(
                    EvaluatorGate(
                        tier=tier,
                        status=GateStatus.SKIPPED,
                        reason=f"blocked by {blocked_by}: a cheaper decisive tier already failed",
                    )
                )
                continue
            gate = self._run_tier(tier, base_context)
            gates.append(gate)
            if gate.status is GateStatus.FAILED:
                blocked_by = tier

        result = EvaluationResult(
            evaluator_id=self.profile.profile_id,
            evaluator_version=evaluator_version,
            config_digest=self.profile.digest(),
            candidate_digest=candidate_digest,
            experiment_id=experiment_id,
            gates=gates,
            baseline_digest=baseline_digest,
        )
        logger.debug(
            "evaluated candidate %s under profile %s: %s in %.3fs",
            candidate_digest[:12],
            self.profile.profile_id,
            result.truth_label().value,
            time.monotonic() - started,
        )
        return result

    # ------------------------------------------------------------------
    def _run_tier(self, tier: str, context: dict[str, Any]) -> EvaluatorGate:
        runner = self.runners.get(tier)
        if runner is None:
            return EvaluatorGate(
                tier=tier,
                status=GateStatus.SKIPPED,
                reason=f"no runner bound for tier {tier!r}; tier is in profile.allow_skip",
                bound_to_candidate=True,
            )
        began = time.monotonic()
        try:
            outcome = runner(dict(context))
        except Exception as exc:  # noqa: BLE001 - a runner that raised has not passed
            logger.warning("evaluator tier %s raised %s: %s", tier, type(exc).__name__, exc)
            return EvaluatorGate(
                tier=tier,
                status=GateStatus.INCONCLUSIVE,
                reason=f"runner raised {type(exc).__name__}: {exc}",
                duration_seconds=time.monotonic() - began,
                bound_to_candidate=True,
            )
        if not isinstance(outcome, TierOutcome):  # pragma: no cover - contract guard
            raise TypeError(f"runner for tier {tier!r} returned {type(outcome).__name__}, expected TierOutcome")
        return EvaluatorGate(
            tier=tier,
            status=outcome.status,
            reason=outcome.reason,
            command=outcome.command,
            exit_code=outcome.exit_code,
            duration_seconds=outcome.duration_seconds if outcome.duration_seconds is not None else round(time.monotonic() - began, 6),
            artifact_refs=list(outcome.artifact_refs),
            bound_to_candidate=True,
        )
