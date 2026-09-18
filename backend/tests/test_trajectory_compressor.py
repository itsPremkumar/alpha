"""Tests for Atomic Trajectory Compressor."""

from agent_workspace.trajectory.trajectory_compressor import TrajectoryCompactor, TrajectoryTurn


def test_trajectory_compactor_pair_preservation_and_head_tail():
    compactor = TrajectoryCompactor(target_max_tokens=30)

    turns = [
        TrajectoryTurn(role="system", content="System prompt instructions"),
        TrajectoryTurn(role="user", content="Initial user request to solve bug"),
        TrajectoryTurn(role="assistant", content="Let me run search", is_tool_call=True),
        TrajectoryTurn(role="tool", content="Search results output", is_tool_response=True),
        TrajectoryTurn(role="assistant", content="Let me inspect code", is_tool_call=True),
        TrajectoryTurn(role="tool", content="Code inspection output", is_tool_response=True),
        TrajectoryTurn(role="assistant", content="Final solution synthesized"),
    ]

    compacted = compactor.compact_turns(turns, protect_head_turns=2, protect_tail_turns=2)

    assert compacted[0].content == "System prompt instructions"
    assert compacted[1].content == "Initial user request to solve bug"
    assert compacted[-1].content == "Final solution synthesized"

    summary_turns = [t for t in compacted if "[CONTEXT COMPACTION SUMMARY" in t.content]
    assert len(summary_turns) == 1
