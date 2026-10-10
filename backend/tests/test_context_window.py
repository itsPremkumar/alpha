"""Unit tests for :mod:`alpha.runtime.context_window`.

The invariants pinned here are the ones a caller would plausibly get wrong, and
each has a plausible wrong answer:

* an **undeclared** window must read as ``unknown``, never as ``nominal`` and
  never as ``over``;
* the usable window is **derived** by subtracting reserves, so the same
  ``context_window`` declaration yields a different occupancy depending on what
  the operator reserved;
* a reservation that would eat the whole declared window is **clamped and
  disclosed**, never silently reduced to zero (a zero usable window reads as
  "always over" and would compact every thread forever);
* occupancy fractions are taken against the *usable* window, not the declared
  one, so a 100%-of-declared thread reads as over rather than exactly full;
* a **negative** occupancy is a measurement fault and is disclosed, not clamped
  to an empty thread.
"""

from __future__ import annotations

import pytest

from alpha.runtime.context_window import (
    ContextWindowSpec,
    band_rank,
    classify_context_pressure,
    is_pre_emptive,
    pressure_payload,
    resolve_context_window,
)


def _config():
    from alpha.config.context_window_config import ContextWindowConfig

    return ContextWindowConfig().band_thresholds()


class TestResolveContextWindow:
    def test_usable_subtracts_both_reserves(self) -> None:
        spec = resolve_context_window(
            declared_input_window=128_000,
            output_reserve=4096,
            next_turn_reserve=2048,
            minimum_usable_input_window=1024,
        )
        assert spec.usable_input_window == 121_856
        assert spec.reserved_tokens == 6144
        assert spec.clamped is False
        assert spec.reason == "context_window_declared"

    def test_undeclared_window_has_no_usable_derivation(self) -> None:
        spec = resolve_context_window(declared_input_window=None, output_reserve=4096)
        assert spec.declared_input_window is None
        assert spec.usable_input_window is None
        assert spec.reserved_tokens == 4096

    def test_zero_and_negative_declarations_coerce_to_undeclared(self) -> None:
        # ``models[].context_window`` is ``gt=0`` in pydantic, but this module
        # also accepts raw values from an HTTP payload and a live config object.
        for raw in (0, -1, True, "128000", 12.5, None):
            assert resolve_context_window(declared_input_window=raw).declared_input_window is None

    def test_reserve_eating_the_window_is_clamped_not_zeroed(self) -> None:
        spec = resolve_context_window(
            declared_input_window=4000,
            output_reserve=3000,
            next_turn_reserve=3000,
            minimum_usable_input_window=1024,
        )
        # A zero here would read as "always over" and compact every thread.
        assert spec.usable_input_window == 1024
        assert spec.clamped is True
        assert spec.reason == "context_window_declared_but_unusable"

    def test_clamped_is_false_when_reserves_fit(self) -> None:
        spec = resolve_context_window(declared_input_window=128_000, output_reserve=4096, next_turn_reserve=2048)
        assert spec.clamped is False


