"""Automated tests for Concrete Backend Slash Command Handlers.

Verifies:
1. /skill:create, /skill:list, /skill:test against real SkillStorage — /skill:test
   derives `passed` from real executed checks (exists / frontmatter validator /
   container path / SkillScan secret audit); broken frontmatter => passed False
2. /loop:start, /loop:status, /loop:pause, /loop:resume against ContinuousGoalRunner
3. /goal:decompose against CognitiveMetaPlanner
4. /subagent:spawn, /subagent:list against SubagentLifecycleManager
5. /doctor, /security-review, /compact run their real in-process probes against
   the loaded configuration, skill registry, safety guards and scanners (not a
   canned "system runtime" verdict): the overall status/`secure` flag is derived
   from individual probe outcomes, an injected failing or unknown check forces a
   non-ready / secure=false verdict, and /compact honestly reports that it did
   not run any compaction
6. Gateway API dispatch execution via TestClient
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import alpha.commands.backend_handlers as bh
from alpha.commands import command_registry
from app.gateway.app import create_app


@pytest.fixture(autouse=True)
def _isolated_lifecycle_home(tmp_path, monkeypatch):
    """Keep spawn records and skill writes out of the developer's real home.

    The lifecycle manager persists every spawn to disk and reloads them on
    construction; without isolation, repeated runs accumulate RUNNING records
    until the per-parent cap rejects new spawns. Skill writes
    (/skill:create) and reads (/skill:list, /skill:test, the doctor skills
    probe) are redirected to a temp skills root so tests never mutate the
    repository's skills/ tree (AGENT_WORKSPACE_HOME alone does not redirect
    the skills path — SkillsConfig reads AGENT_WORKSPACE_SKILLS_PATH).
    """
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    skills_root = tmp_path / "skills"
    (skills_root / "custom").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENT_WORKSPACE_SKILLS_PATH", str(skills_root))
    import alpha.subagents.lifecycle as lifecycle_mod

    monkeypatch.setattr(lifecycle_mod, "_GLOBAL_LIFECYCLE_MANAGER", None)
    yield


def test_skill_creator_handlers():
    # 1. Create skill
    res_create = command_registry.execute("/skill:create test-parser description: Extract structured invoice data from scanned documents")
    assert res_create.status == "success"
    assert "test-parser" in res_create.output
    assert res_create.data.get("skill_name") == "test-parser"

    # 2. List skills
    res_list = command_registry.execute("/skill:list")
    assert res_list.status == "success"
    assert res_list.data.get("total", 0) > 0

    # 3. Test skill validity — `passed` must be derived from the per-check
    # evidence, not hardcoded. The created skill's frontmatter is validator-clean,
    # so all real checks pass.
    res_test = command_registry.execute("/skill:test test-parser")
    assert res_test.status == "success"
    checks = res_test.data.get("checks") or []
    assert checks, "skill:test must return per-check evidence"
    for check in checks:
        assert check["name"]
        assert check["detail"], f"check {check['name']!r} must carry evidence"
        assert check["state"] in ("pass", "fail", "error", "unknown")
    assert res_test.data.get("passed") == all(c["ok"] for c in checks)
    assert res_test.data.get("passed") is True
    assert "Skill is healthy and ready for autonomous invocation." in res_test.output


def test_skill_test_broken_frontmatter_reports_failure():
    """A skill with validator-rejected frontmatter must fail honestly.

    The loader tolerates unknown frontmatter keys, so this skill loads into the
    registry — but the real frontmatter validator rejects it, and /skill:test
    must report passed=False and must not claim the skill is healthy.
    """
    from alpha.skills.storage import get_or_new_skill_storage, reset_skill_storage

    storage = get_or_new_skill_storage()
    broken_content = "---\nname: broken-probe\ndescription: Loads fine because name and description are present.\nunexpected-key: not in the allowed frontmatter set\n---\nBody text.\n"
    storage.write_custom_skill("broken-probe", "SKILL.md", broken_content)
    reset_skill_storage()

    res = command_registry.execute("/skill:test broken-probe")
    assert res.status == "success"
    checks = {c["name"]: c for c in res.data["checks"]}
    assert "Valid frontmatter schema" in checks
    assert checks["Valid frontmatter schema"]["ok"] is False
    assert checks["Valid frontmatter schema"]["detail"]
    assert res.data.get("passed") is False
    assert "Skill is healthy and ready for autonomous invocation." not in res.output
    assert "Skill test FAILED" in res.output
    assert "[FAIL] Valid frontmatter schema" in res.output


def test_loop_handlers():
    # 1. Start loop
    res_start = command_registry.execute("/loop:start Deploy high-availability Kubernetes cluster")
    assert res_start.status == "success"
    goal_id = res_start.data.get("goal_id")
    assert goal_id is not None
    assert "Continuous Autonomous Loop active" in res_start.output

    # 2. Loop status
    res_status = command_registry.execute("/loop:status")
    assert res_status.status == "success"
    assert res_status.data.get("active_loops", 0) > 0

    # 3. Pause loop
    res_pause = command_registry.execute("/loop:pause")
    assert res_pause.status == "success"

    # 4. Resume loop
    res_resume = command_registry.execute("/loop:resume")
    assert res_resume.status == "success"


def test_goal_decompose_handler():
    res_decomp = command_registry.execute("/goal:decompose Build an automated trading execution engine")
    assert res_decomp.status == "success"
    assert "Autonomous Goal Decomposition" in res_decomp.output
    assert "decision" in res_decomp.data
    assert "execution_waves" in res_decomp.data


def test_subagent_handlers():
    # 1. Spawn subagent
    res_spawn = command_registry.execute("/subagent:spawn security-auditor Inspect smart contract bytecode")
    assert res_spawn.status == "success"
    subagent_id = res_spawn.data.get("subagent_id")
    assert subagent_id is not None
    assert "Subagent spawned successfully" in res_spawn.output

    # 2. List subagents
    res_list = command_registry.execute("/subagent:list")
    assert res_list.status == "success"
    assert res_list.data.get("total", 0) > 0


def test_doctor_status_derivation_rules():
    """Pin the derivation rules directly: ready only if every check passed."""
    assert bh._derive_doctor_status([{"name": "a", "ok": True, "state": "pass", "detail": "x"}]) == "ready"
    assert bh._derive_doctor_status([{"name": "a", "ok": True, "state": "pass", "detail": "x"}, {"name": "b", "ok": False, "state": "fail", "detail": "y"}]) == "not_ready"
    assert bh._derive_doctor_status([{"name": "b", "ok": False, "state": "error", "detail": "y"}]) == "not_ready"
    # unknown must not count as a pass for READY
    assert bh._derive_doctor_status([{"name": "a", "ok": True, "state": "pass", "detail": "x"}, {"name": "u", "ok": False, "state": "unknown", "detail": "not run"}]) == "degraded"
    assert bh._derive_doctor_status([]) != "ready"


def test_doctor_derives_status_from_real_checks():
    res_doc = command_registry.execute("/doctor")
    assert res_doc.status == "success"
    assert "Alpha System Doctor" in res_doc.output
    checks = res_doc.data.get("checks") or []
    assert checks, "doctor must run and report real checks"
    for check in checks:
        assert check["name"]
        assert check["detail"], f"check {check['name']!r} must carry evidence"
        assert check["state"] in ("pass", "fail", "error", "unknown")
    # The overall status must be derived from the individual outcomes.
    assert res_doc.data.get("status") == bh._derive_doctor_status(checks)
    all_pass = all(c["ok"] and c["state"] == "pass" for c in checks)
    assert ("System Status: READY" in res_doc.output) is all_pass
    if all_pass:
        assert res_doc.data.get("status") == "ready"
    else:
        assert res_doc.data.get("status") != "ready"
        # Failures/unknowns must be listed, not hidden.
        assert res_doc.data.get("failed") or res_doc.data.get("unknown")


def test_doctor_failing_check_forces_not_ready(monkeypatch):
    original = bh._run_doctor_checks

    def with_failure():
        checks = original()
        checks.append({"name": "Injected Failure", "ok": False, "state": "fail", "detail": "deliberately failing probe"})
        return checks

    monkeypatch.setattr(bh, "_run_doctor_checks", with_failure)
    res = command_registry.execute("/doctor")
    assert res.status == "success"
    assert res.data.get("status") != "ready"
    assert "Injected Failure" in res.data.get("failed", [])
    assert "Injected Failure" in res.output
    assert "System Status: READY" not in res.output


def test_doctor_unknown_check_never_counts_as_ready(monkeypatch):
    original = bh._run_doctor_checks

    def with_unknown():
        checks = original()
        checks.append({"name": "Injected Unknown", "ok": False, "state": "unknown", "detail": "not run: probe unavailable"})
        return checks

    monkeypatch.setattr(bh, "_run_doctor_checks", with_unknown)
    res = command_registry.execute("/doctor")
    assert res.status == "success"
    assert res.data.get("status") != "ready"
    assert "Injected Unknown" in res.data.get("unknown", [])
    assert "System Status: READY" not in res.output


def test_security_review_derives_secure_from_executed_checks():
    res_sec = command_registry.execute("/security-review")
    assert res_sec.status == "success"
    assert "Security Review Gate" in res_sec.output
    checks = res_sec.data.get("checks") or []
    assert checks, "security-review must execute real checks"
    for check in checks:
        assert check["name"]
        assert check["detail"], f"check {check['name']!r} must carry evidence"
        assert check["state"] in ("pass", "fail", "error", "unknown")
    # `secure` is derived from the evidence, never asserted independently of it.
    assert res_sec.data.get("secure") == bh._derive_secure(checks)
    # The old fabricated verdict text must be gone.
    assert "All security invariants verified" not in res_sec.output
    assert "No privilege leaks detected" not in res_sec.output


def test_security_review_injected_failing_check_forces_secure_false(monkeypatch):
    original = bh._run_security_review_checks

    def with_failure():
        checks = original()
        checks.append({"name": "Injected Probe", "ok": False, "state": "fail", "detail": "deliberately failing probe"})
        return checks

    monkeypatch.setattr(bh, "_run_security_review_checks", with_failure)
    res = command_registry.execute("/security-review")
    assert res.status == "success"
    assert res.data.get("secure") is False
    failing = [c for c in res.data["checks"] if not c["ok"]]
    assert any(c["name"] == "Injected Probe" for c in failing)
    assert "[FAIL] Injected Probe" in res.output
    assert "secure=false" in res.output


def test_compact_reports_honest_not_implemented():
    """No compaction runs in this handler, so none may be claimed."""
    res_comp = command_registry.execute("/compact")
    assert res_comp.status == "error"
    assert res_comp.data.get("completed") is False
    assert res_comp.data.get("implemented") is False
    assert "not implemented" in res_comp.data.get("reason", "")
    assert "POST /api/threads/{thread_id}/compact" in res_comp.data.get("reason", "")
    # The old fabricated claim must be gone.
    assert "summarized and preserved" not in res_comp.output
    assert not res_comp.autonomous_directives


def test_gateway_backend_dispatch():
    app = create_app()
    client = TestClient(app)

    # Dispatch /skill:list through HTTP
    resp = client.post("/api/commands/execute", json={"command": "/skill:list"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "total" in data["data"]

    # Dispatch /doctor through HTTP — the overall status must be derived from
    # the checks that actually ran, whatever the environment looks like.
    resp_doc = client.post("/api/commands/execute", json={"command": "/doctor"})
    assert resp_doc.status_code == 200
    data_doc = resp_doc.json()
    assert data_doc["status"] == "success"
    checks = data_doc["data"]["checks"]
    assert checks
    assert data_doc["data"]["status"] == bh._derive_doctor_status(checks)
    all_pass = all(c["ok"] and c["state"] == "pass" for c in checks)
    if all_pass:
        assert data_doc["data"]["status"] == "ready"
    else:
        assert data_doc["data"]["status"] != "ready"
