from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from alpha.reasoning.atoms import AtomicThought, contract, decompose
from alpha.reasoning.budget import BudgetLimits, ReasoningBudgetLedger
from alpha.reasoning.config import (
    ReasoningConfig,
    ReasoningConfigError,
    is_reasoning_enabled,
    load_reasoning_config,
)
from alpha.reasoning.events import ReasoningEvent, ReasoningEventType
from alpha.reasoning.loopguard import LoopGuardCaps, LoopObservation, evaluate_loopguard
from alpha.reasoning.models import (
    AtomicThought as AtomicThoughtModel,
)
from alpha.reasoning.models import (
    BudgetSnapshot,
    EvidenceRecord,
    EvidenceSourceType,
    HypothesisRecord,
    ReasoningMode,
    ReasoningState,
    UncertaintyCalibration,
    UncertaintyEstimate,
)
from alpha.reasoning.policy import PolicyThresholds, TaskSignals, select_policy
from alpha.reasoning.summary import SummarySettings, generate_reasoning_summary, write_safe_reasoning_payload
from alpha.reasoning.ttcs import TTCSAllocator, TTCSSettings
from alpha.reasoning.uncertainty import UncertaintyContext, UncertaintyRecord, decide_uncertainty

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "backend" / "packages" / "harness" / "alpha" / "reasoning"


def test_state_clamps_ranges_and_discloses_every_change() -> None:
    record = EvidenceRecord(
        id="ev-1",
        claim="a claim",
        source="test",
        source_type=EvidenceSourceType.TEST,
        confidence=1.4,
    )
    state = ReasoningState(
        objective="verify the disclosed clamp",
        retry_count=-3,
        confidence=UncertaintyEstimate(value=-0.2, method="fixture"),
        hypotheses=[],
    )
    atom = AtomicThoughtModel(
        id="a1",
        question="q",
        statement="s",
        answer_equivalence_key="k",
        token_estimate=-4,
    )

    assert record.confidence == 1.0
    assert state.retry_count == 0
    assert state.confidence is not None and state.confidence.value == 0.0
    assert atom.token_estimate == 1
    assert any("confidence" in notice for notice in record.clamp_disclosures)
    assert any("retry_count" in notice for notice in state.clamp_disclosures)
    assert any("value" in notice for notice in state.confidence.clamp_disclosures)
    assert any("token_estimate" in notice for notice in atom.clamp_disclosures)


def test_state_uses_closed_enum_and_status_sets() -> None:
    state = ReasoningState(objective="closed enums")
    assert state.status.value == "created"
    assert state.mode is ReasoningMode.EXPLICIT

    with pytest.raises(ValidationError):
        HypothesisRecord(id="h", statement="s", status="trusted-by-vibes")
    with pytest.raises(ValidationError):
        HypothesisRecord(id="h", statement="s", kind="private_monologue")
    with pytest.raises(ValidationError):
        BudgetSnapshot.model_validate({"limits": {"gpu_seconds": 1}})


def test_calibrated_uncertainty_requires_an_evaluation_reference() -> None:
    with pytest.raises(ValidationError, match="calibration_ref"):
        UncertaintyEstimate(
            value=0.4,
            calibration_status=UncertaintyCalibration.CALIBRATED,
            method="evaluated",
        )
    estimate = UncertaintyEstimate(
        value=0.4,
        calibration_status=UncertaintyCalibration.CALIBRATED,
        method="evaluated",
        calibration_ref="fixture-suite-v1",
    )
    assert estimate.calibration_ref == "fixture-suite-v1"


def test_default_config_is_off_and_has_no_storage_side_effect(tmp_path: Path) -> None:
    config = ReasoningConfig()
    assert config.enabled is False
    assert config.storage_path is None
    assert config.loop_guard.enabled is False
    assert config.ttcs.regime.value == "single_trajectory"
    assert is_reasoning_enabled(config) is False
    assert not list(tmp_path.iterdir())


def test_runtime_home_override_loads_without_shared_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    override_dir = tmp_path / "reasoning"
    override_dir.mkdir()
    payload = {
        "enabled": True,
        "max_atoms": 7,
        "max_depth": 3,
        "storage_path": "state/reasoning.json",
    }
    (override_dir / "config.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))

    config = load_reasoning_config()
    assert config.enabled is True
    assert config.max_atoms == 7
    assert config.storage_path == (override_dir / "state" / "reasoning.json").resolve()


def test_malformed_or_missing_explicit_config_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(ReasoningConfigError, match="does not exist"):
        load_reasoning_config(tmp_path / "missing.json")
    invalid = tmp_path / "invalid.json"
    invalid.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ReasoningConfigError, match="JSON object"):
        load_reasoning_config(invalid)


