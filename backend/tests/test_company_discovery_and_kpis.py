"""Comprehensive tests for Continuous Work Discovery & KPI Autonomous Loops."""

from __future__ import annotations

from agent_workspace.company.discovery import ContinuousWorkDiscoveryEngine
from agent_workspace.company.kpi import KPIEngine
from agent_workspace.company.models import WorkCategory, WorkPriority


def test_continuous_work_discovery_and_scoring():
    engine = ContinuousWorkDiscoveryEngine()
    signals = [
        {
            "title": "Critical RCE vulnerability detected in markdown parser",
            "category": "security",
            "impact": 0.95,
            "urgency": 0.95,
            "value": 0.90,
            "risk": 0.10,
            "cost": 0.10,
        },
        {
            "title": "Clean up whitespace in legacy comments",
            "category": "maintenance",
            "impact": 0.10,
            "urgency": 0.10,
            "value": 0.10,
            "risk": 0.05,
            "cost": 0.80,
        },
    ]

    items, should_sleep = engine.discover_from_sources(signals)
    assert len(items) >= 1
    # Security item should be prioritized high
    top_item = items[0]
    assert top_item.category == WorkCategory.SECURITY
    assert top_item.priority == WorkPriority.DO_NOW
    assert top_item.score >= 0.70
    assert should_sleep is False


def test_work_discovery_zero_wasted_compute_rule():
    engine = ContinuousWorkDiscoveryEngine()
    # Empty signals
    items, should_sleep = engine.discover_from_sources([])
    assert len(items) == 0
    # Zero Wasted Compute Rule: workers should sleep
    assert should_sleep is True


def test_kpi_engine_autonomous_corrective_work_trigger():
    engine = KPIEngine()

    # Normal update (above threshold) -> no corrective task
    kpi_norm, no_task = engine.update_metric("kpi-availability", 99.92)
    assert kpi_norm.current_value == 99.92
    assert no_task is None

    # Critical breach (availability falls below 99.0% threshold) -> triggers corrective task
    kpi_breach, task = engine.update_metric("kpi-availability", 98.40)
    assert kpi_breach.current_value == 98.40
    assert kpi_breach.trend == "deteriorating"
    assert task is not None
    assert "System Availability" in task["title"]
    assert task["department"] == "sre"
    assert task["urgency"] == "critical"

    # Verify task recorded in history
    assert len(engine.get_triggered_tasks()) == 1
