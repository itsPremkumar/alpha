"""Unit tests for :mod:`alpha.config.context_window_config`.

Load-time validation is fail-closed for the same reason ``ContextSize``'s
validator is: a threshold the context can never reach looks like a working
guard, which is worse than no threshold. These tests pin both directions —
the refused values, and the ones that resolve.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from alpha.config.context_window_config import (
    DEFAULT_CRITICAL_FRACTION,
    DEFAULT_ELEVATED_FRACTION,
    DEFAULT_OVER_FRACTION,
    ContextWindowConfig,
)


class TestBandThresholdValidation:
    def test_defaults_are_strictly_increasing(self) -> None:
        cfg = ContextWindowConfig()
        assert cfg.elevated_fraction < cfg.critical_fraction < cfg.over_fraction
        assert cfg.over_fraction < 1.0

    def test_percent_style_fraction_is_refused(self) -> None:
        # The same foot-gun ``ContextSize`` refuses: 80 written where 0.8 was
        # meant resolves to a threshold the context can never reach.
        with pytest.raises(ValidationError, match="must be in \\(0, 1\\]"):
            ContextWindowConfig(critical_fraction=80)

    def test_zero_fraction_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="must be in \\(0, 1\\]"):
            ContextWindowConfig(elevated_fraction=0)

    def test_above_one_fraction_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="must be in \\(0, 1\\]"):
            ContextWindowConfig(over_fraction=1.5)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"elevated_fraction": 0.9, "critical_fraction": 0.5, "over_fraction": 0.95},
            {"elevated_fraction": 0.5, "critical_fraction": 0.5, "over_fraction": 0.95},
            {"elevated_fraction": 0.5, "critical_fraction": 0.95, "over_fraction": 0.8},
        ],
    )
    def test_non_increasing_thresholds_are_refused(self, kwargs: dict) -> None:
        # An inverted set reports a thread as *more* pressured the emptier it
        # gets; equal thresholds make the middle band unreachable.
        with pytest.raises(ValidationError, match="strictly increasing"):
            ContextWindowConfig(**kwargs)

    def test_nan_and_inf_fractions_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            ContextWindowConfig(critical_fraction=float("nan"))
        with pytest.raises(ValidationError):
            ContextWindowConfig(over_fraction=float("inf"))

    def test_unknown_key_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ContextWindowConfig(elevated_threshold=0.6)

    def test_negative_reserves_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            ContextWindowConfig(output_reserve_tokens=-1)

    def test_zero_usable_floor_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ContextWindowConfig(minimum_usable_input_window=0)


class TestProjections:
    def test_band_thresholds_keys_match_the_runtime_kwargs(self) -> None:
        thresholds = ContextWindowConfig().band_thresholds()
        assert set(thresholds) == {"elevated_fraction", "critical_fraction", "over_fraction"}
        # The runtime signature accepts exactly these keywords.
        import inspect

        from alpha.runtime.context_window import classify_context_pressure

        params = set(inspect.signature(classify_context_pressure).parameters)
        assert set(thresholds) <= params

    def test_window_spec_derives_from_the_declared_window(self) -> None:
        cfg = ContextWindowConfig(output_reserve_tokens=1000, next_turn_reserve_tokens=500, minimum_usable_input_window=200)
        spec = cfg.window_spec(declared_input_window=10_000)
        assert spec.usable_input_window == 8_500
        assert spec.reserved_tokens == 1500

    def test_window_spec_tolerates_an_undeclared_window(self) -> None:
        cfg = ContextWindowConfig()
        assert cfg.window_spec(declared_input_window=None).usable_input_window is None

    def test_defaults_expose_the_documented_fractions(self) -> None:
        assert (DEFAULT_ELEVATED_FRACTION, DEFAULT_CRITICAL_FRACTION, DEFAULT_OVER_FRACTION) == (0.60, 0.80, 0.95)
