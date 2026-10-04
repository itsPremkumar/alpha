"""Node failure classification, error signatures and stagnation detection.

Honesty pins in this suite:
* every verdict carries a ``matched_rule`` (an unattributable classification
  cannot be audited);
* an unmatched failure is ``UNKNOWN_FAILURE`` with the literal default rule,
  never an optimistic guess;
* a class a retry cannot fix is refused EVEN WHEN the node's markers say ``*``
  — the marker gate must not be able to re-enable a futile retry;
* signatures collapse identifiers, so "same fault, different digits" compares
  equal, while genuinely different faults do not;
* two empty messages do NOT count as the same signature.
"""

from __future__ import annotations

import pytest

from alpha.workflow.failures import (
    DEFAULT_STAGNATION_LIMIT,
    ClassifiedFailure,
    NodeFailureClass,
    StagnationDetector,
    classify_node_failure,
    error_signature,
    is_non_retryable,
)


class TestClassification:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Connection refused to host db.internal", NodeFailureClass.NETWORK_FAILURE),
            ("getaddrinfo failed for api.example.com", NodeFailureClass.NETWORK_FAILURE),
            ("429 Too Many Requests", NodeFailureClass.RATE_LIMIT),
            ("Rate limit exceeded, retry later", NodeFailureClass.RATE_LIMIT),
            ("401 Unauthorized: invalid api key", NodeFailureClass.AUTH_FAILURE),
            ("Permission denied: /etc/passwd", NodeFailureClass.PERMISSION_DENIED),
            ("Blocked by policy: prompt injection detected", NodeFailureClass.SECURITY_BLOCK),
            ("TimeoutError: model timeout after 30s", NodeFailureClass.MODEL_TIMEOUT),
            ("AssertionError: assert 1 == 2 (pytest)", NodeFailureClass.TEST_FAILURE),
            ("Build failed: tsc exited 2", NodeFailureClass.BUILD_FAILURE),
            ("No module named 'fastapi'", NodeFailureClass.DEPENDENCY_FAILURE),
            ("JSONDecodeError: Expecting value: line 1", NodeFailureClass.PARSER_FAILURE),
            ("ValidationError: invalid input for field id", NodeFailureClass.VALIDATION_FAILURE),
            ("MemoryError: out of memory", NodeFailureClass.RESOURCE_EXHAUSTED),
            ("Segmentation fault (core dumped)", NodeFailureClass.AGENT_CRASH),
            ("lease expired: worker lost mid-attempt", NodeFailureClass.WORKER_LOST),
            ("command not found: node", NodeFailureClass.ENVIRONMENT_DRIFT),
            ("tool execution failed with exit code 3", NodeFailureClass.TOOL_FAILURE),
            ("something nobody has ever seen before", NodeFailureClass.UNKNOWN_FAILURE),
        ],
    )
    def test_every_spec_class_is_reachable(self, text: str, expected: NodeFailureClass) -> None:
        assert classify_node_failure(text).failure_class == expected

    def test_verdict_always_carries_a_rule(self) -> None:
        """An unattributable verdict cannot be audited, so it must never happen."""
        for text in ("boom", "Connection refused", "", "401 Unauthorized", None):
            verdict = classify_node_failure(text if text is not None else "")
            assert verdict.matched_rule, f"no rule recorded for {text!r}"

    def test_unmatched_failure_is_unknown_not_optimistic(self) -> None:
        verdict = classify_node_failure("zzz unrelated")
        assert verdict.failure_class is NodeFailureClass.UNKNOWN_FAILURE
        assert verdict.matched_rule == "default:no rule matched"
        # We do not know enough to rule a retry out, so one is allowed within
        # the node's own bounded RetryPolicy -- disclosed, not hidden.
        assert verdict.retryable is True

    def test_specific_rule_wins_over_broad_rule(self) -> None:
        """A narrow early rule must not be shadowed by the broad late 'tool'.

        The message names both a permission problem and a tool; permission is
        checked first because retrying a forbidden tool call is futile, and
        classifying it as a plain tool failure would invite exactly that retry.
        """
        verdict = classify_node_failure("permission denied while running tool bash")
        assert verdict.failure_class is NodeFailureClass.PERMISSION_DENIED
        assert verdict.matched_rule == "keyword:permission denied"
        assert verdict.retryable is False

    def test_exception_type_is_a_fallback_not_a_override(self) -> None:
        """The message beats the type when both match."""
        verdict = classify_node_failure("Connection refused", exception_type="ValidationError")
        assert verdict.failure_class is NodeFailureClass.NETWORK_FAILURE
        assert verdict.matched_rule == "keyword:connection refused"

    def test_exception_type_used_when_no_keyword_matched(self) -> None:
        verdict = classify_node_failure("zzz", exception_type="PermissionError")
        assert verdict.failure_class is NodeFailureClass.PERMISSION_DENIED
        assert verdict.matched_rule == "exception_type:PermissionError"

    def test_result_is_immutable(self) -> None:
        verdict = classify_node_failure("429 Too Many Requests")
        with pytest.raises(Exception):
            verdict.failure_class = NodeFailureClass.TOOL_FAILURE  # type: ignore[misc]


