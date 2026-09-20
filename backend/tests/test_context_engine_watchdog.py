"""Unit tests for Context Engine and Prefix-Preserving Compaction Watchdog."""

from alpha.context.engine import ContextEngine
from alpha.context.projection import ContextProjection
from alpha.context.watchdog import CompactionWatchdog


def test_context_projection_determinism():
    p1 = ContextProjection.compute(system_prompt="Base instructions", goal_id="goal_123")
    p2 = ContextProjection.compute(system_prompt="Base instructions", goal_id="goal_123")
    assert p1.epoch == p2.epoch
    assert len(p1.epoch) == 12

    # Different prompt produces different epoch
    p3 = ContextProjection.compute(system_prompt="Modified instructions", goal_id="goal_123")
    assert p3.epoch != p1.epoch


def test_compaction_watchdog_prefix_preservation():
    watchdog = CompactionWatchdog()

    messages = [
        {"role": "system", "content": "You are Alpha.", "pinned": True},
        {"role": "user", "content": "Turn 1 request"},
        {"role": "assistant", "content": "Turn 1 answer"},
        {"role": "user", "content": "Turn 2 request"},
        {"role": "assistant", "content": "Turn 2 answer"},
        {"role": "user", "content": "Turn 3 request"},
        {"role": "assistant", "content": "Turn 3 answer"},
        {"role": "user", "content": "Turn 4 request"},
        {"role": "assistant", "content": "Turn 4 answer"},
    ]

    compacted = watchdog.compact(messages, keep_recent_count=2)

    # Prefix preserved
    assert compacted[0]["role"] == "system"
    assert compacted[0]["content"] == "You are Alpha."

    # Compacted middle summary message inserted
    assert compacted[1]["role"] == "system"
    assert compacted[1].get("compacted") is True
    assert "COMPACTED MIDDLE TURNS" in compacted[1]["content"]

    # Last 2 turns preserved intact
    assert compacted[-2]["content"] == "Turn 4 request"
    assert compacted[-1]["content"] == "Turn 4 answer"


def test_context_engine_assembly_and_compaction():
    engine = ContextEngine(max_context_tokens=100, compaction_threshold=0.5, keep_recent_count=2)

    # Generate long conversation history exceeding threshold
    history = [
        {"role": "user", "content": f"Turn {i}: " + "verbose output " * 15}
        for i in range(10)
    ]

    result = engine.assemble(
        system_prompt="System Prompt Instructions",
        goal_text="Deliver continuous engine",
        scratchpad="Current state: green",
        history_messages=history,
        goal_id="g_test",
    )

    assert result.is_compacted is True
    assert result.projection.epoch is not None
    # Verify pinned system instructions and active goal are present at top
    assert result.messages[0]["pinned"] is True
    assert "System Prompt Instructions" in result.messages[0]["content"]
    assert "ACTIVE AUTONOMOUS GOAL" in result.messages[1]["content"]
    assert "Deliver continuous engine" in result.messages[1]["content"]
