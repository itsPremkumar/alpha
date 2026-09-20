from alpha.avo import VersionRecord, get_avo_runner
from alpha.epistemics import EpistemicStatus, get_epistemic_engine
from alpha.rsi import RSIStage, get_rsi_engine
from alpha.trajectory.store import get_trajectory_store


def test_avo_lineage_and_pareto():
    runner = get_avo_runner("test_proj_avo")
    lineage = runner.lineage

    # Root candidate
    root = VersionRecord(
        hypothesis="Baseline attention kernel",
        modification="standard pytorch",
        correctness=True,
        performance_score=0.70,
        quality_score=0.80,
    )
    assert lineage.commit_candidate(root) is True
    assert lineage.head_id == root.version_id

    # Improved candidate
    v1 = VersionRecord(
        parent_id=root.version_id,
        hypothesis="Vectorized memory coalescing",
        modification="coalesced load",
        correctness=True,
        performance_score=0.85,
        quality_score=0.88,
    )
    assert lineage.commit_candidate(v1) is True
    assert lineage.head_id == v1.version_id

    # Regressed candidate should be rejected
    v_bad = VersionRecord(
        parent_id=v1.version_id,
        hypothesis="Uncalibrated unroll",
        modification="unroll x16",
        correctness=True,
        performance_score=0.60,
        quality_score=0.50,
    )
    assert lineage.commit_candidate(v_bad) is False

    frontier = lineage.get_pareto_frontier()
    assert len(frontier) >= 1
    assert any(c.version_id == v1.version_id for c in frontier)


def test_epistemic_bayesian_updating():
    engine = get_epistemic_engine("test_proj_epistemic")
    claim = engine.register_claim(
        text="FastAPI router mounts all workforce endpoints properly",
        status=EpistemicStatus.HYPOTHESIS,
        prior_confidence=0.50,
        falsification_test="Route integration test suite",
    )
    assert claim.confidence == 0.50
    assert not claim.is_verified

    # Add supporting evidence
    engine.update_with_evidence(claim.claim_id, "Route tests passed with 200 OK", is_supporting=True, likelihood_ratio=4.0)
    assert claim.confidence > 0.50

    # Add second strong supporting evidence to reach FACT status (>= 0.90)
    engine.update_with_evidence(claim.claim_id, "Verified by autonomous test healer", is_supporting=True, likelihood_ratio=4.0)
    assert claim.confidence >= 0.90
    assert claim.status == EpistemicStatus.FACT
    assert claim.is_verified is True


def test_rsi_closed_loop_execution():
    engine = get_rsi_engine("test_proj_rsi")
    result = engine.run_rsi_cycle(bottleneck="High token consumption in prompt templates", target_component="compaction")

    assert result.stage == RSIStage.PREVIEW
    assert result.promoted is False
    assert result.hypothesis is not None
    assert result.candidate is not None
    assert result.ab_test is not None
    assert result.holdout is not None

    status = engine.get_status()
    assert "stage" in status
    assert "active_configurations" in status


def test_trajectory_time_travel_and_replay():
    import uuid

    store = get_trajectory_store("test_proj_traj")
    goal = f"test_deploy_{uuid.uuid4().hex[:6]}"

    s0 = store.record_step(
        goal_id=goal,
        step_index=0,
        thought="Inspect environment",
        tool_name="shell",
        tool_input={"cmd": "uname -a"},
        tool_output="Linux x86_64",
        status="success",
    )
    s1 = store.record_step(
        goal_id=goal,
        step_index=1,
        thought="Compile binary",
        tool_name="compiler",
        tool_input={"target": "release"},
        tool_output="Build complete",
        status="success",
    )

    trace = store.get_trajectory(goal)
    assert trace.total_steps == 2
    assert trace.steps[0].step_id == s0.step_id
    assert trace.steps[1].step_id == s1.step_id

    # Time-travel replay
    replay = store.replay_from_step(goal, from_step_index=0)
    assert replay["replayed"] is True
    assert replay["from_step_index"] == 0

    # Fork trajectory from step 0
    fork_goal = f"test_forked_{uuid.uuid4().hex[:6]}"
    forked = store.fork_trajectory(goal, from_step_index=0, new_goal_id=fork_goal)
    assert forked.total_steps == 1
    assert forked.steps[0].thought == "Inspect environment"
