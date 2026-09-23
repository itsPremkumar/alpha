"""Scoped hierarchical budget tests: contextvar API, min() inheritance,
parent exhaustion blocking children, unchanged default path, and
cost-governor scope sub-budgets with circuit-breaker inheritance."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from alpha.agents.middlewares.token_budget_middleware import TokenBudgetMiddleware
from alpha.config.token_budget_config import (
    ScopeBudgetConfig,
    TokenBudgetConfig,
    budget_scope,
    get_current_budget_scope,
    reset_budget_scope,
    set_budget_scope,
)
from alpha.models.cost_governor import (
    BudgetConfig,
    CircuitBreakerTrippedError,
    CostGovernor,
)

# ---------------------------------------------------------------------------
# helpers (mirrors tests/test_token_budget_middleware.py)
# ---------------------------------------------------------------------------


def _make_runtime(thread_id="test-thread", run_id="test-run"):
    runtime = MagicMock()
    runtime.context = {"thread_id": thread_id, "run_id": run_id}
    return runtime


def _state(total: int, msg_id: str):
    msg = AIMessage(id=msg_id, content="", usage_metadata={"input_tokens": total, "output_tokens": 0, "total_tokens": total})
    return {"messages": [msg]}


def _scoped_config(**kwargs) -> TokenBudgetConfig:
    defaults = dict(
        enabled=True,
        max_tokens=1_000_000,  # per-run limit far above the scope limits under test
        warn_threshold=0.8,
        hard_stop_threshold=1.0,
        scopes=[
            ScopeBudgetConfig(scope_id="project:p1", max_tokens=10_000),
            ScopeBudgetConfig(scope_id="workflow:w1", parent="project:p1", max_tokens=5_000),
            ScopeBudgetConfig(scope_id="node:n1", parent="project:p1", max_tokens=1_000),
        ],
    )
    defaults.update(kwargs)
    return TokenBudgetConfig(**defaults)


# ---------------------------------------------------------------------------
# contextvar scope API
# ---------------------------------------------------------------------------


def test_scope_contextvar_default_is_none_and_nests():
    assert get_current_budget_scope() is None

    with budget_scope("workflow:w1"):
        assert get_current_budget_scope() == "workflow:w1"
        with budget_scope("node:n1"):
            assert get_current_budget_scope() == "node:n1"
        # inner binding restored on exit
        assert get_current_budget_scope() == "workflow:w1"
    assert get_current_budget_scope() is None

    token = set_budget_scope("project:p1")
    assert get_current_budget_scope() == "project:p1"
    reset_budget_scope(token)
    assert get_current_budget_scope() is None


def test_scope_contextvar_restores_on_exception():
    with pytest.raises(RuntimeError), budget_scope("workflow:w1"):
        raise RuntimeError("boom")
    assert get_current_budget_scope() is None


# ---------------------------------------------------------------------------
# config validation + min() inheritance math
# ---------------------------------------------------------------------------


def test_scope_config_validation_rejects_bad_hierarchy():
    with pytest.raises(ValueError, match="unknown parent"):
        TokenBudgetConfig(scopes=[ScopeBudgetConfig(scope_id="workflow:w1", parent="project:missing")])
    with pytest.raises(ValueError, match="duplicate budget scope id"):
        TokenBudgetConfig(scopes=[ScopeBudgetConfig(scope_id="project:p1"), ScopeBudgetConfig(scope_id="project:p1")])
    with pytest.raises(ValueError, match="cycle"):
        TokenBudgetConfig(
            scopes=[
                ScopeBudgetConfig(scope_id="alpha-scope", parent="beta-scope"),
                ScopeBudgetConfig(scope_id="beta-scope", parent="alpha-scope"),
            ]
        )
    with pytest.raises(ValueError, match="cannot hang under parent"):
        TokenBudgetConfig(
            scopes=[
                ScopeBudgetConfig(scope_id="workflow:w1"),
                ScopeBudgetConfig(scope_id="project:p1", parent="workflow:w1"),
            ]
        )


def test_level_defaults_from_scope_id_prefix():
    assert ScopeBudgetConfig(scope_id="workflow:w1", max_tokens=100).level == "workflow"
    assert ScopeBudgetConfig(scope_id="ad-hoc", max_tokens=100).level is None
    with pytest.raises(ValueError, match="unknown level"):
        ScopeBudgetConfig(scope_id="x", level="dimension")


def test_effective_limit_is_min_of_child_and_parent_remaining():
    config = _scoped_config()

    # parent has 6k remaining of 10k -> min(5_000, 6_000) = 5_000
    limits = config.effective_scope_limits("workflow:w1", {"project:p1": {"total": 4_000, "input": 0, "output": 0}})
    assert limits is not None
    assert limits.max_tokens == 5_000

    # parent has only 2k remaining -> min(5_000, 2_000) = 2_000
    limits = config.effective_scope_limits("workflow:w1", {"project:p1": {"total": 8_000, "input": 0, "output": 0}})
    assert limits is not None
    assert limits.max_tokens == 2_000
    assert any(b["scope_id"] == "project:p1" and b["remaining"] == 2_000 for b in limits.bounders)

    # undeclared scope -> no scoped limit enforceable (per-run behaviour only)
    assert config.effective_scope_limits("agent:ghost", {}) is None


def test_middleware_child_hard_stops_at_parent_remaining():
    """End-to-end: the child's effective budget is min(child, remaining parent),
    so a child with a large own limit still stops at the parent's remainder."""
    config = _scoped_config()
    mw = TokenBudgetMiddleware.from_config(config)
    runtime = _make_runtime(run_id="scoped-run")

    # Charge 8k to the parent scope first (parent limit 10k -> 2k remaining).
    with budget_scope("project:p1"):
        assert mw._apply(_state(8_000, "parent-msg"), runtime) is None

    # Now a 1.5k call inside the child scope: min(5_000 child, 2_000 parent
    # remaining before this call) -> 1.5k already exceeds the bound.
    with budget_scope("workflow:w1"):
        result = mw._apply(_state(1_500, "child-msg"), runtime)

    assert result is not None
    stop_text = result["messages"][0].content
    assert "TOKEN BUDGET EXCEEDED" in stop_text
    assert "workflow:w1" in stop_text

    exhaustion = mw.consume_scope_exhaustion("scoped-run")
    assert exhaustion is not None
    assert exhaustion["scope_id"] == "workflow:w1"
    assert exhaustion["consumed"] == 1_500
    # parent charge: 8_000 + 1_500 = 9_500 of 10_000 -> 500 remaining bound the child
    assert exhaustion["limit"] == 500
    assert any(b["scope_id"] == "project:p1" and b["consumed"] == 9_500 and b["remaining"] == 500 for b in exhaustion["bounders"])

    # Explicit status stamped for the caller - never a silent "completed".
    assert runtime.context["budget_status"] == "BUDGET_EXHAUSTED"
    assert runtime.context["budget_exhausted"]["scope_id"] == "workflow:w1"
    assert mw.consume_stop_reason("scoped-run") == "token_capped"
    # consumption is one-shot
    assert mw.consume_scope_exhaustion("scoped-run") is None


