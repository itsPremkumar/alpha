"""Hermetic contract tests for Alpha's memory-admission policy engine."""

from __future__ import annotations

import ast
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import alpha.memory.policy as policy_root
from alpha.memory.policy.config import PolicyConfig
from alpha.memory.policy.engine import AdmissionEngine, PolicySet, default_policy
from alpha.memory.policy.loader import (
    DEFAULT_POLICY_PATH,
    PolicyLoader,
    PolicyValidationError,
    parse_policy_document,
    resolve_policy_path,
)
from alpha.memory.policy.models import AdmissionCandidate
from alpha.memory.policy.provenance import read_entries
from alpha.memory.policy.rules import (
    DEFAULT_RULES,
    RULE_ACTIONS,
    RULE_IDS,
    AdmissionRule,
    detect_secret_like,
)
from alpha.memory.policy.scoring import (
    DEFAULT_SCORE_WEIGHTS,
    SCORE_SIGNALS,
    score_candidate,
    validate_score_weights,
    weighted_score,
)


def candidate(**overrides: Any) -> AdmissionCandidate:
    values: dict[str, Any] = {
        "candidate_id": "candidate-1",
        "content": "A routine implementation note",
        "types": [],
        "source": "assistant",
    }
    values.update(overrides)
    return AdmissionCandidate(**values)


# ---------------------------------------------------------------------------
# Models and exact section-10 scoring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("signals", "expected", "expected_terms"),
    [
        (
            {
                "importance": 1.0,
                "future_utility": 1.0,
                "novelty": 1.0,
                "confidence": 1.0,
                "recurrence": 1.0,
                "task_relevance": 1.0,
                "explicit_user_request": True,
            },
            1.0,
            {
                "importance": 0.25,
                "future_utility": 0.20,
                "novelty": 0.15,
                "confidence": 0.15,
                "recurrence": 0.10,
                "task_relevance": 0.10,
                "explicit_user_request": 0.05,
            },
        ),
        (
            {
                "importance": 0.8,
                "future_utility": 0.6,
                "novelty": 0.4,
                "confidence": 0.2,
                "recurrence": 0.1,
                "task_relevance": 0.3,
                "explicit_user_request": False,
            },
            0.45,
            {
                "importance": 0.20,
                "future_utility": 0.12,
                "novelty": 0.06,
                "confidence": 0.03,
                "recurrence": 0.01,
                "task_relevance": 0.03,
                "explicit_user_request": 0.0,
            },
        ),
        (
            {
                "importance": 0.5,
                "future_utility": 0.25,
                "novelty": 0.0,
                "confidence": 1.0,
                "recurrence": 0.75,
                "task_relevance": 0.4,
                "explicit_user_request": True,
            },
            0.49,
            {
                "importance": 0.125,
                "future_utility": 0.05,
                "novelty": 0.0,
                "confidence": 0.15,
                "recurrence": 0.075,
                "task_relevance": 0.04,
                "explicit_user_request": 0.05,
            },
        ),
    ],
)
def test_weighted_score_matches_hand_computed_values(
    signals: dict[str, Any],
    expected: float,
    expected_terms: dict[str, float],
) -> None:
    result = score_candidate(candidate(**signals))

    assert result.score == expected
    assert weighted_score(candidate(**signals)) == expected
    assert dict(result.terms) == expected_terms
    assert math.isclose(math.fsum(result.terms.values()), expected, abs_tol=1e-12)
    assert result.missing_signals == ()
    assert set(result.terms) == set(SCORE_SIGNALS)


def test_missing_signals_are_zero_and_disclosed_never_imputed() -> None:
    result = score_candidate(candidate(importance=0.8))

    assert result.score == 0.20
    assert dict(result.terms) == {
        "importance": 0.20,
        "future_utility": 0.0,
        "novelty": 0.0,
        "confidence": 0.0,
        "recurrence": 0.0,
        "task_relevance": 0.0,
        "explicit_user_request": 0.0,
    }
    assert result.missing_signals == (
        "future_utility",
        "novelty",
        "confidence",
        "recurrence",
        "task_relevance",
        "explicit_user_request",
    )
    assert "missing_signal:confidence=0.0" in result.disclosures


