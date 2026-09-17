from agent_workspace.harness.continuous.loop_detector import (
    ToolLoopDetector,
    hash_tool_call,
)


def test_hash_tool_call_ignores_volatile():
    h1 = hash_tool_call("bash", {"command": "ls", "timestamp": 12345}, {"output": "files", "elapsed_ms": 50})
    h2 = hash_tool_call("bash", {"command": "ls", "timestamp": 99999}, {"output": "files", "elapsed_ms": 990})
    assert h1 == h2

def test_hash_tool_call_distinguishes_different_inputs():
    h1 = hash_tool_call("bash", {"command": "ls"}, {"output": "files"})
    h2 = hash_tool_call("bash", {"command": "pwd"}, {"output": "files"})
    assert h1 != h2

def test_consecutive_duplicate_limit():
    detector = ToolLoopDetector(consecutive_duplicate_limit=3, failure_streak_limit=5)
    
    # 1st call
    interv = detector.record_and_check("read_file", {"path": "test.txt"}, {"content": "hello"})
    assert interv is None
    
    # 2nd call
    interv = detector.record_and_check("read_file", {"path": "test.txt"}, {"content": "hello"})
    assert interv is None
    
    # 3rd identical call triggers loop breaker
    interv = detector.record_and_check("read_file", {"path": "test.txt"}, {"content": "hello"})
    assert interv is not None
    assert interv.action == "pivot_strategy"
    assert "consecutive times" in interv.reason

def test_failure_streak_limit():
    detector = ToolLoopDetector(consecutive_duplicate_limit=5, failure_streak_limit=3)
    
    interv1 = detector.record_and_check("http_request", {"url": "http://a"}, {"error": "Connection refused"})
    assert interv1 is None
    interv2 = detector.record_and_check("http_request", {"url": "http://b"}, {"error": "404 Not Found"})
    assert interv2 is None
    interv3 = detector.record_and_check("http_request", {"url": "http://c"}, {"error": "500 Server Error"})
    assert interv3 is not None
    assert interv3.action == "pivot_strategy"
    assert "consecutive errors" in interv3.reason

def test_post_compaction_guard():
    detector = ToolLoopDetector(consecutive_duplicate_limit=5, failure_streak_limit=5, post_compaction_guard=True)
    
    # Run a failing tool before compaction
    detector.record_and_check("compile", {"target": "main.c"}, {"error": "syntax error on line 1"})
    
    # Notify compaction happened
    detector.notify_compaction()
    
    # Agent tries exact same failing action immediately after compaction
    interv = detector.record_and_check("compile", {"target": "main.c"}, {"error": "syntax error on line 1"})
    assert interv is not None
    assert interv.action == "pivot_strategy"
    assert "post-compaction" in interv.reason
