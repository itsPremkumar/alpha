
from alpha.security.autonomy import (
    ActionRiskClassifier,
    ActionRiskLevel,
    AuthorizationDecision,
    AutonomyPolicyEngine,
    AutonomyProfile,
)


def test_action_risk_classifier():
    classifier = ActionRiskClassifier()

    # Low / safe tool
    assert classifier.classify_tool_call("view_file") == ActionRiskLevel.LOW_SAFE
    assert classifier.classify_shell_command("git status") == ActionRiskLevel.LOW_SAFE
    assert classifier.classify_shell_command("pytest -v") == ActionRiskLevel.LOW_SAFE

    # Medium risk
    assert classifier.classify_tool_call("write_to_file") == ActionRiskLevel.MEDIUM_RISK
    assert classifier.classify_shell_command("git checkout -b feature") == ActionRiskLevel.MEDIUM_RISK

    # High risk destructive
    assert classifier.classify_shell_command("rm -rf /") == ActionRiskLevel.HIGH_RISK_DESTRUCTIVE
    assert classifier.classify_shell_command("git push origin main --force") == ActionRiskLevel.HIGH_RISK_DESTRUCTIVE
    assert classifier.classify_shell_command("DROP DATABASE production") == ActionRiskLevel.HIGH_RISK_DESTRUCTIVE


def test_autonomy_policy_observer_profile():
    engine = AutonomyPolicyEngine(default_profile=AutonomyProfile.OBSERVER)

    # Safe read allowed
    res_safe = engine.evaluate_action("view_file")
    assert res_safe.decision == AuthorizationDecision.ALLOW

    # Edit requires human approval
    res_edit = engine.evaluate_action("write_to_file")
    assert res_edit.decision == AuthorizationDecision.REQUIRE_APPROVAL


def test_autonomy_policy_assistant_and_operator_profiles():
    engine = AutonomyPolicyEngine(default_profile=AutonomyProfile.ASSISTANT)

    # File edit allowed in assistant mode
    res_edit = engine.evaluate_action("write_to_file")
    assert res_edit.decision == AuthorizationDecision.ALLOW

    # Destructive command requires approval
    res_rm = engine.evaluate_action("run_command", "rm -rf /")
    assert res_rm.decision == AuthorizationDecision.REQUIRE_APPROVAL

    # Switch to Operator
    engine.set_profile(AutonomyProfile.OPERATOR)
    assert engine.current_profile == AutonomyProfile.OPERATOR

    # Shell test command allowed in operator mode
    res_test = engine.evaluate_action("run_command", "python -m pytest")
    assert res_test.decision == AuthorizationDecision.ALLOW

    # Force push still requires approval
    res_force = engine.evaluate_action("run_command", "git push --force")
    assert res_force.decision == AuthorizationDecision.REQUIRE_APPROVAL


def test_autonomy_policy_autonomous_profile():
    engine = AutonomyPolicyEngine(default_profile=AutonomyProfile.AUTONOMOUS)

    res_routine = engine.evaluate_action("run_command", "cargo build --release")
    assert res_routine.decision == AuthorizationDecision.ALLOW

    # Extreme destruction intercepts even in autonomous mode
    res_drop = engine.evaluate_action("run_command", "DROP DATABASE prod")
    assert res_drop.decision == AuthorizationDecision.REQUIRE_APPROVAL