def test_total_is_clamped_and_disclosed_without_changing_term_breakdown() -> None:
    equal_weights = {name: 1.0 / len(SCORE_SIGNALS) for name in SCORE_SIGNALS}
    out_of_range = {name: 4.0 for name in SCORE_SIGNALS if name != "explicit_user_request"}
    out_of_range["explicit_user_request"] = True
    result = score_candidate(candidate(**out_of_range), weights=equal_weights)

    assert result.score == 1.0
    assert result.terms["importance"] > 0.0
    assert math.fsum(result.terms.values()) > 1.0
    assert any(item.startswith("score_clamped:high:") for item in result.disclosures)


def test_weight_override_must_be_complete_normalized_and_non_boolean() -> None:
    assert validate_score_weights(DEFAULT_SCORE_WEIGHTS) == DEFAULT_SCORE_WEIGHTS
    with pytest.raises(ValueError, match="missing"):
        validate_score_weights({"importance": 1.0})
    with pytest.raises(ValueError, match="sum to 1.0"):
        validate_score_weights({name: 0.1 for name in SCORE_SIGNALS})
    with pytest.raises(ValueError, match="boolean"):
        validate_score_weights({**DEFAULT_SCORE_WEIGHTS, "importance": True})


# ---------------------------------------------------------------------------
# Hard rules, first-match precedence, and no-blind-promotion safeguards
# ---------------------------------------------------------------------------


def test_default_rule_names_actions_and_security_order_match_plan() -> None:
    assert tuple(rule.rule_id for rule in DEFAULT_RULES) == RULE_IDS
    assert RULE_IDS[0] == "secret_like"
    assert {rule.rule_id: rule.action for rule in DEFAULT_RULES} == {
        "secret_like": "reject",
        "explicit_remember": "always_store",
        "project_decision": "durable_project",
        "user_preference": "durable_profile",
        "successful_procedure": "promote_to_skill_candidate",
        "transient_status": "session_only",
        "raw_tool_output": "episodic_archive",
    }
    assert {rule.rule_id: rule.action for rule in DEFAULT_RULES} == RULE_ACTIONS


@pytest.mark.parametrize(
    ("overrides", "action", "rule_id"),
    [
        (
            {"content": "Please remember this deployment convention"},
            "always_store",
            "explicit_remember",
        ),
        (
            {
                "content": "The project will use PostgreSQL",
                "types": ["project_decision"],
                "provenance": "trace-123:decision-4",
            },
            "durable_project",
            "project_decision",
        ),
        (
            {
                "content": "The user prefers concise answers",
                "types": ["user_preference"],
                "confidence": 0.70,
            },
            "durable_profile",
            "user_preference",
        ),
        (
            {
                "content": "Run the migration validation sequence",
                "types": ["successful_procedure"],
                "success_count": 2,
            },
            "promote_to_skill_candidate",
            "successful_procedure",
        ),
        (
            {"content": "The deploy is currently running", "is_transient": True},
            "session_only",
            "transient_status",
        ),
        (
            {"content": "tool stdout", "source": "tool_output"},
            "episodic_archive",
            "raw_tool_output",
        ),
        (
            {"content": "caller-classified credential", "is_secret_like": True},
            "reject",
            "secret_like",
        ),
    ],
)
def test_each_hard_rule_fires_for_its_trigger(
    overrides: dict[str, Any],
    action: str,
    rule_id: str,
) -> None:
    decision = AdmissionEngine().evaluate(candidate(**overrides))

    assert decision.action == action
    assert decision.rule_id == rule_id
    assert decision.reason
    assert set(decision.score_breakdown) == set(SCORE_SIGNALS)
    assert decision.admit is (action != "reject")


