"""Unit and integration tests for the Core Triad of First-Party Enforcer Mods.

- FleetEstopMod: universal emergency stop circuit breaker.
- BlastRadiusGuardMod: tool risk classification (R0-R5) and hold-and-release gate.
- VerificationEvidenceGateMod: claim honesty and verifiable acceptance criteria gate.
"""

import pytest

from alpha.mods.enforcers.blast_radius_mod import BlastRadiusGuardMod, RiskLevel
from alpha.mods.enforcers.estop_mod import FleetEstopMod
from alpha.mods.enforcers.verification_gate_mod import VerificationEvidenceGateMod
from alpha.mods.kernel import ModKernel
from alpha.mods.types import AlphaEvent, CorrelationContext, EventOutcome


@pytest.fixture
def kernel():
    return ModKernel()


# =========================================================================
# FleetEstopMod Tests
# =========================================================================


@pytest.mark.asyncio
async def test_fleet_estop_halts_when_engaged(kernel, tmp_path, monkeypatch):
    from alpha.runtime.estop import EmergencyStopManager

    # Point ESTOP manager to isolated temp home
    estop_mgr = EmergencyStopManager(root_dir=tmp_path)
    monkeypatch.setattr("alpha.runtime.estop.get_estop_manager", lambda root_dir=None: estop_mgr)

    mod = FleetEstopMod()
    kernel.register_mod(mod)

    # 1. When disengaged -> should pass through
    assert not estop_mgr.is_engaged()
    ev_tool = AlphaEvent(
        name="tool.requested",
        payload={"tool_name": "view_file", "tool_args": {}},
        correlation=CorrelationContext.create(),
    )
    res = await kernel.dispatch(ev_tool)
    assert res.outcome == EventOutcome.CONTINUE

    # 2. Engage ESTOP
    estop_mgr.engage("Operator triggered emergency stop.")
    assert estop_mgr.is_engaged()

    # Event dispatch must now be DENIED
    res_stopped = await kernel.dispatch(ev_tool)
    assert res_stopped.outcome == EventOutcome.DENY
    assert "FLEET_ESTOP_ACTIVE" in res_stopped.reason

    # Agent spawn must also be DENIED
    ev_agent = AlphaEvent(
        name="agent.spawn_requested",
        payload={"agent": "coder"},
        correlation=CorrelationContext.create(),
    )
    res_agent = await kernel.dispatch(ev_agent)
    assert res_agent.outcome == EventOutcome.DENY
    assert "FLEET_ESTOP_ACTIVE" in res_agent.reason

    res_task = await kernel.dispatch(
        AlphaEvent(
            name="task.admit",
            payload={"prompt": "continue the mission"},
            correlation=CorrelationContext.create(),
        )
    )
    assert res_task.outcome == EventOutcome.DENY

    # Autonomy tick must also be DENIED
    ev_tick = AlphaEvent(
        name="autonomy.tick",
        payload={"loop_id": "sentinel"},
        correlation=CorrelationContext.create(),
    )
    res_tick = await kernel.dispatch(ev_tick)
    assert res_tick.outcome == EventOutcome.DENY

    # 3. Disengage ESTOP
    estop_mgr.disengage()
    res_resumed = await kernel.dispatch(ev_tool)
    assert res_resumed.outcome == EventOutcome.CONTINUE


@pytest.mark.asyncio
async def test_an_unreadable_estop_state_denies_without_claiming_a_stop_was_tripped(kernel, monkeypatch):
    """A control that cannot be read must still refuse -- and say so honestly.

    `EmergencyStopManager.get_status()` always supplies a `reason`, so a status
    carrying no `reason` key is the *unreadable* case and nothing else. Reporting
    it as "Emergency stop active across fleet" tells the operator they hit a stop
    nobody engaged and sends recovery looking for a disengage instead of a broken
    read. Still a DENY either way: an unreadable safety control may not
    authorize new work.
    """
    from alpha.runtime import estop as estop_module

    def _unreadable(root_dir=None):
        raise OSError("ESTOP volume is unavailable")

    monkeypatch.setattr(estop_module, "get_estop_manager", _unreadable)

    kernel.register_mod(FleetEstopMod())
    res = await kernel.dispatch(
        AlphaEvent(
            name="run.admit",
            payload={"run_id": "run-unreadable-estop"},
            correlation=CorrelationContext.create(run_id="run-unreadable-estop"),
        )
    )

    assert res.outcome == EventOutcome.DENY
    assert "could not be read" in res.reason
    assert "ESTOP volume is unavailable" in res.reason
    assert "Emergency stop active across fleet" not in res.reason