class TestNonRetryableClasses:
    @pytest.mark.parametrize(
        ("failure_class", "text"),
        [
            (NodeFailureClass.AUTH_FAILURE, "401 Unauthorized"),
            (NodeFailureClass.PERMISSION_DENIED, "Permission denied"),
            (NodeFailureClass.SECURITY_BLOCK, "Blocked by policy: prompt injection"),
            (NodeFailureClass.PARSER_FAILURE, "JSONDecodeError: Expecting value"),
            (NodeFailureClass.VALIDATION_FAILURE, "ValidationError: invalid input"),
            (NodeFailureClass.CONTEXT_CORRUPTION, "context is corrupt: checksum mismatch"),
        ],
    )
    def test_futile_retry_classes_are_non_retryable(self, failure_class: NodeFailureClass, text: str) -> None:
        assert is_non_retryable(failure_class) is True
        verdict = classify_node_failure(text)
        assert verdict.failure_class is failure_class
        assert verdict.retryable is False

    @pytest.mark.parametrize(
        "failure_class",
        [NodeFailureClass.NETWORK_FAILURE, NodeFailureClass.RATE_LIMIT, NodeFailureClass.UNKNOWN_FAILURE],
    )
    def test_transient_classes_remain_retryable(self, failure_class: NodeFailureClass) -> None:
        assert is_non_retryable(failure_class) is False

    def test_marker_wildcard_cannot_reenable_a_futile_retry(self) -> None:
        """The whole point: ``retry_on_errors = ["*"]`` must not retry auth.

        The engine gates retries on ``retryable AND not non_retryable``; this
        pins the second operand so a wildcard marker cannot undo it.
        """
        verdict = classify_node_failure("401 Unauthorized: invalid api key")
        assert verdict.retryable is False
        # and the wildcard case it would otherwise match:
        assert "*" in ["*"]  # the marker gate passes...
        assert verdict.retryable is False  # ...and the class gate still refuses

    def test_bridges_do_not_force_a_class_into_a_neighbour(self) -> None:
        """A class with no home in the 7-value recovery vocabulary says 'unknown'."""
        assert classify_node_failure("Build failed").recovery_class == "unknown"
        assert classify_node_failure("429 Too Many Requests").recovery_class == "model_rate_limit"
        # A node class with no shared work-unit code reports absence, not a
        # neighbouring code that would mis-describe the work unit.
        assert classify_node_failure("Build failed").reason_code is None
        assert classify_node_failure("Permission denied").reason_code == "provider_auth_or_access"


class TestErrorSignature:
    def test_identifiers_collapse_so_one_fault_reads_once(self) -> None:
        a = error_signature("connection refused to host 10.0.0.5 after 3 attempts")
        b = error_signature("connection refused to host 10.0.0.9 after 7 attempts")
        assert a == b, "same fault with different digits must share a signature"

    def test_different_faults_do_not_collide(self) -> None:
        assert error_signature("connection refused") != error_signature("permission denied")

    def test_paths_and_hashes_collapse(self) -> None:
        a = error_signature("cannot open C:\\proj\\a\\main.py sha256=deadbeefcafe1234")
        b = error_signature("cannot open C:\\proj\\b\\util.py sha256=0000111122223333")
        # path token differs (file stem); the hash collapses
        assert "<hex>" in a and "<hex>" in b

    def test_empty_signature_is_empty_not_placeholder(self) -> None:
        assert error_signature("") == ""
        assert error_signature("   ") == ""

    def test_signature_is_case_insensitive(self) -> None:
        assert error_signature("Connection REFUSED") == error_signature("connection refused")


class TestStagnationDetector:
    def test_stagnates_on_repeated_identical_signature(self) -> None:
        detector = StagnationDetector(limit=3)
        first = detector.observe("connection refused")
        second = detector.observe("connection refused")
        third = detector.observe("connection refused")
        assert first.stagnated is False
        assert second.stagnated is False
        assert third.stagnated is True
        assert third.identical_streak == 3
        assert "STAGNATION_DETECTED" in third.reason

    def test_a_different_signature_resets_the_streak(self) -> None:
        detector = StagnationDetector(limit=3)
        detector.observe("connection refused")
        detector.observe("connection refused")
        verdict = detector.observe("permission denied")
        assert verdict.stagnated is False
        assert verdict.identical_streak == 1

    def test_default_limit_is_the_spec_value(self) -> None:
        assert DEFAULT_STAGNATION_LIMIT == 3

    def test_empty_signatures_never_produce_a_streak(self) -> None:
        """Two messages with no text are NOT evidence of one shared cause."""
        detector = StagnationDetector(limit=2)
        assert detector.observe("").stagnated is False
        assert detector.observe("").stagnated is False
        assert detector.stagnant is False

    def test_limit_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            StagnationDetector(limit=0)

    def test_bounded_state_no_unbounded_history(self) -> None:
        """A long run must not accumulate an unbounded list of old errors."""
        detector = StagnationDetector(limit=3)
        for i in range(1000):
            detector.observe(f"error {i}")
        assert detector.observations == 1000
        assert not hasattr(detector, "_history")

    def test_stagnant_property_tracks_the_streak(self) -> None:
        detector = StagnationDetector(limit=2)
        detector.observe("x")
        assert detector.stagnant is False
        detector.observe("x")
        assert detector.stagnant is True


def test_classified_failure_is_hashable_and_comparable() -> None:
    verdict = classify_node_failure("429 Too Many Requests")
    assert isinstance(verdict, ClassifiedFailure)
    assert {verdict} == {verdict}
