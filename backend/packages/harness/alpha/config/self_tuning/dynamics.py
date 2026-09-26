"""Bounded, damped resource-governor proposals.

The governor proposes; it never applies.  Its shape follows classical feedback
control as summarized in Åström & Murray's *Feedback Systems*: proportional
steps, saturation at explicit actuator bounds, and a deadband.  The mandatory
cooldown on the injected clock prevents the oscillation that an unthrottled
feedback loop would otherwise create.

This mirrors the conservative policy described by Netflix's *Seeking
SLOs*/*Fault Tolerance* practice and the bounded rollback requirement in
Google SRE's *Release Engineering*: a governor asks for a small reversible
change, and the same validated release pipeline decides whether it happens.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import SelfTuningConfig
from .models import ChangeKind, ChangeSet, Clock, ConfigChange
from .targets import PathResolutionStatus, TargetRegistry, coerce_target_value

SignalName = Literal["queue_depth", "latency_ms", "error_rate", "memory_pressure", "spend"]
TuningDirection = Literal["increase", "decrease"]


class ResourceSignals(BaseModel):
    """Injected samples; every field is optional and never filled with a default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    queue_depth: float | None = None
    latency_ms: float | None = None
    error_rate: float | None = None
    memory_pressure: float | None = None
    spend: float | None = None

    @field_validator("queue_depth", "latency_ms", "error_rate", "memory_pressure", "spend")
    @classmethod
    def _finite_when_present(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("resource signals must be finite when present")
        return value


class ResourceRule(BaseModel):
    """One explicit signal-to-target control rule."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_path: str = Field(min_length=1)
    signal: SignalName
    direction: TuningDirection = Field(description="Change direction when the signal rises above its deadband.")
    deadband: float = Field(default=0.0, ge=0.0)
    full_scale: float = Field(default=100.0, gt=0.0, description="Signal excess that produces the maximum allowed step.")
    kind: ChangeKind = Field(default=ChangeKind.TUNE)


class ResourceGovernor:
    """Propose one small, reversible, bounded adjustment from real samples."""

    def __init__(self, config: SelfTuningConfig, clock: Clock, registry: TargetRegistry) -> None:
        self.config = config
        self.clock = clock
        self.registry = registry
        self._last_proposed_at: dict[str, datetime] = {}

    def _in_cooldown(self, target_path: str, now: datetime) -> bool:
        previous = self._last_proposed_at.get(target_path)
        if previous is None:
            return False
        elapsed = (now - previous).total_seconds()
        if elapsed < 0:
            return True
        return elapsed < self.config.cooldown_seconds

    def _signal_value(self, samples: ResourceSignals, name: str) -> float | None:
        value = getattr(samples, name)
        return float(value) if value is not None else None

    def propose(
        self,
        rule: ResourceRule,
        current_value: int | float,
        samples: ResourceSignals,
        *,
        author: str,
        rationale: str,
    ) -> ChangeSet | None:
        """Return a bounded proposal, or ``None`` when evidence/policy is insufficient."""
        if not self.config.enabled or self.config.bounds_source != "registry":
            return None
        resolution = self.registry.resolve(rule.target_path)
        if resolution.status is not PathResolutionStatus.ALLOWED or resolution.target is None:
            return None
        target = resolution.target
        if not target.hot_reloadable:
            # A startup-only actuator cannot be observed in a bounded canary.
            return None

        sample = self._signal_value(samples, rule.signal)
        if sample is None:
            # Missing evidence is not zero and never becomes a plausible default.
            return None
        now = self.clock.now()
        if self._in_cooldown(target.path, now):
            return None
        try:
            current = coerce_target_value(target, current_value)
        except ValueError:
            return None
        if current < target.floor or current > target.ceiling:
            return None

        excess = max(0.0, sample - rule.deadband)
        intensity = min(1.0, excess / rule.full_scale)
        span = target.ceiling - target.floor
        maximum_step = span * self.config.max_step_ratio
        raw_delta = maximum_step * intensity
        direction = 1.0 if rule.direction == "increase" else -1.0
        proposed = current + direction * raw_delta
        if target.domain.annotation is int:
            proposed_value: int | float = int(round(proposed))
        else:
            proposed_value = round(proposed, 6)
        proposed_value = min(target.ceiling, max(target.floor, proposed_value))
        if proposed_value == current:
            return None

        change = ConfigChange.create(
            clock=self.clock,
            path=target.path,
            proposed_value=proposed_value,
            previous_value=current,
            rationale=rationale,
            author=author,
            expected_effect=f"damped {rule.direction} of at most {maximum_step:g} from the {rule.signal} signal",
            blast_radius=target.blast_radius,
            reversible=True,
            rollback_value=current,
        )
        # The cooldown is recorded at proposal time, not apply time: repeated
        # evaluation inside one window must not manufacture a second candidate.
        self._last_proposed_at[target.path] = now
        return ChangeSet.create(
            clock=self.clock,
            id=f"tune:{target.path}:{int(now.timestamp())}",
            changes=(change,),
            author=author,
            kind=rule.kind,
        )
