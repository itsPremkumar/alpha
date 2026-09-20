import json
import tempfile
from pathlib import Path

from alpha.tools.builtins import (
    blackboard_query,
    blackboard_record_evidence,
    check_metacognitive_health,
    compile_cognitive_plan,
    execute_transactional_action,
    inspect_repo_twin,
    run_avo_variation,
)


def test_blackboard_tools_integration():
    # 1. Record evidence
    rec_out = blackboard_record_evidence.invoke({
        "plane_id": "plane_4_deep_research",
        "evidence_type": "empirical_benchmark",
        "content_json": json.dumps({"metric": "accuracy", "val": 0.94}),
        "confidence": 0.95,
    })
    rec_data = json.loads(rec_out)
    assert rec_data["status"] == "recorded"
    assert rec_data["plane_id"] == "plane_4_deep_research"

    # 2. Query evidence
    query_out = blackboard_query.invoke({
        "plane_id": "plane_4_deep_research",
        "min_confidence": 0.90,
    })
    query_data = json.loads(query_out)
    assert query_data["total_evidence_count"] >= 1
    assert any(e["evidence_type"] == "empirical_benchmark" for e in query_data["evidence"])


def test_cognitive_compiler_tool_integration():
    plan_out = compile_cognitive_plan.invoke({
        "goal": "Build distributed rate limiter",
        "risk_tier": "R2",
        "task_dag_json": json.dumps({"init": [], "build": ["init"], "test": ["build"]}),
    })
    plan_data = json.loads(plan_out)
    assert "plan_id" in plan_data
    assert plan_data["goal"] == "Build distributed rate limiter"
    assert len(plan_data["execution_waves"]) == 3
    assert plan_data["chosen_strategy"]["composite_score"] is not None
    assert "recovery_tree" in plan_data


def test_action_transaction_tool_integration():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "version.txt"
        test_file.write_text("v1.0.0", encoding="utf-8")

        # Successful update
        tx_out = execute_transactional_action.invoke({
            "primitive": "update",
            "target_path": str(test_file),
            "content": "v1.1.0",
        })
        tx_data = json.loads(tx_out)
        assert tx_data["status"] == "success"
        assert tx_data["stage"] == "verified"
        assert test_file.read_text(encoding="utf-8") == "v1.1.0"


def test_avo_lineage_tool_integration():
    # Iteration 1: initial baseline commit
    out1 = run_avo_variation.invoke({
        "hypothesis": "Add LRU cache",
        "modification": "lru_cache decor",
        "correctness": True,
        "performance_score": 0.6,
        "quality_score": 0.7,
    })
    data1 = json.loads(out1)
    assert data1["committed"] is True
    assert data1["iteration"] >= 1

    # Iteration 2: broken tests should NOT commit
    out2 = run_avo_variation.invoke({
        "hypothesis": "Aggressive pruning",
        "modification": "broken prune",
        "correctness": False,
        "performance_score": 0.9,
    })
    data2 = json.loads(out2)
    assert data2["committed"] is False


def test_repo_twin_tool_integration():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "pyproject.toml").write_text("[tool.pytest]\n", encoding="utf-8")
        mod_file = root / "core_mod.py"
        mod_file.write_text("def engine(): pass\n", encoding="utf-8")

        out = inspect_repo_twin.invoke({
            "root_path": str(root),
            "target_file_for_blast_radius": str(mod_file),
        })
        data = json.loads(out)
        assert "python/pip/pyproject" in data["reconnaissance"]["build_systems"]
        assert data["total_symbols_indexed"] >= 1
        assert data["blast_radius"] is not None
        assert "core_mod.py" in data["blast_radius"]["target_file"]


def test_metacognitive_tool_integration():
    action_history = [
        {"tool": "bash", "success": False},
        {"tool": "bash", "success": False},
        {"tool": "bash", "success": False},
    ]

    out = check_metacognitive_health.invoke({
        "action_history_json": json.dumps(action_history),
        "current_confidence": 0.92,
        "cognitive_mode": "deliberative",
    })
    data = json.loads(out)
    assert "plan_stagnation" in data["detected_biases"]
    assert "overconfidence" in data["detected_biases"]
    assert data["should_switch_strategy"] is True
    assert data["calibrated_confidence"] <= 0.50
