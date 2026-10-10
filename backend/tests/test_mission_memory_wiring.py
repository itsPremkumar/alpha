"""Mission memory wiring + behavior: tool, middleware, config, and honesty.

Offline. The tool's runtime (owner/thread/paths/config) is monkeypatched so the
model-facing surface and the per-turn anchor are exercised against a real,
temp-backed durable file with no Gateway and no model. The properties pinned here
are the ones the manifest wire-up depends on: the tool is registered, the
middleware is on the chain, the config validates, the anchor is injected exactly
once, and nothing here ever asserts a milestone is *verified* from prose.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import SystemMessage

from alpha.config.mission_memory_config import MissionMemoryConfig
from alpha.config.paths import Paths
from alpha.runtime.missions import MissionManager, MissionStack

# ---------------------------------------------------------------------------
# Registration (what keeps the manifest wiring test green)
# ---------------------------------------------------------------------------


def test_tool_is_registered_in_builtin_tools() -> None:
    from alpha.tools.tools import BUILTIN_TOOLS

    names = {getattr(t, "name", None) for t in BUILTIN_TOOLS}
    assert "mission_memory" in names


def test_tool_exported_from_builtins() -> None:
    from alpha.tools.builtins import mission_memory as tool

    assert tool.name == "mission_memory"


def test_middleware_exposes_the_model_call_hooks() -> None:
    from alpha.agents.middlewares.mission_memory_middleware import MissionMemoryMiddleware

    for hook in ("before_agent", "wrap_model_call", "awrap_model_call"):
        assert hasattr(MissionMemoryMiddleware, hook)


def test_middleware_is_wired_into_the_lead_agent_chain() -> None:
    source = (Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "agents" / "lead_agent" / "agent.py").read_text(encoding="utf-8")
    assert "mission_memory_middleware" in source


def test_config_validates_and_example_declares_it() -> None:
    cfg = MissionMemoryConfig()
    assert cfg.enabled and 500 <= cfg.max_anchor_chars <= 20000 and cfg.max_status_lines >= 1

    from alpha.config.app_config import AppConfig

    ac = AppConfig.from_file(str(Path(__file__).resolve().parents[2] / "config.example.yaml"))
    assert ac.mission_memory.enabled


# ---------------------------------------------------------------------------
# Tool behavior: record, verify, advance, status
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, thread_id: str) -> None:
        self.context = {"thread_id": thread_id}


@pytest.fixture()
def tool_env(tmp_path, monkeypatch):
    import alpha.config as alpha_config
    import alpha.tools.builtins.mission_memory_tool as tool_mod

    # Resolve owner + paths + config deterministically at the tool boundary.
    monkeypatch.setattr(tool_mod, "resolve_runtime_user_id", lambda runtime: "u1")
    monkeypatch.setattr(tool_mod, "get_paths", lambda: Paths(str(tmp_path)))
    monkeypatch.setattr(alpha_config, "get_app_config", lambda: SimpleNamespace(mission_memory=MissionMemoryConfig()))
    return SimpleNamespace(func=tool_mod.mission_memory.func, runtime=_FakeRuntime("t1"))


def test_tool_records_spec_plan_status_and_scratch(tool_env) -> None:
    f = tool_env.func
    rt = tool_env.runtime

    out = f(runtime=rt, action="set_spec", objective="Ship durable memory", constraints_json='["no API change"]', done_when_json='["tests green"]')
    assert out["success"] and out["objective"] == "Ship durable memory"

    out = f(runtime=rt, action="set_plan", milestones_json='[{"id":"m1","title":"Write","acceptance":"file exists","validation":"ls"},{"id":"m2","title":"Test","acceptance":"green","validation":"pytest -q"}]')
    assert out["success"] and out["milestone_total"] == 2 and out["current_milestone"] == "m1"

    assert f(runtime=rt, action="log", status="wrote the file")["success"]
    noted = f(runtime=rt, action="note", scratch="considered a cache")
    assert noted["success"] and noted["scratch_notes"] == 1

    status = f(runtime=rt, action="status")
    assert status["success"]
    assert status["objective"] == "Ship durable memory"
    assert status["has_plan"] and status["milestone_total"] == 2
    assert status["current_milestone"] == "m1"


def test_tool_persists_across_reload_and_advance_is_evidence_gated(tmp_path, tool_env) -> None:
    f = tool_env.func
    rt = tool_env.runtime
    assert f(runtime=rt, action="set_spec", objective="Reduce latency")["success"]
    assert f(runtime=rt, action="set_plan", milestones_json='[{"id":"m1","title":"A","acceptance":"a","validation":"cmd"},{"id":"m2","title":"B","acceptance":"b","validation":"cmd"}]')["success"]

    # advance refuses before verify (stop-and-fix)
    out = f(runtime=rt, action="advance")
    assert out["success"] is False and out["error"] == "invalid_plan" and "stop-and-fix" in out["reason"]

    # verify with a measured result, then advance
    assert f(runtime=rt, action="verify", milestone_id="m1", passed=True, evidence="exit_code=0")["success"]
    advanced = f(runtime=rt, action="advance")
    assert advanced["success"] and advanced["current_milestone"] == "m2"

    # a fresh reload from disk sees the same durable state
    mgr = MissionManager(Paths(str(tmp_path)))
    reloaded = mgr.load("u1", "t1")
    assert reloaded.plan.get("m1").status == "verified"
    assert reloaded.plan.active_index == 1


def test_tool_status_returns_anchor_and_never_claims_verification(tool_env) -> None:
    out = tool_env.func(runtime=tool_env.runtime, action="set_spec", objective="objective", done_when_json='["done"]')
    assert out["success"]
    anchor = out["anchor"]
    assert anchor.startswith("<system_memory") and "objective" in anchor and "done" in anchor
    assert "verified" not in anchor.lower()  # the anchor asserts no verdict


def test_tool_refuses_unknown_action_and_empty_plan(tool_env) -> None:
    assert tool_env.func(runtime=tool_env.runtime, action="nope")["error"] == "unknown_action"
    assert tool_env.func(runtime=tool_env.runtime, action="set_plan", milestones_json="[]")["error"] == "empty_plan"


def test_tool_verify_requires_a_plan(tool_env) -> None:
    assert tool_env.func(runtime=tool_env.runtime, action="verify", milestone_id="m1", passed=True)["error"] == "no_plan"


def test_tool_unresolved_scope_is_refused(tmp_path, monkeypatch) -> None:
    import alpha.tools.builtins.mission_memory_tool as tool_mod

    monkeypatch.setattr(tool_mod, "resolve_runtime_user_id", lambda runtime: None)
    out = tool_mod.mission_memory.func(runtime=_FakeRuntime("t1"), action="status")
    assert out == {"success": False, "error": "scope_unresolved"}


# ---------------------------------------------------------------------------
# Middleware: inject once, idempotent, fail-open
# ---------------------------------------------------------------------------


class _FakeRequest:
    def __init__(self, messages) -> None:
        self.messages = messages

    def override(self, *, messages):  # mimics ModelRequest.override
        return _FakeRequest(messages)


def _middleware(tmp_path, monkeypatch) -> object:
    import alpha.agents.middlewares.mission_memory_middleware as mw_mod

    monkeypatch.setattr(mw_mod, "get_paths", lambda: Paths(str(tmp_path)))
    cfg = SimpleNamespace(mission_memory=MissionMemoryConfig())
    mw = mw_mod.MissionMemoryMiddleware(app_config=cfg)
    mw._owner = "u1"
    mw._thread_id = "t1"
    return mw


def test_middleware_injects_anchor_then_is_idempotent(tmp_path, monkeypatch) -> None:
    MissionManager(Paths(str(tmp_path))).save("u1", "t1", MissionStack().with_spec(objective="steady the course"))
    mw = _middleware(tmp_path, monkeypatch)

    request = _FakeRequest([SystemMessage(content="sys")])
    out = mw._build_override(request)
    injected = [m for m in out.messages if isinstance(m.content, str) and "<system_memory" in m.content]
    assert injected and injected[0].additional_kwargs.get("hide_from_ui") is True
    assert injected[0].additional_kwargs.get("mission_memory_anchor") is True

    # A request that already carries the anchor is not injected again.
    again = mw._build_override(out)
    assert len([m for m in again.messages if isinstance(m.content, str) and "<system_memory" in m.content]) == 1


def test_middleware_fail_open_on_empty_and_corrupt(tmp_path, monkeypatch) -> None:
    # No mission file -> no injection.
    mw = _middleware(tmp_path, monkeypatch)
    request = _FakeRequest([SystemMessage(content="sys")])
    assert mw._build_override(request) is request

    # Corrupt file -> still no injection, never invented content.
    path = MissionManager(Paths(str(tmp_path)))._path("u1", "t1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{bad", encoding="utf-8")
    assert mw._build_override(request) is request
