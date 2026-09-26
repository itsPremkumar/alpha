"""Unit & Integration Tests for AI Workforce OS Phase 6 Capabilities.

Tests:
1. Visual QA & Playwright Browser Verifier (DOM checks, stability scores, evidence receipts)
2. Bi-directional GitHub Webhook Bridge (Issue auctions, PR audits, CI healing)
3. Long-Horizon Workspace Checkpointing & Warm Resume (State snapshots, lock restoration)
4. Containerized Sandbox Execution Runner (Host fallback, environment execution)
5. Local LLM Failover & Hybrid Cost Router (Ollama/vLLM probing, threshold routing)
6. Canary Staging & Auto-Rollback Watchdog (Synthetic probes, auto-rollback triggers)
7. Autonomous Retrospective & Prompt Self-Evolution (Postmortem pattern analysis, proposals)
8. SWE-Bench Evaluation Arena & Bot Leaderboard (Challenge execution, dynamic scoring)
"""

import tempfile
from pathlib import Path

from alpha.benchmarks.arena import get_benchmark_arena
from alpha.evolution.retrospective_engine import get_retrospective_engine
from alpha.integrations.github_bridge import get_github_workforce_bridge
from alpha.models.local_llm_failover import get_local_failover_router
from alpha.projects.canary_watchdog import get_canary_watchdog
from alpha.projects.checkpoint_engine import CheckpointEngine
from alpha.projects.visual_verifier import get_visual_qa_engine
from alpha.sandbox.container_runner import get_container_sandbox_runner


def test_visual_qa_verifier_lifecycle():
    """Verify DOM structure assertions, layout scoring, and contract evidence receipt."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_html = Path(tmpdir) / "index.html"
        test_html.write_text(
            "<!DOCTYPE html><html><body><div id='root'><h1>Alpha Studio</h1><button class='btn-primary'>Deploy</button></div></body></html>",
            encoding="utf-8",
        )

        qa = get_visual_qa_engine("test-project-vqa")
        receipt = qa.verify_preview(
            str(test_html),
            required_selectors=["root", "btn-primary"],
            verifier_bot="qa_bot",
        )

        assert receipt.passed is True
        assert receipt.dom_nodes_checked > 0
        assert receipt.visual_stability_score == 1.0
        assert receipt.verified_by == "qa_bot"
        assert receipt.screenshot_path is not None
        assert len(qa.get_history()) >= 1


def test_github_bridge_event_dispatch():
    """Verify inbound GitHub webhook routing to auctions, audits, and self-healing."""
    bridge = get_github_workforce_bridge()

    # 1. Issue opened -> triggers Task Auction
    issue_payload = {
        "action": "opened",
        "issue": {
            "number": 101,
            "title": "Fix race condition in task lock manager",
            "body": "Multiple bots attempting concurrent lock acquisition causes lock deadlock.",
            "labels": [{"name": "backend"}, {"name": "concurrency"}],
        },
    }
    res_issue = bridge.dispatch_event("issues", issue_payload, project_id="gh-proj-1")
    assert res_issue["status"] == "auction_awarded"
    assert res_issue["winning_bot"] is not None
    assert "task_id" in res_issue

    # 2. PR opened -> triggers Audit Council
    pr_payload = {
        "action": "opened",
        "pull_request": {
            "number": 42,
            "title": "feat: add token cost governor",
            "body": "Implements financial circuit breaker for autonomous bots.",
        },
    }
    res_pr = bridge.dispatch_event("pull_request", pr_payload, project_id="gh-proj-1")
    assert res_pr["status"] == "audit_completed"
    assert res_pr["verdict"] in ("approved", "caution", "rejected")

    # 3. CI failure -> triggers Self-Healing Runner
    ci_payload = {
        "action": "completed",
        "workflow_run": {"name": "Integration Test Suite", "conclusion": "failure"},
    }
    res_ci = bridge.dispatch_event("workflow_run", ci_payload, project_id="gh-proj-1")
    assert res_ci["status"] == "self_healing_executed"
    assert res_ci["attempts"] >= 1


def test_checkpoint_engine_save_and_restore():
    """Verify complete workspace serialization and warm state restoration."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine = CheckpointEngine("test-ckpt-proj", storage_dir=tmpdir)

        # 1. Create Checkpoint
        ckpt = engine.create_checkpoint(tag="v1.0.0-rc1", metadata={"author": "lead_agent"})
        assert ckpt.checkpoint_id.startswith("ckpt-")
        assert ckpt.tag == "v1.0.0-rc1"
        assert len(engine.list_checkpoints()) >= 1

        # 2. Restore Checkpoint
        restore_res = engine.restore_checkpoint(ckpt.checkpoint_id)
        assert restore_res["restored"] is True
        assert restore_res["checkpoint_id"] == ckpt.checkpoint_id
        assert restore_res["tag"] == "v1.0.0-rc1"


