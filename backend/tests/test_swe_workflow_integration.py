"""End-to-end integration test exercising all OpenHands advanced subsystems together."""

from alpha.agent.oracle import OracleService
from alpha.context.condenser import PipelineCondenser
from alpha.critic import AgentFinishedCritic, CriticPipeline, EmptyPatchCritic
from alpha.events.stream import (
    CmdOutputObservation,
    CmdRunAction,
    CriticObservation,
    EventStreamLedger,
    FileEditAction,
    FileEditObservation,
)
from alpha.reasoning.tom import TheoryOfMindConsultant
from alpha.security.shell_ast import ConfirmationPolicy, ExecutionDecision
from alpha.skills.triggers import MicroAgentRegistry, TriggerContext
from alpha.workspace_changes.patch_synthesizer import PatchSynthesizer


def test_openhands_full_engineering_workflow(tmp_path):
    """Simulates a complete SWE workflow using all 8 integrated OpenHands subsystems."""

    # 1. Theory of Mind Consultation
    user_goal = "Refactor the authentication token verification to use asymmetric RSA keys"
    tom = TheoryOfMindConsultant()
    intent_hyp = tom.consult(task_description=user_goal)
    assert intent_hyp.risk_tolerance.value in {"low", "medium"}
    assert len(intent_hyp.unstated_expectations) > 0

    # 2. Dynamic MicroAgent Activation
    registry = MicroAgentRegistry(load_defaults=True)
    trigger_ctx = TriggerContext(query=user_goal, file_paths=["backend/auth/jwt.py", "tests/test_auth.py"])
    active_agents = registry.match(trigger_ctx)
    assert len(active_agents) >= 1
    prompt_injections = registry.render_active_instructions(trigger_ctx)
    assert "Active MicroAgent" in prompt_injections

    # 3. Isolated Oracle Inquiry
    oracle = OracleService()
    advisory = oracle.consult("best practices for asymmetric JWT token verification")
    assert advisory.confidence >= 0.9

    # 4. Defense-in-Depth Shell AST Security Check
    security_policy = ConfirmationPolicy()
    safe_cmd = "pytest tests/test_auth.py -v"
    eval_safe = security_policy.evaluate(safe_cmd)
    assert eval_safe.decision == ExecutionDecision.ALLOW

    # Verify that dangerous commands are actively blocked
    eval_blocked = security_policy.evaluate("rm -rf /")
    assert eval_blocked.decision == ExecutionDecision.BLOCK

    # 5. EventStream Action/Observation Recording
    ledger = EventStreamLedger(session_id="integration_test_run")

    action_cmd = CmdRunAction(command=safe_cmd, thought="Running verification suite")
    ledger.append_action(action_cmd)

    obs_cmd = CmdOutputObservation(exit_code=0, stdout="================ 5 passed in 0.45s ================", action_id=action_cmd.action_id)
    ledger.append_observation(obs_cmd)

    action_edit = FileEditAction(path="auth/jwt.py", content="...", mode="write", thought="Updated key verification")
    ledger.append_action(action_edit)

    obs_edit = FileEditObservation(path="auth/jwt.py", success=True, lines_added=24, lines_removed=10, action_id=action_edit.action_id)
    ledger.append_observation(obs_edit)

    # 6. Multi-stage Pipeline Context Condensation
    condenser = PipelineCondenser(max_budget_chars=5000)
    messages = [
        {"role": "user", "content": user_goal},
        {"role": "tool", "name": "run_command", "content": obs_cmd.content},
        {"role": "assistant", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "auth/jwt.py"}}]},
        {"role": "tool", "name": "write_to_file", "content": "File saved"},
    ]
    condensed, state = condenser.condense(messages)
    assert "auth/jwt.py" in state.modified_files

    # 7. Patch Synthesizer & Hygiene Validation
    synthesizer = PatchSynthesizer()
    generated_patch = """diff --git a/auth/jwt.py b/auth/jwt.py
--- a/auth/jwt.py
+++ b/auth/jwt.py
@@ -10,3 +10,5 @@ def verify_token(token):
-    return symmetric_verify(token)
+    return asymmetric_verify(token, public_key)
"""
    patch_validation = synthesizer.validate_patch_hygiene(generated_patch)
    assert patch_validation.is_valid
    assert patch_validation.stats["files_changed_count"] == 1

    # 8. Completion Critic Verification
    critic_pipeline = CriticPipeline(critics=[
        AgentFinishedCritic(check_unresolved_errors=True),
        EmptyPatchCritic(force_check=False),
    ])

    execution_history = [
        {"tool_name": "run_command", "status": "success", "exit_code": 0},
        {"tool_name": "write_to_file", "path": "auth/jwt.py", "status": "success"},
    ]

    verdict = critic_pipeline.evaluate(
        task_description=user_goal,
        execution_history=execution_history,
        workspace_dir=str(tmp_path),
    )
    assert verdict.is_approved

    # Add critic verdict to eventstream
    obs_critic = CriticObservation(
        critic_name="CriticPipeline",
        verdict=verdict.verdict.value,
        reason=verdict.reason,
    )
    ledger.append_observation(obs_critic)

    # Verify final audit trail
    stats = ledger.summary_stats()
    assert stats["total_actions"] == 2
    assert stats["total_observations"] == 3
    assert stats["total_errors"] == 0