# =========================================================================
# BlastRadiusGuardMod Tests
# =========================================================================


def test_blast_radius_risk_classification():
    guard = BlastRadiusGuardMod()

    # R0: Read-only tools
    r0_lvl, _, _ = guard.classify_risk("view_file", {"path": "a.txt"})
    assert r0_lvl == RiskLevel.R0

    r0_grep, _, _ = guard.classify_risk("grep_search", {"Query": "pattern"})
    assert r0_grep == RiskLevel.R0

    r0_cmd, _, _ = guard.classify_risk("run_command", {"CommandLine": "ls -la"})
    assert r0_cmd == RiskLevel.R0

    # R1: Reversible single-file edit
    r1_lvl, _, _ = guard.classify_risk("write_to_file", {"TargetFile": "src/code.py"})
    assert r1_lvl == RiskLevel.R1

    # R3: Network / credential commands
    r3_curl, _, _ = guard.classify_risk("run_command", {"CommandLine": "curl https://example.com"})
    assert r3_curl == RiskLevel.R3

    # R4: Destructive commands
    r4_rm, _, _ = guard.classify_risk("run_command", {"CommandLine": "rm -rf /"})
    assert r4_rm == RiskLevel.R4

    r4_sql, _, _ = guard.classify_risk("run_command", {"CommandLine": "DROP TABLE users;"})
    assert r4_sql == RiskLevel.R4

    r4_push, _, _ = guard.classify_risk("run_command", {"CommandLine": "git push origin main --force"})
    assert r4_push == RiskLevel.R4

    # Sensitive path write
    r4_env, _, _ = guard.classify_risk("write_to_file", {"TargetFile": "/app/.env"})
    assert r4_env == RiskLevel.R4


@pytest.mark.asyncio
async def test_blast_radius_hold_and_release_flow(kernel):
    guard = BlastRadiusGuardMod()
    kernel.register_mod(guard)

    # 1. Dispatch R4 destructive command
    ev_rm = AlphaEvent(
        name="tool.requested",
        payload={
            "tool_name": "run_command",
            "tool_args": {"CommandLine": "rm -rf /"},
        },
        correlation=CorrelationContext.create(tool_call_id="call_destroy_1"),
    )

    res = await kernel.dispatch(ev_rm)
    assert res.outcome == EventOutcome.DEFER
    assert "APPROVAL_REQUIRED" in res.reason
    assert res.response_payload is not None
    hold_id = res.response_payload["hold_id"]
    assert hold_id is not None
    assert res.response_payload["risk_level"] == "R4"

    # Verify held in queue
    held = guard.list_held_actions()
    assert len(held) == 1
    assert held[0]["hold_id"] == hold_id

    # 2. Operator rejects
    guard.reject(hold_id, reason="Too dangerous")
    assert len(guard.list_held_actions()) == 0

    # 3. New attempt held, then approved
    second_attempt = ev_rm.copy(correlation=CorrelationContext.create(tool_call_id="call_destroy_2"))
    res2 = await kernel.dispatch(second_attempt)
    assert res2.outcome == EventOutcome.DEFER
    hold_id_2 = res2.response_payload["hold_id"]

    approved = guard.approve(hold_id_2)
    assert approved is True

    # Re-dispatching the approved tool_call_id now passes through!
    res_approved = await kernel.dispatch(second_attempt)
    assert res_approved.outcome == EventOutcome.CONTINUE


# =========================================================================
# VerificationEvidenceGateMod Tests
# =========================================================================


