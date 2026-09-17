from agent_workspace.subagents.yield_handoff import (
    SubagentYieldRegistry,
    get_yield_registry,
    sessions_settle,
    sessions_yield,
)


def test_yield_all_settle_protocol():
    registry = SubagentYieldRegistry()
    parent_id = "orch-01"
    child_ids = ["worker-1", "worker-2"]

    registry.register_yield(parent_id, child_ids, wait_condition="all")
    assert registry.is_parent_yielded(parent_id) is True

    # 1. Settle worker-1
    res1 = registry.record_settle("worker-1", "completed", result={"files_found": 10})
    assert res1["parent_woken"] is False
    assert res1["parent_session_id"] == parent_id
    assert registry.is_parent_yielded(parent_id) is True

    # 2. Settle worker-2
    res2 = registry.record_settle("worker-2", "completed", result={"tests_run": 5})
    assert res2["parent_woken"] is True
    assert res2["parent_session_id"] == parent_id
    assert res2["resumption_payload"]["children_settled"] == 2
    assert res2["resumption_payload"]["results"]["worker-1"]["result"] == {"files_found": 10}
    assert res2["resumption_payload"]["results"]["worker-2"]["result"] == {"tests_run": 5}
    assert registry.is_parent_yielded(parent_id) is False


def test_yield_any_settle_protocol():
    registry = SubagentYieldRegistry()
    parent_id = "orch-02"
    child_ids = ["search-fast", "search-deep"]

    registry.register_yield(parent_id, child_ids, wait_condition="any")
    res = registry.record_settle("search-fast", "completed", result="fast match found")
    assert res["parent_woken"] is True
    assert res["resumption_payload"]["results"]["search-fast"]["result"] == "fast match found"


def test_sessions_yield_helpers():
    reg = get_yield_registry()
    reg.clear()

    y_res = sessions_yield("orch-03", ["c1", "c2"])
    assert y_res["action"] == "yield"

    s1 = sessions_settle("c1", "completed", result="done c1")
    assert s1["parent_woken"] is False

    s2 = sessions_settle("c2", "completed", result="done c2")
    assert s2["parent_woken"] is True
    assert s2["resumption_payload"]["children_settled"] == 2