@pytest.mark.parametrize(
    ("overrides", "expected_rule"),
    [
        ({"content": "I remember the old deployment"}, "admission_threshold"),
        (
            {"content": "The project chose SQLite", "types": ["project_decision"]},
            "project_decision",
        ),
        (
            {"content": "The user likes dark mode", "types": ["user_preference"], "confidence": 0.69},
            "admission_threshold",
        ),
        (
            {"content": "Try the recovery steps", "types": ["procedure"], "success_count": 1},
            "admission_threshold",
        ),
        ({"content": "Build completed", "is_transient": False}, "admission_threshold"),
        ({"content": "assistant text", "source": "assistant"}, "admission_threshold"),
    ],
)
def test_hard_rule_near_misses_do_not_fire(overrides: dict[str, Any], expected_rule: str) -> None:
    decision = AdmissionEngine().evaluate(candidate(**overrides))

    assert decision.rule_id == expected_rule
    if expected_rule == "admission_threshold":
        assert decision.admit is False
        assert "near misses:" in decision.reason
    else:
        assert decision.action == "reject"
        assert "provenance" in decision.reason


def test_project_decision_without_provenance_is_rejected_not_defaulted() -> None:
    decision = AdmissionEngine().evaluate(
        candidate(
            content="The project will deploy on Fridays",
            types=["project_decision"],
            provenance="",
        )
    )

    assert decision.admit is False
    assert decision.action == "reject"
    assert decision.rule_id == "project_decision"
    assert "requires provenance" in decision.reason
    assert dict(decision.score_breakdown)


def test_first_match_wins_and_secret_boundary_cannot_be_reordered() -> None:
    overlapping = candidate(
        content="Keep the project's chosen database",
        explicit_user_request=True,
        types=["project_decision", "user_preference", "procedure"],
        provenance="trace-1",
        confidence=0.95,
        success_count=3,
    )
    default_engine = AdmissionEngine()

    assert default_engine.evaluate(overlapping).rule_id == "explicit_remember"

    reordered = (
        DEFAULT_RULES[0],
        DEFAULT_RULES[2],
        DEFAULT_RULES[3],
        DEFAULT_RULES[4],
        DEFAULT_RULES[5],
        DEFAULT_RULES[6],
        DEFAULT_RULES[1],
    )
    reordered_engine = AdmissionEngine(PolicySet(rules=reordered))

    assert reordered_engine.evaluate(overlapping).rule_id == "project_decision"

    with pytest.raises(ValueError, match="secret_like must be the first"):
        PolicySet(rules=(DEFAULT_RULES[1], DEFAULT_RULES[0], *DEFAULT_RULES[2:]))

    secret_overlap = candidate(
        content="Please remember this private key",
        explicit_user_request=True,
        is_secret_like=True,
    )
    assert default_engine.evaluate(secret_overlap).rule_id == "secret_like"


@pytest.mark.parametrize(
    ("overrides", "action"),
    [
        (
            {
                "content": "The project probably uses Redis",
                "types": ["project_decision"],
                "provenance": "model-note-7",
                "claim_status": "guess",
            },
            "reject",
        ),
        (
            {
                "content": "The user currently prefers compact output",
                "types": ["user_preference"],
                "confidence": 0.95,
                "claim_status": "temporary",
            },
            "episodic_archive",
        ),
        (
            {
                "content": "Restarting the worker fixed the issue",
                "types": ["procedure"],
                "success_count": 4,
                "claim_status": "volatile",
            },
            "episodic_archive",
        ),
        (
            {
                "content": "The vendor promises this endpoint is stable",
                "types": ["project_decision"],
                "provenance": "web-note-2",
                "source": "third_party",
                "is_verified": False,
            },
            "episodic_archive",
        ),
    ],
)
def test_do_not_promote_blindly_guards_durable_actions(
    overrides: dict[str, Any],
    action: str,
) -> None:
    decision = AdmissionEngine().evaluate(candidate(**overrides))

    assert decision.action == action
    assert decision.admit is (action != "reject")
    assert decision.reason
    assert decision.rule_id != "admission_threshold"


def test_verified_third_party_project_claim_can_be_durable() -> None:
    decision = AdmissionEngine().evaluate(
        candidate(
            content="The project standardizes on OAuth device flow",
            types=["project_decision"],
            source="third_party",
            provenance="vendor-doc-12",
            is_verified=True,
        )
    )

    assert decision.action == "durable_project"
    assert decision.rule_id == "project_decision"


