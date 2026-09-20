"""Tests for per-tool-class pruner tiers (DeepSeek-Harness-style pruner tiers)."""

from __future__ import annotations

import pytest

from alpha.agents.middlewares.tool_output_budget_middleware import (
    ToolOutputBudgetMiddleware,
    _budget_content,
    _effective_trigger,
    _resolve_externalize_threshold,
)
from alpha.config.tool_output_config import ToolOutputConfig


def _config(**kwargs) -> ToolOutputConfig:
    defaults = {"externalize_min_chars": 12_000, "fallback_max_chars": 0}
    defaults.update(kwargs)
    return ToolOutputConfig(**defaults)


class TestResolveExternalizeThreshold:
    def test_global_default_when_no_tier_or_override(self):
        assert _resolve_externalize_threshold("bash", _config()) == 12_000

    def test_tier_wins_over_global(self):
        config = _config(prune_tiers={"bash": 65_536})
        assert _resolve_externalize_threshold("bash", config) == 65_536
        assert _resolve_externalize_threshold("web_fetch", config) == 12_000

    def test_override_wins_over_tier(self):
        config = _config(prune_tiers={"bash": 65_536}, tool_overrides={"bash": 4_000})
        assert _resolve_externalize_threshold("bash", config) == 4_000

    def test_zero_tier_disables_externalization(self):
        config = _config(prune_tiers={"noisy": 0})
        assert _resolve_externalize_threshold("noisy", config) == 0

    def test_negative_tier_rejected_loudly(self):
        with pytest.raises(ValueError):
            ToolOutputConfig(prune_tiers={"bash": -1})


class TestEffectiveTriggerMirrorsTiers:
    def test_trigger_uses_tier(self):
        config = _config(prune_tiers={"bash": 65_536})
        assert _effective_trigger("bash", config) == 65_536
        assert _effective_trigger("other", config) == 12_000

    def test_disabled_tier_leaves_no_externalize_candidate(self):
        config = _config(prune_tiers={"noisy": 0})
        assert _effective_trigger("noisy", config) == -1


class TestBudgetContentWithTiers:
    def test_content_under_tier_passes_through(self, tmp_path):
        config = _config(prune_tiers={"bash": 65_536})
        assert (
            _budget_content(
                "x" * 20_000,
                tool_name="bash",
                tool_call_id="call-1",
                outputs_path=str(tmp_path),
                config=config,
            )
            is None
        )

    def test_content_over_tier_externalizes(self, tmp_path):
        config = _config(prune_tiers={"bash": 100})
        replacement, kind = _budget_content(
            "x" * 1_000,
            tool_name="bash",
            tool_call_id="call-1",
            outputs_path=str(tmp_path),
            config=config,
        )
        assert kind == "externalized"
        assert "read_file" in replacement

    def test_zero_tier_disables_even_when_over_global(self, tmp_path):
        config = _config(externalize_min_chars=100, prune_tiers={"noisy": 0})
        assert (
            _budget_content(
                "x" * 1_000,
                tool_name="noisy",
                tool_call_id="call-1",
                outputs_path=str(tmp_path),
                config=config,
            )
            is None
        )

    def test_override_zero_beats_tier(self, tmp_path):
        config = _config(externalize_min_chars=100, prune_tiers={"bash": 50}, tool_overrides={"bash": 0})
        assert (
            _budget_content(
                "x" * 1_000,
                tool_name="bash",
                tool_call_id="call-1",
                outputs_path=str(tmp_path),
                config=config,
            )
            is None
        )


class TestReleasePolicyIncludesTiers:
    def test_tiers_in_assembly_identity(self):
        middleware = ToolOutputBudgetMiddleware(config=_config(prune_tiers={"bash": 65_536}))
        params = middleware.release_policy_parameters()
        assert params["config"]["prune_tiers"] == {"bash": 65_536}
