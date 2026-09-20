import json

from alpha.tools.builtins.variation_operator_tool import run_variation_operator_step


def test_variation_operator_tool_actions():
    # 1. Action: knowledge_query
    q_res_str = run_variation_operator_step.invoke({
        "action": "knowledge_query",
        "query_text": "branchless accumulator",
    })
    q_data = json.loads(q_res_str)
    assert "results" in q_data
    assert len(q_data["results"]) > 0
    assert "Branchless" in q_data["results"][0]["title"]

    # 2. Action: vary (successful baseline initialization)
    vary_res_str = run_variation_operator_step.invoke({
        "action": "vary",
        "hypothesis": "Establish initial baseline",
        "modification": "Initial kernel implementation",
        "code_snippet": "__global__ void kernel() {}",
        "metrics_json": json.dumps({"seq_4k": 1000.0, "seq_8k": 1100.0}),
        "correctness": True,
    })
    vary_data = json.loads(vary_res_str)
    assert vary_data["committed"] is True
    assert vary_data["geometric_mean"] > 1000.0
    v1_id = vary_data["version_id"]

    # 3. Action: vary (failed correctness -> strictly zero score and rejected)
    fail_res_str = run_variation_operator_step.invoke({
        "action": "vary",
        "hypothesis": "Broken variation",
        "modification": "Introduced division by zero",
        "code_snippet": "__global__ void kernel() { int x = 1/0; }",
        "metrics_json": json.dumps({"seq_4k": 2000.0, "seq_8k": 2200.0}),
        "correctness": False,
        "parent_id": v1_id,
    })
    fail_data = json.loads(fail_res_str)
    assert fail_data["committed"] is False
    assert fail_data["geometric_mean"] == 0.0

    # 4. Action: inspect_frontier
    frontier_res_str = run_variation_operator_step.invoke({"action": "inspect_frontier"})
    frontier_data = json.loads(frontier_res_str)
    assert frontier_data["frontier_size"] >= 1

    # 5. Action: stats
    stats_res_str = run_variation_operator_step.invoke({"action": "stats"})
    stats_data = json.loads(stats_res_str)
    assert "lineage" in stats_data
    assert "supervisor" in stats_data
    assert "knowledge_base" in stats_data
