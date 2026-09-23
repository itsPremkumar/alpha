"""Honesty pins for confidence/autonomy defaults (known-issues fix).

Every surface here used to ship a fabricated default (0.85 / 0.8 / 0.9-style
"confidence" with no evidence, and a router gate default that silently granted
autonomy). These tests pin the NEW honest behavior:

* omitted numeric confidence fields default to the disclosed neutral
  baseline 0.5 with basis ``neutral_baseline_0.5`` — never a measured value;
* explicitly supplied values are labeled ``caller_supplied``;
* bootstrap seed scores are labeled ``seed_demo_data`` (illustrative demo
  records, not live measurements);
* ``GateRequest.autonomous_mode`` is opt-in: omission never grants autonomy.

No test here is skipped, deleted, or weakened; they pin honest disclosure.
"""

from __future__ import annotations

import pytest

from alpha.enterprise.models import (
    CONFIDENCE_BASIS_CALLER_SUPPLIED,
    CONFIDENCE_BASIS_NEUTRAL,
    CONFIDENCE_BASIS_SEED_DEMO,
    NEUTRAL_CONFIDENCE_BASELINE,
    DebateArgument,
    RFCReview,
)
from alpha.enterprise.rfc import EnterpriseRFCProtocol
from app.gateway.routers.enterprise import SubmitDebateArgumentRequest, SubmitRFCReviewRequest
from app.gateway.routers.evolution import GateRequest
from app.gateway.routers.memory import SemanticBeliefCreateRequest


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path, monkeypatch):
    """Keep any incidental workspace I/O inside a temp dir."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


# --- 1. Request-model defaults: neutral 0.5, disclosed ----------------------

def test_submit_rfc_review_request_defaults_to_neutral_baseline() -> None:
    req = SubmitRFCReviewRequest(
        reviewer_bot="bot-cto",
        department="architecture",
        verdict="approve",
        argument="Looks sound.",
    )
    assert req.epistemic_confidence == NEUTRAL_CONFIDENCE_BASELINE == 0.5
    # The description must disclose the basis of the default, not imply measurement.
    field = SubmitRFCReviewRequest.model_fields["epistemic_confidence"]
    assert "neutral_baseline_0.5" in field.description


def test_submit_debate_argument_request_defaults_to_neutral_baseline() -> None:
    req = SubmitDebateArgumentRequest(
        speaker_bot="bot-ciso",
        department="security",
        stance="pro",
        claim="Tamper-proof.",
    )
    assert req.epistemic_weight == NEUTRAL_CONFIDENCE_BASELINE == 0.5
    field = SubmitDebateArgumentRequest.model_fields["epistemic_weight"]
    assert "neutral_baseline_0.5" in field.description


def test_semantic_belief_create_request_defaults_to_neutral_baseline() -> None:
    req = SemanticBeliefCreateRequest(
        subject="User",
        predicate="prefers",
        object_val="dark mode",
    )
    assert req.confidence == NEUTRAL_CONFIDENCE_BASELINE == 0.5
    field = SemanticBeliefCreateRequest.model_fields["confidence"]
    assert "neutral_baseline_0.5" in field.description


# --- 2. Stored-model defaults: value + basis label --------------------------

def test_rfc_review_model_default_is_disclosed_neutral() -> None:
    review = RFCReview(reviewer_bot="bot-cto", department="architecture", verdict="approve", argument="ok")
    assert review.epistemic_confidence == 0.5
    assert review.confidence_basis == CONFIDENCE_BASIS_NEUTRAL == "neutral_baseline_0.5"


def test_debate_argument_model_default_is_disclosed_neutral() -> None:
    arg = DebateArgument(speaker_bot="bot-ciso", department="security", stance="pro", claim="c", evidence="e")
    assert arg.epistemic_weight == 0.5
    assert arg.confidence_basis == CONFIDENCE_BASIS_NEUTRAL == "neutral_baseline_0.5"


# --- 3. Protocol methods: omitted ⇒ neutral, explicit ⇒ caller_supplied -----

def _fresh_protocol():
    protocol = EnterpriseRFCProtocol()
    rfc = protocol.create_rfc(
        title="RFC-HONESTY: disclose confidence provenance",
        author_bot="bot-cto",
        department="architecture",
        summary="Confidence defaults must be honest.",
        proposal_content="Replace fabricated defaults with a disclosed neutral baseline.",
        affected_departments=["architecture", "security"],
    )
    return protocol, rfc


def test_submit_review_omitted_confidence_is_disclosed_neutral() -> None:
    protocol, rfc = _fresh_protocol()
    review = protocol.submit_review(
        rfc_id=rfc.rfc_id,
        reviewer_bot="bot-cto",
        department="architecture",
        verdict="approve",
        argument="No fabricated score.",
    )
    assert review.epistemic_confidence == 0.5
    assert review.confidence_basis == CONFIDENCE_BASIS_NEUTRAL


def test_submit_review_explicit_confidence_is_labeled_caller_supplied() -> None:
    protocol, rfc = _fresh_protocol()
    review = protocol.submit_review(
        rfc_id=rfc.rfc_id,
        reviewer_bot="bot-ciso",
        department="security",
        verdict="approve",
        argument="Explicit rating supplied by caller.",
        epistemic_confidence=0.9,
    )
    assert review.epistemic_confidence == 0.9
    assert review.confidence_basis == CONFIDENCE_BASIS_CALLER_SUPPLIED == "caller_supplied"


def test_submit_debate_argument_omitted_weight_is_disclosed_neutral() -> None:
    protocol, rfc = _fresh_protocol()
    arg = protocol.submit_debate_argument(
        rfc_id=rfc.rfc_id,
        speaker_bot="bot-eng-lead",
        department="engineering",
        stance="pro",
        claim="No fabricated weight.",
        evidence="",
    )
    assert arg.epistemic_weight == 0.5
    assert arg.confidence_basis == CONFIDENCE_BASIS_NEUTRAL


def test_submit_debate_argument_explicit_weight_is_labeled_caller_supplied() -> None:
    protocol, rfc = _fresh_protocol()
    arg = protocol.submit_debate_argument(
        rfc_id=rfc.rfc_id,
        speaker_bot="bot-eng-lead",
        department="engineering",
        stance="con",
        claim="Explicit weight supplied.",
        evidence="caller rating",
        epistemic_weight=0.7,
    )
    assert arg.epistemic_weight == 0.7
    assert arg.confidence_basis == CONFIDENCE_BASIS_CALLER_SUPPLIED


# --- 4. Bootstrap seeds are labeled demo data, not measurements -------------

def test_seeded_rfcs_carry_seed_demo_basis_label() -> None:
    protocol = EnterpriseRFCProtocol()
    seeded = protocol.list_rfcs()
    assert seeded, "expected bootstrap sample RFCs"
    checked = 0
    for rfc in seeded:
        for review in rfc.reviews:
            assert review.confidence_basis == CONFIDENCE_BASIS_SEED_DEMO == "seed_demo_data"
            checked += 1
        for arg in rfc.debate_thread:
            assert arg.confidence_basis == CONFIDENCE_BASIS_SEED_DEMO
            checked += 1
    assert checked >= 9, "expected all 9 seeded scores to be labeled seed_demo_data"


# --- 5. GateRequest: autonomy is opt-in -------------------------------------

def test_gate_request_autonomous_mode_is_opt_in_false() -> None:
    assert GateRequest().autonomous_mode is False
    assert GateRequest(autonomous_mode=True).autonomous_mode is True
    assert GateRequest.model_fields["autonomous_mode"].default is False