def test_parent_exhaustion_blocks_child():
    """An exhausted parent blocks the child even when the child's own usage
    is far below the child's own limit."""
    config = _scoped_config()
    mw = TokenBudgetMiddleware.from_config(config)

    parent_runtime = _make_runtime(run_id="parent-run")
    with budget_scope("project:p1"):
        parent_result = mw._apply(_state(10_000, "parent-exhaust"), parent_runtime)
    assert parent_result is not None  # parent itself hard-stops at its own limit
    assert mw.consume_scope_exhaustion("parent-run")["limit"] == 10_000

    child_runtime = _make_runtime(run_id="child-run")
    with budget_scope("node:n1"):
        child_result = mw._apply(_state(100, "child-after"), child_runtime)

    # child used only 100 of its own 1_000 limit, but the parent has 0 left
    assert child_result is not None
    exhaustion = mw.consume_scope_exhaustion("child-run")
    assert exhaustion is not None
    assert exhaustion["scope_id"] == "node:n1"
    assert exhaustion["limit"] == 0
    assert any(b["scope_id"] == "project:p1" and b["remaining"] == 0 for b in exhaustion["bounders"])
    assert child_runtime.context["budget_status"] == "BUDGET_EXHAUSTED"
    assert mw.consume_stop_reason("child-run") == "token_capped"