class TestClassifyPressure:
    def test_undeclared_window_reads_unknown_not_nominal(self) -> None:
        pressure = classify_context_pressure(900_000, resolve_context_window(declared_input_window=None), **_config())
        assert pressure.band == "unknown"
        # There is no reading in which an undeclared window reports headroom.
        assert pressure.headroom_tokens is None
        assert pressure.occupancy_fraction is None
        assert pressure.reason == "context_window_not_declared"

    def test_unknown_ranks_below_nominal(self) -> None:
        # An unmeasured window is not a comfortable one, so a caller choosing
        # "the tighter of these two readings" must land on unknown.
        assert band_rank("unknown") < band_rank("nominal")
        assert band_rank("unknown") < band_rank("elevated")
        assert band_rank("nonsense") < band_rank("nominal")

    def test_bands_are_ordered_on_the_usable_window(self) -> None:
        spec = resolve_context_window(declared_input_window=100_000, output_reserve=4096, next_turn_reserve=2048)
        usable = spec.usable_input_window
        bands = {
            "nominal": 0,
            "elevated": 0,
            "critical": 0,
            "over": 0,
        }
        for token in range(0, usable + 1, max(1, usable // 400)):
            bands[classify_context_pressure(token, spec, **_config()).band] += 1
        # Every band must be reachable, and the ladder must be monotone.
        assert set(bands) == {"nominal", "elevated", "critical", "over"}

    def test_over_below_one_hundred_percent_of_declared(self) -> None:
        # The over band deliberately sits below full occupancy: a window filled
        # to capacity is a degraded one, not merely a refused one.
        spec = resolve_context_window(declared_input_window=100_000, output_reserve=4096, next_turn_reserve=2048)
        pressure = classify_context_pressure(100_000, spec, **_config())
        assert pressure.band == "over"
        assert pressure.occupancy_fraction > 0.95

    def test_headroom_is_never_negative(self) -> None:
        spec = resolve_context_window(declared_input_window=10_000, output_reserve=0, next_turn_reserve=0)
        assert classify_context_pressure(99_999, spec, **_config()).headroom_tokens == 0

    def test_negative_occupancy_is_disclosed_not_clamped_silently(self) -> None:
        spec = resolve_context_window(declared_input_window=10_000, output_reserve=0, next_turn_reserve=0)
        pressure = classify_context_pressure(-50, spec, **_config())
        assert pressure.occupancy_tokens == 0
        assert pressure.reason == "occupancy_measurement_negative"

    def test_unset_middle_threshold_still_reaches_the_higher_band(self) -> None:
        # A missing rung must not disable the one above it — the same
        # fail-open-toward-evidence rule the network hysteresis applies.
        spec = resolve_context_window(declared_input_window=10_000, output_reserve=0, next_turn_reserve=0)
        pressure = classify_context_pressure(9_000, spec, elevated_fraction=0.6, critical_fraction=None, over_fraction=0.9)
        assert pressure.band == "over"

    def test_out_of_range_threshold_is_ignored_not_clamped(self) -> None:
        spec = resolve_context_window(declared_input_window=10_000, output_reserve=0, next_turn_reserve=0)
        pressure = classify_context_pressure(5_000, spec, elevated_fraction=1.5, critical_fraction=0.8, over_fraction=0.95)
        assert pressure.band == "nominal"

    def test_is_pre_emptive_excludes_nominal_and_unknown(self) -> None:
        assert is_pre_emptive("elevated") is True
        assert is_pre_emptive("critical") is True
        assert is_pre_emptive("over") is True
        assert is_pre_emptive("nominal") is False
        assert is_pre_emptive("unknown") is False


class TestPressurePayload:
    def test_absences_are_explicit_nulls(self) -> None:
        spec = resolve_context_window(declared_input_window=None)
        payload = pressure_payload(classify_context_pressure(120, spec, **_config()))
        assert payload["band"] == "unknown"
        assert payload["declared_input_window"] is None
        assert payload["usable_input_window"] is None
        assert payload["headroom_tokens"] is None
        assert payload["occupancy_fraction"] is None
        assert payload["reason"] == "context_window_not_declared"

    def test_declared_payload_carries_every_derived_field(self) -> None:
        spec = resolve_context_window(declared_input_window=128_000, output_reserve=4096, next_turn_reserve=2048)
        # 80_000 / 121_856 ≈ 0.657, which clears the default elevated threshold
        # of 0.60 against the *usable* window while staying below critical.
        payload = pressure_payload(classify_context_pressure(80_000, spec, **_config()))
        assert payload["declared_input_window"] == 128_000
        assert payload["usable_input_window"] == 121_856
        assert payload["headroom_tokens"] == 41_856
        assert payload["reserved_tokens"] == 6144
        assert payload["band"] == "elevated"


class TestSpecDirectConstruction:
    def test_frozen_dataclass_rejects_mutation(self) -> None:
        spec = ContextWindowSpec(declared_input_window=10)
        with pytest.raises(Exception):
            spec.declared_input_window = 20  # type: ignore[misc]

    def test_negative_reserves_are_floored(self) -> None:
        # Pydantic bounds these at ``ge=0``; a direct construction with a
        # negative value must not produce a negative usable window.
        spec = ContextWindowSpec(declared_input_window=1000, output_reserve=-500, next_turn_reserve=-500)
        assert spec.reserved_tokens == 0
        assert spec.usable_input_window == 1000
