from __future__ import annotations

import pytest
from pydantic import ValidationError

from alpha.runtime.runs.verification import verify_acceptance_criteria
from app.gateway.run_models import RunCreateRequest


def test_acceptance_criteria_is_validated_and_serializable() -> None:
    request = RunCreateRequest(
        acceptance_criteria=[
            {
                "id": "tests-pass",
                "description": "The targeted test suite passes.",
                "required_evidence_kinds": ["test_result"],
            }
        ]
    )

    assert request.acceptance_criteria[0].model_dump(mode="json") == {
        "id": "tests-pass",
        "description": "The targeted test suite passes.",
        "required_evidence_kinds": ["test_result"],
    }


def test_autonomous_mode_is_opt_in() -> None:
    assert RunCreateRequest().autonomous is False
    assert RunCreateRequest(autonomous=True).autonomous is True


def test_acceptance_criteria_rejects_duplicate_evidence_kinds() -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        RunCreateRequest(
            acceptance_criteria=[
                {"id": "proof", "description": "proof", "required_evidence_kinds": ["test_result", "test_result"]}
            ]
        )


def test_acceptance_criteria_rejects_duplicate_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate ids"):
        RunCreateRequest(
            acceptance_criteria=[
                {"id": "proof", "description": "first"},
                {"id": "proof", "description": "second"},
            ]
        )


def test_verification_requires_passing_evidence_for_every_required_kind() -> None:
    result = verify_acceptance_criteria(
        [{"id": "release", "required_evidence_kinds": ["test_result", "artifact_digest"]}],
        [
            {"criterion_id": "release", "kind": "test_result", "passed": True, "reference": "pytest://targeted"},
            {"criterion_id": "release", "kind": "artifact_digest", "passed": False, "reference": "sha256:bad"},
        ],
    )

    assert result.verified is False
    assert result.verdicts[0].reason == "Missing passing evidence: artifact_digest"


def test_verification_succeeds_with_independent_passing_evidence() -> None:
    result = verify_acceptance_criteria(
        [{"id": "release", "required_evidence_kinds": ["test_result", "artifact_digest"]}],
        [
            {"criterion_id": "release", "kind": "test_result", "passed": True, "reference": "pytest://targeted"},
            {"criterion_id": "release", "kind": "artifact_digest", "passed": True, "reference": "sha256:good"},
        ],
    )

    assert result.verified is True
    assert result.verdicts[0].evidence_references == ("pytest://targeted", "sha256:good")
