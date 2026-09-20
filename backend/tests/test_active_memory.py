from pathlib import Path

from alpha.memory.active_memory import ActiveMemoryRouter


def test_tier1_deterministic_resolution(tmp_path: Path):
    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text(
        "# Core Memory\n"
        "- Deploy script requires AWS_REGION=us-east-1 and credentials configured.\n"
        "- Database connection pool max size is 25 connections.\n",
        encoding="utf-8",
    )

    router = ActiveMemoryRouter(memory_files=[mem_file], escalation_threshold=0.50)

    # Query with exact matching keywords
    res = router.query("What is the AWS_REGION for deploy script?")
    assert res.tier == 1
    assert res.escalated is False
    assert len(res.matches) >= 1
    assert "AWS_REGION=us-east-1" in res.matches[0]


def test_tier2_escalation_trigger(tmp_path: Path):
    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text(
        "# Core Memory\n"
        "- Some unrelated note about redis caching.\n",
        encoding="utf-8",
    )

    router = ActiveMemoryRouter(memory_files=[mem_file], escalation_threshold=0.70)

    def mock_tier2_agent(q: str, context_lines: list[str]) -> str:
        return f"Synthesized deep reasoning answer for: {q}"

    # Query with low overlap against unrelated notes
    res = router.query("How did our security architecture evolve across sprints?", escalation_handler=mock_tier2_agent)
    assert res.tier == 2
    assert res.escalated is True
    assert "Synthesized deep reasoning answer" in res.matches[0]
    assert "below threshold" in res.reason
