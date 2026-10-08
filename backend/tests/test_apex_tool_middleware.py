"""APEX policy is enforced at the actual LangGraph tool boundary."""

import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from langgraph.prebuilt.tool_node import ToolCallRequest

from alpha.agents.middlewares.tool_error_handling_middleware import ApexContractToolMiddleware
from alpha.apex.contract import narrow_contract, profile_for
from alpha.apex.mode import ApexModeStore
from alpha.apex.store import ApexSessionState, ApexStore


def _setup(tmp_path: Path, monkeypatch, *, token_budget: int | None = None, runtime_budget: int | None = None):
    scope = "thread-apex-gate"
    owner = "operator"
    contract = profile_for("apex_max", mission_id=scope)
    budgets = {}
    if token_budget is not None:
        budgets["max_total_tokens"] = token_budget
    if runtime_budget is not None:
        budgets["max_runtime_minutes"] = runtime_budget
    if budgets:
        contract = narrow_contract(contract, budget=budgets)
    mode = ApexModeStore(tmp_path / "mode.json")
    mode.enable(scope, "apex_max", owner=owner)
    store = ApexStore(tmp_path / "sessions.json")
    session = store.create(
        owner=owner,
        objective="exercise tool policy",
        profile="apex_max",
        contract_digest=contract.digest(),
        contract_snapshot=contract.to_dict(),
        thread_id=scope,
    )
    store.set_state(session.session_id, ApexSessionState.ACTIVE)
    monkeypatch.setattr("alpha.apex.store.get_apex_store", lambda: store)
    monkeypatch.setattr("alpha.apex.mode.get_apex_mode_store", lambda: mode)
    monkeypatch.setattr("alpha.config.app_config.get_app_config", lambda: SimpleNamespace(tool_governance={}))
    runtime = MagicMock()
    runtime.context = {
        "__alpha_apex_session_id": session.session_id,
        "thread_id": scope,
        "user_id": owner,
    }
    tool = SimpleNamespace(metadata={"governance_confirmation": "ask"})
    request = ToolCallRequest(
        tool_call={"name": "write_report", "args": {"path": "report.txt"}, "id": "call-1"},
        tool=tool,
        state={"messages": []},
        runtime=runtime,
    )
    return store, session, request


def test_apex_tool_approval_is_bound_to_one_action_and_resumes_once(tmp_path: Path, monkeypatch) -> None:
    store, session, request = _setup(tmp_path, monkeypatch)
    middleware = ApexContractToolMiddleware()

    denied = middleware._authorize_and_reserve(request)
    assert denied is not None and "parked" in denied.content
    parked = store.get(session.session_id)
    assert parked.state is ApexSessionState.BLOCKED
    approval = store.pending_approval(session.session_id)
    assert approval is not None
    assert approval.action == {
        "tool_name": "write_report",
        "action_class": "tool_governance",
        "contract_digest": session.contract_digest,
        "arguments_digest": hashlib.sha256(json.dumps({"path": "report.txt"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest(),
    }

    assert store.decide_approval(approval.approval_id, verdict="approved", operator="admin") is not None
    admitted = middleware._authorize_and_reserve(request)
    assert admitted is None
    assert store.get(session.session_id).usage.tool_calls == 1
    assert store.get(session.session_id).approvals[0]["consumed_at"] is not None

    request.tool_call["args"] = {"path": "different.txt"}
    denied_again = middleware._authorize_and_reserve(request)
    assert denied_again is not None and "parked" in denied_again.content
    assert store.get(session.session_id).usage.tool_calls == 1


def test_apex_tool_runtime_budget_starts_at_dispatch_not_session_creation(tmp_path: Path, monkeypatch) -> None:
    store, session, request = _setup(tmp_path, monkeypatch, runtime_budget=60)
    store.update(session.session_id, dispatch_started_at=time.time() - (24 * 60 * 60 + 1))

    denied = ApexContractToolMiddleware()._authorize_and_reserve(request)

    assert denied is not None
    assert "runtime budget is exhausted" in denied.content
    assert store.get(session.session_id).usage.tool_calls is None


def test_apex_max_unlimited_session_quotas_do_not_block_by_elapsed_runtime(tmp_path: Path, monkeypatch) -> None:
    store, session, request = _setup(tmp_path, monkeypatch)
    store.update(session.session_id, dispatch_started_at=time.time() - (30 * 24 * 60 * 60))
    store.record_run_usage(session.session_id, run_id="run-unlimited", input_tokens=2_500_000, output_tokens=0, llm_calls=1)

    denied = ApexContractToolMiddleware()._authorize_and_reserve(request)

    assert denied is not None
    assert "runtime budget is exhausted" not in denied.content
    assert "requires operator approval" in denied.content
    assert store.get(session.session_id).usage.tool_calls is None


def test_apex_tool_gate_refuses_calls_after_token_budget_is_spent(tmp_path: Path, monkeypatch) -> None:
    store, session, request = _setup(tmp_path, monkeypatch, token_budget=100)
    generation = store.claim_dispatch(session.session_id)
    assert generation == 1
    assert store.record_dispatch_run(session.session_id, generation=generation, run_id="run-token-cap", status="running")
    assert store.record_run_usage(session.session_id, run_id="run-token-cap", input_tokens=100, output_tokens=0, llm_calls=1)

    denied = ApexContractToolMiddleware()._authorize_and_reserve(request)

    assert denied is not None
    assert "total-token budget is exhausted (100/100)" in denied.content
    assert store.get(session.session_id).usage.tool_calls is None