def test_scope_usage_charged_to_scope_and_ancestors():
    config = _scoped_config()
    mw = TokenBudgetMiddleware.from_config(config)
    runtime = _make_runtime(run_id="charge-run")

    with budget_scope("workflow:w1"):
        mw._apply(_state(1_200, "charge-msg"), runtime)

    assert mw.scope_usage("workflow:w1").total == 1_200
    assert mw.scope_usage("project:p1").total == 1_200  # ancestor charged too
    assert mw.scope_usage("node:n1") is None  # untouched sibling branch


def test_default_path_without_scope_is_unchanged():
    """No bound scope -> exactly the legacy per-run behaviour, no scoped state."""
    config = TokenBudgetConfig(max_tokens=100_000, hard_stop_threshold=1.0, enabled=True)
    mw = TokenBudgetMiddleware.from_config(config)
    assert get_current_budget_scope() is None

    ok_runtime = _make_runtime(run_id="ok-run")
    assert mw._apply(_state(50_000, "ok-msg"), ok_runtime) is None
    assert mw.consume_scope_exhaustion("ok-run") is None
    assert "budget_status" not in ok_runtime.context

    capped_runtime = _make_runtime(run_id="capped-run")
    tool_calls = [{"name": "bash", "args": {"command": "ls"}, "id": "call_1"}]
    msg = AIMessage(id="cap", content="partial", tool_calls=tool_calls, usage_metadata={"input_tokens": 105_000, "output_tokens": 0, "total_tokens": 105_000})
    result = mw._apply({"messages": [msg]}, capped_runtime)
    assert result is not None
    assert result["messages"][0].tool_calls == []
    assert "TOKEN BUDGET EXCEEDED" in result["messages"][0].content
    # legacy stop-reason contract intact; no scoped report fabricated
    assert mw.consume_stop_reason("capped-run") == "token_capped"
    assert mw.consume_scope_exhaustion("capped-run") is None
    assert "budget_status" not in capped_runtime.context
    assert mw.scope_usage("anything") is None


# ---------------------------------------------------------------------------
# cost governor: scope sub-budgets + circuit-breaker inheritance
# ---------------------------------------------------------------------------


def test_cost_governor_scope_charge_and_inherited_trip(tmp_path: Path):
    storage = tmp_path / "costs.json"
    gov = CostGovernor(storage_path=storage)
    gov.set_scope_budget("workflow:wf", limit_usd=0.01)
    gov.set_scope_budget("node:nd", limit_usd=100.0, parent="workflow:wf")

    # Charge 1: 10k input claude tokens = $0.03 -> crosses the parent's $0.01
    # limit; the crossing charge is recorded and trips the breaker.
    rec = gov.record_usage(
        project_id="proj_scope",
        bot_name="coder",
        model_name="claude-3-7-sonnet",
        input_tokens=10_000,
        output_tokens=0,
        scope_id="node:nd",
    )
    parent = gov.get_scope_state("workflow:wf")
    child = gov.get_scope_state("node:nd")
    assert parent is not None and parent.tripped is True
    assert parent.spent_usd >= parent.limit_usd
    assert child is not None
    # ancestor inherits the child's spend through the chain charge
    assert parent.spent_usd == pytest.approx(child.spent_usd)
    child_spent_after_first = child.spent_usd
    spend_after_first = gov.get_project_spend("proj_scope")
    assert spend_after_first >= rec.cost_usd

    # Charge 2 (scope taken from the contextvar): parent breaker is tripped ->
    # the child charge is blocked with an explicit exception, but the spend is
    # still recorded (real usage is never erased).
    with pytest.raises(CircuitBreakerTrippedError) as excinfo:
        with budget_scope("node:nd"):
            gov.record_usage(
                project_id="proj_scope",
                bot_name="coder",
                model_name="claude-3-7-sonnet",
                input_tokens=1_000,
                output_tokens=0,
            )
    assert excinfo.value.blocked_by == "workflow:wf"
    assert excinfo.value.scope_id == "node:nd"
    assert gov.get_project_spend("proj_scope") > spend_after_first
    # the blocked child charge was still accounted
    assert gov.get_scope_state("node:nd").spent_usd > child_spent_after_first


