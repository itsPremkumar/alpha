"""Unit tests for Tool Call Repair and Stream Normalizer."""

from agent_workspace.tools.repair.normalizer import ToolCallNormalizer, repair_json_payload
from agent_workspace.tools.repair.promoter import ToolCallPromoter


def test_repair_json_payload_variations():
    # 1. Clean JSON wrapped in markdown
    raw1 = "```json\n{\"command\": \"git status\"}\n```"
    res1 = repair_json_payload(raw1)
    assert res1 == {"command": "git status"}

    # 2. Trailing commas
    raw2 = '{"items": [1, 2, 3,], "active": true,}'
    res2 = repair_json_payload(raw2)
    assert res2 == {"items": [1, 2, 3], "active": True}

    # 3. Python literals True/False/None
    raw3 = '{"enabled": True, "cache": None, "debug": False}'
    res3 = repair_json_payload(raw3)
    assert res3 == {"enabled": True, "cache": None, "debug": False}

    # 4. Unquoted keys
    raw4 = '{status: "ok", code: 200}'
    res4 = repair_json_payload(raw4)
    assert res4 == {"status": "ok", "code": 200}

    # 5. Embedded in noisy text
    raw5 = 'Here is the response: {"query": "SELECT * FROM users"} hope this helps!'
    res5 = repair_json_payload(raw5)
    assert res5 == {"query": "SELECT * FROM users"}


def test_tool_call_normalizer_arguments():
    # Dict unchanged
    assert ToolCallNormalizer.normalize_arguments({"key": "val"}) == {"key": "val"}
    # String repaired
    res = ToolCallNormalizer.normalize_arguments("{arg: 42,}")
    assert res == {"arg": 42}


def test_tool_call_promoter_patterns():
    # 1. XML-style tag
    text1 = "Executing now:\n<tool_call>{\"name\": \"bash\", \"arguments\": {\"command\": \"ls -l\"}}</tool_call>"
    promoted1 = ToolCallPromoter.detect_and_promote(text1)
    assert len(promoted1) == 1
    assert promoted1[0].name == "bash"
    assert promoted1[0].arguments == {"command": "ls -l"}

    # 2. Markdown block with tool name
    text2 = "```json\n{\n  \"tool\": \"python_repl\",\n  \"arguments\": {\"code\": \"x = 10\"\n}\n}\n```"
    promoted2 = ToolCallPromoter.detect_and_promote(text2)
    assert len(promoted2) == 1
    assert promoted2[0].name == "python_repl"
    assert promoted2[0].arguments == {"code": "x = 10"}

    # 3. Action / Action Input pattern
    text3 = "Action: bash\nAction Input: {\"command\": \"git diff\"}"
    promoted3 = ToolCallPromoter.detect_and_promote(text3)
    assert len(promoted3) == 1
    assert promoted3[0].name == "bash"
    assert promoted3[0].arguments == {"command": "git diff"}
