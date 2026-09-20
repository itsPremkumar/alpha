"""Unit tests for Ethical and Safety Guardrails."""

from alpha.safety.guard import SafetyGuard


def test_safety_guard_destructive_commands():
    guard = SafetyGuard()

    # Destructive commands blocked
    res_rm = guard.evaluate_command("rm -rf /")
    assert res_rm.allowed is False
    assert res_rm.risk_level == "critical"

    res_format = guard.evaluate_command("format c:")
    assert res_format.allowed is False

    res_fork = guard.evaluate_command(":(){ :|:& };:")
    assert res_fork.allowed is False

    # Safe commands allowed
    res_safe = guard.evaluate_command("pytest backend/tests -v")
    assert res_safe.allowed is True
    assert res_safe.risk_level == "low"


def test_safety_guard_sensitive_files():
    guard = SafetyGuard()

    # Sensitive credentials blocked
    res_ssh = guard.evaluate_file_access("C:/Users/test/.ssh/id_rsa")
    assert res_ssh.allowed is False
    assert res_ssh.risk_level == "high"

    res_shadow = guard.evaluate_file_access("/etc/shadow")
    assert res_shadow.allowed is False

    # Ordinary files allowed
    res_file = guard.evaluate_file_access("backend/src/main.py")
    assert res_file.allowed is True


def test_safety_guard_ethical_goals():
    guard = SafetyGuard()

    # Prohibited unethical goals blocked
    res_bad = guard.evaluate_goal("Develop ransomware to encrypt server files")
    assert res_bad.allowed is False
    assert "ethical policy" in res_bad.reason

    res_phish = guard.evaluate_goal("Deploy phishing campaign for user credentials")
    assert res_phish.allowed is False

    # Legitimate engineering goals allowed
    res_good = guard.evaluate_goal("Build continuous integration pipeline and optimize tests")
    assert res_good.allowed is True