@pytest.mark.asyncio
async def test_verification_gate_rejects_unverified_completion(kernel):
    gate = VerificationEvidenceGateMod()
    kernel.register_mod(gate)

    corr = CorrelationContext.create(run_id="run_test_honesty")

    # Agent emits success claim without any evidence in ledger
    ev_claim = AlphaEvent(
        name="turn.complete",
        payload={
            "status": "success",
            "message": "I have fixed the bug and all tests pass with flying colors.",
        },
        correlation=corr,
    )

    res = await kernel.dispatch(ev_claim)
    # The rewrite is fed through the rest of the pipeline and retained as evidence.
    assert res.outcome == EventOutcome.CONTINUE
    assert res.event.payload.get("remediation_required") is True
    rewrite = res.metadata["mod_rewrites"][0]
    assert "EVIDENCE_GATE_DENIAL" in rewrite["reason"]
    assert "lacks measured execution evidence" in rewrite["remediation_prompt"]


@pytest.mark.asyncio
async def test_verification_gate_approves_with_valid_evidence(kernel):
    gate = VerificationEvidenceGateMod()
    kernel.register_mod(gate)

    corr = CorrelationContext.create(run_id="run_verified_honesty")

    # Record passing evidence receipt
    ctx = kernel._create_context(gate)
    ctx.evidence.record(
        {
            "kind": "test",
            "exit_code": 0,
            "summary": "pytest tests/ passed 10/10",
            "status": "passed",
        },
        correlation=corr,
    )

    # Now agent emits success claim
    ev_claim = AlphaEvent(
        name="turn.complete",
        payload={
            "status": "success",
            "message": "Task complete: tests pass.",
        },
        correlation=corr,
    )

    res = await kernel.dispatch(ev_claim)
    # Must pass through as CONTINUE
    assert res.outcome == EventOutcome.CONTINUE

    # Verify acceptance receipt was created
    receipts = ctx.evidence.get_by_correlation(corr)
    assert any(r.get("kind") == "acceptance_verdict" for r in receipts)


@pytest.mark.asyncio
async def test_verification_gate_does_not_accept_another_runs_evidence(kernel):
    gate = VerificationEvidenceGateMod()
    kernel.register_mod(gate)
    evidence_context = kernel._create_context(gate)
    evidence_context.evidence.record(
        {"kind": "test", "exit_code": 0, "status": "passed"},
        correlation=CorrelationContext.create(run_id="run-with-evidence"),
    )

    claim = AlphaEvent(
        name="turn.complete",
        payload={"status": "success", "message": "Task complete."},
        correlation=CorrelationContext.create(run_id="different-run"),
    )
    result = await kernel.dispatch(claim)

    assert result.event.payload.get("remediation_required") is True
    assert result.metadata["mod_rewrites"][0]["mod"] == gate.name


@pytest.mark.asyncio
async def test_verification_gate_ignores_non_claim_turns(kernel):
    gate = VerificationEvidenceGateMod()
    kernel.register_mod(gate)

    corr = CorrelationContext.create()
    ev_progress = AlphaEvent(
        name="turn.complete",
        payload={"message": "Currently searching for relevant files in src/."},
        correlation=corr,
    )

    res = await kernel.dispatch(ev_progress)
    assert res.outcome == EventOutcome.CONTINUE


def test_blast_radius_powershell_and_git_reset():
    guard = BlastRadiusGuardMod()

    # PowerShell recursive delete
    pwsh_lvl, _, _ = guard.classify_risk("run_command", {"CommandLine": "Remove-Item -Recurse -Force ./target"})
    assert pwsh_lvl == RiskLevel.R4

    # Git hard reset
    git_lvl, _, _ = guard.classify_risk("run_command", {"CommandLine": "git reset --hard HEAD~1"})
    assert git_lvl == RiskLevel.R4

    # Separated flag rm -f -r /
    rm_lvl, _, _ = guard.classify_risk("run_command", {"CommandLine": "rm -f -r /tmp/dir"})
    assert rm_lvl == RiskLevel.R4

    # Git clean -fdx
    clean_lvl, _, _ = guard.classify_risk("run_command", {"CommandLine": "git clean -fdx"})
    assert clean_lvl == RiskLevel.R4


