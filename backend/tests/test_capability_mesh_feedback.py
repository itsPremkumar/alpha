"""Tests for specialist-generated checks and the user feedback loop (plan §20/§21).

The load-bearing rules pinned here:

* a generated check is a PROPOSAL — a non-deterministic check can never be
  accepted (plan §20: all generated tests are untrusted until reviewed and
  executed), and :class:`CheckProvenance` refuses to be hand-built into saying
  otherwise;
* user feedback carries a rating, an optional correction and an opaque
  ``task_fingerprint`` — never the prompt text, and private task content is
  never stored by default;
* ratings are bounded to 1..5 and a future-dated ``recorded_at`` is refused;
* fewer than :data:`FEEDBACK_MIN_SAMPLES` ratings yield weight ``0.0`` with the
  reason "insufficient feedback" — a handful of ratings never reorders routing
  (plan §21: each item is a categorised observation, not universal truth);
* the ledger is durable, atomic JSON and a corrupt document raises rather than
  silently starting empty and later writing amnesia over the file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from alpha.capability_mesh.feedback import (
    FEEDBACK_FILE_NAME,
    FEEDBACK_MIN_SAMPLES,
    MAX_CORRECTION_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_FINGERPRINT_CHARS,
    CheckKind,
    CheckProvenance,
    FeedbackLedger,
    FeedbackStoreError,
    GeneratedCheck,
    PreferenceUpdate,
    UserFeedback,
    review_check,
)


def _check(**kwargs) -> GeneratedCheck:
    kwargs.setdefault("check_id", "chk_1")
    kwargs.setdefault("description", "the parser rejects an unterminated string")
    kwargs.setdefault("kind", CheckKind.UNIT)
    kwargs.setdefault("target", "tests/test_parser.py")
    kwargs.setdefault("source_agent", "agent:test-specialist")
    kwargs.setdefault("deterministic", True)
    return GeneratedCheck(**kwargs)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _feedback(**kwargs) -> UserFeedback:
    kwargs.setdefault("feedback_id", "fb_1")
    kwargs.setdefault("rating", 4)
    kwargs.setdefault("task_fingerprint", "fp_0123456789abcdef")
    kwargs.setdefault("recorded_at", _now_iso())
    return UserFeedback(**kwargs)


# ------------------------------------------------------------- generated check


def test_generated_check_refusals():
    with pytest.raises(ValueError, match="check_id must be non-empty"):
        _check(check_id="")
    with pytest.raises(ValueError, match="target must be non-empty"):
        _check(target="")
    with pytest.raises(ValueError, match="source_agent must be non-empty"):
        _check(source_agent="")
    with pytest.raises(ValueError, match="description must be at most"):
        _check(description="x" * (MAX_DESCRIPTION_CHARS + 1))
    with pytest.raises(ValueError, match="deterministic must be a bool"):
        _check(deterministic="yes")  # type: ignore[arg-type]
    with pytest.raises(ValueError) as excinfo:
        _check(kind="snapshot")
    assert "unknown kind" in str(excinfo.value)
    assert "unit" in str(excinfo.value)


def test_generated_check_kind_is_coerced_and_round_trips():
    check = _check(kind="property")
    assert check.kind is CheckKind.PROPERTY
    assert {kind.value for kind in CheckKind} == {"unit", "mutation", "property", "source_backed"}
    assert GeneratedCheck.from_dict(check.to_dict()) == check


def test_review_check_accepts_a_deterministic_check_but_still_calls_it_a_proposal():
    provenance = review_check(_check(deterministic=True))
    assert provenance.accepted is True
    assert provenance.check.deterministic is True
    assert "untrusted" in provenance.reason or "proposal" in provenance.reason


def test_review_check_never_accepts_a_non_deterministic_check():
    provenance = review_check(_check(deterministic=False, kind=CheckKind.MUTATION))
    assert provenance.accepted is False
    assert "non-deterministic" in provenance.reason


def test_check_provenance_refuses_to_be_hand_built_into_accepting_a_flaky_check():
    flaky = _check(deterministic=False)
    with pytest.raises(ValueError, match="cannot accept"):
        CheckProvenance(check=flaky, accepted=True, reason="trust me")
    with pytest.raises(ValueError, match="reason must be non-empty"):
        CheckProvenance(check=_check(), accepted=True, reason="")
    with pytest.raises(ValueError, match="check must be a GeneratedCheck"):
        CheckProvenance(check="nope", accepted=False, reason="quarantined")  # type: ignore[arg-type]


def test_check_provenance_round_trips():
    provenance = review_check(_check())
    assert CheckProvenance.from_dict(provenance.to_dict()) == provenance


# -------------------------------------------------------------- user feedback


def test_user_feedback_refuses_ratings_outside_one_to_five():
    for bad in (0, 6, -1):
        with pytest.raises(ValueError, match="rating must be an int in 1..5"):
            _feedback(rating=bad)
    with pytest.raises(ValueError, match="rating must be an int in 1..5"):
        _feedback(rating=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="rating must be an int in 1..5"):
        _feedback(rating="5")  # type: ignore[arg-type]


def test_user_feedback_requires_an_opaque_task_fingerprint():
    with pytest.raises(ValueError, match="task_fingerprint must be non-empty"):
        _feedback(task_fingerprint="")
    with pytest.raises(ValueError, match=f"at most {MAX_FINGERPRINT_CHARS}"):
        _feedback(task_fingerprint="fp_" + "0" * (MAX_FINGERPRINT_CHARS + 1))


def test_user_feedback_bounds_the_correction_text():
    with pytest.raises(ValueError, match=f"correction_text must be at most {MAX_CORRECTION_CHARS}"):
        _feedback(correction_text="x" * (MAX_CORRECTION_CHARS + 1))
    assert _feedback(correction_text="this source is outdated").correction_text == "this source is outdated"


def test_user_feedback_refuses_a_future_dated_record():
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="recorded_at must not be in the future"):
        _feedback(recorded_at=future)
    with pytest.raises(ValueError, match="recorded_at must be an ISO-8601 timestamp"):
        _feedback(recorded_at="last tuesday")


def test_user_feedback_round_trips_and_stores_no_prompt_text():
    feedback = _feedback(correction_text="too complicated", preferred_capability="explanation")
    document = feedback.to_dict()
    assert UserFeedback.from_dict(document) == feedback
    assert set(document) == {"feedback_id", "rating", "correction_text", "preferred_capability", "task_fingerprint", "recorded_at"}
    # The only task-linked field is the opaque fingerprint.
    assert "prompt" not in json.dumps(document).lower()
    assert feedback.task_fingerprint == "fp_0123456789abcdef"


# ------------------------------------------------------------- preference update


def test_preference_update_refusals():
    with pytest.raises(ValueError, match="capability must be non-empty"):
        PreferenceUpdate(capability="", weight=0.5, sample_count=5, reason="because")
    with pytest.raises(ValueError, match="weight must be a finite number in \\[0, 1\\]"):
        PreferenceUpdate(capability="code", weight=1.2, sample_count=5, reason="because")
    with pytest.raises(ValueError, match="sample_count must be an int >= 0"):
        PreferenceUpdate(capability="code", weight=0.5, sample_count=-1, reason="because")
    with pytest.raises(ValueError, match="reason must be non-empty"):
        PreferenceUpdate(capability="code", weight=0.5, sample_count=5, reason="")


# ----------------------------------------------------------------- the ledger


def test_ledger_records_and_reads_back(tmp_path):
    ledger = FeedbackLedger(tmp_path)
    ledger.record(_feedback(feedback_id="fb_1", rating=5, preferred_capability="code"))
    ledger.record(_feedback(feedback_id="fb_2", rating=3, preferred_capability="code"))
    ledger.record(_feedback(feedback_id="fb_3", rating=4, preferred_capability="research"))
    assert len(ledger.for_capability("code")) == 2
    assert ledger.ratings("code") == (5, 3)
    assert ledger.ratings("research") == (4,)
    assert ledger.for_capability("nobody") == ()
    assert (tmp_path / FEEDBACK_FILE_NAME).exists()


def test_ledger_refuses_a_duplicate_feedback_id(tmp_path):
    ledger = FeedbackLedger(tmp_path)
    ledger.record(_feedback(feedback_id="fb_1"))
    with pytest.raises(ValueError, match="duplicate feedback_id 'fb_1'"):
        ledger.record(_feedback(feedback_id="fb_1"))


def test_ledger_refuses_a_non_feedback_row(tmp_path):
    with pytest.raises(ValueError, match="record\\(\\) expects a UserFeedback"):
        FeedbackLedger(tmp_path).record({"feedback_id": "fb_1"})  # type: ignore[arg-type]


def test_ledger_round_trips_across_instances(tmp_path):
    first = FeedbackLedger(tmp_path)
    first.record(_feedback(feedback_id="fb_1", rating=2, preferred_capability="code", correction_text="this does not run"))
    second = FeedbackLedger(tmp_path)
    restored = second.for_capability("code")
    assert [item.feedback_id for item in restored] == ["fb_1"]
    assert restored[0].correction_text == "this does not run"
    assert second.ratings("code") == (2,)
    assert len(second.all()) == 1


def test_ledger_refuses_a_corrupt_document_instead_of_starting_empty(tmp_path):
    (tmp_path / FEEDBACK_FILE_NAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(FeedbackStoreError, match="not valid JSON"):
        FeedbackLedger(tmp_path)


def test_ledger_refuses_a_document_with_a_bad_row(tmp_path):
    (tmp_path / FEEDBACK_FILE_NAME).write_text(json.dumps({"document_version": 1, "feedback": [{"feedback_id": ""}]}), encoding="utf-8")
    with pytest.raises(FeedbackStoreError, match="feedback #0 is invalid"):
        FeedbackLedger(tmp_path)


def test_ledger_refuses_a_non_object_document(tmp_path):
    (tmp_path / FEEDBACK_FILE_NAME).write_text("[]", encoding="utf-8")
    with pytest.raises(FeedbackStoreError, match="must be a JSON object"):
        FeedbackLedger(tmp_path)


def test_preference_is_zero_below_the_minimum_samples(tmp_path):
    ledger = FeedbackLedger(tmp_path)
    for index in range(FEEDBACK_MIN_SAMPLES - 1):
        ledger.record(_feedback(feedback_id=f"fb_{index}", rating=5, preferred_capability="code"))
    update = ledger.to_capability_preference("code")
    assert update.weight == 0.0
    assert update.sample_count == FEEDBACK_MIN_SAMPLES - 1
    assert update.reason == "insufficient feedback"


def test_preference_is_measured_at_and_above_the_minimum_samples(tmp_path):
    ledger = FeedbackLedger(tmp_path)
    for index in range(FEEDBACK_MIN_SAMPLES):
        ledger.record(_feedback(feedback_id=f"fb_{index}", rating=5, preferred_capability="code"))
    update = ledger.to_capability_preference("code")
    assert update.sample_count == FEEDBACK_MIN_SAMPLES
    assert update.weight == pytest.approx(1.0)
    assert update.reason != "insufficient feedback"
    assert "FEEDBACK_MIN_SAMPLES" in update.reason or "measured" in update.reason


def test_neutral_ratings_land_on_a_neutral_weight(tmp_path):
    ledger = FeedbackLedger(tmp_path)
    for index in range(FEEDBACK_MIN_SAMPLES):
        ledger.record(_feedback(feedback_id=f"fb_{index}", rating=3, preferred_capability="code"))
    assert ledger.to_capability_preference("code").weight == pytest.approx(0.5)


def test_preference_for_an_unknown_capability_is_zero_and_says_why(tmp_path):
    ledger = FeedbackLedger(tmp_path)
    for index in range(FEEDBACK_MIN_SAMPLES):
        ledger.record(_feedback(feedback_id=f"fb_{index}", rating=5, preferred_capability="code"))
    update = ledger.to_capability_preference("translation")
    assert update.capability == "translation"
    assert update.weight == 0.0
    assert update.sample_count == 0
    assert update.reason == "insufficient feedback"


def test_ledger_requires_a_directory():
    with pytest.raises(ValueError, match="FeedbackLedger requires a directory"):
        FeedbackLedger(None)  # type: ignore[arg-type]


def test_ledger_writes_utf8_without_ascii_escaping(tmp_path):
    ledger = FeedbackLedger(tmp_path)
    ledger.record(_feedback(feedback_id="fb_1", rating=1, correction_text="die Quelle ist veraltet"))
    raw = (tmp_path / FEEDBACK_FILE_NAME).read_text(encoding="utf-8")
    assert "die Quelle ist veraltet" in raw