def test_container_sandbox_runner_execution():
    """Verify execution sandbox runs commands with duration tracking and safe fallback."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = get_container_sandbox_runner()
        res = runner.execute(
            worktree_path=tmpdir,
            command="python -c \"print('Sandbox execution successful')\"",
        )

        assert res.exit_code == 0
        assert "Sandbox execution successful" in res.stdout
        assert res.duration_ms > 0
        assert res.sandbox_type in ("docker", "podman", "host_fallback")


def test_local_llm_failover_routing():
    """Verify hybrid routing shifts low-complexity or budget-strained tasks to local runtimes."""
    router = get_local_failover_router()

    # Low-complexity task check
    should_fail, provider, model, reason = router.should_failover(
        project_id="default",
        task_type="formatting",
    )
    # If no local endpoint is actively bound, falls back gracefully
    assert isinstance(should_fail, bool)
    assert isinstance(provider, str)
    assert isinstance(reason, str)

    # All endpoint status probing
    statuses = router.get_all_statuses()
    assert "ollama" in statuses
    assert "vllm" in statuses
    assert "lmstudio" in statuses


def test_canary_watchdog_probe_and_rollback():
    """Verify synthetic canary probe evaluation and automatic rollback execution."""
    dog = get_canary_watchdog("test-canary-proj")

    # 1. Successful canary probe (mock)
    healthy_probe = dog.probe_staging(port=3000, mock_success=True)
    assert healthy_probe.status == "healthy"
    assert healthy_probe.recommendation == "proceed_with_merge"

    # Rollback on healthy probe should not execute
    res_noop = dog.execute_rollback_if_failed(healthy_probe, branch_name="test-branch")
    assert res_noop["rollback_executed"] is False

    # 2. Degraded/Failed probe (unbound port)
    failed_probe = dog.probe_staging(port=59999, timeout_seconds=0.1, mock_success=False)
    assert failed_probe.status == "failed"
    assert failed_probe.recommendation == "trigger_auto_rollback"

    # Rollback execution cleans branch and emits alert
    res_rollback = dog.execute_rollback_if_failed(failed_probe, branch_name="failed-branch")
    assert res_rollback["rollback_executed"] is True
    assert res_rollback["branch"] == "failed-branch"


def test_retrospective_engine_learning_synthesis():
    """Verify learning extraction from postmortems into prompt evolution proposals."""
    retro = get_retrospective_engine("test-retro-proj")
    proposals = retro.analyze_recent_learnings(bot_name="coder", queue_for_approval=False)

    assert len(proposals) >= 1
    p = proposals[0]
    assert p.bot_name == "coder"
    assert len(p.proposed_instruction) > 10
    assert p.target_prompt_section in ("coding_guidelines", "verification_rules", "safety")


def test_benchmark_arena_and_leaderboard():
    """Verify SWE-Bench arena challenge runs, dynamic scoring, and ranked leaderboard."""
    arena = get_benchmark_arena("test-arena-proj")
    challenges = arena.list_challenges()
    assert len(challenges) >= 3

    # Execute Challenge from fully caller-supplied inputs (no defaults are
    # accepted: a run receipt can never be fabricated by the arena itself).
    res = arena.run_challenge(
        challenge_id="swe-01-null-guard",
        bot_name="coder",
        simulated_pass=True,
        duration_seconds=5.0,
        tokens_consumed=850,
    )
    assert res.passed is True
    assert res.score >= 80.0
    assert res.cost_usd > 0.0
    # Honesty pin: with no challenge executor wired, the recorded run is
    # disclosed as simulated evidence, never as a measurement.
    assert res.evidence_kind == "simulated"

    # Verify Leaderboard Rankings
    board = arena.get_leaderboard()
    assert len(board) >= 3
    assert board[0].rank == 1
    # HONESTY INVERSION:
    #   OLD: assert board[0].reputation_score >= board[1].reputation_score
    # That compared canned default scores (85.0) invented for bots with zero
    # attempts. Simulated runs are excluded from leaderboard aggregates, so no
    # bot has measured statistics yet — every stat must be None, never a
    # fabricated number.
    assert all(e.challenges_attempted == 0 for e in board)
    assert all(
        e.pass_rate is None and e.avg_duration_seconds is None and e.reputation_score is None
        for e in board
    )
    # The simulated run itself stays on record with its disclosure intact.
    recorded_runs = [r for r in arena._runs if r.challenge_id == "swe-01-null-guard"]
    assert len(recorded_runs) >= 1
    assert all(r.evidence_kind == "simulated" for r in recorded_runs)
