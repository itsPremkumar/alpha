"""Automated tests for Concrete Backend Slash Command Handlers.

Verifies:
1. /skill:create, /skill:list, /skill:test against real SkillStorage — /skill:test
   derives `passed` from real executed checks (exists / frontmatter validator /
   container path / SkillScan secret audit); broken frontmatter => passed False
2. /loop:start, /loop:pause, /loop:resume REFUSE by name (the legacy surface is
   retired and no worker dispatched those records); /loop:status stays read-only
   and reports every record as retired and never verified. The retirement stamp
   is `goal_engine action="retire"`, which deletes nothing. /goal create is
   deliberately NOT retired — it is the natural-language trigger that emits a
   directive for the run already in flight, not a loop mutator — but it no
   longer mints a goal record
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
from alpha.commands.catalog import get_default_catalog_entries
from app.gateway.app import create_app


@pytest.fixture(autouse=True)
def _isolated_lifecycle_home(tmp_path, monkeypatch):
    """Keep spawn records and skill writes out of the developer's real home.

    The lifecycle manager persists every spawn to disk and reloads them on
    construction; without isolation, repeated runs accumulate RUNNING records
    until the per-parent cap rejects new spawns. Skill writes
    (/skill:create) and reads (/skill:list, /skill:test, the doctor skills
    probe) are redirected to a temp skills root so tests never mutate the
    repository's skills/ tree (ALPHA_HOME alone does not redirect
    the skills path — SkillsConfig reads ALPHA_SKILLS_PATH).
    """
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    skills_root = tmp_path / "skills"
    (skills_root / "custom").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALPHA_SKILLS_PATH", str(skills_root))
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


def test_loop_mutation_handlers_refuse_the_retired_surface(monkeypatch, tmp_path):
    """`/loop:start`, `/loop:pause` and `/loop:resume` must refuse by name.

    Nothing dispatches these records, so every one of these verbs used to write
    a row that read like launched work — a resume persisted `executing` and
    reported success with no worker behind it. The refusal has to say *where to
    go instead*; a bare "unsupported" leaves the caller nowhere.
    """
    from alpha.harness.continuous.models import LEGACY_MIGRATION_GUIDANCE, LEGACY_RETIREMENT_REASON
    from alpha.harness.continuous.store import GoalStore

    store = GoalStore(storage_path=tmp_path / "goals.json")
    existing = store.create_goal(title="Pre-existing record")
    store.update_goal_status(existing.goal_id, "executing", strategy_note="Worker claimed it.")
    monkeypatch.setattr("alpha.harness.continuous.store.get_goal_store", lambda: store)

    res_start = command_registry.execute("/loop:start Deploy high-availability Kubernetes cluster")
    assert res_start.status == "error"
    assert "/loop:start is retired." in res_start.output
    assert LEGACY_RETIREMENT_REASON in res_start.output
    assert LEGACY_MIGRATION_GUIDANCE in res_start.output
    assert "No goal record was created." in res_start.output
    assert res_start.data == {"retired": True, "created": False, "migration": LEGACY_MIGRATION_GUIDANCE}
    # A refused start must leave the store exactly as it found it.
    assert [g.goal_id for g in store.list_goals()] == [existing.goal_id]

    for handler, command in ((bh.handle_loop_pause, "/loop:pause"), (bh.handle_loop_resume, "/loop:resume")):
        result = handler(existing.goal_id)
        assert result.status == "error"
        assert f"{command} is retired." in result.output
        assert LEGACY_MIGRATION_GUIDANCE in result.output
        assert result.data["changed"] is False
        assert result.data["goal_id"] == existing.goal_id
        # The lie this retirement removes: `executing` written with no worker.
        assert store.get_goal(existing.goal_id).status == "executing"

    # The no-argument form refuses too, and names no target it could have meant.
    ambiguous = bh.handle_loop_pause("")
    assert ambiguous.status == "error"
    assert ambiguous.data["goal_id"] is None


def test_retirement_is_not_published_as_a_slash_command():
    """The stamp is a tool action, not a conversation verb.

    A new `/loop:*` row would have to be published in the command catalog, and
    `tests/test_discovery_plane_parity.py` holds every handler binding to a real
    catalog row — registering it without publishing it leaks an unpublished alias.
    The capability stays reachable through `goal_engine action="retire"`.
    """
    published = {row[0] for row in get_default_catalog_entries()}
    assert not any(name.startswith("/loop") and "retire" in name for name in published)

    # ...and nothing binds a `/loop ... retire` name to a handler either, so the
    # command cannot be reached by typing it even though the function is gone.
    for name in ("/loop:retire", "/loop retire"):
        definition, _args = command_registry.find_command(name)
        assert definition is None or not definition.command.startswith("/loop retire")


def test_goal_create_still_directs_the_active_run_without_creating_a_record():
    """`/goal create` is the NL trigger, not the retired loop surface.

    It never dispatched a worker — it emitted an instruction for the run already
    in flight — so it stays. What it must not do any more is mint a legacy goal
    record, because that is what made "a goal was created" read as "work
    started".
    """
    from alpha.harness.continuous.models import LEGACY_MIGRATION_GUIDANCE

    res = command_registry.execute("/goal create Build Mars Rover")

    assert res.status == "success"
    assert res.command == "/goal create"
    assert res.data["created_goal_record"] is False
    assert res.data["is_autonomous_trigger"] is True
    assert res.data["migration"] == LEGACY_MIGRATION_GUIDANCE
    assert res.autonomous_directives
    assert "Build Mars Rover" in res.autonomous_directives[0]
    assert "not evidence that anything ran" in res.output


def test_loop_status_reports_every_record_as_retired_and_never_verified(monkeypatch, tmp_path):
    """The read half of the retirement.

    54 records still carrying `executing` used to render as "54 active" — the
    exact impression of a fleet of running loops behind a store nothing polls.
    They now render as recorded-status history on retired records.
    """
    from alpha.harness.continuous.models import LEGACY_MIGRATION_GUIDANCE, LEGACY_RETIREMENT_REASON
    from alpha.harness.continuous.store import GoalStore

    store = GoalStore(storage_path=tmp_path / "goals.json")
    goals = [store.create_goal(title=f"Goal {index}") for index in range(55)]
    for goal in goals[:-1]:
        store.update_goal_status(goal.goal_id, "executing")
    store.add_milestone(goals[-1].goal_id, "Inspect target goal")
    store.update_goal_status(goals[-1].goal_id, "paused")
    monkeypatch.setattr("alpha.harness.continuous.store.get_goal_store", lambda: store)

    result = bh.handle_loop_status("")

    assert result.status == "success"
    assert result.data["active_loops"] == 0
    assert result.data["retired_goals"] == 55
    assert result.data["total_goals"] == 55
    assert result.data["status_counts"] == {"executing": 54, "paused": 1}
    assert len(result.data["goals"]) == 10
    assert result.data["omitted_goals"] == 45
    assert result.data["retirement_reason"] == LEGACY_RETIREMENT_REASON
    assert result.data["migration"] == LEGACY_MIGRATION_GUIDANCE
    assert "0 active" in result.output
    assert "legacy loop surface is retired" in result.output
    assert "None of these records is evidence" in result.output
    assert "45 additional goal(s) omitted" in result.output
    assert "Analyze environment and dependencies" not in result.output
    # Every projected row is retired, and none of them claims verification.
    for view in result.data["goals"]:
        assert view["status"] == "retired"
        assert view["verified"] == "never"

    detail = bh.handle_loop_status(goals[-1].goal_id)
    assert detail.status == "success"
    assert detail.data["goal_id"] == goals[-1].goal_id
    assert detail.data["retired"] is True
    assert detail.data["verified"] == "never"
    assert "Recorded status: `paused` | Verified: never" in detail.output
    assert "Inspect target goal [unverified; recorded `pending`]" in detail.output

    missing = bh.handle_loop_status("goal_missing")
    assert missing.status == "error"
    assert missing.data == {"goal_id": "goal_missing", "status": "not_found"}


def test_loop_status_reports_unknown_rather_than_empty_for_an_unreadable_store(monkeypatch, tmp_path):
    from alpha.harness.continuous.store import GoalStore

    corrupt_path = tmp_path / "goals.json"
    corrupt_path.write_text("{not json", encoding="utf-8")
    store = GoalStore(storage_path=corrupt_path)
    monkeypatch.setattr("alpha.harness.continuous.store.get_goal_store", lambda: store)

    result = bh.handle_loop_status("")

    assert result.status == "error"
    assert result.data["reported"] is False
    assert result.data["goals"] == []
    assert "Goal state is UNKNOWN, not empty" in result.output


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