def test_every_top_level_config_key_has_a_real_reader(tmp_path: Path) -> None:
    storage = tmp_path / "state.json"
    config = ReasoningConfig(
        enabled=True,
        default_mode=ReasoningMode.EXPLICIT,
        policy_thresholds=PolicyThresholds(trivial_max_complexity=0.2, fast_max_complexity=0.4, deep_min_complexity=0.8),
        budget_defaults=BudgetLimits(max_model_calls=5, max_tokens=5000),
        ttcs=TTCSSettings(default_samples=2, max_samples=3, max_tokens_per_sample=100),
        uncertainty_enabled=True,
        uncertainty_require_calibration_for_stop=False,
        summary=SummarySettings(max_items=2, max_chars=1000, max_payload_bytes=100_000),
        loop_guard=LoopGuardCaps(enabled=True, signal_threshold=2, max_attempts=3),
        max_atoms=3,
        max_depth=2,
        max_width=2,
        storage_path=storage,
    )

    assert is_reasoning_enabled(config)
    policy = select_policy(
        TaskSignals(),
        thresholds=config.policy_thresholds,
        default_mode=config.default_mode,
    )
    assert policy.mode is ReasoningMode.EXPLICIT
    assert policy.under_determined is True

    ledger = ReasoningBudgetLedger.from_config(config)
    assert ledger.limits.max_model_calls == 5
    allocator = TTCSAllocator.from_config(config)
    assert allocator.default_request().regime is config.ttcs.regime
    assert allocator.default_request().requested_samples == 2

    uncertainty = UncertaintyRecord(factual=UncertaintyEstimate(value=0.1, method="fixture"))
    decision = decide_uncertainty(
        uncertainty,
        UncertaintyContext(verification_passed=True),
        config=config,
    )
    assert decision.action.value == "stop"

    state = ReasoningState(objective="reader exercise", plan=["one"])
    assert generate_reasoning_summary(state, config=config).result

    observations = [
        LoopObservation(
            sequence=index,
            created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            action_signature="same",
        )
        for index in range(2)
    ]
    loop_decision = evaluate_loopguard(
        observations,
        attempts=1,
        reflections=0,
        replans=0,
        caps=config.loop_guard,
    )
    assert loop_decision.intervention is not None

    atoms = [
        AtomicThought(
            id=f"a{index}",
            question=f"q{index}",
            statement=f"s{index}",
            answer_equivalence_key="key",
        )
        for index in range(2)
    ]
    dag = decompose(
        "reader exercise",
        atoms,
        max_atoms=config.max_atoms,
        max_depth=config.max_depth,
        max_width=config.max_width,
    )
    assert contract(dag).token_estimate > 0
    assert write_safe_reasoning_payload(None, state, config=config) == storage.resolve()
    assert storage.is_file()


def test_package_root_preserves_legacy_governor_config_and_exposes_new_alias() -> None:
    import alpha.reasoning as reasoning

    legacy = reasoning.ReasoningConfig
    assert legacy.__module__ == "alpha.reasoning.governor"
    assert "tier" in legacy.__dataclass_fields__
    assert reasoning.ReasoningPlaneConfig.__module__ == "alpha.reasoning.config"
    assert reasoning.ReasoningPlaneConfig().enabled is False
    assert "ReasoningConfig" in reasoning.__all__


def test_importing_package_root_does_not_load_shared_config_or_gateway() -> None:
    env = dict(os.environ)
    pythonpath = os.pathsep.join(
        (
            str(REPO_ROOT / "backend"),
            str(REPO_ROOT / "backend" / "packages" / "harness"),
            env.get("PYTHONPATH", ""),
        )
    )
    env["PYTHONPATH"] = pythonpath
    code = "import sys; import alpha.reasoning; assert 'alpha.config' not in sys.modules; assert 'app.gateway.app' not in sys.modules; print('lazy-ok')"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "lazy-ok"


def test_new_reasoning_modules_do_not_import_shared_config_or_gateway() -> None:
    forbidden = {"alpha.config", "app.gateway.app"}
    new_files = {
        "__init__.py",
        "atoms.py",
        "budget.py",
        "config.py",
        "events.py",
        "loopguard.py",
        "models.py",
        "policy.py",
        "summary.py",
        "ttcs.py",
        "uncertainty.py",
    }
    for name in new_files:
        tree = ast.parse((PACKAGE_ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module not in forbidden, (name, node.module)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in forbidden, (name, alias.name)


def test_event_records_are_emit_only_and_serializable() -> None:
    event = ReasoningEvent(run_id="run-1", event_type=ReasoningEventType.STARTED)
    payload = event.model_dump_json()
    assert ReasoningEvent.model_validate_json(payload) == event
    source = (PACKAGE_ROOT / "events.py").read_text(encoding="utf-8")
    assert "alpha.events" not in source
    assert "runtime.events" not in source
