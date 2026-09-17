from agent_workspace.reasoning.governor import ReasoningGovernor


def test_reasoning_governor_fast_mode():
    governor = ReasoningGovernor()
    cfg1 = governor.evaluate("grep for import errors in logger")
    assert cfg1.tier == "fast"
    assert cfg1.thinking_budget_tokens == 0
    assert cfg1.temperature == 0.1

    cfg2 = governor.evaluate("check git status")
    assert cfg2.tier == "fast"
    assert cfg2.thinking_budget_tokens == 0


def test_reasoning_governor_deep_reasoning():
    governor = ReasoningGovernor()
    cfg = governor.evaluate("Perform a deep security audit and refactor the consensus architecture to eliminate race conditions")
    assert cfg.tier == "deep"
    assert cfg.thinking_budget_tokens == 16000
    assert cfg.timeout_seconds == 180.0


def test_reasoning_governor_balanced():
    governor = ReasoningGovernor()
    cfg = governor.evaluate("Please add a new helper function that formats date strings to ISO format with some tests")
    assert cfg.tier == "balanced"
    assert cfg.thinking_budget_tokens == 4000


def test_reasoning_governor_override():
    governor = ReasoningGovernor()
    governor.set_override("deep")
    cfg = governor.evaluate("ls")
    assert cfg.tier == "deep"

    governor.set_override(None)
    cfg2 = governor.evaluate("ls")
    assert cfg2.tier == "fast"
