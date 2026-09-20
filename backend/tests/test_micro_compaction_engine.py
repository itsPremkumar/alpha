from alpha.context.micro_compaction import apply_micro_compaction, compact_tool_output


def test_compact_tool_output():
    short_out = "File saved successfully."
    res_short = compact_tool_output("write_file", {"path": "a.txt"}, short_out)
    assert res_short == short_out

    long_pytest = (
        "============================= test session starts =============================\n"
        + "\n".join(f"test_{i}.py . [ {i}%]" for i in range(1, 101))
        + "\n============================= 100 passed in 12.5s ============================="
    )
    res_long = compact_tool_output("pytest", {"cmd": "pytest"}, long_pytest)
    assert "[TOOL RECEIPT: pytest" in res_long
    assert "SUCCESS" in res_long
    assert len(res_long) < 250


def test_apply_micro_compaction_pipeline():
    messages = [
        {"role": "user", "content": "Please run the test suite"},
        {"role": "assistant", "content": "Running tests now..."},
        {"role": "tool", "name": "bash", "content": "x" * 1500},
        {"role": "assistant", "content": "Tests finished, now updating README"},
        {"role": "tool", "name": "read_file", "content": "README contents line 1\nline 2"},
        {"role": "user", "content": "What was the final status?"},
    ]

    compacted, tokens_saved = apply_micro_compaction(messages, protected_tail_count=2)
    assert len(compacted) == 6
    # Tool message at index 2 (outside protected tail) was compacted
    assert compacted[2]["micro_compacted"] is True
    assert "[TOOL RECEIPT: bash" in compacted[2]["content"]
    assert len(compacted[2]["content"]) < 250
    assert tokens_saved > 200

    # Recent tail tool message at index 4 was preserved verbatim
    assert "micro_compacted" not in compacted[4]
    assert "README contents line 1" in compacted[4]["content"]
