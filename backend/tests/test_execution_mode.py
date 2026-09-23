"""Gap 7 — unified execution-mode switch: honesty, derivation, and endpoint pins.

Pins (per ``references/ALPHA_WORKSWARM_GAPS_IMPLEMENTATION_PLAN.md`` §9.4):

- default-on-missing-file with the honest note; corrupt JSON -> default, no raise
- ``set_mode`` -> ``load_mode`` round-trip and on-disk shape
- atomic write via tmp + ``os.replace`` with no ``.tmp`` residue
- ``mode_context`` restores the prior mode even on exception
- four-mode truth table for ``is_plan_mode`` / ``side_effects_allowed``
- ``plan_side_effect_decision`` via the stubbed ``alpha.runtime.execution_mode._evaluate``
- factory/client ``None``-sentinel derivation and explicit-override precedence
- endpoint payload honesty through direct router function calls (no app.py changes)
- the ``/mode`` command row + handler honesty

``AGENT_WORKSPACE_HOME`` is NOT isolated by the global test environment, so every
test here pins it to a temp directory via the autouse fixture below.
"""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

import alpha.runtime.execution_mode as em
from alpha.runtime.execution_mode import (
    DEFAULT_MODE,
    DEFAULT_NOTE,
    ExecutionMode,
    get_mode,
    is_plan_mode,
    load_mode,
    load_mode_record,
    mode_context,
    mode_path,
    mode_status,
    plan_side_effect_decision,
    save_mode,
    set_mode,
    side_effects_allowed,
)

PLAN_REASON_WORK = "plan-mode:work.plan: side effects require exiting plan mode"
PLAN_REASON_CODE = "plan-mode:code.plan: side effects require exiting plan mode"


