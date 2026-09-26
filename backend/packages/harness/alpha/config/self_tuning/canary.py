"""Bounded, injected canary stage for configuration proposals.

This follows Google SRE's *Canarying Releases*: expose a bounded cohort,
observe it for a fixed window, and promote only on affirmative evidence.  The
implementation is collaborator-only, so it can run without a live Gateway.
Missing probe data is an abort, never permission to continue.
"""

from __future__ import annotations

from threading import Lock
from typing import Protocol, runtime_checkable

from .config import SelfTuningConfig
from .models import (
    CanaryDecision,
    CanaryObservation,
    CanaryResult,
    CanaryStatus,
    ChangeSet,
    Clock,
)


@runtime_checkable
class ScopeSelector(Protocol):
    """Injected collaborator that exposes a change to one bounded scope."""

    def scope_for(self, change_set: ChangeSet) -> str | None:
        """Return a tenant/thread/percentage scope, or ``None`` when unavailable."""
        ...

    def apply_scoped(self, change_set: ChangeSet, scope: str) -> None:
        """Apply only inside the selected scope."""
        ...

    def clear_scope(self, scope: str) -> None:
        """Remove the scoped change and restore the previous scoped behavior."""
        ...


@runtime_checkable
class CanaryProbe(Protocol):
    """Injected source of real canary evidence."""

    def observe(self, change_set: ChangeSet, scope: str) -> CanaryObservation | None:
        """Return one observation, or ``None`` when evidence is unavailable."""
        ...


class ScriptedCanaryProbe:
    """Deterministic probe that consumes explicit observations in order."""

    def __init__(self, observations: tuple[CanaryObservation | None, ...]) -> None:
        self._observations = observations
        self._index = 0
        self._lock = Lock()

    def observe(self, change_set: ChangeSet, scope: str) -> CanaryObservation | None:
        """Return the next scripted result without contacting a live system."""
        del change_set, scope
        with self._lock:
            if self._index >= len(self._observations):
                return None
            result = self._observations[self._index]
            self._index += 1
            return result


class CanaryRunner:
    """Apply, observe, and clear one scoped proposal on an injected clock."""

    def __init__(
        self,
        config: SelfTuningConfig,
        clock: Clock,
        scope_selector: ScopeSelector,
        probe: CanaryProbe,
    ) -> None:
        self.config = config
        self.clock = clock
        self.scope_selector = scope_selector
        self.probe = probe

    @staticmethod
    def _abort(change_set_id: str, reason: str, status: CanaryStatus = CanaryStatus.UNAVAILABLE) -> CanaryResult:
        return CanaryResult(
            change_set_id=change_set_id,
            decision=CanaryDecision.ABORT,
            status=status,
            reason=reason,
        )

    def run(self, change_set: ChangeSet) -> CanaryResult:
        """Run the bounded canary.  Any uncertainty clears scope and aborts."""
        if not self.config.enabled:
            return self._abort(change_set.id, "self_tuning_disabled", CanaryStatus.NOT_RUN)

        scope: str | None = None
        observation: CanaryObservation | None = None
        failure: str | None = None
        try:
            scope = self.scope_selector.scope_for(change_set)
            if not scope:
                raise RuntimeError("scope selector returned no bounded scope")
            self.scope_selector.apply_scoped(change_set, scope)
            self.clock.sleep(self.config.canary_window_seconds)
            observation = self.probe.observe(change_set, scope)
            if observation is None:
                failure = "canary probe returned no evidence"
        except Exception as exc:
            failure = f"canary stage unavailable: {exc}"

        if scope is not None:
            try:
                self.scope_selector.clear_scope(scope)
            except Exception as exc:
                return self._abort(change_set.id, f"canary scope cleanup failed: {exc}")

        if failure is not None or observation is None:
            return self._abort(change_set.id, failure or "canary probe returned no evidence")
        if observation.status is not CanaryStatus.HEALTHY:
            return CanaryResult(
                change_set_id=change_set.id,
                decision=CanaryDecision.ABORT,
                status=observation.status,
                scope=scope,
                reason=observation.detail or f"canary probe reported {observation.status.value}",
                observed_at=self.clock.now(),
                metrics=observation.metrics,
            )
        return CanaryResult(
            change_set_id=change_set.id,
            decision=CanaryDecision.PROMOTE,
            status=CanaryStatus.HEALTHY,
            scope=scope,
            reason=observation.detail or "canary probe reported healthy evidence",
            observed_at=self.clock.now(),
            metrics=observation.metrics,
        )
