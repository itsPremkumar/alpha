"""Pure memory-admission engine with atomically swappable policy state.

``AdmissionEngine.evaluate`` performs no I/O.  File-backed policy loading and
provenance are explicit APIs so evaluation remains deterministic and easy to
embed in the L1 capture seam.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from .config import PolicyConfig
from .models import (
    AdmissionAction,
    AdmissionCandidate,
    AdmissionDecision,
    AdmissionTier,
)
from .provenance import ProvenanceResult, append_decision
from .rules import (
    DEFAULT_RULES,
    RULE_ACTIONS,
    RULE_IDS,
    AdmissionRule,
    evaluate_rule,
)
from .scoring import DEFAULT_SCORE_WEIGHTS, ScoreResult, score_candidate, validate_score_weights

if TYPE_CHECKING:  # pragma: no cover - avoids the engine <-> loader runtime cycle
    from .loader import PolicyLoader as PolicyLoader

DEFAULT_SESSION_TTL_SECONDS = 86_400


@dataclass(frozen=True, slots=True)
class PolicyThresholds:
    """Non-rule admission thresholds."""

    min_score_to_admit: float = 0.60
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS

    def __post_init__(self) -> None:
        if not math.isfinite(self.min_score_to_admit) or not 0.0 <= self.min_score_to_admit <= 1.0:
            raise ValueError("min_score_to_admit must be finite and between 0 and 1")
        if self.session_ttl_seconds < 1:
            raise ValueError("session_ttl_seconds must be at least 1")


@dataclass(frozen=True, slots=True)
class PolicySet:
    """Immutable ordered rules, score weights, and thresholds.

    The secret boundary is required to be first.  The remaining documented
    rules may be reordered, which makes first-match precedence explicit and
    testable without weakening the section-21 secret invariant.
    """

    rules: tuple[AdmissionRule, ...] = DEFAULT_RULES
    score_weights: Mapping[str, float] = field(
        default_factory=lambda: dict(DEFAULT_SCORE_WEIGHTS)
    )
    thresholds: PolicyThresholds = field(default_factory=PolicyThresholds)
    fail_closed_reason: str = ""

    def __post_init__(self) -> None:
        rules = tuple(self.rules)
        ids = tuple(rule.rule_id for rule in rules)
        if len(ids) != len(set(ids)):
            raise ValueError("policy rules must have unique ids")
        if set(ids) != set(RULE_IDS):
            missing = sorted(set(RULE_IDS) - set(ids))
            unknown = sorted(set(ids) - set(RULE_IDS))
            raise ValueError(f"policy rule set mismatch: missing={missing}, unknown={unknown}")
        if not ids or ids[0] != "secret_like":
            raise ValueError("secret_like must be the first admission rule")
        for rule in rules:
            if rule.action != RULE_ACTIONS[rule.rule_id]:
                raise ValueError(f"rule {rule.rule_id!r} has a non-source-of-truth action")
        object.__setattr__(self, "rules", rules)
        object.__setattr__(self, "score_weights", MappingProxyType(validate_score_weights(self.score_weights)))
        if not isinstance(self.thresholds, PolicyThresholds):
            raise TypeError("thresholds must be PolicyThresholds")

    @classmethod
    def fail_closed(cls, reason: str) -> PolicySet:
        """Return a usable deny-all policy with a disclosed failure reason."""

        return cls(fail_closed_reason=str(reason or "policy_load_failed")[:2000])

    def with_overrides(
        self,
        *,
        score_weights: Mapping[str, float] | None = None,
        min_score_to_admit: float | None = None,
    ) -> PolicySet:
        """Return a new policy with validated configuration overrides."""

        thresholds = self.thresholds
        if min_score_to_admit is not None:
            thresholds = replace(thresholds, min_score_to_admit=float(min_score_to_admit))
        return PolicySet(
            rules=self.rules,
            score_weights=self.score_weights if score_weights is None else score_weights,
            thresholds=thresholds,
            fail_closed_reason=self.fail_closed_reason,
        )


def default_policy() -> PolicySet:
    """Return a fresh instance of Alpha's in-package default policy."""

    return PolicySet()


@dataclass(frozen=True, slots=True)
class AdmissionAudit:
    """A pure decision plus the explicit provenance append result."""

    decision: AdmissionDecision
    provenance: ProvenanceResult


def _lifecycle(action: AdmissionAction, session_ttl_seconds: int) -> tuple[AdmissionTier, int | None]:
    if action == "session_only":
        return "compressed", session_ttl_seconds
    if action == "episodic_archive":
        return "archived", None
    if action == "reject":
        return "archived", 0
    return "active", None


