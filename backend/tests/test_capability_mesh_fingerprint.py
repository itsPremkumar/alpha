"""Tests for situation fingerprints (plan §25): an honest, deliberately crude baseline.

The keyword heuristic is a pinned TERM table matched with word boundaries —
deterministic, inspectable, never intelligent. UNKNOWN is the honest
complexity default (never a guess), risk/pressure are required statements,
and the fingerprint round-trips through JSON with its validation intact.
"""

from __future__ import annotations

import pytest

from alpha.capability_mesh.fingerprint import (
    LONG_TASK_PRESSURE_CHARS,
    TERMS,
    Band,
    Pressure,
    Risk,
    SituationFingerprint,
    fingerprint_task,
)


def _fp(**kwargs: object) -> SituationFingerprint:
    base: dict[str, object] = {"task_type": "general", "risk_band": Risk.LOW, "context_pressure": Pressure.LOW}
    base.update(kwargs)
    return SituationFingerprint(**base)  # type: ignore[arg-type]


class TestConstruction:
    def test_task_type_is_required_non_empty(self) -> None:
        with pytest.raises(ValueError, match="task_type"):
            SituationFingerprint(task_type="", risk_band=Risk.LOW, context_pressure=Pressure.LOW)

    def test_risk_band_and_context_pressure_are_required_not_defaulted(self) -> None:
        # The plan declares both required: defaulting risk to "low" would
        # guess precisely in the unsafe direction, so the constructor must
        # demand them.
        with pytest.raises(TypeError):
            SituationFingerprint(task_type="general")  # type: ignore[call-arg]

    def test_unknown_enum_values_are_refused_listing_allowed(self) -> None:
        with pytest.raises(ValueError, match="unknown complexity_band"):
            _fp(complexity_band="urgent")
        with pytest.raises(ValueError, match="unknown risk_band"):
            _fp(risk_band="fine")
        with pytest.raises(ValueError, match="unknown context_pressure"):
            _fp(context_pressure="enormous")

    def test_honest_defaults(self) -> None:
        fingerprint = _fp()
        assert fingerprint.complexity_band is Band.UNKNOWN  # never guessed
        assert fingerprint.recency_required is False
        assert fingerprint.subtask_type is None
        assert fingerprint.required_capabilities == ()
        assert fingerprint.required_tools == ()
        assert fingerprint.user_policy == "balanced"

    def test_recency_required_must_be_a_bool(self) -> None:
        with pytest.raises(ValueError, match="recency_required"):
            _fp(recency_required="yes")

    def test_empty_subtask_type_is_refused(self) -> None:
        with pytest.raises(ValueError, match="subtask_type"):
            _fp(subtask_type="")

    def test_user_policy_must_be_non_empty(self) -> None:
        with pytest.raises(ValueError, match="user_policy"):
            _fp(user_policy="")

    def test_empty_requirement_entries_are_refused(self) -> None:
        with pytest.raises(ValueError, match="required_capabilities"):
            _fp(required_capabilities=("coding", ""))
        with pytest.raises(ValueError, match="required_tools"):
            _fp(required_tools=("",))

    def test_requirements_are_coerced_to_tuples(self) -> None:
        fingerprint = _fp(required_capabilities=["a", "b"], required_tools=["bash"])  # type: ignore[arg-type]
        assert fingerprint.required_capabilities == ("a", "b")
        assert fingerprint.required_tools == ("bash",)

    def test_round_trip(self) -> None:
        fingerprint = _fp(subtask_type="patch", complexity_band=Band.HIGH, recency_required=True, risk_band=Risk.CRITICAL, context_pressure=Pressure.HIGH, required_capabilities=("code",), required_tools=("bash",), user_policy="code_safe")
        assert SituationFingerprint.from_dict(fingerprint.to_dict()) == fingerprint


