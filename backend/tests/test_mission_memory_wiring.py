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


# ---------------------------------------------------------------------------
# Evidence-driven verify: reuse alpha.mission.acceptance collectors
# ---------------------------------------------------------------------------


def _seed_plan(tmp_path, tool_env) -> None:
    assert tool_env.func(runtime=tool_env.runtime, action="set_spec", objective="Ship it")["success"]
    assert tool_env.func(
        runtime=tool_env.runtime,
        action="set_plan",
        milestones_json='[{"id":"m1","title":"tests","acceptance":"green","validation":"pytest -q"},{"id":"m2","title":"Ship","acceptance":"artifact","validation":"file"}]',
    )["success"]


def test_tool_verify_from_a_real_test_exit_report(tmp_path, tool_env) -> None:
    _seed_plan(tmp_path, tool_env)
    report = tmp_path / "report.json"
    report.write_text('{"exit_code": 0, "passed": 12}', encoding="utf-8")
    out = tool_env.func(runtime=tool_env.runtime, action="verify", milestone_id="m1", evidence_kind="test_exit_report", evidence_source=str(report))
    assert out["success"] and out["verdict"] == "verified"
    assert "exit_code=0" in out["verified_evidence"] and "test_exit_report" in out["verified_evidence"]

    reloaded = MissionManager(Paths(str(tmp_path))).load("u1", "t1")
    assert reloaded.plan.get("m1").status == "verified"


def test_tool_verify_failing_report_marks_failed(tmp_path, tool_env) -> None:
    _seed_plan(tmp_path, tool_env)
    report = tmp_path / "report.json"
    report.write_text('{"exit_code": 1, "failed": 3}', encoding="utf-8")
    out = tool_env.func(runtime=tool_env.runtime, action="verify", milestone_id="m1", evidence_kind="test_exit_report", evidence_source=str(report))
    assert out["verdict"] == "failed" and "exit_code=1" in out["verified_evidence"]


def test_tool_verify_unmeasured_leaves_milestone_active(tmp_path, tool_env) -> None:
    _seed_plan(tmp_path, tool_env)
    # Missing report -> UNVERIFIED, milestone untouched, never a pass.
    out = tool_env.func(runtime=tool_env.runtime, action="verify", milestone_id="m1", evidence_kind="test_exit_report", evidence_source=str(tmp_path / "nope.json"))
    assert out["success"] and out["outcome"] == "unverified" and out["current_status"] == "active"
    reloaded = MissionManager(Paths(str(tmp_path))).load("u1", "t1")
    assert reloaded.plan.get("m1").status == "active"


def test_tool_verify_artifact_digest_and_escape_refused(tmp_path, tool_env) -> None:
    _seed_plan(tmp_path, tool_env)
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "page.html").write_text("<h1>done</h1>", encoding="utf-8")
    out = tool_env.func(runtime=tool_env.runtime, action="verify", milestone_id="m2", evidence_kind="artifact_digest", evidence_source="page.html", artifact_root=str(root))
    assert out["verdict"] == "verified" and "sha256=" in out["verified_evidence"]
    # A path escaping the confined root is refused, not followed.
    escaped = tool_env.func(runtime=tool_env.runtime, action="verify", milestone_id="m2", evidence_kind="artifact_digest", evidence_source="../../secret", artifact_root=str(root))
    assert escaped["success"] is False


def test_tool_verify_owner_assertion_still_records_but_is_labelled(tmp_path, tool_env) -> None:
    _seed_plan(tmp_path, tool_env)
    out = tool_env.func(runtime=tool_env.runtime, action="verify", milestone_id="m1", passed=True, evidence="looks good")
    assert out["verdict"] == "verified"
    assert "observed_fact:owner-assertion" in out["verified_evidence"]  # provenance, not a measured check


# ---------------------------------------------------------------------------
# Steering, resume checkpoint, reject/defer: Codex mid-flight steering + the
# fix for the documented compaction restart loop.
# ---------------------------------------------------------------------------


def test_tool_steer_and_checkpoint_persist_across_reload(tmp_path, tool_env) -> None:
    f = tool_env.func
    assert f(runtime=tool_env.runtime, action="set_spec", objective="Ship it")["success"]
    assert f(runtime=tool_env.runtime, action="steer", steer="prioritize the happy path")["success"]
    assert f(runtime=tool_env.runtime, action="reject", reject="inline styles regress a11y")["success"]
    assert f(runtime=tool_env.runtime, action="defer", defer="dark mode")["success"]
    cp = f(runtime=tool_env.runtime, action="checkpoint", checkpoint_json='{"current_phase":"writing tests","next_action":"run pytest","do_not_repeat":"weaken assertions"}')
    assert cp["success"]
    reloaded = MissionManager(Paths(str(tmp_path))).load("u1", "t1")
    assert reloaded.steers == ("prioritize the happy path",)
    assert reloaded.rejected == ("inline styles regress a11y",)
    assert reloaded.deferred == ("dark mode",)
    assert reloaded.checkpoint is not None and reloaded.checkpoint.next_action == "run pytest"


def test_tool_brief_returns_the_resume_checkpoint_without_side_effects(tmp_path, tool_env) -> None:
    assert tool_env.func(runtime=tool_env.runtime, action="set_spec", objective="Ship it")["success"]
    assert tool_env.func(runtime=tool_env.runtime, action="steer", steer="correctness first")["success"]
    brief = tool_env.func(runtime=tool_env.runtime, action="brief")
    assert brief["success"] and brief["action"] == "brief"
    assert brief["steers"] == ["correctness first"]
    assert brief["anchor"].startswith("<system_memory")


def test_tool_checkpoint_rejects_non_object_json(tmp_path, tool_env) -> None:
    assert tool_env.func(runtime=tool_env.runtime, action="set_spec", objective="x")["success"]
    assert tool_env.func(runtime=tool_env.runtime, action="checkpoint", checkpoint_json="[1,2]")["error"] == "invalid_json"


def test_middleware_surfaces_steer_and_resume_checkpoint(tmp_path, monkeypatch) -> None:
    from alpha.runtime.missions import ResumeCheckpoint

    stack = MissionStack().with_spec(objective="steady the course").steer("prioritize correctness").with_checkpoint(ResumeCheckpoint(current_phase="testing", next_action="run pytest", do_not_repeat="weaken the suite"))
    MissionManager(Paths(str(tmp_path))).save("u1", "t1", stack)
    mw = _middleware(tmp_path, monkeypatch)
    overridden = mw._build_override(_FakeRequest([SystemMessage(content="sys")]))
    body = [m.content for m in overridden.messages if isinstance(m.content, str) and "<system_memory" in m.content][0]
    assert "## Operator steer" in body and "prioritize correctness" in body
    assert "## Resume checkpoint" in body and "run pytest" in body
    assert "Do not repeat: weaken the suite" in body  # the checkpoint's own do-not-repeat survives
