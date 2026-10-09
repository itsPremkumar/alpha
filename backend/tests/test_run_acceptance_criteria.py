from __future__ import annotations

import pytest
from pydantic import ValidationError

from alpha.runtime.runs.verification import verify_acceptance_criteria
from app.gateway.routers.evolution import GateRequest
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
    # Router-level gate request: omission must never grant autonomy.
    assert GateRequest().autonomous_mode is False
    assert GateRequest(autonomous_mode=True).autonomous_mode is True


def test_acceptance_criteria_rejects_duplicate_evidence_kinds() -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        RunCreateRequest(acceptance_criteria=[{"id": "proof", "description": "proof", "required_evidence_kinds": ["test_result", "test_result"]}])


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


def test_no_declared_criteria_is_not_a_vacuous_verification() -> None:
    """Nothing declared means nothing verified — never a vacuous ``True``."""
    result = verify_acceptance_criteria(None, None)
    assert result.verified is False
    assert result.verdicts == ()

    empty = verify_acceptance_criteria([], [{"criterion_id": "release", "kind": "test_result", "passed": True}])
    assert empty.verified is False
    assert empty.verdicts == ()
    # The evidence itself is disclosed rather than absorbed by a silent pass.
    assert any("not declared" in note for note in empty.notes)


def test_evidence_for_an_undeclared_criterion_is_disclosed_not_absorbed() -> None:
    """A typo'd criterion id cannot masquerade as coverage, and cannot vanish."""
    result = verify_acceptance_criteria(
        [{"id": "release", "required_evidence_kinds": ["test_result"]}],
        [
            {"criterion_id": "relaese", "kind": "test_result", "passed": True, "reference": "pytest://typo"},
            {"source": "no criterion id"},
        ],
    )

    assert result.verified is False
    assert result.verdicts[0].reason == "Missing passing evidence: test_result"
    assert result.verdicts[0].evidence_references == ()
    notes = " ".join(result.notes)
    assert "relaese" in notes, "evidence for an undeclared criterion must be named"
    assert "names no criterion" in notes


def test_evidence_for_one_criterion_does_not_verify_another() -> None:
    result = verify_acceptance_criteria(
        [
            {"id": "a", "required_evidence_kinds": ["test_result"]},
            {"id": "b", "required_evidence_kinds": ["test_result"]},
        ],
        [{"criterion_id": "a", "kind": "test_result", "passed": True, "reference": "pytest://a"}],
    )

    assert [verdict.verified for verdict in result.verdicts] == [True, False]
    assert result.verified is False
    assert result.verdicts[1].reason == "Missing passing evidence: test_result"


def test_only_an_exact_true_counts_as_passing_evidence() -> None:
    """A truthy non-boolean (``"true"``, ``1``) is not a measurement."""
    result = verify_acceptance_criteria(
        [{"id": "release", "required_evidence_kinds": ["test_result"]}],
        [{"criterion_id": "release", "kind": "test_result", "passed": "true", "reference": "pytest://string"}],
    )

    assert result.verified is False
    assert result.verdicts[0].evidence_references == ()


def test_a_reference_is_cited_provenance_not_verified_provenance() -> None:
    """The overlay records where the caller says the proof lives; it cannot
    check that it does. A reference must never substitute for the measurement,
    and a passing item's reference is carried through verbatim."""
    result = verify_acceptance_criteria(
        [{"id": "release", "required_evidence_kinds": ["test_result"]}],
        [
            {
                "criterion_id": "release",
                "kind": "test_result",
                "passed": True,
                "reference": "operator says the suite passed",
            }
        ],
    )

    assert result.verified is True
    assert result.verdicts[0].evidence_references == ("operator says the suite passed",)


def test_model_written_prose_is_not_acceptance_evidence() -> None:
    """A worker's own output rows can never satisfy a criterion.

    The swarm workers emit evidence rows that carry provenance only — the exact
    shape below (``source``/``role``/``model``, tool-receipt and bash-execution
    counts) — plus one row whose ``passed`` flag is False.  None of those is a
    measurement, so the criterion they are offered against stays unverified and
    contributes no reference.  This is the boundary the module docstring claims:
    a model's prose alone is never considered evidence.
    """
    result = verify_acceptance_criteria(
        [{"id": "release", "required_evidence_kinds": ["test_result"]}],
        [
            {"source": "bot:coder", "role": "coder", "model": "default"},
            {"source": "tool_receipts", "count": 3},
            {
                "criterion_id": "release",
                "kind": "test_result",
                "passed": False,
                "reference": "worker summary: the tests looked green",
            },
        ],
    )

    assert result.verified is False
    assert result.verdicts[0].verified is False
    assert result.verdicts[0].reason == "Missing passing evidence: test_result"
    assert result.verdicts[0].evidence_references == ()


def test_every_declared_criterion_receives_exactly_one_verdict() -> None:
    """Coverage is one verdict per declared criterion, in declared order.

    Repeated evidence rows for a kind that is already covered cannot fabricate
    a second verdict, and a criterion nobody supplied evidence for is refused
    individually rather than being averaged away by the aggregate.
    """
    result = verify_acceptance_criteria(
        [
            {"id": "alpha", "required_evidence_kinds": ["test_result"]},
            {"id": "beta", "required_evidence_kinds": ["artifact_digest"]},
        ],
        [
            {"criterion_id": "alpha", "kind": "test_result", "passed": True, "reference": "pytest://alpha"},
            {"criterion_id": "alpha", "kind": "test_result", "passed": True, "reference": "pytest://alpha-again"},
        ],
    )

    assert [verdict.criterion_id for verdict in result.verdicts] == ["alpha", "beta"]
    assert result.verdicts[0].verified is True
    assert result.verdicts[0].evidence_references == ("pytest://alpha", "pytest://alpha-again")
    assert result.verdicts[1].verified is False
    assert result.verdicts[1].reason == "Missing passing evidence: artifact_digest"
    assert result.verified is False