class TestTermsTable:
    def test_term_groups_are_pinned(self) -> None:
        assert set(TERMS) == {"complexity_high", "complexity_low", "recency", "risk_critical", "risk_high", "pressure_high"}

    def test_pinned_words_stay_pinned(self) -> None:
        # These exact words decide routing behaviour; changing one is a
        # behaviour change and must fail this test first.
        assert "debug" in TERMS["complexity_high"]
        assert "root cause" in TERMS["complexity_high"]
        assert "define" in TERMS["complexity_low"]
        assert "latest" in TERMS["recency"]
        assert "recent" in TERMS["recency"]
        assert "2026" in TERMS["recency"]
        assert "deploy" in TERMS["risk_critical"]
        assert "production" in TERMS["risk_critical"]
        assert "irreversible" in TERMS["risk_critical"]
        assert "merge" in TERMS["risk_high"]
        assert "urgent" in TERMS["pressure_high"]


class TestFingerprintTask:
    def test_empty_text_is_refused(self) -> None:
        with pytest.raises(ValueError, match="non-empty text"):
            fingerprint_task("")

    def test_code_debug_vocabulary_is_high_complexity(self) -> None:
        fingerprint = fingerprint_task("Please debug this failing test and find the root cause")
        assert fingerprint.complexity_band is Band.HIGH
        assert fingerprint.risk_band is Risk.LOW  # no side-effect vocabulary present

    def test_summary_vocabulary_is_low_complexity(self) -> None:
        fingerprint = fingerprint_task("define the term polymorphism")
        assert fingerprint.complexity_band is Band.LOW

    def test_recency_vocabulary_sets_recency_required(self) -> None:
        fingerprint = fingerprint_task("what is the latest news about the 2026 election")
        assert fingerprint.recency_required is True

    def test_deploy_vocabulary_is_critical_risk(self) -> None:
        fingerprint = fingerprint_task("deploy the service to production immediately")
        assert fingerprint.risk_band is Risk.CRITICAL
        assert fingerprint.context_pressure is Pressure.HIGH

    def test_matched_nothing_is_unknown_not_easy(self) -> None:
        fingerprint = fingerprint_task("tell me about cats")
        assert fingerprint.complexity_band is Band.UNKNOWN  # honest: not a guess of "low"
        assert fingerprint.recency_required is False
        assert fingerprint.risk_band is Risk.LOW
        assert fingerprint.context_pressure is Pressure.LOW

    def test_word_boundaries_not_substring_matches(self) -> None:
        # "prod" must not fire on "produce"; substring matching would
        # silently raise the risk of ordinary prose.
        fingerprint = fingerprint_task("produce a report on farming")
        assert fingerprint.risk_band is Risk.LOW
        assert fingerprint.complexity_band is Band.UNKNOWN

    def test_long_text_is_context_pressing(self) -> None:
        long_text = "word " * (LONG_TASK_PRESSURE_CHARS // 4 + 1)  # >= LONG_TASK_PRESSURE_CHARS chars, no keyword hits
        fingerprint = fingerprint_task(long_text)
        assert fingerprint.context_pressure is Pressure.MEDIUM
        assert fingerprint.risk_band is Risk.LOW

    def test_declared_requirements_travel_and_deduplicate(self) -> None:
        fingerprint = fingerprint_task("whatever", required_capabilities=("code", "code", "review"), required_tools=("bash", "bash"))
        assert fingerprint.required_capabilities == ("code", "review")
        assert fingerprint.required_tools == ("bash",)

    def test_task_type_and_policy_are_carried(self) -> None:
        fingerprint = fingerprint_task("whatever", task_type="coding", subtask_type="patch", user_policy="code_safe")
        assert fingerprint.task_type == "coding"
        assert fingerprint.subtask_type == "patch"
        assert fingerprint.user_policy == "code_safe"

    def test_deterministic(self) -> None:
        text = "deploy the latest build to production, urgent"
        assert fingerprint_task(text) == fingerprint_task(text)

    def test_output_round_trips(self) -> None:
        fingerprint = fingerprint_task("debug and deploy", task_type="coding")
        assert SituationFingerprint.from_dict(fingerprint.to_dict()) == fingerprint
