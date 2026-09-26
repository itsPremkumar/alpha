"""Post-apply, fail-closed health verification.

The comparison rule is the same evidence discipline used by Google SRE's
*Canarying Releases*: a candidate is retained only when observed service
health does not regress from a real pre-change baseline.  This module has no
sampling defaults; missing metrics, an exhausted probe, or an exception all
produce ``unavailable`` and therefore ``keep=False``.
"""

from __future__ import annotations

from threading import Lock
from typing import Protocol, runtime_checkable

from .config import SelfTuningConfig
from .models import (
    ChangeSet,
    HealthSnapshot,
    VerificationResult,
    VerificationStatus,
)


@runtime_checkable
class HealthCheck(Protocol):
    """Injected source of real before/after health snapshots."""

    def snapshot(self) -> HealthSnapshot | None:
        """Return a real snapshot, or ``None`` when health data is unavailable."""
        ...


class ScriptedHealthCheck:
    """Deterministic health source that consumes explicit snapshots in order."""

    def __init__(self, snapshots: tuple[HealthSnapshot | None, ...]) -> None:
        self._snapshots = snapshots
        self._index = 0
        self._lock = Lock()

    def snapshot(self) -> HealthSnapshot | None:
        """Return the next scripted snapshot without contacting a live system."""
        with self._lock:
            if self._index >= len(self._snapshots):
                return None
            result = self._snapshots[self._index]
            self._index += 1
            return result


class HealthVerifier:
    """Compare post-change health against a required pre-change snapshot."""

    def __init__(self, config: SelfTuningConfig, health_check: HealthCheck) -> None:
        self.config = config
        self.health_check = health_check

    def verify(self, change_set: ChangeSet, before: HealthSnapshot | None) -> VerificationResult:
        """Keep only real evidence that every required metric did not regress."""
        if not self.config.enabled:
            return VerificationResult(
                change_set_id=change_set.id,
                status=VerificationStatus.NOT_RUN,
                keep=False,
                reason="self_tuning_disabled",
                before=before,
            )
        if before is None:
            return VerificationResult(
                change_set_id=change_set.id,
                status=VerificationStatus.UNAVAILABLE,
                keep=False,
                reason="pre-change health snapshot is unavailable",
                before=None,
            )

        try:
            after = self.health_check.snapshot()
        except Exception as exc:
            return VerificationResult(
                change_set_id=change_set.id,
                status=VerificationStatus.UNAVAILABLE,
                keep=False,
                reason=f"post-change health probe failed: {exc}",
                before=before,
            )
        if after is None:
            return VerificationResult(
                change_set_id=change_set.id,
                status=VerificationStatus.UNAVAILABLE,
                keep=False,
                reason="post-change health data is unavailable",
                before=before,
                after=None,
            )

        policy = self.config.verification_policy
        missing = [metric for metric in policy.required_metrics if metric not in before.metrics or metric not in after.metrics]
        if missing:
            return VerificationResult(
                change_set_id=change_set.id,
                status=VerificationStatus.UNAVAILABLE,
                keep=False,
                reason=f"required health metrics are missing from one or both snapshots: {missing}",
                before=before,
                after=after,
            )

        tolerance = policy.relative_tolerance
        regressions: list[str] = []
        for metric in policy.higher_is_better:
            baseline = float(before.metrics[metric])
            observed = float(after.metrics[metric])
            allowed_drop = abs(baseline) * tolerance
            if observed < baseline - allowed_drop:
                regressions.append(f"{metric} decreased from {baseline} to {observed}")
        for metric in policy.lower_is_better:
            baseline = float(before.metrics[metric])
            observed = float(after.metrics[metric])
            allowed_rise = abs(baseline) * tolerance
            if observed > baseline + allowed_rise:
                regressions.append(f"{metric} increased from {baseline} to {observed}")

        if regressions:
            return VerificationResult(
                change_set_id=change_set.id,
                status=VerificationStatus.REGRESSED,
                keep=False,
                reason="; ".join(regressions),
                before=before,
                after=after,
            )
        return VerificationResult(
            change_set_id=change_set.id,
            status=VerificationStatus.HEALTHY,
            keep=True,
            reason="all required health metrics did not regress",
            before=before,
            after=after,
        )
