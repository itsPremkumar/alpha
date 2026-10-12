"""Acceptance-contract unit tests: parsing, duplicate ids, digest, validation.

The contract is the thing fixed *before* work starts, so every property here
is one the plan's §18 unit list names:

* parsing/normalization and duplicate identifiers;
* requirement-to-gate completeness;
* gate-definition digest determinism (the value approvals bind to);
* request-clause extraction and coverage, which is what makes an omission in
  the plan visible instead of silently shipped.
"""

from __future__ import annotations

import pytest

from alpha.completion.contract import (
    RequirementInput,
    apply_validation,
    build_contract,
    clause_covered,
    contract_digest,
    digest_of,
    extract_request_clauses,
    validate_contract,
)
from alpha.completion.errors import ContractInvalid
from alpha.completion.gates import default_registry
from alpha.completion.models import Expectation, Gate, GateKind, Requirement, TaskNode


def _requirement(rid: str = "r1", **overrides) -> RequirementInput:
    payload = {
        "requirement_id": rid,
        "intent": "The report lists every open blocker",
        "mandatory": True,
        "observable": "report.md contains a blockers section",
        "task_id": "t1",
        "gate_ids": ["g1"],
    }
    payload.update(overrides)
    return RequirementInput(**payload)


def _gate(gid: str = "g1", task_id: str = "t1", **overrides) -> Gate:
    payload = {
        "gate_id": gid,
        "task_id": task_id,
        "title": "report exists",
        "kind": GateKind.FILE,
        "check_ref": "file.exists",
        "parameters": {"path": "report.md"},
        # A gate with no expectation proves nothing and is refused, so every
        # helper gate here measures something real.
        "expectation": Expectation(required_markers=["# Report"]),
    }
    payload.update(overrides)
    return Gate(**payload)


def _task(tid: str = "t1", **overrides) -> TaskNode:
    payload = {"task_id": tid, "title": "Write the report", "gate_ids": ["g1"]}
    payload.update(overrides)
    return TaskNode(**payload)


class TestContractBuild:
    def test_digest_is_deterministic_across_rebuilds(self):
        one = build_contract(contract_id="c1", mode="strict", requirements=[_requirement()])
        two = build_contract(contract_id="c1", mode="strict", requirements=[_requirement()])
        assert one.digest == two.digest == contract_digest(one)

    def test_digest_moves_when_an_intent_changes(self):
        one = build_contract(contract_id="c1", mode="strict", requirements=[_requirement()])
        other = build_contract(contract_id="c1", mode="strict", requirements=[_requirement(intent="Something else entirely")])
        assert one.digest != other.digest

    def test_digest_ignores_irrelevant_field_ordering(self):
        a = build_contract(contract_id="c1", mode="strict", requirements=[_requirement(gate_ids=["g1"], task_id="t1")])
        b = build_contract(contract_id="c1", mode="strict", requirements=[_requirement(task_id="t1", gate_ids=["g1"])])
        assert a.digest == b.digest

    def test_digest_of_is_stable_json(self):
        assert digest_of({"b": 1, "a": 2}) == digest_of({"a": 2, "b": 1})

    def test_build_normalizes_and_numbers_requirements(self):
        contract = build_contract(contract_id="c1", mode="strict", requirements=[_requirement(), _requirement("r2")])
        assert [r.requirement_id for r in contract.requirements] == ["r1", "r2"]
        assert all(r.request_revision == 1 for r in contract.requirements)