# ---------------------------------------------------------------------------
# Secret detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "pattern_id", "confidence"),
    [
        ("OPENAI_API_KEY=sk-abcdefghijklmnop1234", "provider_api_key", "certain"),
        ("Authorization: Bearer N7m2Kq9Lx4Pd8Rt1Vw6Z", "bearer_token", "certain"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private_key_header", "certain"),
        ("postgresql://alpha:s3cr3t-password@db.internal/alpha", "credentialed_connection_string", "certain"),
        ('api_key = "A1b2C3d4E5f6G7h8I9j0"', "high_entropy_assignment", "uncertain"),
        ('api_key = "0123456789abcdef0123456789abcdef"', "high_entropy_assignment", "uncertain"),
    ],
)
def test_secret_detector_catches_documented_patterns(
    text: str,
    pattern_id: str,
    confidence: str,
) -> None:
    detection = detect_secret_like(text)

    assert detection.is_secret_like is True
    assert detection.pattern_id == pattern_id
    assert detection.confidence == confidence

    decision = AdmissionEngine().evaluate(candidate(content=text))
    assert decision.action == "reject"
    assert decision.rule_id == "secret_like"
    assert f"confidence={confidence}" in decision.reason


@pytest.mark.parametrize(
    "text",
    [
        "Rotate the API key according to the project runbook.",
        'api_key = "not-a-real-placeholder"',
        "postgresql://alpha@db.internal/alpha has no embedded password",
    ],
)
def test_secret_detector_does_not_flag_documented_near_misses(text: str) -> None:
    detection = detect_secret_like(text)

    assert detection.is_secret_like is False
    assert detection.pattern_id == "none"


def test_disabling_patterns_never_overrides_explicit_secret_hint() -> None:
    config = PolicyConfig(enabled=True, secret_patterns_enabled=False)
    engine = AdmissionEngine(config=config)
    content = "OPENAI_API_KEY=sk-abcdefghijklmnop1234"

    pattern_off = engine.evaluate(candidate(content=content, is_secret_like=False))
    assert pattern_off.action == "reject"
    assert "secret pattern detection disabled" in pattern_off.reason

    caller_hint = engine.evaluate(candidate(content="classified", is_secret_like=True))
    assert caller_hint.action == "reject"
    assert caller_hint.rule_id == "secret_like"
    assert "caller marked" in caller_hint.reason


# ---------------------------------------------------------------------------
# Threshold, batch behavior, and runtime policy swap
# ---------------------------------------------------------------------------


def test_min_score_boundary_is_inclusive_for_episodic_only_admission() -> None:
    engine = AdmissionEngine()
    at_boundary = candidate(
        content="A useful but non-durable project observation",
        importance=1.0,
        future_utility=1.0,
        novelty=1.0,
        confidence=0.0,
        recurrence=0.0,
        task_relevance=0.0,
        explicit_user_request=False,
    )
    below = candidate(**{**at_boundary.model_dump(), "importance": 0.998})

    assert score_candidate(at_boundary).score == 0.60
    at_decision = engine.evaluate(at_boundary)
    below_decision = engine.evaluate(below)

    assert at_decision.admit is True
    assert at_decision.action == "episodic_archive"
    assert at_decision.tier == "archived"
    assert below_decision.admit is False
    assert below_decision.action == "reject"
    assert below_decision.rule_id == "admission_threshold"
    assert "below min_score_to_admit" in below_decision.reason


def test_explicit_hard_rule_bypasses_score_floor() -> None:
    decision = AdmissionEngine().evaluate(
        candidate(content="Remember this exact note", explicit_user_request=False)
    )

    assert decision.score == 0.0
    assert decision.admit is True
    assert decision.action == "always_store"
    assert decision.rule_id == "explicit_remember"


