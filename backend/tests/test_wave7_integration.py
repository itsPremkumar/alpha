import json

from agent_workspace.tools.builtins import (
    execute_sandboxed_computer_action,
    manage_durable_orchestration,
    manage_model_performance_registry,
    query_knowledge_graph,
    run_task_evaluation_benchmark,
)


def test_manage_model_performance_registry_tool():
    # 1. Record run
    res_rec = manage_model_performance_registry.invoke({
        "action": "record_run",
        "model_id": "test-gpt-6",
        "task_type": "coding",
        "success": True,
        "tool_successes": 3,
        "tool_calls": 3,
        "latency_ms": 1100.0,
        "cost_usd": 0.008,
        "verification_score": 1.0,
    })
    data_rec = json.loads(res_rec)
    assert data_rec["status"] == "recorded"

    # 2. Select optimal model
    res_sel = manage_model_performance_registry.invoke({
        "action": "select_optimal_model",
        "task_type": "coding",
        "candidate_models_csv": "test-gpt-6, other-model",
    })
    data_sel = json.loads(res_sel)
    assert "selected_model" in data_sel


def test_query_knowledge_graph_tool():
    # 1. Add Entity
    res_ent = query_knowledge_graph.invoke({
        "action": "add_entity",
        "entity_id": "service:billing",
        "entity_name": "Billing Service",
        "entity_type": "service",
    })
    assert json.loads(res_ent)["status"] == "entity_added"

    # 2. Add Vendor
    res_ven = query_knowledge_graph.invoke({
        "action": "add_entity",
        "entity_id": "vendor:stripe_corp",
        "entity_name": "Stripe",
        "entity_type": "vendor",
    })
    assert json.loads(res_ven)["status"] == "entity_added"

    # 3. Add Relation
    res_rel = query_knowledge_graph.invoke({
        "action": "add_relation",
        "source_id": "service:billing",
        "relation_type": "contracts_with",
        "target_id": "vendor:stripe_corp",
    })
    assert json.loads(res_rel)["status"] == "relation_added"

    # 4. Query Dependencies
    res_deps = query_knowledge_graph.invoke({
        "action": "query_dependencies",
        "entity_id": "service:billing",
    })
    data_deps = json.loads(res_deps)
    assert data_deps["total_dependencies"] == 1


def test_run_task_evaluation_benchmark_tool():
    # 1. List benchmarks
    res_list = run_task_evaluation_benchmark.invoke({"action": "list_benchmarks"})
    benchmarks = json.loads(res_list)
    assert len(benchmarks) >= 6

    # 2. Evaluate run
    res_eval = run_task_evaluation_benchmark.invoke({
        "action": "evaluate_run",
        "task_id": "research_001",
        "agent_response": "Synthesized architecture between OmO and Hermes with memory and orchestration differences.",
        "tool_calls_json": json.dumps([{"tool_name": "search_web", "exit_code": 0}]),
        "elapsed_time_sec": 5.0,
        "cost_usd": 0.01,
        "exit_code": 0,
    })
    data_eval = json.loads(res_eval)
    assert data_eval["status"] == "evaluated"
    assert data_eval["result"]["success"] is True


def test_execute_sandboxed_computer_action_tool():
    # 1. Classify forbidden
    res_cls = execute_sandboxed_computer_action.invoke({
        "action": "classify_command",
        "command": "rm -rf /",
    })
    assert json.loads(res_cls)["tier"] == "forbidden"

    # 2. Execute safe command
    res_safe = execute_sandboxed_computer_action.invoke({
        "action": "execute_command",
        "command": "pytest --version",
    })
    assert json.loads(res_safe)["status"] == "executed"


def test_manage_durable_orchestration_tool():
    # 1. Append event
    res_ev = manage_durable_orchestration.invoke({
        "action": "append_event",
        "task_id": "task_int_test",
        "event_type": "TASK_STARTED",
        "payload_json": json.dumps({"goal": "build feature"}),
        "idempotency_key": "step_0",
    })
    assert json.loads(res_ev)["status"] == "event_appended"

    # 2. Save checkpoint
    res_cp = manage_durable_orchestration.invoke({
        "action": "save_checkpoint",
        "task_id": "task_int_test",
        "step_index": 1,
        "state": "running",
        "payload_json": json.dumps({"var_a": 42}),
    })
    assert json.loads(res_cp)["status"] == "checkpoint_saved"

    # 3. Recover task
    res_rec = manage_durable_orchestration.invoke({
        "action": "recover_task",
        "task_id": "task_int_test",
    })
    data_rec = json.loads(res_rec)
    assert data_rec["status"] == "task_recovered"
    assert data_rec["recovery"]["restored_variables"]["var_a"] == 42
