"""Tests for Oracle Consultation Service and Tool."""

import json

import pytest

from alpha.agent.oracle.service import OracleResponse, OracleService
from alpha.tools.builtins.ask_oracle_tool import ask_oracle


def test_oracle_service_domains():
    oracle = OracleService()

    # Async query
    resp_async = oracle.consult("best practices for asyncio event loop in worker threads")
    assert isinstance(resp_async, OracleResponse)
    assert "async" in resp_async.guidance.lower()
    assert len(resp_async.best_practices) > 0
    assert len(resp_async.common_pitfalls) > 0
    assert "PEP" in resp_async.references[0]
    # Confidence is keyword-match quality (2 of the 3 async/loop/coroutine
    # triggers appear), not a fixed fabricated default.
    assert resp_async.confidence == pytest.approx(0.67)

    # Docker query
    resp_docker = oracle.consult("docker security and layer optimization")
    assert "container" in resp_docker.guidance.lower() or "docker" in resp_docker.guidance.lower()
    assert any("secret" in p.lower() or "root" in p.lower() for p in resp_docker.common_pitfalls)
    assert resp_docker.confidence == pytest.approx(0.5)


def test_oracle_generic_fallback_abstains_with_disclosure():
    """A query matching no curated domain must report 0.0 and say so."""
    oracle = OracleService()
    resp = oracle.consult("advice on choosing a color palette for party invitations")
    assert resp.confidence == 0.0
    assert "generic answer, no domain match" in resp.guidance.lower()


def test_ask_oracle_tool_invocation():
    result_str = ask_oracle.invoke({"query": "unified diff patch generation standard flags"})
    data = json.loads(result_str)

    assert data["query"] == "unified diff patch generation standard flags"
    assert "guidance" in data
    assert "markdown" in data
    assert "Oracle Advisory" in data["markdown"]
    # Match-derived confidence: "diff" and "patch" hit 2 of the 3 git-domain
    # triggers, so 0.67 — never the old fabricated 0.98/0.9+.
    assert data["confidence"] == pytest.approx(0.67)


def test_ask_oracle_tool_generic_fallback_serializes_honest_zero():
    result_str = ask_oracle.invoke({"query": "recipies for vanilla pudding"})
    data = json.loads(result_str)
    assert data["confidence"] == 0.0
    assert "no domain match" in data["guidance"].lower()