def test_batch_evaluation_keeps_one_ordered_decision_per_item() -> None:
    decisions = AdmissionEngine().evaluate_batch(
        [
            candidate(candidate_id="a", content="Please remember this first note"),
            candidate(candidate_id="b", content="classified", is_secret_like=True),
            candidate(
                candidate_id="c",
                content="The user prefers dark mode",
                types=["user_preference"],
                confidence=0.9,
            ),
        ]
    )

    assert [decision.action for decision in decisions] == [
        "always_store",
        "reject",
        "durable_profile",
    ]
    assert [decision.rule_id for decision in decisions] == [
        "explicit_remember",
        "secret_like",
        "user_preference",
    ]


def test_policy_can_be_swapped_at_runtime_without_losing_explanations() -> None:
    item = candidate(
        content="The user prefers concise status updates",
        types=["user_preference"],
        confidence=0.8,
    )
    strict_policy = PolicySet(
        rules=(
            DEFAULT_RULES[0],
            DEFAULT_RULES[1],
            DEFAULT_RULES[2],
            AdmissionRule("user_preference", "durable_profile", min_confidence=0.9),
            *DEFAULT_RULES[4:],
        )
    )
    engine = AdmissionEngine()

    assert engine.evaluate(item).admit is True
    previous = engine.swap_policy(strict_policy)
    assert previous.thresholds == default_policy().thresholds
    decision = engine.evaluate(item)
    assert decision.admit is False
    assert "min_confidence=0.9" in decision.reason
    assert dict(decision.score_breakdown)


# ---------------------------------------------------------------------------
# Configuration and provenance
# ---------------------------------------------------------------------------


def test_every_policy_config_key_has_a_real_reader_and_no_host_config_import() -> None:
    expected = {
        "enabled",
        "policy_path",
        "strict",
        "min_score_to_admit",
        "weights_override",
        "secret_patterns_enabled",
        "storage_path",
    }
    config = PolicyConfig(
        enabled=True,
        policy_path="state/policy.yaml",
        strict=False,
        min_score_to_admit=0.42,
        weights_override=DEFAULT_SCORE_WEIGHTS,
        secret_patterns_enabled=False,
        storage_path="state",
    )

    assert set(PolicyConfig.model_fields) == expected
    assert set(config.read_all_keys()) == expected
    assert PolicyConfig().enabled is False
    assert AdmissionEngine.from_config(PolicyConfig()).evaluate(candidate()).rule_id == "policy_disabled"

    package = Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "memory" / "policy"
    expected_reader = {
        "enabled": "engine.py",
        "policy_path": "loader.py",
        "strict": "loader.py",
        "min_score_to_admit": "engine.py",
        "weights_override": "engine.py",
        "secret_patterns_enabled": "engine.py",
        "storage_path": "engine.py",
    }
    for key, filename in expected_reader.items():
        assert key in (package / filename).read_text(encoding="utf-8"), key
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").endswith("memory_config")
            if isinstance(node, ast.Import):
                assert all(not alias.name.endswith("memory_config") for alias in node.names)


def test_partial_config_weight_override_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PolicyConfig(weights_override={"importance": 1.0})


def test_provenance_writes_one_append_only_row_per_evaluated_candidate(tmp_path: Path) -> None:
    config = PolicyConfig(enabled=True, storage_path=str(tmp_path / "state"))
    engine = AdmissionEngine(config=config)
    admitted = candidate(
        candidate_id="candidate-a",
        user_id="user-a",
        content="The project chose event sourcing",
        types=["project_decision"],
        provenance="trace-1",
    )
    rejected = candidate(
        candidate_id="candidate-b",
        user_id="user-a",
        content="A value classified as secret",
        is_secret_like=True,
    )

    first = engine.evaluate_and_record(admitted, now=0.0)
    second = engine.evaluate_and_record(rejected, now=0.0)
    entries = read_entries("1970-01-01", user_id="user-a", storage_path=str(tmp_path / "state"))

    assert first.provenance.ok is True
    assert second.provenance.ok is True
    assert first.provenance.path == second.provenance.path
    assert [entry["action"] for entry in entries] == ["durable_project", "reject"]
    assert [entry["rule_id"] for entry in entries] == ["project_decision", "secret_like"]
    assert entries[0]["candidate_id"] == "candidate-a"
    assert len(entries[0]["candidate_hash"]) == 64
    assert entries[0]["score"] == 0.0
    assert set(entries[0]["score_breakdown"]) == set(SCORE_SIGNALS)
    raw_log = first.provenance.path.read_text(encoding="utf-8") if first.provenance.path else ""
    assert admitted.content not in raw_log
    assert rejected.content not in raw_log


