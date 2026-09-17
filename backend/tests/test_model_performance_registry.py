from agent_workspace.models.performance_registry import (
    DynamicCostLatencyRouter,
    ModelPerformanceRegistry,
)


def test_model_performance_registry_recording():
    registry = ModelPerformanceRegistry()

    # Record 3 runs for gpt-4o on coding
    registry.record_run(
        model_id="gpt-4o",
        task_type="coding",
        success=True,
        tool_successes=5,
        tool_calls=5,
        latency_ms=1000.0,
        cost_usd=0.01,
        verification_score=1.0,
    )
    registry.record_run(
        model_id="gpt-4o",
        task_type="coding",
        success=True,
        tool_successes=4,
        tool_calls=4,
        latency_ms=1200.0,
        cost_usd=0.01,
        verification_score=0.9,
    )
    m = registry.record_run(
        model_id="gpt-4o",
        task_type="coding",
        success=False,
        tool_successes=2,
        tool_calls=3,
        latency_ms=1400.0,
        cost_usd=0.01,
        verification_score=0.5,
    )

    assert m.sample_count == 3
    assert round(m.success_rate, 2) == 0.67
    assert m.avg_latency_ms == 1200.0
    assert m.avg_cost_usd == 0.01
    assert round(m.avg_verification_score, 2) == 0.8


def test_dynamic_cost_latency_router_selection():
    registry = ModelPerformanceRegistry()

    # Fast cheap model
    for _ in range(5):
        registry.record_run(
            model_id="kimi-highspeed",
            task_type="quick_edit",
            success=True,
            tool_successes=2,
            tool_calls=2,
            latency_ms=400.0,
            cost_usd=0.001,
            verification_score=0.95,
        )

    # Slow expensive model
    for _ in range(5):
        registry.record_run(
            model_id="claude-opus-5",
            task_type="quick_edit",
            success=True,
            tool_successes=2,
            tool_calls=2,
            latency_ms=3500.0,
            cost_usd=0.04,
            verification_score=0.98,
        )

    router = DynamicCostLatencyRouter(registry=registry)

    # For quick_edit with budget limit, kimi-highspeed should be selected
    choice = router.select_optimal_model(
        task_type="quick_edit",
        candidate_models=["kimi-highspeed", "claude-opus-5"],
        max_cost_usd=0.01,
    )
    assert choice["selected_model"] == "kimi-highspeed"
