"""Audited automatic and operator-requested rollback.

Rollback follows the fail-closed recovery guidance in Google SRE's *Release
Engineering*: losing confidence in a candidate returns the system to the last
known-good state.  Every reversal is itself a first-class ``rollback`` change
set, so the audit chain can answer who reversed what, when, and why without a
special side channel.

Verification uncertainty is treated exactly like measured regression: the
manager performs the reversal instead of merely recommending one.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Protocol

import yaml

from .apply import AtomicConfigApplier
from .config import SelfTuningConfig
from .models import (
    ApplyOutcome,
    ApplyResult,
    ChangeKind,
    ChangeSet,
    Clock,
    ConfigChange,
    HealthSnapshot,
    VerificationResult,
)
from .targets import TargetRegistry
from .verify import HealthVerifier


class RollbackRecorder(Protocol):
    """Minimal injected provenance surface used by the rollback manager."""

    def record_rollback(self, change_set: ChangeSet, status: ApplyOutcome, reason: str) -> object | None:
        """Record the rollback proposal and its outcome."""
        ...


def _nested(document: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = document
    for segment in path:
        if not isinstance(value, Mapping) or segment not in value:
            return None
        value = value[segment]
    return value


class RollbackManager:
    """Create and apply reversible change sets with optimistic conflict checks."""

    def __init__(
        self,
        config: SelfTuningConfig,
        clock: Clock,
        applier: AtomicConfigApplier,
        *,
        registry: TargetRegistry | None = None,
        recorder: RollbackRecorder | None = None,
    ) -> None:
        self.config = config
        self.clock = clock
        self.applier = applier
        self.registry = registry or TargetRegistry(config)
        self.recorder = recorder

    def _current_document(self) -> dict[str, Any]:
        with self.applier.config_path.open(encoding="utf-8") as handle:
            document = yaml.safe_load(handle) or {}
        if not isinstance(document, dict):
            raise ValueError("config root must be a mapping")
        return document

    def _observed_value(self, document: Mapping[str, Any], target_path: str) -> Any:
        resolution = self.registry.resolve(target_path)
        if resolution.status.value != "allowed" or resolution.target is None:
            raise PermissionError(resolution.reason)
        value = _nested(document, tuple(target_path.split(".")))
        if value is not None:
            return value
        return getattr(resolution.target.owner_model(), resolution.target.field_name)

    def build_rollback(self, original: ChangeSet, *, reason: str, requested_by: str) -> ChangeSet:
        """Build a rollback set only when every target is original or known-good."""
        document = self._current_document()
        now = self.clock.now()
        changes: list[ConfigChange] = []
        for change in original.changes:
            observed = self._observed_value(document, change.target_path)
            if observed not in (change.proposed_value, change.previous_value):
                raise RuntimeError(f"rollback conflict at {change.target_path}: current value {observed!r} is neither proposed {change.proposed_value!r} nor previous {change.previous_value!r}")
            changes.append(
                ConfigChange(
                    path=change.path,
                    key=change.key,
                    proposed_value=change.previous_value,
                    previous_value=observed,
                    rationale=reason,
                    author=requested_by,
                    created_at=now,
                    expected_effect=f"restore {change.target_path} to its recorded pre-proposal value",
                    blast_radius=change.blast_radius,
                    reversible=True,
                    rollback_value=observed,
                )
            )
        digest = hashlib.sha256(f"{original.id}:{reason}:{requested_by}:{now.isoformat()}".encode()).hexdigest()[:16]
        return ChangeSet(
            id=f"rollback:{original.id}:{digest}",
            changes=tuple(changes),
            author=requested_by,
            kind=ChangeKind.ROLLBACK,
            created_at=now,
        )

    def rollback(self, original: ChangeSet, *, reason: str, requested_by: str) -> ApplyResult:
        """Apply and immediately commit a recorded reversal to known-good values."""
        if not self.config.enabled:
            return ApplyResult(change_set_id=original.id, status=ApplyOutcome.NO_OP, reason="self_tuning_disabled", at=self.clock.now())
        try:
            rollback_set = self.build_rollback(original, reason=reason, requested_by=requested_by)
        except Exception as exc:
            return ApplyResult(change_set_id=original.id, status=ApplyOutcome.FAILED, reason=f"rollback proposal could not be built: {exc}", at=self.clock.now())

        applied = self.applier.apply(rollback_set, allow_pending=True)
        terminal: ApplyResult
        if applied.status is ApplyOutcome.APPLIED:
            # The rollback target is already a schema-validated known-good value;
            # no speculative health promotion is required to confirm restoration.
            try:
                self.applier.confirm(rollback_set.id)
            except Exception as exc:
                terminal = ApplyResult(change_set_id=original.id, status=ApplyOutcome.FAILED, reason=f"rollback write applied but could not be confirmed/cleaned: {exc}", at=self.clock.now())
            else:
                terminal = ApplyResult(
                    change_set_id=original.id,
                    status=ApplyOutcome.ROLLED_BACK,
                    reason=f"recorded rollback {rollback_set.id} applied: {reason}",
                    at=self.clock.now(),
                )
        elif applied.status is ApplyOutcome.NO_OP:
            terminal = ApplyResult(
                change_set_id=original.id,
                status=ApplyOutcome.ROLLED_BACK,
                reason=f"known-good value was already present; recorded rollback {rollback_set.id} was a no-op: {reason}",
                at=self.clock.now(),
            )
        else:
            terminal = ApplyResult(change_set_id=original.id, status=applied.status, reason=f"rollback failed: {applied.reason}", at=self.clock.now())

        if self.recorder is not None:
            try:
                self.recorder.record_rollback(rollback_set, terminal.status, terminal.reason)
            except Exception as exc:
                terminal = ApplyResult(
                    change_set_id=original.id,
                    status=ApplyOutcome.FAILED,
                    reason=f"rollback reached {terminal.status.value} but its provenance record failed: {exc}",
                    at=self.clock.now(),
                )
        return terminal

    def verify_or_rollback(
        self,
        original: ChangeSet,
        before: HealthSnapshot | None,
        verifier: HealthVerifier,
        *,
        requested_by: str,
    ) -> tuple[VerificationResult, ApplyResult | None]:
        """Keep healthy evidence, otherwise reverse for regression or uncertainty."""
        verification = verifier.verify(original, before)
        if verification.keep:
            return verification, None
        outcome = self.rollback(
            original,
            reason=f"automatic rollback: {verification.status.value}: {verification.reason}",
            requested_by=requested_by,
        )
        return verification, outcome