def test_provenance_failure_is_disclosed_not_reported_as_success(tmp_path: Path) -> None:
    not_a_directory = tmp_path / "state-file"
    not_a_directory.write_text("occupied", encoding="utf-8")
    config = PolicyConfig(enabled=True, storage_path=str(not_a_directory))
    engine = AdmissionEngine(config=config)

    audit = engine.evaluate_and_record(candidate(user_id="user-a"))

    assert audit.decision.action == "reject"
    assert audit.provenance.ok is False
    assert audit.provenance.path is None
    assert audit.provenance.error


# ---------------------------------------------------------------------------
# Strict YAML validation and hot reload
# ---------------------------------------------------------------------------


def test_in_package_default_policy_is_valid_and_runtime_override_path_is_deterministic(tmp_path: Path) -> None:
    policy = parse_policy_document(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    runtime_file = tmp_path / "memory-admission.yaml"
    runtime_file.write_text(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    assert tuple(rule.rule_id for rule in policy.rules) == RULE_IDS
    assert resolve_policy_path(storage_path=tmp_path) == runtime_file.resolve()
    assert resolve_policy_path() == DEFAULT_POLICY_PATH.resolve()


def test_hot_reload_applies_changed_thresholds_and_tracks_mtime_and_hash(tmp_path: Path) -> None:
    policy_file = tmp_path / "policy.yaml"
    initial_text = DEFAULT_POLICY_PATH.read_text(encoding="utf-8")
    policy_file.write_text(initial_text, encoding="utf-8")
    loader = PolicyLoader(policy_file)
    engine = AdmissionEngine(loader.current_policy)
    item = candidate(
        content="The user prefers concise answers",
        types=["user_preference"],
        confidence=0.8,
    )
    first_mtime = loader.loaded_mtime_ns
    first_hash = loader.loaded_sha256

    assert engine.evaluate(item).action == "durable_profile"
    unchanged = loader.reload_if_changed()
    assert unchanged.status == "unchanged"
    assert unchanged.changed is False

    changed_text = initial_text.replace("min_confidence: 0.70", "min_confidence: 0.90")
    policy_file.write_text(changed_text, encoding="utf-8")
    loaded = loader.reload_if_changed()
    engine.swap_policy(loaded.policy)
    decision = engine.evaluate(item)

    assert loaded.status == "loaded"
    assert loaded.changed is True
    assert loaded.observed_mtime_ns != first_mtime
    assert loaded.observed_sha256 != first_hash
    assert loader.loaded_sha256 == loaded.observed_sha256
    assert decision.admit is False
    assert "min_confidence=0.9" in decision.reason


@pytest.mark.parametrize(
    "bad_text",
    [
        "policy: [\n",
        "policy:\n  secret_like:\n    action: reject\n  secret_like:\n    action: reject\n",
        DEFAULT_POLICY_PATH.read_text(encoding="utf-8").replace(
            "  secret_like:\n", "  mystery_rule:\n    action: reject\n  secret_like:\n", 1
        ),
        DEFAULT_POLICY_PATH.read_text(encoding="utf-8").replace(
            "    action: reject\n", "    action: episodic_archive\n", 1
        ),
    ],
)
def test_invalid_reload_is_loud_and_keeps_last_known_good_policy(
    tmp_path: Path,
    bad_text: str,
) -> None:
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    loader = PolicyLoader(policy_file)
    engine = AdmissionEngine(loader.current_policy)
    good_policy = loader.current_policy
    good_hash = loader.loaded_sha256
    item = candidate(
        content="The user prefers concise answers",
        types=["user_preference"],
        confidence=0.8,
    )
    assert engine.evaluate(item).action == "durable_profile"

    policy_file.write_text(bad_text, encoding="utf-8")
    result = loader.reload_if_changed()

    assert result.status == "failed"
    assert result.ok is False
    assert result.reason == "policy_validation_failed"
    assert result.error
    assert result.policy is good_policy
    assert loader.current_policy is good_policy
    assert loader.loaded_sha256 == good_hash
    # The caller need not swap on failure; the engine remains on its last good
    # policy and never receives a permissive fallback.
    assert engine.evaluate(item).action == "durable_profile"


def test_initial_invalid_policy_fails_closed_with_a_reason(tmp_path: Path) -> None:
    policy_file = tmp_path / "broken.yaml"
    policy_file.write_text("policy: [\n", encoding="utf-8")
    loader = PolicyLoader(policy_file)
    decision = AdmissionEngine(loader.current_policy).evaluate(candidate())

    assert loader.last_result.status == "failed"
    assert "invalid YAML" in loader.current_policy.fail_closed_reason
    assert decision.admit is False
    assert decision.action == "reject"
    assert decision.rule_id == "policy_load_failure"
    assert "failing closed" in decision.reason
    assert dict(decision.score_breakdown)


def test_strict_false_allows_only_documented_metadata_never_unknown_rules() -> None:
    text = DEFAULT_POLICY_PATH.read_text(encoding="utf-8") + "\nmetadata:\n  owner: memory-team\n"
    assert parse_policy_document(text, strict=False).rules

    unknown_top = text + "future_option: true\n"
    with pytest.raises(PolicyValidationError, match="unknown top-level"):
        parse_policy_document(unknown_top, strict=False)

    unknown_rule = text.replace("  secret_like:\n", "  future_rule:\n    action: reject\n  secret_like:\n", 1)
    with pytest.raises(PolicyValidationError, match="unknown rules"):
        parse_policy_document(unknown_rule, strict=False)


def test_from_config_reads_injected_policy_path_and_strict_flag(tmp_path: Path) -> None:
    policy_file = tmp_path / "custom-policy.yaml"
    policy_file.write_text(
        DEFAULT_POLICY_PATH.read_text(encoding="utf-8").replace(
            "min_score_to_admit: 0.60", "min_score_to_admit: 0.25"
        ),
        encoding="utf-8",
    )
    config = PolicyConfig(
        enabled=True,
        policy_path=str(policy_file),
        strict=True,
        storage_path=str(tmp_path / "state"),
    )
    engine = AdmissionEngine.from_config(config)

    assert engine.policy.thresholds.min_score_to_admit == 0.25
    assert engine.enabled is True
    assert engine.policy.rules[3].min_confidence == 0.70


# ---------------------------------------------------------------------------
# Lazy package-root contract
# ---------------------------------------------------------------------------


def test_package_root_is_lazy_and_uses_explicit_type_checking_reexports() -> None:
    fresh_check = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import alpha.memory.policy as p; "
                "assert 'AdmissionEngine' not in p.__dict__; "
                "assert callable(p.__getattr__); "
                "assert p.AdmissionEngine.__name__ == 'AdmissionEngine'; "
                "print('lazy-root-ok')"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert fresh_check.stdout.strip() == "lazy-root-ok"
    assert "AdmissionEngine" in policy_root.__dict__ or callable(policy_root.__getattr__)

    resolved = policy_root.AdmissionEngine
    assert resolved is AdmissionEngine
    assert "AdmissionEngine" in policy_root.__dict__
    assert "AdmissionEngine" in dir(policy_root)
    assert "AdmissionEngine" in policy_root.__all__

    source = (Path(policy_root.__file__)).read_text(encoding="utf-8")
    source_lower = source.lower()
    assert "from alpha.memory._lazy_exports import install_lazy_exports" in source
    assert "from .engine import AdmissionEngine as AdmissionEngine" in source
    assert "sections 10, 21, 25, and 31" in source_lower
    assert "section 10" in source_lower
    assert "section 21" in source_lower
    assert "section 25" in source_lower
    assert "section 31" in source_lower
