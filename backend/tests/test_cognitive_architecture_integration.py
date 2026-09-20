"""End-to-end integration testing: Mission Compiler, Epistemics, RSI, Consequence Simulation, and Self-Healing."""

from alpha.consequence import AffordanceModel, ConsequenceSimulator
from alpha.epistemics import EpistemicBeliefEngine, EpistemicStatus
from alpha.mission import MissionCompiler, RiskTier
from alpha.rsi import RSIEngine, RSIStage
from alpha.runtime.selfheal import SelfHealingWatchdog


def test_full_hermes_agi_executive_lifecycle(tmp_path):
    # 1. Step 1: Compile complex, ambiguous objective into formal Mission Contract
    raw_prompt = "Refactor the payment gateway to add webhook idempotency. You must ensure backward compatibility. Never expose API secrets."
    compiler = MissionCompiler()
    mission = compiler.compile(raw_prompt)

    assert mission.risk_tier in {RiskTier.R2, RiskTier.R4}
    assert len(mission.constraints["hard"]) > 0
    assert len(mission.constraints["forbidden"]) > 0
    assert len(mission.proof_obligations) >= 2

    # 2. Step 2: Establish Epistemic Beliefs & Falsification
    epistemic = EpistemicBeliefEngine()
    claim = epistemic.register_claim(
        text="Webhook replay attacks succeed because event_id is not indexed uniquely",
        status=EpistemicStatus.HYPOTHESIS,
        prior_confidence=0.5,
        falsification_test="Sending duplicate event_id with current code returns HTTP 200 twice",
    )

    # Supply empirical evidence
    claim = epistemic.update_with_evidence(
        claim_id=claim.claim_id,
        evidence="Simulated duplicate POST /webhook with identical ID processed twice successfully",
        is_supporting=True,
    )
    assert claim.bayesian_posterior > 0.7

    # 3. Step 3: Probe Environment Affordances & Simulate Action Consequences
    affordance = AffordanceModel()
    env_snapshot = affordance.probe(str(tmp_path))
    assert env_snapshot.is_writable is True

    simulator = ConsequenceSimulator(affordance_model=affordance)
    sim_report = simulator.simulate(
        action_name="write_to_file",
        parameters={"TargetFile": str(tmp_path / "alembic/versions/002_idempotency_idx.py")},
        workspace_path=str(tmp_path),
    )
    assert sim_report.blast_radius == "PACKAGE_DEPENDENCY"
    assert len(sim_report.remediation_suggestions) > 0

    # 4. Step 4: Trigger an RSI optimization cycle for runtime performance
    rsi = RSIEngine()
    rsi_result = rsi.run_rsi_cycle(
        bottleneck="Database index verification query latency high under burst",
        target_component="tool_router",
    )
    assert rsi_result.stage == RSIStage.PREVIEW
    assert rsi_result.promoted is False

    # 5. Step 5: Execute Self-Healing Watchdog sanity scan
    watchdog = SelfHealingWatchdog()
    health_report = watchdog.scan_and_heal(workspace_dir=str(tmp_path), auto_remediate=True)
    assert health_report.healthy is True
    assert health_report.scan_duration_ms >= 0.0
