"""Tests for Kibitzer Resident Memory Sidecar."""

from alpha.memory.kibitzer import (
    KibitzerMemoryBank,
    KibitzerObserver,
    redact_secrets,
)


def test_redact_secrets():
    raw = "My api_key = 'sk-1234567890123456789012345' and token: 'ghp_123456789012345678901234567890123456'"
    clean = redact_secrets(raw)
    assert "sk-" not in clean
    assert "ghp_" not in clean
    assert "[REDACTED_SECRET]" in clean


def test_kibitzer_nudge_generation():
    bank = KibitzerMemoryBank()
    bank.add_entry(
        entry_id="mem_pytest_venv",
        topic="Testing environment",
        keywords=["pytest", "unit test", "test runner"],
        hint="Always specify PYTHONPATH='backend/packages/harness;backend' when running pytest on Windows.",
    )
    bank.add_entry(
        entry_id="mem_git_worktree",
        topic="Git Worktree isolation",
        keywords=["worktree", "branching"],
        hint="Run git commands in the specific worktree path to prevent HEAD detachment.",
    )

    observer = KibitzerObserver(memory_bank=bank)

    # Turn 1: Mentions pytest -> receives pytest hint
    nudges = observer.observe(prompt="Can you run pytest on the tests folder?")
    assert len(nudges) == 1
    assert "recalled memory: Always specify PYTHONPATH" in nudges[0]

    # Turn 2: Mentions pytest again -> suppressed to avoid duplicate clutter
    nudges2 = observer.observe(prompt="The pytest run failed.")
    assert len(nudges2) == 0

    # Turn 3: Mentions worktree -> receives worktree hint
    nudges3 = observer.observe(tool_name="git_worktree_add", tool_result="Created worktree")
    assert len(nudges3) == 1
    assert "recalled memory: Run git commands in the specific worktree" in nudges3[0]
