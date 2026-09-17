"""Tests for Intent-Driven Category Routing Matrix & Dual Fallbacks."""

import pytest

from agent_workspace.models.category_router import (
    CategoryRouter,
)


def test_resolve_builtin_categories():
    router = CategoryRouter()
    
    # Check ultrabrain
    ub = router.resolve_category("ultrabrain")
    assert ub.name == "ultrabrain"
    assert ub.reasoning_effort == "max"
    assert ub.models[0] == "gpt-4o"

    # Check quick
    qk = router.resolve_category("quick")
    assert qk.name == "quick"
    assert qk.reasoning_effort == "low"
    assert "kimi-highspeed" in qk.models


def test_proactive_availability_filtering():
    router = CategoryRouter()
    # Assume only deepseek-r1 and claude-opus-5 are configured
    available = {"claude-opus-5", "deepseek-r1"}

    resolved = router.resolve_category("deep", available_models=available)
    assert resolved.models[0] == "claude-opus-5"
    assert "gpt-4o" not in resolved.models


def test_reactive_fallback_recovery():
    router = CategoryRouter()
    # Current primary fails with 429
    fallback = router.get_reactive_fallback("ultrabrain", "gpt-4o")
    assert fallback == "claude-opus-5"

    # Second model fails -> falls to third
    fallback_2 = router.get_reactive_fallback("ultrabrain", "claude-opus-5")
    assert fallback_2 == "gpt-5.6-sol"

    # Last model has no further fallback
    assert router.get_reactive_fallback("ultrabrain", "gpt-5.6-sol") is None


def test_unknown_category_raises():
    router = CategoryRouter()
    with pytest.raises(KeyError, match="Unknown category"):
        router.resolve_category("non-existent-category")