@pytest.fixture(autouse=True)
def _isolated_mode_home(tmp_path, monkeypatch):
    """Pin AGENT_WORKSPACE_HOME to a temp dir (the suite does not isolate it)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    monkeypatch.delenv("AGENT_WORKSPACE_PROJECT_ROOT", raising=False)
    yield tmp_path


# ---------------------------------------------------------------------------
# Persistence: default note, corruption, round-trip, atomicity
# ---------------------------------------------------------------------------


def test_mode_path_lives_under_runtime_home(_isolated_mode_home):
    from pathlib import Path

    assert mode_path().parent == Path(_isolated_mode_home).resolve()
    assert mode_path().name == "execution_mode.json"


def test_missing_file_returns_default_with_honest_note():
    assert not mode_path().exists()
    record = load_mode_record()
    assert record["mode"] == "work.normal"
    assert record["note"] == DEFAULT_NOTE
    assert record["persisted"] is False
    # No fabricated last-mode history:
    assert record["updated_at"] == ""
    assert record["actor"] == ""
    assert load_mode() is ExecutionMode.WORK_NORMAL
    assert get_mode() is ExecutionMode.WORK_NORMAL


def test_corrupt_json_returns_default_without_raising():
    mode_path().parent.mkdir(parents=True, exist_ok=True)
    mode_path().write_text("{this is not json", encoding="utf-8")

    assert load_mode() is ExecutionMode.WORK_NORMAL  # must not raise
    record = load_mode_record()
    assert record["mode"] == "work.normal"
    assert record["persisted"] is False
    assert record["note"].startswith(DEFAULT_NOTE)
    assert "corrupt" in record["note"]
    assert record["updated_at"] == ""


def test_unknown_mode_value_in_file_is_treated_as_corrupt():
    mode_path().parent.mkdir(parents=True, exist_ok=True)
    mode_path().write_text(json.dumps({"mode": "banana"}), encoding="utf-8")

    assert load_mode() is DEFAULT_MODE
    record = load_mode_record()
    assert record["persisted"] is False
    assert "corrupt" in record["note"]


def test_set_mode_load_mode_round_trip():
    result = set_mode("code.plan", actor="tester")
    assert result["mode"] == "code.plan"
    assert result["previous"] == "work.normal"
    assert result["persisted"] is True
    assert load_mode() is ExecutionMode.CODE_PLAN
    assert get_mode() is ExecutionMode.CODE_PLAN

    on_disk = json.loads(mode_path().read_text(encoding="utf-8"))
    assert set(on_disk) == {"mode", "updated_at", "actor", "note"}
    assert on_disk["mode"] == "code.plan"
    assert on_disk["actor"] == "tester"
    assert on_disk["updated_at"]  # a real timestamp was written
    # and it parses as ISO-8601:
    from datetime import datetime

    datetime.fromisoformat(on_disk["updated_at"])

    record = load_mode_record()
    assert record["persisted"] is True
    assert record["note"]  # whatever note is on disk, not an invented one


def test_set_mode_invalid_value_raises_and_writes_nothing():
    with pytest.raises(ValueError, match="unknown execution mode"):
        set_mode("banana")
    assert not mode_path().exists()


def test_save_mode_is_atomic_tmp_replace_with_no_residue(monkeypatch, _isolated_mode_home):
    calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(em.os, "replace", spy_replace)

    written = save_mode(ExecutionMode.WORK_PLAN, actor="atomic")

    assert calls, "save_mode must go through os.replace (atomic rename)"
    tmp_name, dst_name = calls[0]
    assert tmp_name.endswith(".tmp"), f"write must stage through a .tmp file, got {tmp_name}"
    assert dst_name == str(mode_path())
    assert written == mode_path()

    home = _isolated_mode_home.resolve()
    assert not list(home.glob("*.tmp")), "no .tmp residue may remain after a successful save"
    assert mode_path().exists()


def test_set_mode_reports_persisted_false_with_real_error(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(em, "save_mode", _boom)

    result = set_mode("work.plan", actor="ops")
    assert result["persisted"] is False
    assert "simulated disk failure" in result["note"]
    assert result["mode"] == "work.plan"
    assert result["previous"] == "work.normal"
    # Nothing landed on disk — no fabricated persistence:
    assert not mode_path().exists()


# ---------------------------------------------------------------------------
# mode_context + truth table
# ---------------------------------------------------------------------------


def test_mode_context_restores_on_exception():
    assert get_mode() is ExecutionMode.WORK_NORMAL
    with pytest.raises(RuntimeError, match="boom"):
        with mode_context("code.plan"):
            assert get_mode() is ExecutionMode.CODE_PLAN
            assert is_plan_mode() is True
            raise RuntimeError("boom")
    assert get_mode() is ExecutionMode.WORK_NORMAL


def test_mode_context_nesting_restores_previous_override():
    with mode_context("work.plan"):
        with mode_context("code.normal"):
            assert get_mode() is ExecutionMode.CODE_NORMAL
        assert get_mode() is ExecutionMode.WORK_PLAN
    assert get_mode() is ExecutionMode.WORK_NORMAL


def test_mode_context_does_not_persist():
    with mode_context("code.plan"):
        assert not mode_path().exists()
    assert not mode_path().exists()


def test_four_mode_truth_table():
    table = {
        ExecutionMode.WORK_NORMAL: (False, True),
        ExecutionMode.WORK_PLAN: (True, False),
        ExecutionMode.CODE_NORMAL: (False, True),
        ExecutionMode.CODE_PLAN: (True, False),
    }
    for mode, (plan, side_effects) in table.items():
        assert is_plan_mode(mode) is plan, mode
        assert side_effects_allowed(mode) is side_effects, mode
        # String values behave identically (str Enum):
        assert is_plan_mode(mode.value) is plan
        assert side_effects_allowed(mode.value) is side_effects


def test_truth_table_defaults_to_active_mode():
    with mode_context("work.plan"):
        assert is_plan_mode() is True
        assert side_effects_allowed() is False
    with mode_context("work.normal"):
        assert is_plan_mode() is False
        assert side_effects_allowed() is True


# ---------------------------------------------------------------------------
# plan_side_effect_decision — stubbed _evaluate seam + real base rules
# ---------------------------------------------------------------------------


def _stub_evaluate(monkeypatch, verdict, reason):
    monkeypatch.setattr(
        em,
        "_evaluate",
        lambda action: SimpleNamespace(verdict=verdict, reason=reason, matched_rule="stub"),
    )


@pytest.mark.parametrize("prefix", ["read:", "search:", "test:", "lint:"])
def test_plan_mode_keeps_read_only_prefixes_allowed(monkeypatch, prefix):
    _stub_evaluate(monkeypatch, "allow", "stub-readonly")
    for mode in (ExecutionMode.WORK_PLAN, ExecutionMode.CODE_PLAN):
        verdict, reason = plan_side_effect_decision(f"{prefix}whatever", mode)
        assert verdict == "allow"
        assert reason == "stub-readonly"


def test_plan_mode_side_effect_gated_with_plan_reason(monkeypatch):
    _stub_evaluate(monkeypatch, "allow", "would-allow-elsewhere")
    verdict, reason = plan_side_effect_decision("fs:write:/tmp/x", ExecutionMode.WORK_PLAN)
    assert verdict == "approval"
    assert reason == PLAN_REASON_WORK

    verdict, reason = plan_side_effect_decision("shell:git push", ExecutionMode.CODE_PLAN)
    assert verdict == "approval"
    assert reason == PLAN_REASON_CODE


def test_plan_mode_destructive_action_stays_deny(monkeypatch):
    _stub_evaluate(monkeypatch, "deny", "irreversible destruction")
    verdict, reason = plan_side_effect_decision("db:drop", ExecutionMode.WORK_PLAN)
    assert verdict == "deny"
    assert reason == PLAN_REASON_WORK


def test_plan_mode_never_grants_a_policy_deny(monkeypatch):
    # Mode only constrains: a policy deny on a read-only action survives plan mode.
    _stub_evaluate(monkeypatch, "deny", "read blocked by operator policy")
    verdict, reason = plan_side_effect_decision("read:secrets.env", ExecutionMode.WORK_PLAN)
    assert verdict == "deny"
    assert reason == "read blocked by operator policy"


def test_normal_modes_pass_policy_verdict_through_unchanged(monkeypatch):
    _stub_evaluate(monkeypatch, "approval", "policy says review this")
    for mode in (ExecutionMode.WORK_NORMAL, ExecutionMode.CODE_NORMAL):
        verdict, reason = plan_side_effect_decision("git:push", mode)
        assert verdict == "approval"
        assert reason == "policy says review this"


def test_real_policy_engine_base_rules_in_normal_mode():
    # No stub: isolated runtime home -> engine runs BASE_RULES only.
    assert plan_side_effect_decision("read:notes.txt") == ("allow", "read-only observation")
    assert plan_side_effect_decision("git:push") == ("approval", "code leaves the machine")
    assert plan_side_effect_decision("db:drop") == ("deny", "irreversible destruction")


def test_real_policy_engine_gates_writes_in_plan_mode():
    verdict, reason = plan_side_effect_decision("git:push", ExecutionMode.WORK_PLAN)
    assert verdict == "approval"
    assert reason == PLAN_REASON_WORK

    verdict, reason = plan_side_effect_decision("db:drop", ExecutionMode.WORK_PLAN)
    assert verdict == "deny"
    assert reason == PLAN_REASON_WORK

    verdict, reason = plan_side_effect_decision("read:notes.txt", ExecutionMode.WORK_PLAN)
    assert verdict == "allow"


# ---------------------------------------------------------------------------
# Factory None-sentinel derivation (explicit True/False wins)
# ---------------------------------------------------------------------------


def _mw_names(mock_create_agent):
    return [type(m).__name__ for m in mock_create_agent.call_args[1]["middleware"]]


def test_resolve_plan_mode_sentinel_contract():
    from alpha.agents.factory import _resolve_plan_mode

    assert _resolve_plan_mode(True) is True
    assert _resolve_plan_mode(False) is False
    set_mode("work.plan")
    assert _resolve_plan_mode(None) is True
    set_mode("code.normal")
    assert _resolve_plan_mode(None) is False


@patch("alpha.agents.factory.create_agent")
def test_factory_none_derives_from_global_mode(mock_create_agent):
    from alpha.agents.factory import create_agent_workspace_agent
    from alpha.agents.features import RuntimeFeatures

    mock_create_agent.return_value = MagicMock(name="graph")
    model = MagicMock(name="model")

    set_mode("work.plan")
    create_agent_workspace_agent(model, features=RuntimeFeatures(sandbox=False), plan_mode=None)
    assert "TodoMiddleware" in _mw_names(mock_create_agent)

    set_mode("work.normal")
    mock_create_agent.reset_mock()
    create_agent_workspace_agent(model, features=RuntimeFeatures(sandbox=False), plan_mode=None)
    assert "TodoMiddleware" not in _mw_names(mock_create_agent)


@patch("alpha.agents.factory.create_agent")
def test_factory_explicit_overrides_win(mock_create_agent):
    from alpha.agents.factory import create_agent_workspace_agent
    from alpha.agents.features import RuntimeFeatures

    mock_create_agent.return_value = MagicMock(name="graph")
    model = MagicMock(name="model")

    # Explicit False wins over a persisted plan mode:
    set_mode("work.plan")
    create_agent_workspace_agent(model, features=RuntimeFeatures(sandbox=False), plan_mode=False)
    assert "TodoMiddleware" not in _mw_names(mock_create_agent)

    # Explicit True wins over a normal mode:
    set_mode("work.normal")
    mock_create_agent.reset_mock()
    create_agent_workspace_agent(model, features=RuntimeFeatures(sandbox=False), plan_mode=True)
    assert "TodoMiddleware" in _mw_names(mock_create_agent)


# ---------------------------------------------------------------------------
# Client None-sentinel derivation (explicit True/False wins)
# ---------------------------------------------------------------------------


def _make_client(**kwargs):
    from alpha.client import AgentWorkspaceClient

    cfg = MagicMock()
    cfg.database.checkpoint_channel_mode = "full"
    cfg.database.checkpoint_delta.snapshot_frequency = 10
    with patch("alpha.client.get_app_config", return_value=cfg):
        return AgentWorkspaceClient(**kwargs)


def _is_plan_flag(client, thread_id="t1", **overrides):
    config = client._get_runnable_config(thread_id, **overrides)
    return config.get("configurable", {}).get("is_plan_mode")


def test_client_default_derives_and_explicit_wins():
    # Persisted plan mode: the default (None) derives True.
    set_mode("work.plan")
    default_client = _make_client()
    assert default_client._plan_mode is True
    assert _is_plan_flag(default_client) is True

    # Explicit False wins over the persisted plan mode.
    off_client = _make_client(plan_mode=False)
    assert off_client._plan_mode is False
    assert _is_plan_flag(off_client) is False

    # No persisted plan mode: the default behaves exactly like the old
    # ``plan_mode=False`` default (backward compatible).
    set_mode("work.normal")
    default_client2 = _make_client()
    assert default_client2._plan_mode is False
    assert _is_plan_flag(default_client2) is False

    # Explicit True still wins in a normal mode.
    on_client = _make_client(plan_mode=True)
    assert on_client._plan_mode is True
    assert _is_plan_flag(on_client) is True

    # Per-call override still wins over the construction-time value.
    assert _is_plan_flag(default_client2, plan_mode=True) is True
    assert _is_plan_flag(default_client, plan_mode=False) is False


# ---------------------------------------------------------------------------
# Endpoints — direct router function calls (no app.py changes)
# ---------------------------------------------------------------------------


def _admin_request(system_role="admin"):
    return SimpleNamespace(
        state=SimpleNamespace(
            user=SimpleNamespace(system_role=system_role),
            auth_source=None,
        )
    )


def _get_mode_endpoint():
    from app.gateway.routers.plan_mode import get_execution_mode

    return asyncio.run(get_execution_mode())


def _post_mode_endpoint(mode, actor="", system_role="admin"):
    from app.gateway.routers.plan_mode import ExecutionModeRequest, set_execution_mode

    payload = ExecutionModeRequest(mode=mode, actor=actor)
    return asyncio.run(set_execution_mode(payload, _admin_request(system_role)))


def test_get_endpoint_missing_file_returns_default_with_honest_note():
    assert not mode_path().exists()
    payload = _get_mode_endpoint()
    assert payload["mode"] == "work.normal"
    assert payload["note"] == DEFAULT_NOTE
    assert payload["source"] == "default"
    assert payload["persisted"] is False
    # Never a fabricated last-mode:
    assert payload["updated_at"] == ""
    assert payload["actor"] == ""


def test_get_endpoint_corrupt_file_discloses_corruption():
    mode_path().parent.mkdir(parents=True, exist_ok=True)
    mode_path().write_text("{broken", encoding="utf-8")
    payload = _get_mode_endpoint()
    assert payload["mode"] == "work.normal"
    assert payload["persisted"] is False
    assert payload["note"].startswith(DEFAULT_NOTE)
    assert "corrupt" in payload["note"]


def test_post_endpoint_success_is_proven_by_the_file():
    result = _post_mode_endpoint("work.plan", actor="ops")
    assert result["persisted"] is True
    assert result["mode"] == "work.plan"
    assert result["previous"] == "work.normal"

    # The success claim must be exactly what the written file proves:
    on_disk = json.loads(mode_path().read_text(encoding="utf-8"))
    assert on_disk["mode"] == "work.plan"
    assert on_disk["actor"] == "ops"

    status = _get_mode_endpoint()
    assert status["mode"] == "work.plan"
    assert status["persisted"] is True
    assert status["source"] == "persisted"
    assert status["updated_at"] == on_disk["updated_at"]
    assert status["actor"] == "ops"


def test_post_endpoint_write_failure_returns_persisted_false_with_real_error(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(em, "save_mode", _boom)

    response = _post_mode_endpoint("code.plan", actor="ops")
    assert isinstance(response, JSONResponse)
    assert response.status_code == 500
    body = json.loads(response.body)
    assert body["persisted"] is False
    assert "simulated disk failure" in body["note"]
    # No file was written, so nothing may claim the mode changed:
    assert not mode_path().exists()
    status = _get_mode_endpoint()
    assert status["mode"] == "work.normal"
    assert status["persisted"] is False


def test_post_endpoint_invalid_mode_is_422_and_writes_nothing():
    with pytest.raises(HTTPException) as excinfo:
        _post_mode_endpoint("banana")
    assert excinfo.value.status_code == 422
    assert "expected one of" in str(excinfo.value.detail)
    assert not mode_path().exists()


def test_post_endpoint_is_admin_gated():
    with pytest.raises(HTTPException) as excinfo:
        _post_mode_endpoint("work.plan", system_role="user")
    assert excinfo.value.status_code == 403
    from app.gateway.routers.plan_mode import _MODE_ADMIN_REQUIRED_DETAIL

    assert excinfo.value.detail == _MODE_ADMIN_REQUIRED_DETAIL
    # The rejected request must not have persisted anything:
    assert not mode_path().exists()


def test_mode_status_reports_context_override_honestly():
    with mode_context("code.plan"):
        status = mode_status()
        assert status["mode"] == "code.plan"
        assert status["source"] == "context"
        assert status["persisted"] is False  # nothing on disk backs this value
        assert DEFAULT_NOTE in status["note"]


# ---------------------------------------------------------------------------
# /mode command row + handler honesty
# ---------------------------------------------------------------------------


def test_mode_command_catalog_row_registered():
    from alpha.commands import CommandCategory, command_registry

    cmd_def = command_registry.get("/mode")
    assert cmd_def is not None
    assert cmd_def.category == CommandCategory.PLANNING
    assert cmd_def.is_core is True
    assert "work.plan" in cmd_def.usage


def test_mode_command_shows_default_honestly():
    from alpha.commands import command_registry

    result = command_registry.execute("/mode")
    assert result.status == "success"
    assert "Mode: work.normal" in result.output
    assert DEFAULT_NOTE in result.output
    assert result.data["persisted"] is False


def test_mode_command_sets_mode():
    from alpha.commands import command_registry

    result = command_registry.execute("/mode set code.plan")
    assert result.status == "success"
    assert load_mode() is ExecutionMode.CODE_PLAN
    on_disk = json.loads(mode_path().read_text(encoding="utf-8"))
    assert on_disk["mode"] == "code.plan"

    shown = command_registry.execute("/mode")
    assert "Mode: code.plan" in shown.output
    assert shown.data["persisted"] is True


def test_mode_command_invalid_value_is_an_error_not_a_success():
    from alpha.commands import command_registry

    result = command_registry.execute("/mode banana")
    assert result.status == "error"
    assert "unknown execution mode" in result.output
    assert not mode_path().exists()


def test_mode_command_persist_failure_is_reported_as_error(monkeypatch):
    from alpha.commands import command_registry

    def _boom(*_args, **_kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(em, "save_mode", _boom)
    result = command_registry.execute("/mode work.plan")
    assert result.status == "error"
    assert "simulated disk failure" in result.output
    assert not mode_path().exists()
