"""Comprehensive test suite for the Autonomous Sub-Agent Control Plane Subsystem.

Validates:
1. Asynchronous subagent provisioning with time-bounded leases and recursion limits.
2. Heartbeat emission, lease renewal, and real-time stall detection.
3. Step checkpointing and hot-replacement restoring state from failed workers.
4. Parent crash resilience: orphan queue registration and supervisor adoption.
5. Specialist archetypes (Critic, Judge, Red-Team, Verifier) and dynamic role generation.
6. Execution metrics tracking and automated promotion to permanent Hermes Bots.
7. Built-in subagent_control tool actions.
8. Gateway REST endpoints.
9. Strict architectural boundary firewall (zero imports from app.*).
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import alpha.subagents.lifecycle as life_mod
import alpha.subagents.promotion as prom_mod
import alpha.subagents.resilience as res_mod
from alpha.subagents.lifecycle import (
    SubagentContract,
    SubagentLifecycleManager,
    SubagentStatusEnum,
)
from alpha.subagents.promotion import SubagentPromotionManager
from alpha.subagents.resilience import SubagentResilienceEngine
from alpha.subagents.specialists import (
    SpecialistRoleArchetype,
    generate_dynamic_role,
    get_archetype_template,
)
from alpha.tools.builtins.subagent_control_tool import subagent_control


@pytest.fixture(autouse=True)
def _isolated_subagent_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    life_mod._GLOBAL_LIFECYCLE_MANAGER = None
    res_mod._GLOBAL_RESILIENCE_ENGINE = None
    prom_mod._GLOBAL_PROMOTION_MANAGER = None
    yield
    life_mod._GLOBAL_LIFECYCLE_MANAGER = None
    res_mod._GLOBAL_RESILIENCE_ENGINE = None
    prom_mod._GLOBAL_PROMOTION_MANAGER = None


# 1. Lifecycle, Leases & Hierarchy Limits
def test_subagent_async_spawn_and_hierarchy_limits():
    mgr = SubagentLifecycleManager()
    contract = SubagentContract(objective="Verify SQL indexes", lease_duration_seconds=30)

    # Valid spawn
    rec = mgr.spawn_subagent(parent_agent_id="parent-bot", contract=contract, depth=1)
    assert rec.subagent_id.startswith("sub-")
    assert rec.status == SubagentStatusEnum.READY
    assert rec.lease.is_valid()
    assert rec.depth == 1

    # Max depth enforcement
    with pytest.raises(ValueError, match="recursion depth"):
        mgr.spawn_subagent(parent_agent_id="parent-bot", contract=contract, depth=4)

    # Max active children enforcement (limit is 10)
    for _ in range(9):
        mgr.spawn_subagent(parent_agent_id="parent-bot", contract=contract, depth=1)

    with pytest.raises(ValueError, match="already has 10 active subagents"):
        mgr.spawn_subagent(parent_agent_id="parent-bot", contract=contract, depth=1)


# 2. Heartbeats, Leases & Stall Detection
def test_heartbeat_lease_renewal_and_stall_detection():
    mgr = SubagentLifecycleManager()
    contract = SubagentContract(objective="Long research loop", lease_duration_seconds=10)
    rec = mgr.spawn_subagent(parent_agent_id="lead", contract=contract)
    mgr.start_subagent(rec.subagent_id)

    # Record heartbeat
    ok = mgr.record_heartbeat(
        rec.subagent_id,
        current_action="Querying arXiv API",
        progress_percent=35.0,
        last_tool="browser",
        tokens_used=1200,
    )
    assert ok is True
    updated = mgr.get_subagent(rec.subagent_id)
    assert updated.last_heartbeat is not None
    assert updated.last_heartbeat.progress_percent == 35.0
    assert updated.lease.renew_count >= 1

    # Simulate expired lease
    updated.lease.expires_at = time.time() - 10.0
    report = mgr.check_liveness_and_stalls()
    assert report["stalled_count"] == 1
    assert rec.subagent_id in report["stalled_ids"]
    assert mgr.get_subagent(rec.subagent_id).status == SubagentStatusEnum.STALLED


# 3. Recursive Cancellation & Pause/Resume Propagation
def test_recursive_cancellation_and_propagation():
    mgr = SubagentLifecycleManager()
    contract = SubagentContract(objective="Parent task")
    parent_rec = mgr.spawn_subagent("root", contract, depth=1)
    mgr.start_subagent(parent_rec.subagent_id)

    child_rec = mgr.spawn_subagent(parent_rec.subagent_id, SubagentContract(objective="Child 1"), depth=2)
    mgr.start_subagent(child_rec.subagent_id)

    # Pause propagation
    mgr.pause_subagent(parent_rec.subagent_id)
    assert mgr.get_subagent(parent_rec.subagent_id).status == SubagentStatusEnum.WAITING
    assert mgr.get_subagent(child_rec.subagent_id).status == SubagentStatusEnum.WAITING

    # Resume propagation
    mgr.resume_subagent(parent_rec.subagent_id)
    assert mgr.get_subagent(parent_rec.subagent_id).status == SubagentStatusEnum.RUNNING
    assert mgr.get_subagent(child_rec.subagent_id).status == SubagentStatusEnum.RUNNING

    # Cancel propagation
    mgr.cancel_subagent(parent_rec.subagent_id, reason="Mission aborted")
    assert mgr.get_subagent(parent_rec.subagent_id).status == SubagentStatusEnum.CANCELLED
    assert mgr.get_subagent(child_rec.subagent_id).status == SubagentStatusEnum.CANCELLED


# 4. Step Checkpointing and Hot-Replacement
def test_checkpointing_and_hot_replacement():
    mgr = SubagentLifecycleManager()
    resilience = SubagentResilienceEngine(mgr)

    contract = SubagentContract(objective="Migrate database tables")
    rec = mgr.spawn_subagent("db-lead", contract)
    mgr.start_subagent(rec.subagent_id)

    # Save step checkpoint
    cp = resilience.save_checkpoint(
        subagent_id=rec.subagent_id,
        step_index=3,
        intermediate_artifacts=["migration_001.sql"],
        decisions=[{"table": "users", "action": "column_renamed"}],
        evidence=[{"check": "schema_diff_clean"}],
        next_action="Run dry-run migration on staging",
    )
    assert cp.step_index == 3
    assert resilience.get_latest_checkpoint(rec.subagent_id).step_index == 3

    # Hot-replace worker
    replacement = resilience.hot_replace_subagent(rec.subagent_id, reason="Worker OOM crash")
    assert replacement.subagent_id != rec.subagent_id
    assert replacement.parent_agent_id == "db-lead"
    assert "restored_checkpoint" in replacement.contract.injected_context
    assert replacement.contract.injected_context["restored_checkpoint"]["step_index"] == 3
    assert mgr.get_subagent(rec.subagent_id).status == SubagentStatusEnum.FAILED


# 5. Parent Crash Recovery and Orphan Adoption
def test_parent_failure_and_orphan_adoption():
    mgr = SubagentLifecycleManager()
    resilience = SubagentResilienceEngine(mgr)

    # Spawn 2 children under a parent that will die
    c1 = mgr.spawn_subagent("dying-parent", SubagentContract(objective="Child 1", survival_policy="transfer_on_parent_failure"))
    c2 = mgr.spawn_subagent("dying-parent", SubagentContract(objective="Child 2", survival_policy="cancel_on_parent_failure"))
    mgr.start_subagent(c1.subagent_id)
    mgr.start_subagent(c2.subagent_id)

    # Parent dies
    report = resilience.handle_parent_failure("dying-parent")
    assert c1.subagent_id in report["orphaned_subagents"]
    assert c2.subagent_id in report["cancelled_subagents"]
    assert mgr.get_subagent(c1.subagent_id).is_orphaned is True
    assert mgr.get_subagent(c2.subagent_id).status == SubagentStatusEnum.CANCELLED

    # Org Supervisor adopts orphaned child
    adopted = resilience.adopt_orphaned_subagents(new_parent_id="org-supervisor-bot")
    assert c1.subagent_id in adopted
    c1_updated = mgr.get_subagent(c1.subagent_id)
    assert c1_updated.parent_agent_id == "org-supervisor-bot"
    assert c1_updated.is_orphaned is False


# 6. Specialist Archetypes and Auto-Role Generation
def test_specialist_archetypes_and_dynamic_roles():
    critic = get_archetype_template(SpecialistRoleArchetype.CRITIC)
    assert critic.role_title == "Artifact & Quality Critic"
    assert critic.model_tier == "frontier"

    # Dynamic role generation from prompt
    sec_contract = generate_dynamic_role("Perform security code audit looking for token leaks and CVE vulnerabilities")
    assert sec_contract.role == "security_auditor"
    assert "astra_security_manage" in sec_contract.tools

    judge_contract = generate_dynamic_role("Compare debate outputs and judge the superior architectural consensus")
    assert judge_contract.role == "judge"

    code_contract = generate_dynamic_role("Implement backend REST endpoint in Python and write unit tests")
    assert code_contract.role == "coder"
    assert code_contract.workspace_mode == "git_worktree"


# 7. Sub-Agent Promotion to Permanent Hermes Bot
def test_subagent_promotion_to_hermes_bot():
    prom = SubagentPromotionManager()

    # Initially ineligible (< 5 executions)
    for _ in range(4):
        prom.record_execution(
            role="postgres_optimizer",
            success=True,
            runtime_seconds=12.0,
            instruction="Optimize query plan",
            tools=["sql_explain", "index_advisor"],
        )

    eligible, metrics = prom.check_promotion_eligibility("postgres_optimizer")
    assert eligible is False
    assert metrics["total_executions"] == 4

    # 5th execution reaches eligibility threshold
    prom.record_execution(role="postgres_optimizer", success=True, runtime_seconds=8.0)
    eligible, metrics = prom.check_promotion_eligibility("postgres_optimizer")
    assert eligible is True
    assert metrics["reliability"] == 1.0

    # Promote to Permanent Hermes Bot
    bot = prom.promote_to_hermes_bot("postgres_optimizer", bot_name="bot-postgres-optimizer")
    assert bot["name"] == "bot-postgres-optimizer"
    assert bot["origin"] == "promoted_subagent"
    assert "sql_explain" in bot["tools"]


# 8. Built-in Agent Tool `subagent_control`
def test_subagent_control_tool_invocation():
    # Archetypes action
    arch_res = subagent_control.invoke({"action": "archetypes"})
    assert "Available Specialist Sub-Agent Archetypes" in arch_res
    assert "critic" in arch_res

    # Spawn action
    spawn_res = subagent_control.invoke(
        {
            "action": "spawn",
            "parent_agent_id": "architect-bot",
            "objective": "Audit authentication invariants",
            "role": "critic",
        }
    )
    assert "Sub-Agent Provisioned Asynchronously" in spawn_res

    # Extract subagent ID
    import re

    sid_match = re.search(r"Sub-Agent ID\*\*: `(sub-[0-9a-f]+)`", spawn_res)
    assert sid_match is not None
    sid = sid_match.group(1)

    # Status action
    stat_res = subagent_control.invoke({"action": "status", "subagent_id": sid})
    assert f"Sub-Agent Status: `{sid}`" in stat_res
    assert "Lease Valid" in stat_res and "`YES`" in stat_res

    # Heartbeat action
    hb_res = subagent_control.invoke(
        {
            "action": "heartbeat",
            "subagent_id": sid,
            "progress_percent": 50.0,
            "current_action": "Critiquing auth token schema",
        }
    )
    assert "Heartbeat recorded" in hb_res

    # Checkpoint action
    cp_res = subagent_control.invoke(
        {
            "action": "checkpoint",
            "subagent_id": sid,
            "step_index": 2,
        }
    )
    assert "saved for" in cp_res

    # Replace action
    replace_res = subagent_control.invoke(
        {
            "action": "replace",
            "subagent_id": sid,
            "reason": "Simulated hardware fault",
        }
    )
    assert "Hot-Replacement Completed" in replace_res


# 9. Gateway REST Endpoints
@pytest.mark.asyncio
async def test_gateway_subagent_control_router():
    from app.gateway.routers import subagent_control as ctrl_router

    admin_req = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(system_role="admin")))

    # 1. Spawn endpoint
    spawn_resp = await ctrl_router.spawn_subagent(
        ctrl_router.SpawnSubagentRequest(
            parent_agent_id="ceo-bot",
            objective="Deep research LLM memory retention",
            role="researcher",
        ),
        admin_req,
    )
    sid = spawn_resp["subagent_id"]
    assert sid.startswith("sub-")
    assert spawn_resp["status"] == "ready"

    # 2. Get details
    detail_resp = await ctrl_router.get_subagent_details(sid)
    assert detail_resp["subagent_id"] == sid
    assert detail_resp["parent_agent_id"] == "ceo-bot"

    # 3. Heartbeat endpoint
    hb_resp = await ctrl_router.record_subagent_heartbeat(
        sid,
        ctrl_router.SubagentHeartbeatRequest(current_action="Reading papers", progress_percent=40.0),
    )
    assert hb_resp["status"] == "heartbeat_recorded"

    # 4. Checkpoint endpoint
    cp_resp = await ctrl_router.save_subagent_checkpoint(
        sid,
        ctrl_router.SubagentCheckpointRequest(step_index=1, intermediate_artifacts=["notes.md"]),
        admin_req,
    )
    assert cp_resp["step_index"] == 1

    # 5. Hot-replace endpoint
    rep_resp = await ctrl_router.replace_subagent(
        sid,
        ctrl_router.SubagentReplaceRequest(reason="Process stalled"),
        admin_req,
    )
    assert rep_resp["replaced_worker_id"] == sid
    assert rep_resp["new_worker"]["subagent_id"] != sid

    # 6. Promotion endpoint
    prom_resp = await ctrl_router.promote_subagent_role(
        ctrl_router.SubagentPromoteRequest(role="security_auditor", bot_name="bot-sec-audit"),
        admin_req,
    )
    assert prom_resp["name"] == "bot-sec-audit"
    assert prom_resp["origin"] == "promoted_subagent"


# 10. Strict Harness Boundary Invariant
def test_subagent_control_plane_boundary_integrity():
    """Confirms packages/harness/alpha/subagents contains zero forbidden imports from app.*."""
    import pathlib

    subagents_dir = pathlib.Path(__file__).parent.parent / "packages" / "harness" / "alpha" / "subagents"
    for py_file in subagents_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "from app." not in content, f"Boundary violation in {py_file}: contains 'from app.'"
        assert "import app." not in content, f"Boundary violation in {py_file}: contains 'import app.'"