def test_blast_radius_production_infra_r5():
    guard = BlastRadiusGuardMod()

    # Kubernetes apply / delete
    k8s_lvl, _, _ = guard.classify_risk("run_command", {"CommandLine": "kubectl apply -f deployment.yaml"})
    assert k8s_lvl == RiskLevel.R5

    # Terraform apply
    tf_lvl, _, _ = guard.classify_risk("run_command", {"CommandLine": "terraform apply --auto-approve"})
    assert tf_lvl == RiskLevel.R5

    # Named deploy tool
    deploy_lvl, _, _ = guard.classify_risk("deploy_cluster_service", {"cluster": "prod-1"})
    assert deploy_lvl == RiskLevel.R5


@pytest.mark.asyncio
async def test_blast_radius_operator_rejection_denies_subsequent_attempts(kernel):
    guard = BlastRadiusGuardMod()
    kernel.register_mod(guard)

    corr = CorrelationContext.create(tool_call_id="call_rejected_test")
    ev = AlphaEvent(
        name="tool.requested",
        payload={"tool_name": "run_command", "tool_args": {"CommandLine": "rm -rf /"}},
        correlation=corr,
    )

    # 1. First dispatch -> DEFER hold
    res = await kernel.dispatch(ev)
    assert res.outcome == EventOutcome.DEFER
    hold_id = res.response_payload["hold_id"]

    # 2. Operator rejects
    rejected = guard.reject(hold_id, reason="Security policy forbids rm -rf")
    assert rejected is True

    # 3. Subsequent dispatch with the same tool_call_id is immediately DENIED
    res_subsequent = await kernel.dispatch(ev)
    assert res_subsequent.outcome == EventOutcome.DENY
    assert "ACTION_REJECTED" in res_subsequent.reason


def test_blast_radius_cleanup_expired_holds():
    guard = BlastRadiusGuardMod()
    guard._held_actions["hold_old"] = {"timestamp": 1000.0}
    guard._held_actions["hold_new"] = {"timestamp": 9999999999.0}

    expired_count = guard.cleanup_expired_holds(max_age_seconds=3600.0)
    assert expired_count == 1
    assert "hold_old" not in guard._held_actions
    assert "hold_new" in guard._held_actions


@pytest.mark.asyncio
async def test_verification_gate_excludes_acceptance_verdict_circular_receipt(kernel):
    gate = VerificationEvidenceGateMod()
    kernel.register_mod(gate)

    corr = CorrelationContext.create(run_id="run_circular_test")
    ctx = kernel._create_context(gate)

    # Record ONLY an acceptance_verdict (which shouldn't count as test execution proof)
    ctx.evidence.record(
        {"kind": "acceptance_verdict", "status": "VERIFIED"},
        correlation=corr,
    )

    ev_claim = AlphaEvent(
        name="task.completion_requested",
        payload={"status": "completed"},
        correlation=corr,
    )

    res = await kernel.dispatch(ev_claim)
    # Must NOT accept circular verdict; should rewrite to remediation
    assert res.event.payload.get("remediation_required") is True


@pytest.mark.asyncio
async def test_verification_gate_auto_captures_tool_completed_test_receipt(kernel):
    gate = VerificationEvidenceGateMod()
    kernel.register_mod(gate)

    corr = CorrelationContext.create(run_id="run_auto_capture")

    # 1. Agent runs pytest via tool; tool.completed event is dispatched
    ev_tool_done = AlphaEvent(
        name="tool.completed",
        payload={
            "tool_name": "run_command",
            "tool_args": {"CommandLine": "pytest tests/"},
            "content": "=== 15 passed in 0.42s ===",
            "status": "success",
            "exit_code": 0,
        },
        correlation=corr,
    )
    res_tool = await kernel.dispatch(ev_tool_done)
    assert res_tool.outcome == EventOutcome.CONTINUE

    # 2. Agent now declares completion
    ev_complete = AlphaEvent(
        name="task.completion_requested",
        payload={"status": "completed"},
        correlation=corr,
    )
    res_complete = await kernel.dispatch(ev_complete)
    # Evidence was captured, so completion is APPROVED!
    assert res_complete.outcome == EventOutcome.CONTINUE
