"""Tests for Multi-stage Pipeline Context Condenser."""

from agent_workspace.context.condenser.pipeline import PipelineCondenser
from agent_workspace.context.condenser.pruner import DeterministicPruner
from agent_workspace.context.condenser.summarizer import StructuredStateCondenser, WorkingState
from agent_workspace.context.condenser.truncator import HeadTailBudgetTruncator


def test_deterministic_pruner():
    pruner = DeterministicPruner(keep_last_n_observations=2)

    messages = [
        {"role": "user", "content": "list directory contents"},
        {"role": "tool", "name": "list_dir", "content": "file1.py\nfile2.py\n" + "long line\n" * 50},
        {"role": "assistant", "content": "I see file1.py"},
        {"role": "tool", "name": "view_file", "content": "def foo():\n    pass\n" + "# extra line\n" * 40},
        {"role": "assistant", "content": "Examining foo"},
        {"role": "tool", "name": "view_file", "content": "def bar():\n    return 42"},
        {"role": "tool", "name": "run_command", "content": "tests passed"},
    ]

    pruned = pruner.prune(messages)
    # Total message count remains identical
    assert len(pruned) == len(messages)
    # The first two tool observations should be pruned
    assert pruned[1].get("pruned") is True
    assert "[Pruned prior output from 'list_dir'" in pruned[1]["content"]
    assert pruned[3].get("pruned") is True
    # The last two observations must be untouched
    assert not pruned[5].get("pruned")
    assert not pruned[6].get("pruned")


def test_head_tail_truncator():
    truncator = HeadTailBudgetTruncator(max_budget_chars=500, keep_head_turns=2, keep_tail_turns=2)

    messages = [
        {"role": "system", "content": "You are DeerFlow."},
        {"role": "user", "content": "Fix bug in payment logic."},
    ]
    # Add 10 bulky middle messages
    for i in range(10):
        messages.append({"role": "assistant", "content": f"Analyzing step {i} " + "X" * 100})

    messages.append({"role": "assistant", "content": "I found the bug."})
    messages.append({"role": "user", "content": "Please verify the patch."})

    truncated = truncator.truncate(messages)
    # Should contain: 2 head + 1 notice + 2 tail = 5 messages
    assert len(truncated) == 5
    assert truncated[0]["content"] == "You are DeerFlow."
    assert truncated[1]["content"] == "Fix bug in payment logic."
    assert truncated[2].get("truncated") is True
    assert "intermediate historical turns were truncated" in truncated[2]["content"]
    assert truncated[-1]["content"] == "Please verify the patch."


def test_structured_state_condenser():
    condenser = StructuredStateCondenser()

    messages = [
        {"role": "user", "content": "Fix the database lock error in repository.py"},
        {"role": "assistant", "tool_calls": [{"name": "replace_file_content", "args": {"TargetFile": "src/repo.py"}}]},
        {"role": "tool", "content": "OperationalError: database table is locked"},
    ]

    state = condenser.condense(messages)
    assert "Fix the database lock error" in state.goal
    assert "src/repo.py" in state.modified_files
    assert any("database table is locked" in err for err in state.encountered_errors)

    md = state.to_markdown()
    assert "Context State Summary" in md
    assert "src/repo.py" in md


def test_pipeline_condenser_full():
    pipeline = PipelineCondenser(max_budget_chars=1000)

    messages = [
        {"role": "system", "content": "Harness instructions."},
        {"role": "user", "content": "Refactor router endpoints."},
    ]
    for i in range(15):
        messages.append({"role": "tool", "name": "view_file", "content": f"line content {i}\n" * 30})

    condensed_messages, state = pipeline.condense(messages)
    assert len(condensed_messages) < len(messages)
    assert any(m.get("is_state_summary") for m in condensed_messages)
    assert isinstance(state, WorkingState)
