"""Contract tests for Alpha's default-off self-configuration protocol.

Every acceptance case fails against an implementation that trusts a proposal,
an unavailable probe, or an unverified write.  Clocks, probes, health checks,
and authorizers are injected; no test sleeps, reaches the network, or mutates
shared process config.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread

import pytest
import yaml
from annotated_types import Ge, Gt, Le, Lt
from pydantic import ValidationError

from alpha.config.app_config import AppConfig
from alpha.config.self_tuning import (
    PROTECTED_PATH_REASONS,
    ApplyOutcome,
    ApplyResult,
    AtomicConfigApplier,
    BlastRadius,
    CanaryDecision,
    CanaryObservation,
    CanaryResult,
    CanaryRunner,
    CanaryStatus,
    ChangeKind,
    ChangeSet,
    ConfigChange,
    ConfigChangeValidator,
    ConfigTarget,
    HealthSnapshot,
    HealthVerifier,
    ManualClock,
    ProvenanceAction,
    ProvenanceChainError,
    ProvenanceLedger,
    RefusalCode,
    ResourceGovernor,
    ResourceRule,
    ResourceSignals,
    RollbackManager,
    ScriptedCanaryProbe,
    ScriptedHealthCheck,
    SelfConfigurationProtocol,
    SelfTuningConfig,
    TargetRegistry,
    ValidationErrorCode,
    ValidationResult,
    VerificationPolicy,
    VerificationResult,
    VerificationStatus,
    default_off_subsystem_paths,
    validate_config_file,
)
from alpha.config.self_tuning.targets import PathResolutionStatus

START = datetime(2026, 1, 1, tzinfo=UTC)


def _policy(**overrides) -> VerificationPolicy:
    values = {
        "required_metrics": ("error_rate", "latency_ms"),
        "higher_is_better": (),
        "lower_is_better": ("error_rate", "latency_ms"),
        "relative_tolerance": 0.0,
    }
    values.update(overrides)
    return VerificationPolicy(**values)


def _config(**overrides) -> SelfTuningConfig:
    values: dict = {
        "enabled": True,
        "canary_window_seconds": 5.0,
        "max_step_ratio": 0.10,
        "cooldown_seconds": 60.0,
        "bounds_source": "registry",
        "protected_paths": tuple(PROTECTED_PATH_REASONS),
        "verification_policy": _policy(),
    }
    values.update(overrides)
    return SelfTuningConfig(**values)


def _document(config: SelfTuningConfig | None = None, **sections) -> dict:
    document = {
        "config_version": 1,
        "sandbox": {"use": "alpha.sandbox.local:LocalSandboxProvider"},
        "autonomy": {"bus": {"queue_maxsize": 256, "handler_timeout_seconds": 30.0}},
        "subagent_batches": {
            "default_max_live_items": 100,
            "default_max_running_items": 3,
            "max_live_items_per_batch": 1000,
        },
        "self_tuning": (config or _config()).model_dump(mode="json"),
    }
    document.update(sections)
    return document


def _write(path: Path, document: dict) -> bytes:
    content = yaml.safe_dump(document, allow_unicode=True, sort_keys=False).encode("utf-8")
    path.write_bytes(content)
    return content


def _change(
    clock: ManualClock,
    *,
    path: str = "autonomy.bus.queue_maxsize",
    previous: object = 256,
    proposed: object = 300,
    token: str | None = None,
    blast_radius: BlastRadius = BlastRadius.PROCESS,
) -> ConfigChange:
    return ConfigChange.create(
        clock=clock,
        path=path,
        previous_value=previous,
        proposed_value=proposed,
        rationale="queue depth is above the operator target",
        author="resource-governor",
        expected_effect="reduce queueing delay without exceeding the target ceiling",
        blast_radius=blast_radius,
        reversible=True,
        rollback_value=previous,
        operator_authorization_token=token,
    )


def _change_set(clock: ManualClock, change: ConfigChange, *, id: str = "proposal-1", kind: ChangeKind = ChangeKind.TUNE) -> ChangeSet:
    return ChangeSet.create(clock=clock, id=id, changes=(change,), author="resource-governor", kind=kind)


def _validation_error(result: ValidationResult, code: ValidationErrorCode) -> str:
    return next(error.message for error in result.errors if error.code is code)


def _applier(path: Path, config: SelfTuningConfig, clock: ManualClock, **kwargs) -> AtomicConfigApplier:
    registry = kwargs.pop("registry", TargetRegistry(config))
    return AtomicConfigApplier(path, config, clock, registry=registry, **kwargs)


def _script_loader(path: Path) -> object:
    return validate_config_file(path)


class RecordingScopeSelector:
    """Deterministic bounded-cohort selector."""

    def __init__(self, scope: str | None = "canary-tenant-1") -> None:
        self.scope = scope
        self.applied: list[str] = []
        self.cleared: list[str] = []

    def scope_for(self, change_set: ChangeSet) -> str | None:
        del change_set
        return self.scope

    def apply_scoped(self, change_set: ChangeSet, scope: str) -> None:
        del change_set
        self.applied.append(scope)

    def clear_scope(self, scope: str) -> None:
        self.cleared.append(scope)


# --- allowlist, authority, and pre-write validation -------------------------


def test_undeclared_path_is_refused_and_protected_path_has_distinct_reason() -> None:
    clock = ManualClock(START)
    config = _config()
    registry = TargetRegistry(config)
    document = _document(config)
    validator = ConfigChangeValidator(config, registry=registry)

    undeclared = validator.validate(_change_set(clock, _change(clock, path="memory.policy.admission", previous=None, proposed="x")), document)
    assert not undeclared.ok
    assert [refusal.code for refusal in undeclared.refusals] == [RefusalCode.UNDECLARED_PATH]

    protected = validator.validate(
        _change_set(clock, _change(clock, path="authorization.fail_closed", previous=True, proposed=False), id="protected-1"),
        document,
    )
    assert not protected.ok
    assert [refusal.code for refusal in protected.refusals] == [RefusalCode.PROTECTED_PATH]
    assert "authorization decisions" in protected.refusals[0].reason

    resolution = registry.resolve("self_tuning.protected_paths")
    assert resolution.status is PathResolutionStatus.PROTECTED
    assert "authority boundary" in resolution.reason


def test_out_of_bounds_change_names_the_exact_bound() -> None:
    clock = ManualClock(START)
    config = _config()
    document = _document(config)
    validator = ConfigChangeValidator(config)
    target = next(target for target in TargetRegistry(config).targets() if target.path == "autonomy.bus.queue_maxsize")

    below = validator.validate(_change_set(clock, _change(clock, proposed=target.floor - 1)), document)
    assert not below.ok
    assert _validation_error(below, ValidationErrorCode.OUTSIDE_BOUNDS).endswith(f"below floor {target.floor}")

    above = validator.validate(_change_set(clock, _change(clock, proposed=target.ceiling + 1), id="above"), document)
    assert not above.ok
    assert _validation_error(above, ValidationErrorCode.OUTSIDE_BOUNDS).endswith(f"above ceiling {target.ceiling}")


def test_change_that_makes_full_config_unloadable_is_named_and_nothing_is_written(tmp_path: Path) -> None:
    clock = ManualClock(START)
    config = _config()
    path = tmp_path / "config.yaml"
    original = _write(path, _document(config))
    validator = ConfigChangeValidator(config)
    change_set = _change_set(
        clock,
        _change(clock, path="subagent_batches.max_live_items_per_batch", previous=1000, proposed=50),
    )

    result = validator.validate(change_set, _document(config))
    assert not result.ok
    message = _validation_error(result, ValidationErrorCode.OWNER_SCHEMA_INVALID)
    assert "subagent_batches" in message
    assert "default_max_live_items" in message

    candidate = _document(config)
    candidate["subagent_batches"]["max_live_items_per_batch"] = 50
    with pytest.raises(ValidationError, match="default_max_live_items"):
        AppConfig.model_validate(candidate)

    applied = _applier(path, config, clock).apply(change_set)
    assert applied.status is ApplyOutcome.FAILED
    assert path.read_bytes() == original


def test_default_off_subsystem_enable_is_refused_without_operator_authority() -> None:
    clock = ManualClock(START)
    config = _config()
    validator = ConfigChangeValidator(config)
    document = _document(config, memory={"l1": {"enabled": False}})
    change = _change(clock, path="memory.affective.enabled", previous=False, proposed=True)
    result = validator.validate(_change_set(clock, change), document)
    assert not result.ok
    assert [refusal.code for refusal in result.refusals] == [RefusalCode.OPERATOR_AUTHORIZATION_REQUIRED]
    assert "product decision" in result.refusals[0].reason

    token_change = _change(clock, path="memory.affective.enabled", previous=False, proposed=True, token="operator-signed-opaque-token")
    unverifiable = validator.validate(_change_set(clock, token_change, id="with-token"), document)
    assert [refusal.code for refusal in unverifiable.refusals] == [RefusalCode.OPERATOR_AUTHORIZATION_UNVERIFIABLE]
    assert not hasattr(validator, "mint_authorization_token")


def test_protected_list_cannot_be_self_modified_even_with_a_token() -> None:
    clock = ManualClock(START)
    config = _config()
    validator = ConfigChangeValidator(config)
    change = _change(clock, path="self_tuning.protected_paths", previous=["auth"], proposed=[], token="forged")
    result = validator.validate(_change_set(clock, change), _document(config))
    assert [refusal.code for refusal in result.refusals] == [RefusalCode.PROTECTED_PATH]


def test_registry_domains_are_introspected_from_real_owning_models() -> None:
    config = _config()
    for target in TargetRegistry(config).targets():
        field = target.owner_model.model_fields[target.field_name]
        ge_values = [item.ge for item in field.metadata if isinstance(item, Ge)]
        gt_values = [item.gt for item in field.metadata if isinstance(item, Gt)]
        le_values = [item.le for item in field.metadata if isinstance(item, Le)]
        lt_values = [item.lt for item in field.metadata if isinstance(item, Lt)]
        expected_floor = float(ge_values[0] if ge_values else gt_values[0]) if (ge_values or gt_values) else None
        expected_ceiling = float(le_values[0] if le_values else lt_values[0]) if (le_values or lt_values) else None
        assert target.domain.schema_floor == expected_floor
        assert target.domain.schema_ceiling == expected_ceiling
        assert target.domain.annotation is field.annotation
        assert target.floor <= target.ceiling
        assert not TargetRegistry(config).is_protected(target.path)


# --- canary -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("observation", "expected_decision", "expected_status"),
    [
        (CanaryObservation(status=CanaryStatus.HEALTHY, metrics={"error_rate": 0.01}), CanaryDecision.PROMOTE, CanaryStatus.HEALTHY),
        (CanaryObservation(status=CanaryStatus.UNHEALTHY, detail="latency doubled"), CanaryDecision.ABORT, CanaryStatus.UNHEALTHY),
        (None, CanaryDecision.ABORT, CanaryStatus.UNAVAILABLE),
    ],
)
def test_canary_promotes_only_on_real_healthy_evidence(observation, expected_decision, expected_status) -> None:
    clock = ManualClock(START)
    config = _config()
    selector = RecordingScopeSelector()
    runner = CanaryRunner(config, clock, selector, ScriptedCanaryProbe((observation,)))
    result = runner.run(_change_set(clock, _change(clock)))
    assert result.decision is expected_decision
    assert result.status is expected_status
    assert selector.applied == ["canary-tenant-1"]
    assert selector.cleared == ["canary-tenant-1"]
    assert (clock.now() - START).total_seconds() == pytest.approx(5.0)


def test_unavailable_scope_or_probe_aborts_and_never_promotes() -> None:
    clock = ManualClock(START)
    config = _config()
    missing_scope = CanaryRunner(config, clock, RecordingScopeSelector(None), ScriptedCanaryProbe((CanaryObservation(status=CanaryStatus.HEALTHY),)))
    assert missing_scope.run(_change_set(clock, _change(clock))).decision is CanaryDecision.ABORT

    raising_probe = CanaryRunner(config, clock, RecordingScopeSelector(), ScriptedCanaryProbe(()))
    result = raising_probe.run(_change_set(clock, _change(clock), id="exhausted"))
    assert result.decision is CanaryDecision.ABORT
    assert result.status is CanaryStatus.UNAVAILABLE


# --- verification and rollback ---------------------------------------------


@pytest.mark.parametrize(
    "after",
    [
        HealthSnapshot(captured_at=START, metrics={"error_rate": 0.05, "latency_ms": 300.0}),
        None,
    ],
)
def test_health_regression_or_unavailable_health_rolls_back(tmp_path: Path, after: HealthSnapshot | None) -> None:
    clock = ManualClock(START)
    config = _config()
    path = tmp_path / "config.yaml"
    _write(path, _document(config))
    applier = _applier(path, config, clock)
    original = _change_set(clock, _change(clock, proposed=300))
    assert applier.apply(original).status is ApplyOutcome.APPLIED

    before = HealthSnapshot(captured_at=START, metrics={"error_rate": 0.01, "latency_ms": 100.0})
    verifier = HealthVerifier(config, ScriptedHealthCheck((after,)))
    manager = RollbackManager(config, clock, applier)
    verification, rollback = manager.verify_or_rollback(original, before, verifier, requested_by="operator:test")
    assert not verification.keep
    assert verification.status in {VerificationStatus.REGRESSED, VerificationStatus.UNAVAILABLE}
    assert rollback is not None and rollback.status is ApplyOutcome.ROLLED_BACK
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["autonomy"]["bus"]["queue_maxsize"] == 256


def test_explicit_operator_rollback_is_a_recorded_rollback_change_set(tmp_path: Path) -> None:
    clock = ManualClock(START)
    config = _config()
    path = tmp_path / "config.yaml"
    _write(path, _document(config))
    applier = _applier(path, config, clock)
    original = _change_set(clock, _change(clock, proposed=300))
    assert applier.apply(original).status is ApplyOutcome.APPLIED

    ledger = ProvenanceLedger(tmp_path / "audit.jsonl", config, clock)
    manager = RollbackManager(config, clock, applier, recorder=ledger)
    rollback_set = manager.build_rollback(original, reason="operator requested", requested_by="operator:alice")
    assert rollback_set.kind is ChangeKind.ROLLBACK
    assert rollback_set.changes[0].proposed_value == 256
    assert manager.rollback(original, reason="operator requested", requested_by="operator:alice").status is ApplyOutcome.ROLLED_BACK
    assert [entry.action for entry in ledger.replay(rollback_set.id)] == [ProvenanceAction.ROLLBACK]
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["autonomy"]["bus"]["queue_maxsize"] == 256


# --- atomicity, verification-before-replace, and concurrency -----------------


def test_fault_between_candidate_write_and_verify_leaves_previous_bytes_identical(tmp_path: Path) -> None:
    clock = ManualClock(START)
    config = _config()
    path = tmp_path / "config.yaml"
    original = _write(path, _document(config))

    def crash(stage: str, change_set: ChangeSet) -> None:
        if stage == "after_temp_write":
            raise RuntimeError("injected crash between write and verify")

    result = _applier(path, config, clock, fault_hook=crash).apply(_change_set(clock, _change(clock)))
    assert result.status is ApplyOutcome.FAILED
    assert "injected crash" in result.reason
    assert path.read_bytes() == original
    assert not _applier(path, config, clock).journal_path.exists()


def test_fault_after_atomic_replace_restores_exact_previous_bytes(tmp_path: Path) -> None:
    clock = ManualClock(START)
    config = _config()
    path = tmp_path / "config.yaml"
    original = _write(path, _document(config))

    def crash(stage: str, change_set: ChangeSet) -> None:
        if stage == "after_atomic_replace":
            raise RuntimeError("injected crash after replace")

    applier = _applier(path, config, clock, fault_hook=crash)
    result = applier.apply(_change_set(clock, _change(clock)))
    assert result.status is ApplyOutcome.ROLLED_BACK
    assert path.read_bytes() == original
    assert not applier.journal_path.exists()


def test_unreloadable_candidate_never_replaces_config(tmp_path: Path) -> None:
    clock = ManualClock(START)
    config = _config()
    path = tmp_path / "config.yaml"
    original = _write(path, _document(config))

    def reject_candidate(candidate: Path) -> object:
        raise ValueError("injected full-config loader rejection")

    result = _applier(path, config, clock, config_file_loader=reject_candidate).apply(_change_set(clock, _change(clock)))
    assert result.status is ApplyOutcome.FAILED
    assert path.read_bytes() == original


def test_two_proposals_on_same_path_serialize_and_loser_is_told_it_lost(tmp_path: Path) -> None:
    clock = ManualClock(START)
    config = _config()
    path = tmp_path / "config.yaml"
    _write(path, _document(config))
    entered = Event()
    release = Event()

    def hold_first(stage: str, change_set: ChangeSet) -> None:
        if change_set.id == "first" and stage == "after_temp_write":
            entered.set()
            assert release.wait(5.0)

    applier = _applier(path, config, clock, fault_hook=hold_first)
    results: dict[str, ApplyResult] = {}
    errors: list[BaseException] = []

    def run(change_set: ChangeSet) -> None:
        try:
            results[change_set.id] = applier.apply(change_set)
        except BaseException as exc:  # pragma: no cover - surfaced by assertion
            errors.append(exc)

    first = _change_set(clock, _change(clock, proposed=300), id="first")
    second = _change_set(clock, _change(clock, proposed=400), id="second")
    first_thread = Thread(target=run, args=(first,))
    second_thread = Thread(target=run, args=(second,))
    first_thread.start()
    assert entered.wait(5.0)
    second_thread.start()
    release.set()
    first_thread.join(5.0)
    second_thread.join(5.0)
    assert not errors
    assert results["first"].status is ApplyOutcome.APPLIED
    assert results["second"].status is ApplyOutcome.FAILED
    assert "concurrent_update_lost" in results["second"].reason
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["autonomy"]["bus"]["queue_maxsize"] == 300
    applier.confirm("first")


def test_recover_pending_restores_interrupted_unverified_candidate(tmp_path: Path) -> None:
    clock = ManualClock(START)
    config = _config()
    path = tmp_path / "config.yaml"
    original = _write(path, _document(config))

    def crash(stage: str, change_set: ChangeSet) -> None:
        del change_set
        if stage == "after_reload":
            # Simulate process death after the verified write but before confirm by
            # leaving the durable journal in its ``applied`` stage.
            raise SystemExit("process exit")

    applier = _applier(path, config, clock, fault_hook=crash)
    with pytest.raises(SystemExit):
        applier.apply(_change_set(clock, _change(clock)))
    recovered = _applier(path, config, clock).recover_pending()
    assert recovered is not None and recovered.status is ApplyOutcome.ROLLED_BACK
    assert path.read_bytes() == original


# --- resource governor ------------------------------------------------------


def test_governor_proposal_is_bounded_damped_and_cooldown_blocks_oscillation() -> None:
    clock = ManualClock(START)
    config = _config()
    governor = ResourceGovernor(config, clock, TargetRegistry(config))
    rule = ResourceRule(target_path="memory.l1.recall_top_k", signal="queue_depth", direction="increase", full_scale=100.0)
    samples = ResourceSignals(queue_depth=100.0)

    proposal = governor.propose(rule, 5, samples, author="governor", rationale="recall queue depth saturated")
    assert proposal is not None
    change = proposal.changes[0]
    target = next(item for item in TargetRegistry(config).targets() if item.path == rule.target_path)
    assert target.floor <= change.proposed_value <= target.ceiling
    assert change.proposed_value - 5 <= (target.ceiling - target.floor) * config.max_step_ratio + 0.5
    assert change.rollback_value == 5

    assert governor.propose(rule, change.proposed_value, samples, author="governor", rationale="retry in cooldown") is None
    clock.advance(config.cooldown_seconds + 1)
    assert governor.propose(rule, change.proposed_value, samples, author="governor", rationale="after cooldown") is not None


def test_governor_missing_signal_and_exhausted_bounds_propose_nothing() -> None:
    clock = ManualClock(START)
    config = _config()
    governor = ResourceGovernor(config, clock, TargetRegistry(config))
    increase = ResourceRule(target_path="system_one.timeout_ms", signal="latency_ms", direction="increase", full_scale=1000.0)
    assert governor.propose(increase, 2000, ResourceSignals(), author="governor", rationale="no data") is None
    assert governor.propose(increase, 10_000, ResourceSignals(latency_ms=5000.0), author="governor", rationale="at ceiling") is None
    decrease = ResourceRule(target_path="system_one.timeout_ms", signal="latency_ms", direction="decrease", full_scale=1000.0)
    assert governor.propose(decrease, 500, ResourceSignals(latency_ms=5000.0), author="governor", rationale="at floor") is None


# --- provenance -------------------------------------------------------------


def test_provenance_full_chain_replays_and_detects_removed_or_reordered_entry(tmp_path: Path) -> None:
    clock = ManualClock(START)
    config = _config()
    ledger = ProvenanceLedger(tmp_path / "audit.jsonl", config, clock)
    change_set = _change_set(clock, _change(clock))
    validation = ValidationResult(ok=True, reason="validated")
    canary = CanaryResult(change_set_id=change_set.id, decision=CanaryDecision.PROMOTE, status=CanaryStatus.HEALTHY, scope="tenant-1", reason="healthy", observed_at=clock.now())
    applied = ApplyResult(change_set_id=change_set.id, status=ApplyOutcome.APPLIED, reason="reloaded", at=clock.now(), verified_reload=True)
    before = HealthSnapshot(captured_at=clock.now(), metrics={"error_rate": 0.01, "latency_ms": 100.0})
    after = HealthSnapshot(captured_at=clock.now(), metrics={"error_rate": 0.01, "latency_ms": 101.0})
    verification = VerificationResult(change_set_id=change_set.id, status=VerificationStatus.HEALTHY, keep=True, reason="no regression", before=before, after=after)

    ledger.record_propose(change_set)
    ledger.record_validation(change_set, validation)
    ledger.record_canary(change_set, canary)
    ledger.record_apply(change_set, applied.status, applied.reason)
    ledger.record_verify(change_set, verification)
    ledger.record_confirm(change_set, verification.reason)
    actions = [entry.action for entry in ledger.replay(change_set.id)]
    assert actions == [
        ProvenanceAction.PROPOSE,
        ProvenanceAction.VALIDATE,
        ProvenanceAction.CANARY,
        ProvenanceAction.APPLY,
        ProvenanceAction.VERIFY,
        ProvenanceAction.CONFIRM,
    ]
    assert ledger.replay(change_set.id)[3].before == 256
    assert ledger.replay(change_set.id)[3].after == 300

    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    ledger.path.write_text("\n".join(lines[:2] + lines[3:]) + "\n", encoding="utf-8")
    with pytest.raises(ProvenanceChainError, match="sequence|predecessor"):
        ledger.read_entries()


def test_provenance_expected_count_detects_truncated_tail_and_redacts_token_values(tmp_path: Path) -> None:
    clock = ManualClock(START)
    config = _config()
    ledger = ProvenanceLedger(tmp_path / "audit.jsonl", config, clock)
    change_set = _change_set(clock, _change(clock, path="system_one.api_key", previous="$OLD_KEY", proposed="$NEW_KEY", token="operator-secret-token"), id="secret-path")
    entries = ledger.record_propose(change_set)
    assert entries[0].after == "<redacted>"
    assert "operator-secret-token" not in ledger.path.read_text(encoding="utf-8")
    assert len(entries) == 1
    ledger.path.write_text("", encoding="utf-8")
    with pytest.raises(ProvenanceChainError, match="count"):
        ledger.read_entries(expected_count=1)


# --- orchestration and default-off ------------------------------------------


def test_full_protocol_runs_state_machine_and_writes_verified_chain(tmp_path: Path) -> None:
    clock = ManualClock(START)
    config = _config()
    path = tmp_path / "config.yaml"
    _write(path, _document(config))
    registry = TargetRegistry(config)
    validator = ConfigChangeValidator(config, registry=registry)
    applier = _applier(path, config, clock, registry=registry, config_file_loader=_script_loader)
    selector = RecordingScopeSelector()
    canary = CanaryRunner(config, clock, selector, ScriptedCanaryProbe((CanaryObservation(status=CanaryStatus.HEALTHY, metrics={"error_rate": 0.01}),)))
    health = ScriptedHealthCheck(
        (
            HealthSnapshot(captured_at=clock.now(), metrics={"error_rate": 0.01, "latency_ms": 100.0}),
            HealthSnapshot(captured_at=clock.now(), metrics={"error_rate": 0.01, "latency_ms": 100.0}),
        )
    )
    ledger = ProvenanceLedger(tmp_path / "audit.jsonl", config, clock)
    rollback = RollbackManager(config, clock, applier, registry=registry, recorder=ledger)
    protocol = SelfConfigurationProtocol(
        path,
        config,
        clock,
        registry=registry,
        validator=validator,
        canary=canary,
        applier=applier,
        health_check=health,
        rollback_manager=rollback,
        ledger=ledger,
    )
    change_set = _change_set(
        clock,
        _change(clock, path="memory.l1.recall_top_k", previous=5, proposed=7, blast_radius=BlastRadius.INSTANCE),
        id="full-run",
    )

    run = protocol.run(change_set)
    assert run.status is ApplyOutcome.APPLIED
    assert run.apply.verified_reload is True
    assert run.verification.keep is True
    assert run.rollback is None
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["memory"]["l1"]["recall_top_k"] == 7
    assert [entry.action for entry in ledger.replay("full-run")] == [
        ProvenanceAction.PROPOSE,
        ProvenanceAction.VALIDATE,
        ProvenanceAction.CANARY,
        ProvenanceAction.APPLY,
        ProvenanceAction.VERIFY,
        ProvenanceAction.CONFIRM,
    ]


def test_canary_abort_automatically_records_a_known_good_rollback(tmp_path: Path) -> None:
    clock = ManualClock(START)
    config = _config()
    path = tmp_path / "config.yaml"
    original = _write(path, _document(config))
    registry = TargetRegistry(config)
    applier = _applier(path, config, clock, registry=registry)
    ledger = ProvenanceLedger(tmp_path / "audit.jsonl", config, clock)
    protocol = SelfConfigurationProtocol(
        path,
        config,
        clock,
        registry=registry,
        validator=ConfigChangeValidator(config, registry=registry),
        canary=CanaryRunner(
            config,
            clock,
            RecordingScopeSelector(),
            ScriptedCanaryProbe((CanaryObservation(status=CanaryStatus.UNHEALTHY, detail="synthetic regression"),)),
        ),
        applier=applier,
        health_check=ScriptedHealthCheck((HealthSnapshot(captured_at=clock.now(), metrics={"error_rate": 0.01, "latency_ms": 100.0}),)),
        rollback_manager=RollbackManager(config, clock, applier, registry=registry, recorder=ledger),
        ledger=ledger,
    )
    change_set = _change_set(
        clock,
        _change(clock, path="memory.l1.recall_top_k", previous=5, proposed=7, blast_radius=BlastRadius.INSTANCE),
        id="canary-abort",
    )
    result = protocol.run(change_set)
    assert result.status is ApplyOutcome.ROLLED_BACK
    assert result.rollback is not None and result.rollback.status is ApplyOutcome.ROLLED_BACK
    assert path.read_bytes() == original
    original_actions = [entry.action for entry in ledger.replay("canary-abort")]
    assert ProvenanceAction.APPLY not in original_actions
    rollback_entries = [entry for entry in ledger.read_entries() if entry.action is ProvenanceAction.ROLLBACK]
    assert len(rollback_entries) == 1
    assert rollback_entries[0].change_set_id.startswith("rollback:canary-abort:")


def test_startup_only_target_needs_human_instead_of_unattended_apply(tmp_path: Path) -> None:
    clock = ManualClock(START)
    config = _config()
    path = tmp_path / "config.yaml"
    _write(path, _document(config))
    registry = TargetRegistry(config)
    applier = _applier(path, config, clock, registry=registry)
    health = ScriptedHealthCheck((HealthSnapshot(captured_at=clock.now(), metrics={"error_rate": 0.01, "latency_ms": 100.0}),))
    ledger = ProvenanceLedger(tmp_path / "audit.jsonl", config, clock)
    protocol = SelfConfigurationProtocol(
        path,
        config,
        clock,
        registry=registry,
        validator=ConfigChangeValidator(config, registry=registry),
        canary=CanaryRunner(config, clock, RecordingScopeSelector(), ScriptedCanaryProbe((CanaryObservation(status=CanaryStatus.HEALTHY),))),
        applier=applier,
        health_check=health,
        rollback_manager=RollbackManager(config, clock, applier, registry=registry, recorder=ledger),
        ledger=ledger,
    )
    change_set = _change_set(clock, _change(clock, path="autonomy.bus.queue_maxsize", proposed=512), id="cold-target")
    result = protocol.run(change_set)
    assert result.status is ApplyOutcome.NEEDS_HUMAN
    assert "startup_only" in result.reason
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["autonomy"]["bus"]["queue_maxsize"] == 256


def test_disabled_protocol_is_a_no_op_across_every_entry_point(tmp_path: Path) -> None:
    clock = ManualClock(START)
    disabled = SelfTuningConfig()
    path = tmp_path / "config.yaml"
    original = _write(path, _document(disabled))
    registry = TargetRegistry(disabled)
    validator = ConfigChangeValidator(disabled, registry=registry)
    change_set = _change_set(clock, _change(clock, proposed=300))
    assert validator.validate(change_set, _document(disabled)).no_op

    applier = _applier(path, disabled, clock, registry=registry)
    assert applier.apply(change_set).status is ApplyOutcome.NO_OP
    assert CanaryRunner(disabled, clock, RecordingScopeSelector(), ScriptedCanaryProbe(())).run(change_set).status is CanaryStatus.NOT_RUN
    before = HealthSnapshot(captured_at=clock.now(), metrics={"error_rate": 0.01, "latency_ms": 100.0})
    assert HealthVerifier(disabled, ScriptedHealthCheck((before,))).verify(change_set, before).status is VerificationStatus.NOT_RUN
    assert RollbackManager(disabled, clock, applier, registry=registry).rollback(change_set, reason="x", requested_by="y").status is ApplyOutcome.NO_OP
    rule = ResourceRule(target_path="system_one.timeout_ms", signal="latency_ms", direction="increase")
    assert ResourceGovernor(disabled, clock, registry).propose(rule, 2000, ResourceSignals(latency_ms=5000.0), author="g", rationale="r") is None
    ledger = ProvenanceLedger(tmp_path / "disabled.jsonl", disabled, clock)
    assert ledger.record_propose(change_set) == ()
    assert not ledger.path.exists()
    assert path.read_bytes() == original


# --- configuration reader coverage ------------------------------------------


def test_every_self_tuning_config_key_has_a_behavioral_reader() -> None:
    policy = _policy(
        required_metrics=("error_rate", "throughput"),
        higher_is_better=("throughput",),
        lower_is_better=("error_rate",),
        relative_tolerance=0.10,
    )
    config = _config(protected_paths=("custom.operator_secret",), verification_policy=policy)
    assert set(type(config).model_fields) == {
        "enabled",
        "canary_window_seconds",
        "max_step_ratio",
        "cooldown_seconds",
        "bounds_source",
        "protected_paths",
        "verification_policy",
    }
    assert set(type(policy).model_fields) == {"required_metrics", "higher_is_better", "lower_is_better", "relative_tolerance"}

    clock = ManualClock(START)
    selector = RecordingScopeSelector()
    CanaryRunner(config, clock, selector, ScriptedCanaryProbe((CanaryObservation(status=CanaryStatus.HEALTHY),))).run(_change_set(clock, _change(clock)))
    assert (clock.now() - START).total_seconds() == config.canary_window_seconds

    registry = TargetRegistry(config)
    assert registry.bounds_source == config.bounds_source == "registry"
    assert registry.is_protected("custom.operator_secret.value")
    assert set(PROTECTED_PATH_REASONS) <= set(config.protected_paths)

    governor = ResourceGovernor(config, clock, registry)
    rule = ResourceRule(target_path="memory.l1.recall_top_k", signal="queue_depth", direction="increase", full_scale=100.0)
    proposal = governor.propose(rule, 5, ResourceSignals(queue_depth=100.0), author="governor", rationale="reader")
    assert proposal is not None
    target = registry.resolve(rule.target_path).target
    assert isinstance(target, ConfigTarget)
    assert proposal.changes[0].proposed_value - 5 <= (target.ceiling - target.floor) * config.max_step_ratio + 0.5
    assert governor.propose(rule, proposal.changes[0].proposed_value, ResourceSignals(queue_depth=100.0), author="governor", rationale="cooldown") is None

    change_set = _change_set(clock, _change(clock), id="verify-reader")
    before = HealthSnapshot(captured_at=clock.now(), metrics={"error_rate": 0.1, "throughput": 10.0})
    tolerated = ScriptedHealthCheck((HealthSnapshot(captured_at=clock.now(), metrics={"error_rate": 0.11, "throughput": 9.0}),))
    assert HealthVerifier(config, tolerated).verify(change_set, before).keep is True
    regressed = ScriptedHealthCheck((HealthSnapshot(captured_at=clock.now(), metrics={"error_rate": 0.12, "throughput": 8.0}),))
    assert HealthVerifier(config, regressed).verify(change_set, before).keep is False
    missing = ScriptedHealthCheck((HealthSnapshot(captured_at=clock.now(), metrics={"error_rate": 0.1}),))
    assert HealthVerifier(config, missing).verify(change_set, before).status is VerificationStatus.UNAVAILABLE


def test_default_off_subsystem_inventory_is_derived_from_real_memory_models() -> None:
    paths = default_off_subsystem_paths()
    assert len(paths) == 11
    assert "memory.l1.enabled" in paths
    assert "memory.affective.enabled" in paths
