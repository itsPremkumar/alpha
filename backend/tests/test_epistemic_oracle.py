"""Tests for Oracle Consultation Service and Tool."""

import json

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

    # Docker query
    resp_docker = oracle.consult("docker security and layer optimization")
    assert "container" in resp_docker.guidance.lower() or "docker" in resp_docker.guidance.lower()
    assert any("secret" in p.lower() or "root" in p.lower() for p in resp_docker.common_pitfalls)


def test_ask_oracle_tool_invocation():
    result_str = ask_oracle.invoke({"query": "unified diff patch generation standard flags"})
    data = json.loads(result_str)

    assert data["query"] == "unified diff patch generation standard flags"
    assert "guidance" in data
    assert "markdown" in data
    assert "Oracle Advisory" in data["markdown"]
    assert data["confidence"] >= 0.9
