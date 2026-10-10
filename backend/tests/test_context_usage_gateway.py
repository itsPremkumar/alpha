"""Gateway-level tests for the context-window pressure surface.

These pin the *wiring*, not the arithmetic (that is
``tests/test_context_window.py``). The arithmetic is easy to get right in
isolation and wrong at the seam, so what is pinned here is which of the three
honest states a caller receives:

* the policy is absent (an older Gateway or ``context_window.enabled: false``)
  → ``None``, never a default band;
* the model declares no window → ``band == "unknown"``, every derived field
  ``None``;
* the model declares a window → the full derived block.
"""

from __future__ import annotations

import pytest

from app.gateway.context_usage import (
    build_context_usage_payload,
    build_pressure_payload,
    resolve_window_config,
)


class _Policy:
    """A stand-in for ``ContextWindowConfig`` that need not be pydantic."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def band_thresholds(self) -> dict[str, float]:
        return {"elevated_fraction": 0.60, "critical_fraction": 0.80, "over_fraction": 0.95}

    def window_spec(self, *, declared_input_window):
        return build_spec(declared_input_window)


def build_spec(declared):
    from alpha.runtime.context_window import resolve_context_window

    return resolve_context_window(
        declared_input_window=declared,
        output_reserve=4096,
        next_turn_reserve=2048,
        minimum_usable_input_window=1024,
    )


class _AppConfig:
    def __init__(self, *, section=None) -> None:
        if section is not None:
            self.context_window = section


class TestResolveWindowConfig:
    def test_present_section_is_returned(self) -> None:
        policy = _Policy()
        assert resolve_window_config(_AppConfig(section=policy)) is policy

    def test_missing_section_is_none_not_a_default(self) -> None:
        # An older Gateway has no section at all; a caller must read that as
        # "no policy reported" rather than assuming the shipped defaults.
        assert resolve_window_config(_AppConfig()) is None

    def test_older_config_object_without_the_attribute(self) -> None:
        class Legacy:
            models: list = []

        assert resolve_window_config(Legacy()) is None


class TestBuildPressurePayload:
    def test_disabled_policy_returns_none(self) -> None:
        assert build_pressure_payload(token_count=100, declared_input_window=128_000, window_config=_Policy(enabled=False)) is None

    def test_absent_policy_returns_none(self) -> None:
        assert build_pressure_payload(token_count=100, declared_input_window=128_000, window_config=None) is None

    def test_undeclared_window_reads_unknown_with_nulls(self) -> None:
        payload = build_pressure_payload(token_count=5_000, declared_input_window=None, window_config=_Policy())
        assert payload is not None
        assert payload["band"] == "unknown"
        assert payload["declared_input_window"] is None
        assert payload["usable_input_window"] is None
        assert payload["headroom_tokens"] is None
        assert payload["occupancy_fraction"] is None
        assert payload["reason"] == "context_window_not_declared"

    def test_declared_window_reports_headroom_against_the_usable_window(self) -> None:
        payload = build_pressure_payload(token_count=80_000, declared_input_window=128_000, window_config=_Policy())
        assert payload is not None
        assert payload["band"] == "elevated"
        assert payload["usable_input_window"] == 121_856
        assert payload["headroom_tokens"] == 41_856

    def test_broken_policy_degrades_to_none_not_a_band(self) -> None:
        class Broken:
            enabled = True

            def band_thresholds(self):
                raise RuntimeError("bad config")

            def window_spec(self, *, declared_input_window):
                raise RuntimeError("bad config")

        # The usage route still answers its percentage block; only the pressure
        # block reports its own absence rather than a band nobody computed.
        assert build_pressure_payload(token_count=100, declared_input_window=128_000, window_config=Broken()) is None


class TestContextUsagePayload:
    def test_percentage_stays_none_when_the_window_is_undeclared(self) -> None:
        payload = build_context_usage_payload(token_count=5_000, max_context_tokens=None)
        assert payload["token_count"] == 5_000
        assert payload["max_context_tokens"] is None
        assert payload["percentage"] is None

    def test_percentage_uses_the_declared_window(self) -> None:
        payload = build_context_usage_payload(token_count=12_800, max_context_tokens=128_000)
        assert payload["percentage"] == 10.0

    @pytest.mark.parametrize("window", [0, -1])
    def test_zero_window_never_yields_a_percentage(self, window: int) -> None:
        assert build_context_usage_payload(token_count=10, max_context_tokens=window)["percentage"] is None