def test_cost_governor_project_breaker_blocks_scopes_but_not_legacy_path(tmp_path: Path):
    storage = tmp_path / "costs.json"
    gov = CostGovernor(storage_path=storage)
    gov.set_project_budget("proj_root", BudgetConfig(daily_budget_usd=0.001, alert_threshold_ratio=999.0))
    gov.set_scope_budget("workflow:wf", limit_usd=1_000.0)

    # Crossing charge: recorded, trips the implicit project root breaker.
    gov.record_usage(
        project_id="proj_root",
        bot_name="coder",
        model_name="claude-3-7-sonnet",
        input_tokens=10_000,
        output_tokens=0,
        scope_id="workflow:wf",
    )
    assert gov.get_project_spend("proj_root") >= 0.001

    # Inherited block: the project breaker stops every scope beneath it.
    with pytest.raises(CircuitBreakerTrippedError) as excinfo:
        gov.record_usage(
            project_id="proj_root",
            bot_name="coder",
            model_name="claude-3-7-sonnet",
            input_tokens=1_000,
            output_tokens=0,
            scope_id="workflow:wf",
        )
    assert excinfo.value.blocked_by == "project:proj_root"

    # Legacy (scope-less) path is untouched by the block: explicit status must
    # come from scoped calls, existing callers keep working unchanged.
    legacy = gov.record_usage(
        project_id="proj_root",
        bot_name="coder",
        model_name="claude-3-7-sonnet",
        input_tokens=1_000,
        output_tokens=0,
    )
    assert legacy.record_id.startswith("USG-")


def test_cost_governor_default_api_backward_compatible(tmp_path: Path):
    storage = tmp_path / "costs.json"
    gov = CostGovernor(storage_path=storage)

    rec1 = gov.record_usage(
        project_id="proj_plain",
        bot_name="architect",
        model_name="claude-3-7-sonnet",
        input_tokens=50_000,
        output_tokens=20_000,
    )
    assert rec1.cost_usd > 0.4
    assert gov.get_scope_state("workflow:wf") is None
    assert gov.get_project_spend("proj_plain") == rec1.cost_usd
    summary = gov.get_project_summary("proj_plain")
    assert summary["project_id"] == "proj_plain"
    assert "architect" in summary["bot_breakdown"]

    # round-trips through persistence with the scope state intact
    gov.set_scope_budget("workflow:wf", limit_usd=0.5, parent="project:p1")
    reloaded = CostGovernor(storage_path=storage)
    state = reloaded.get_scope_state("workflow:wf")
    assert state is not None
    assert state.parent == "project:p1"
    assert json.dumps(state.to_dict())


def test_effective_scope_limits_report_is_serialisable():
    config = _scoped_config()
    limits = config.effective_scope_limits("workflow:w1", {"project:p1": {"total": 9_000, "input": 0, "output": 0}})
    assert limits is not None
    assert json.dumps({"limit": limits.max_tokens, "bounders": list(limits.bounders)})
    assert limits.max_tokens == 1_000  # min(5_000 child, 1_000 parent remaining)