class AdmissionEngine:
    """Evaluate candidates against a swappable :class:`PolicySet`."""

    def __init__(
        self,
        policy: PolicySet | None = None,
        *,
        config: PolicyConfig | None = None,
    ) -> None:
        # A directly constructed engine is a computational object and is
        # active.  Hosts that load PolicyConfig must pass it explicitly; the
        # configuration itself remains OFF by default.
        self._config = config if config is not None else PolicyConfig(enabled=True)
        current = default_policy() if policy is None else policy
        if not isinstance(current, PolicySet):
            raise TypeError("policy must be a PolicySet")
        if self._config.weights_override is not None:
            current = current.with_overrides(score_weights=self._config.weights_override)
        if "min_score_to_admit" in self._config.model_fields_set:
            current = current.with_overrides(min_score_to_admit=self._config.min_score_to_admit)
        self._policy = current
        self._audit_lock = threading.Lock()

    @classmethod
    def from_config(
        cls,
        config: PolicyConfig,
        *,
        loader: PolicyLoader | None = None,
    ) -> AdmissionEngine:
        """Build from local config, loading a policy only when the gate is on."""

        if loader is None:
            if not config.enabled:
                return cls(config=config)
            from .loader import PolicyLoader

            loader = PolicyLoader(
                policy_path=config.policy_path,
                storage_path=config.storage_path,
                strict=config.strict,
            )
        policy = getattr(loader, "current_policy")
        if not isinstance(policy, PolicySet):
            raise TypeError("policy loader returned an invalid policy")
        return cls(policy, config=config)

    @property
    def policy(self) -> PolicySet:
        return self._policy

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def swap_policy(self, policy: PolicySet) -> PolicySet:
        """Atomically replace the evaluation policy and return the prior one."""

        if not isinstance(policy, PolicySet):
            raise TypeError("policy must be a PolicySet")
        previous = self._policy
        self._policy = policy
        return previous

    def evaluate(self, candidate: AdmissionCandidate) -> AdmissionDecision:
        """Pure candidate evaluation: no file, clock, network, or global state."""

        policy = self._policy
        result: ScoreResult = score_candidate(candidate, weights=policy.score_weights)
        common = {
            "score": result.score,
            "score_breakdown": dict(result.terms),
            "missing_signals": list(result.missing_signals),
            "disclosures": list(result.disclosures),
        }
        if not self._config.enabled:
            return AdmissionDecision(
                admit=False,
                action="reject",
                rule_id="policy_disabled",
                reason="memory admission policy is disabled by configuration",
                tier="archived",
                ttl_seconds=0,
                **common,
            )
        if policy.fail_closed_reason:
            return AdmissionDecision(
                admit=False,
                action="reject",
                rule_id="policy_load_failure",
                reason=f"policy load failed; failing closed: {policy.fail_closed_reason}",
                tier="archived",
                ttl_seconds=0,
                **common,
            )

        near_misses: list[str] = []
        for rule in policy.rules:
            evaluation = evaluate_rule(
                candidate,
                rule,
                secret_patterns_enabled=self._config.secret_patterns_enabled,
            )
            if not evaluation.matched:
                if evaluation.near_miss and evaluation.near_miss not in near_misses:
                    near_misses.append(evaluation.near_miss)
                continue
            action = evaluation.action or rule.action
            tier, ttl_seconds = _lifecycle(action, policy.thresholds.session_ttl_seconds)
            return AdmissionDecision(
                admit=action != "reject",
                action=action,
                rule_id=rule.rule_id,
                reason=evaluation.reason,
                tier=tier,
                ttl_seconds=ttl_seconds,
                **common,
            )

        threshold = policy.thresholds.min_score_to_admit
        if result.score >= threshold:
            action = "episodic_archive"
            reason = (
                f"no durable hard rule matched; score {result.score:.12g} meets "
                f"min_score_to_admit={threshold:.12g}, so the candidate is episodic only"
            )
        else:
            action = "reject"
            reason = (
                f"no hard rule matched and score {result.score:.12g} is below "
                f"min_score_to_admit={threshold:.12g}"
            )
        if near_misses:
            reason += "; near misses: " + "; ".join(near_misses)
        tier, ttl_seconds = _lifecycle(action, policy.thresholds.session_ttl_seconds)
        return AdmissionDecision(
            admit=action != "reject",
            action=action,
            rule_id="admission_threshold",
            reason=reason[:2000],
            tier=tier,
            ttl_seconds=ttl_seconds,
            **common,
        )

    def evaluate_batch(self, candidates: Iterable[AdmissionCandidate]) -> list[AdmissionDecision]:
        """Evaluate a batch independently and preserve one decision per item."""

        return [self.evaluate(candidate) for candidate in candidates]

    def record_decision(
        self,
        candidate: AdmissionCandidate,
        decision: AdmissionDecision,
        *,
        now: float | None = None,
    ) -> ProvenanceResult:
        """Explicit I/O seam for callers that evaluated separately."""

        with self._audit_lock:
            return append_decision(
                candidate,
                decision,
                storage_path=self._config.storage_path,
                now=now,
            )

    def evaluate_and_record(
        self,
        candidate: AdmissionCandidate,
        *,
        now: float | None = None,
    ) -> AdmissionAudit:
        """Evaluate and append one audit row, reporting any persistence failure."""

        decision = self.evaluate(candidate)
        return AdmissionAudit(decision, self.record_decision(candidate, decision, now=now))


__all__ = [
    "DEFAULT_SESSION_TTL_SECONDS",
    "AdmissionAudit",
    "AdmissionEngine",
    "PolicySet",
    "PolicyThresholds",
    "default_policy",
]
