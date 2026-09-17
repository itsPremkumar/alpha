"""Tests for Theory of Mind (ToM) Intent Consultant subsystem."""

import json

from agent_workspace.reasoning.tom.consultant import TheoryOfMindConsultant
from agent_workspace.reasoning.tom.models import PriorityDomain, RiskTolerance
from agent_workspace.tools.builtins.tom_consult_tool import tom_consult


def test_tom_consultant_refactor_invariants():
    consultant = TheoryOfMindConsultant()
    hyp = consultant.consult("Please refactor the billing calculation engine")

    assert PriorityDomain.BACKWARD_COMPATIBILITY in hyp.top_priorities
    assert PriorityDomain.CORRECTNESS in hyp.top_priorities
    assert any("public API" in exp for exp in hyp.unstated_expectations)
    assert any("method signatures" in pit for pit in hyp.pitfalls_to_avoid)


def test_tom_consultant_bugfix_invariants():
    consultant = TheoryOfMindConsultant()
    hyp = consultant.consult("Fix the null pointer bug in user token parsing")

    assert PriorityDomain.MINIMAL_DIFF in hyp.top_priorities
    assert PriorityDomain.TEST_COVERAGE in hyp.top_priorities
    assert any("regression test" in exp for exp in hyp.unstated_expectations)
    assert any("minimal and focused" in exp for exp in hyp.unstated_expectations)


def test_tom_consultant_risk_tolerance():
    consultant = TheoryOfMindConsultant()

    # Production context -> LOW risk tolerance
    hyp_prod = consultant.consult("Update security auth filter for production release")
    assert hyp_prod.risk_tolerance == RiskTolerance.LOW

    # Prototype context -> HIGH risk tolerance
    hyp_proto = consultant.consult("Build a quick prototype POC spike for websockets")
    assert hyp_proto.risk_tolerance == RiskTolerance.HIGH


def test_tom_consult_tool_invocation():
    result_str = tom_consult.invoke({"task_description": "optimize database query performance"})
    data = json.loads(result_str)

    assert data["stated_goal"] == "optimize database query performance"
    assert "inferred_intent" in data
    assert "unstated_expectations" in data
    assert "summary_markdown" in data
    assert "Theory of Mind" in data["summary_markdown"]
