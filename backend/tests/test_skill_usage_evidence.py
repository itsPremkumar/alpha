"""E1 tests: skill-usage evidence bridge arithmetic, honesty, and scoping.

Pins: known-row summaries computed only from real tracker fields (uses,
last_used_at age to 2 decimals, created_by); unknown rows -> explicit
"honesty:unknown" record, never zeros-as-fact; stored summary asserted via
sanitize_text (angle brackets stripped); EvidenceStore owner scoping;
build_promotion_readiness refusals (structural-only, failed checks, missing
or erroring evaluation) using the engine's own constants.
"""

import json

import pytest

from alpha.evidence.models import sanitize_text
from alpha.evidence.skill_usage_evidence import build_promotion_readiness, build_usage_evidence
from alpha.evidence.store import EvidenceStore
from alpha.skills.evolution_engine import (
    VALIDATION_STRUCTURAL_AND_SUITE,
    VALIDATION_STRUCTURAL_ONLY,
    SkillEvolutionProposal,
)
from alpha.skills.usage import SkillUsageTracker

NOW = 1_700_000_000.0


@pytest.fixture(autouse=True)
def _workspace_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    return tmp_path


def _tracker(tmp_path, rows) -> SkillUsageTracker:
    path = tmp_path / "usage.json"
    path.write_text(json.dumps({"version": 1, "usage": rows}), encoding="utf-8")
    return SkillUsageTracker(usage_file=path)


def _row(name: str, *, uses: int, last_used_at, created_by: str = "agent") -> dict:
    return {
        "name": name,
        "uses": uses,
        "last_used_at": last_used_at,
        "created_by": created_by,
        "first_seen_at": NOW - 10 * 86400.0,
    }


def test_known_row_arithmetic_and_stored_summary_survives_sanitize(tmp_path):
    tracker = _tracker(tmp_path, [_row("pdf-tools", uses=5, last_used_at=NOW - 2 * 86400.0)])
    payload = build_usage_evidence(tracker, "pdf-tools", owner_id="owner-a", now=NOW)
    assert payload["kind"] == "observation"
    assert payload["ref"] == "skill-usage://pdf-tools"
    assert payload["owner_id"] == "owner-a"
    assert payload["tags"] == ()
    expected = "skill 'pdf-tools' usage: 5 recorded use(s); last used 2.00 days ago; created_by agent"
    assert payload["summary"] == expected
    store = EvidenceStore(tmp_path / "evidence")
    record = store.add_evidence(**payload)
    # assert the STORED value: sanitizer leaves this summary byte-identical
    stored = store.get_evidence(record.id, owner_id="owner-a")
    assert stored.summary == expected
    assert stored.summary == sanitize_text(payload["summary"])
    assert stored.summary == payload["summary"]
    assert stored.kind == "observation"
    assert stored.ref == "skill-usage://pdf-tools"
    assert stored.owner_id == "owner-a"
    assert stored.tags == ()


def test_existing_row_with_zero_uses_reports_the_real_zero(tmp_path):
    tracker = _tracker(tmp_path, [_row("fresh-skill", uses=0, last_used_at=None, created_by="human")])
    payload = build_usage_evidence(tracker, "fresh-skill", owner_id="owner-a", now=NOW)
    assert "0 recorded use(s)" in payload["summary"]
    assert "last used: never recorded" in payload["summary"]
    assert "created_by human" in payload["summary"]


def test_unknown_row_is_honest_unknown_never_zeros(tmp_path):
    tracker = _tracker(tmp_path, [])
    payload = build_usage_evidence(tracker, "ghost-skill", owner_id="owner-a", now=NOW)
    assert payload["summary"] == "usage unknown: no telemetry row for ghost-skill"
    assert payload["tags"] == ("honesty:unknown",)
    assert "0 recorded" not in payload["summary"]  # never zeros-as-fact
    assert payload["kind"] == "observation"
    assert payload["ref"] == "skill-usage://ghost-skill"
    store = EvidenceStore(tmp_path / "evidence")
    stored = store.add_evidence(**payload)
    assert stored.summary == sanitize_text(payload["summary"])
    assert stored.summary == payload["summary"]
    assert stored.tags == ("honesty:unknown",)