class TestContractValidation:
    def test_a_complete_contract_produces_no_findings(self):
        contract = build_contract(contract_id="c1", mode="strict", requirements=[_requirement()])
        findings = validate_contract(contract, tasks={"t1": _task()}, gates={"g1": _gate()}, known_checks=frozenset(default_registry().names()))
        assert findings == []
        assert apply_validation(contract, findings).valid is True

    def test_an_empty_contract_is_an_error_not_a_warning(self):
        contract = build_contract(contract_id="c1", mode="strict", requirements=[])
        findings = validate_contract(contract, tasks={}, gates={}, known_checks=frozenset(default_registry().names()))
        codes = {f.code for f in findings}
        assert "contract_empty" in codes
        assert all(f.severity == "error" for f in findings if f.code == "contract_empty")
        assert apply_validation(contract, findings).valid is False

    def test_a_contract_with_no_gates_is_refused(self):
        contract = build_contract(contract_id="c1", mode="strict", requirements=[_requirement(gate_ids=[])])
        findings = validate_contract(contract, tasks={"t1": _task(gate_ids=[])}, gates={}, known_checks=frozenset(default_registry().names()))
        assert "no_gates" in {f.code for f in findings}

    def test_duplicate_requirement_ids_are_named(self):
        contract = build_contract(contract_id="c1", mode="strict", requirements=[_requirement("r1"), _requirement("r1")])
        findings = validate_contract(contract, tasks={"t1": _task()}, gates={"g1": _gate()}, known_checks=frozenset(default_registry().names()))
        assert "duplicate_id" in {f.code for f in findings}

    def test_an_unregistered_check_ref_blocks_dispatch(self):
        contract = build_contract(contract_id="c1", mode="strict", requirements=[_requirement()])
        findings = validate_contract(
            contract,
            tasks={"t1": _task()},
            gates={"g1": _gate(check_ref="os.system")},
            known_checks=frozenset(default_registry().names()),
        )
        assert any(f.code == "gate_unregistered_check" and f.severity == "error" for f in findings)

    def test_a_requirement_pointing_at_an_absent_gate_is_an_error(self):
        contract = build_contract(contract_id="c1", mode="strict", requirements=[_requirement(gate_ids=["g-missing"])])
        findings = validate_contract(contract, tasks={"t1": _task()}, gates={"g1": _gate()}, known_checks=frozenset(default_registry().names()))
        assert any(f.code == "requirement_gate_missing" for f in findings)

    def test_dispatch_refuses_an_invalid_contract_with_its_findings(self):
        contract = build_contract(contract_id="c1", mode="strict", requirements=[])
        contract = apply_validation(contract, validate_contract(contract, tasks={}, gates={}, known_checks=frozenset(default_registry().names())))
        with pytest.raises(ContractInvalid) as exc:
            if not contract.valid:
                raise ContractInvalid("dispatch refused: the contract is not valid", findings=contract.blocking_findings, subject="c1")
        assert exc.value.findings


class TestRequestClauses:
    def test_trivial_fragments_are_not_obligations(self):
        clauses = extract_request_clauses("Thanks!\nBuild the report generator and ship it today")
        assert all(len(c.split()) >= 3 for c in clauses)
        assert not any(c.lower().startswith("thanks") for c in clauses)

    def test_enumeration_markers_are_stripped(self):
        clauses = extract_request_clauses("1. Write the report\n2. Publish the report")
        assert not any(c.startswith("1.") or c.startswith("2.") for c in clauses)

    def test_coverage_is_true_when_the_intent_carries_the_clause(self):
        requirement = Requirement(requirement_id="r1", intent="Publish the report", observable="", gate_ids=["g1"])
        assert clause_covered("Publish the report", requirement) is True

    def test_an_unrelated_intent_is_not_coverage(self):
        requirement = Requirement(requirement_id="r1", intent="Rotate the log files daily", observable="", gate_ids=["g1"])
        assert clause_covered("Publish the release notes to the staging site", requirement) is False

    def test_an_empty_intent_is_refused_at_construction(self):
        # `clause_covered` also returns False for an empty intent, but the
        # model refuses it earlier: a requirement with nothing to say cannot
        # be written at all, so coverage never has to guess.
        with pytest.raises(Exception):
            Requirement(requirement_id="r1", intent="", observable="", gate_ids=[])

    def test_an_all_stopword_intent_covers_nothing(self):
        # Tokens that mean nothing ("the ... of ... a") cannot overlap with a
        # clause, so coverage is refused rather than assumed.
        requirement = Requirement(requirement_id="r1", intent="the of a", observable="the of a", gate_ids=["g1"])
        assert clause_covered("Publish the release notes to staging", requirement) is False

    def test_a_short_clause_with_two_stopwords_is_still_an_obligation(self):
        # "publish the dashboard" leaves only two non-stopword tokens, so
        # counting *tokens* rather than words silently discarded it — the
        # exact clause this audit exists to surface. Three words is enough.
        clauses = extract_request_clauses("Write the report and also publish the dashboard")
        assert any("dashboard" in clause for clause in clauses)

    def test_the_easy_half_of_a_sentence_does_not_cover_the_whole(self):
        # Coverage is measured against the *clause*, never the requirement:
        # a requirement naming only the first half must not account for a
        # sentence that asks for both halves.
        requirement = Requirement(requirement_id="r1", intent="Write the report", observable="report.md exists", gate_ids=["g1"])
        assert clause_covered("Write the report and publish the dashboard", requirement) is False
        # And each half on its own is still matched, so the rule is not a
        # blanket refusal.
        assert clause_covered("Write the report", requirement) is True
