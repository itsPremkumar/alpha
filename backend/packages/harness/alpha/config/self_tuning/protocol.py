"""Orchestrated propose -> validate -> canary -> apply -> verify state machine.

The sequence is the operational form of Google SRE's *Release Engineering*
and *Canarying Releases*: no stage may imply the next, and every terminal
outcome is one of the closed values in :mod:`.models`.  Unexpected evidence
fails closed toward the last known-good config.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .apply import AtomicConfigApplier
from .canary import CanaryRunner
from .config import SelfTuningConfig
from .models import (
    ApplyOutcome,
    ApplyResult,
    CanaryDecision,
    CanaryResult,
    CanaryStatus,
    ChangeSet,
    Clock,
    ProtocolRun,
    RefusalCode,
    ValidationErrorCode,
    ValidationIssue,
    ValidationResult,
    VerificationResult,
    VerificationStatus,
)
from .provenance import ProvenanceLedger
from .rollback import RollbackManager
from .targets import TargetRegistry
from .validate import ConfigChangeValidator
from .verify import HealthCheck, HealthVerifier


class SelfConfigurationProtocol:
    """Compose the injected stages without granting any stage extra authority."""

    def __init__(
        self,
        config_path: Path,
        config: SelfTuningConfig,
        clock: Clock,
        *,
        registry: TargetRegistry,
        validator: ConfigChangeValidator,
        canary: CanaryRunner,
        applier: AtomicConfigApplier,
        health_check: HealthCheck,
        rollback_manager: RollbackManager,
        ledger: ProvenanceLedger,
    ) -> None:
        self.config_path = config_path.resolve()
        self.config = config
        self.clock = clock
        self.registry = registry
        self.validator = validator
        self.canary = canary
        self.applier = applier
        self.health_check = health_check
        self.verifier = HealthVerifier(config, health_check)
        self.rollback_manager = rollback_manager
        self.ledger = ledger

    def _current_document(self) -> dict:
        with self.config_path.open(encoding="utf-8") as handle:
            document = yaml.safe_load(handle) or {}
        if not isinstance(document, dict):
            raise ValueError("config root must be a mapping")
        return document

    def recover(self) -> ApplyResult | None:
        """Recover an interrupted transaction before serving self-tuning."""
        recovered = self.applier.recover_pending()
        if recovered is not None:
            self.ledger.record_recover(
                recovered.change_set_id,
                author="self-configuration-protocol",
                status=recovered.status.value,
                reason=recovered.reason,
            )
        return recovered

    def run(self, change_set: ChangeSet) -> ProtocolRun:
        """Execute the full state machine for one immutable proposal."""
        if not self.config.enabled:
            return self._disabled_run(change_set)

        self.ledger.record_propose(change_set)
        try:
            current = self._current_document()
        except Exception as exc:
            unreadable = ValidationResult(
                ok=False,
                errors=(ValidationIssue(code=ValidationErrorCode.VALIDATION_UNAVAILABLE, path="config", message=f"known-good config could not be read: {exc}"),),
                reason="current_config_unavailable",
            )
            return self._failed_before_apply(change_set, unreadable, needs_human=True)
        validation = self.validator.validate(change_set, current)
        self.ledger.record_validation(change_set, validation)
        if validation.no_op:
            return self._disabled_run(change_set, reason=validation.reason or "self_tuning_disabled")
        if not validation.ok:
            needs_human = any(refusal.code is RefusalCode.PROTECTED_PATH for refusal in validation.refusals)
            return self._failed_before_apply(change_set, validation, needs_human=needs_human)

        cold_targets = [change.target_path for change in change_set.changes if (resolution := self.registry.resolve(change.target_path)).target is not None and not resolution.target.hot_reloadable]
        if cold_targets:
            cold_validation = ValidationResult(
                ok=False,
                refusals=validation.refusals,
                warnings=validation.warnings,
                blast_radius_class=validation.blast_radius_class,
                reason=f"startup_only_targets_require_operator_restart: {cold_targets}",
            )
            return self._failed_before_apply(change_set, cold_validation, needs_human=True)

        try:
            before = self.health_check.snapshot()
        except Exception as exc:
            before = None
            health_failure = f"pre-change health probe failed: {exc}"
        else:
            health_failure = "pre-change health baseline is unavailable"
        if before is None:
            unavailable = VerificationResult(
                change_set_id=change_set.id,
                status=VerificationStatus.UNAVAILABLE,
                keep=False,
                reason=f"{health_failure}; no config write was attempted",
            )
            unavailable_validation = ValidationResult(
                ok=False,
                errors=(ValidationIssue(code=ValidationErrorCode.VALIDATION_UNAVAILABLE, path="health", message=unavailable.reason),),
                warnings=validation.warnings,
                blast_radius_class=validation.blast_radius_class,
                reason="pre_change_health_unavailable",
            )
            self.ledger.record_verify(change_set, unavailable)
            return self._failed_before_apply(change_set, unavailable_validation, needs_human=True, verification=unavailable)

        canary = self.canary.run(change_set)
        self.ledger.record_canary(change_set, canary)
        if canary.decision is not CanaryDecision.PROMOTE:
            rollback = self.rollback_manager.rollback(
                change_set,
                reason=f"automatic rollback: canary {canary.status.value}: {canary.reason}",
                requested_by="self-configuration-protocol",
            )
            not_run = VerificationResult(change_set_id=change_set.id, status=VerificationStatus.NOT_RUN, keep=False, reason="canary aborted before global apply")
            return ProtocolRun(
                change_set_id=change_set.id,
                status=rollback.status if rollback is not None else ApplyOutcome.FAILED,
                reason=f"canary aborted: {canary.reason}; rollback: {rollback.reason if rollback is not None else 'not attempted'}",
                validation=validation,
                canary=canary,
                apply=ApplyResult(change_set_id=change_set.id, status=ApplyOutcome.NO_OP, reason="canary aborted before global apply", at=self.clock.now()),
                verification=not_run,
                rollback=rollback,
            )

        applied = self.applier.apply(change_set)
        self.ledger.record_apply(change_set, applied.status, applied.reason)
        if applied.status is not ApplyOutcome.APPLIED:
            not_run = VerificationResult(change_set_id=change_set.id, status=VerificationStatus.NOT_RUN, keep=False, reason="config was not applied")
            return ProtocolRun(
                change_set_id=change_set.id,
                status=applied.status,
                reason=applied.reason,
                validation=validation,
                canary=canary,
                apply=applied,
                verification=not_run,
            )

        verification, rollback = self.rollback_manager.verify_or_rollback(
            change_set,
            before,
            self.verifier,
            requested_by="self-configuration-protocol",
        )
        self.ledger.record_verify(change_set, verification)
        if verification.keep:
            self.applier.confirm(change_set.id)
            self.ledger.record_confirm(change_set, verification.reason)
            return ProtocolRun(
                change_set_id=change_set.id,
                status=ApplyOutcome.APPLIED,
                reason=applied.reason,
                validation=validation,
                canary=canary,
                apply=applied,
                verification=verification,
            )
        return ProtocolRun(
            change_set_id=change_set.id,
            status=rollback.status if rollback is not None else ApplyOutcome.FAILED,
            reason=verification.reason,
            validation=validation,
            canary=canary,
            apply=applied,
            verification=verification,
            rollback=rollback,
        )

    def operator_rollback(self, original: ChangeSet, *, requested_by: str, reason: str) -> ApplyResult:
        """Reverse an original change set through the same validated write path."""
        return self.rollback_manager.rollback(original, reason=reason, requested_by=requested_by)

    def _failed_before_apply(
        self,
        change_set: ChangeSet,
        validation: ValidationResult,
        *,
        needs_human: bool,
        verification: VerificationResult | None = None,
    ) -> ProtocolRun:
        not_run = verification or VerificationResult(change_set_id=change_set.id, status=VerificationStatus.NOT_RUN, keep=False, reason="no config write occurred")
        canary = CanaryResult(change_set_id=change_set.id, decision=CanaryDecision.ABORT, status=CanaryStatus.NOT_RUN, reason="stage not reached")
        return ProtocolRun(
            change_set_id=change_set.id,
            status=ApplyOutcome.NEEDS_HUMAN if needs_human else ApplyOutcome.FAILED,
            reason=validation.reason or "pre-apply stage failed",
            validation=validation,
            canary=canary,
            apply=ApplyResult(change_set_id=change_set.id, status=ApplyOutcome.NO_OP, reason="no config write occurred", at=self.clock.now()),
            verification=not_run,
        )

    def _disabled_run(self, change_set: ChangeSet, reason: str = "self_tuning_disabled") -> ProtocolRun:
        return ProtocolRun(
            change_set_id=change_set.id,
            status=ApplyOutcome.NO_OP,
            reason=reason,
            validation=ValidationResult(ok=False, no_op=True, reason=reason),
            canary=CanaryResult(change_set_id=change_set.id, decision=CanaryDecision.ABORT, status=CanaryStatus.NOT_RUN, reason=reason),
            apply=ApplyResult(change_set_id=change_set.id, status=ApplyOutcome.NO_OP, reason=reason, at=self.clock.now()),
            verification=VerificationResult(change_set_id=change_set.id, status=VerificationStatus.NOT_RUN, keep=False, reason=reason),
        )