def test_stored_summary_is_the_sanitized_value_when_name_carries_brackets(tmp_path):
    tracker = _tracker(tmp_path, [_row("weird<skill>", uses=2, last_used_at=NOW - 86400.0, created_by="human")])
    payload = build_usage_evidence(tracker, "weird<skill>", owner_id="owner-a", now=NOW)
    assert "<skill>" in payload["summary"]  # computed from the real name, verbatim
    store = EvidenceStore(tmp_path / "evidence")
    stored = store.add_evidence(**payload)
    assert stored.summary == sanitize_text(payload["summary"])  # assert the STORED value
    assert stored.summary != payload["summary"]  # sanitize strips angle brackets
    assert "<" not in stored.summary
    assert ">" not in stored.summary
    assert "'weird[skill]'" in stored.summary


def test_owner_scoping_through_store_apis(tmp_path):
    tracker = _tracker(tmp_path, [_row("pdf-tools", uses=5, last_used_at=NOW - 86400.0)])
    payload = build_usage_evidence(tracker, "pdf-tools", owner_id="owner-a", now=NOW)
    store = EvidenceStore(tmp_path / "evidence")
    record = store.add_evidence(**payload)
    assert store.get_evidence(record.id, owner_id="owner-a").summary == payload["summary"]
    with pytest.raises(KeyError):
        store.get_evidence(record.id, owner_id="owner-b")
    assert [e.id for e in store.list_evidence("owner-a")] == [record.id]
    assert store.list_evidence("owner-b") == []


def _evaluation(kind, passed, total, error=None) -> dict:
    return {
        "ran_at": "2026-01-01T00:00:00+00:00",
        "error": error,
        "validation_kind": kind,
        "checks_total": total,
        "checks_passed": passed,
        "score": None,
        "checks": [],
        "findings": [],
        "suite": None,
    }


def _proposal(evaluation) -> SkillEvolutionProposal:
    return SkillEvolutionProposal(
        id="a" * 32,
        skill_name="demo-skill",
        candidate_md="# candidate",
        candidate_version="1.0.1",
        status="validated",
        evaluation=evaluation,
    )


def test_readiness_refuses_structural_only_even_when_all_checks_pass():
    result = build_promotion_readiness(_proposal(_evaluation(VALIDATION_STRUCTURAL_ONLY, 9, 9)))
    assert result["ready"] is False
    assert "structural-only" in result["reason"]
    assert "9/9" in result["reason"]
    assert "NOT verified" in result["reason"]
    assert result["checks_passed"] == 9
    assert result["checks_total"] == 9
    assert result["validation_kind"] == VALIDATION_STRUCTURAL_ONLY


def test_readiness_ready_only_with_suite_and_all_checks_passing():
    result = build_promotion_readiness(_proposal(_evaluation(VALIDATION_STRUCTURAL_AND_SUITE, 11, 11)))
    assert result["ready"] is True
    assert "11/11" in result["reason"]
    assert "runtime behavior" in result["reason"]  # honest scope statement
    assert result["validation_kind"] == VALIDATION_STRUCTURAL_AND_SUITE


def test_readiness_refuses_failed_checks():
    result = build_promotion_readiness(_proposal(_evaluation(VALIDATION_STRUCTURAL_AND_SUITE, 10, 11)))
    assert result["ready"] is False
    assert "10/11" in result["reason"]
    assert result["checks_passed"] == 10
    assert result["checks_total"] == 11


def test_readiness_refuses_unknown_validation_kind():
    result = build_promotion_readiness(_proposal(_evaluation("mystery-kind", 5, 5)))
    assert result["ready"] is False
    assert "mystery-kind" in result["reason"]


def test_readiness_no_evaluation_and_error_paths_are_honest():
    no_eval = build_promotion_readiness(_proposal(None))
    assert no_eval["ready"] is False
    assert "no evaluation evidence recorded" in no_eval["reason"]
    assert no_eval["checks_passed"] is None
    errored = build_promotion_readiness(_proposal(_evaluation(None, 0, 0, error="boom: yaml exploded")))
    assert errored["ready"] is False
    assert "boom: yaml exploded" in errored["reason"]
    # accepts the record's to_dict() mapping too -> same verdict
    proposal = _proposal(_evaluation(VALIDATION_STRUCTURAL_ONLY, 3, 3))
    as_dict = build_promotion_readiness(proposal.to_dict())
    assert as_dict["ready"] is False
    assert "structural-only" in as_dict["reason"]
    assert build_promotion_readiness(None)["ready"] is False
