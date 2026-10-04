"""A provider-exhaustion message must say why there was nothing to fail over to.

The defect this pins
-------------------
`config.yaml` declares `models[].model: free:opencode-zen:space-bunny-free` for
the model the operator named `alpha-free`, while the same catalog advertises
**45 healthy free models across 8 providers** and the picker entry for
`alpha-free` is described as "Dynamic auto-routing across verified keyless
providers with failover".

A live run therefore failed with:

    FreeLLMUnavailableError: all 1 free provider attempt(s) failed (1 now cooling down)

`free:<provider>:<model>` is a **pin**: every other provider is dropped from the
candidate list because it does not offer that model id, so the chain has exactly
one member and one HTTP 429 ends the run. That is defensible behaviour. What is
not defensible is the message: it reads as a transient blip on a router with
failover, and it sent the whole investigation looking for a circuit-breaker bug
instead of at the pin in `config.yaml`.

So the exhaustion message now names the pin and the alternative. These tests pin
that honesty, and pin that a genuine multi-candidate chain is *not* given the
same advice.
"""

from __future__ import annotations

import pytest

from alpha.models.free_router.catalog import FreeLLMRouter


class _Spec:
    def __init__(self, name: str, models: list[str]) -> None:
        self.name = name
        self.documented_models = models


class _State:
    def __init__(self, models: list[str]) -> None:
        self.models = [{"id": m} for m in models]
        self.healthy = True
        self.cooldown_until = 0.0


@pytest.fixture
def router() -> FreeLLMRouter:
    r = FreeLLMRouter.__new__(FreeLLMRouter)
    r._states = {
        "alpha": _State(["alpha-auto", "alpha-other"]),
        "beta": _State(["beta-only"]),
    }
    r._loaded = True
    r._last_refresh = 9e18
    r._ttl = 3600.0
    return r


def _reason(router: FreeLLMRouter, model: str, provider: str | None, attempted: int) -> str:
    return router._no_failover_reason(model, provider, attempted)


def test_a_pinned_single_candidate_names_the_pin(router: FreeLLMRouter) -> None:
    reason = _reason(router, "free:alpha:alpha-auto", None, 1)
    assert "alpha" in reason
    assert "auto" in reason, "the message must offer the actionable alternative"
    assert "nothing to fail over to" in reason


def test_the_pin_is_reported_by_provider_name_not_the_whole_spec(router: FreeLLMRouter) -> None:
    """`free:alpha:alpha-auto` must read as `alpha`, not as the whole spec."""
    reason = _reason(router, "free:alpha:alpha-auto", None, 1)
    assert "free:alpha:alpha-auto" not in reason


def test_a_multi_candidate_chain_is_not_given_pin_advice(router: FreeLLMRouter) -> None:
    """Three candidates failed: that is a real outage, not a configuration fact."""
    assert _reason(router, "free:alpha:alpha-auto", None, 3) == ""


def test_a_bare_pin_without_a_provider_segment_still_explains(router: FreeLLMRouter) -> None:
    reason = _reason(router, "free:alpha", None, 1)
    assert "alpha" in reason
    assert "nothing to fail over to" in reason


def test_an_explicit_target_provider_wins(router: FreeLLMRouter) -> None:
    reason = _reason(router, "alpha-auto", "beta", 1)
    assert "beta" in reason


def test_one_candidate_with_no_pin_reports_the_real_cause(router: FreeLLMRouter) -> None:
    """No pin, one candidate: the honest cause is that nothing else had models."""
    reason = _reason(router, "auto", None, 1)
    assert "only one provider had discovered models" in reason
    assert "nothing to fail over to" not in reason


def test_the_real_deployment_pin_produces_an_actionable_message() -> None:
    """The exact spec from `config.yaml`."""
    r = FreeLLMRouter.__new__(FreeLLMRouter)
    reason = r._no_failover_reason("free:opencode-zen:space-bunny-free", None, 1)
    assert "opencode-zen" in reason
    assert "single-endpoint pin" in reason
    assert "'auto'" in reason


def test_candidates_still_pin_rather_than_silently_widening() -> None:
    """The honesty fix must not change which providers are attempted.

    A message that says "you pinned one endpoint" while the router quietly tried
    forty-five others would be a different lie.
    """
    r = FreeLLMRouter.__new__(FreeLLMRouter)
    r._states = {"alpha": _State(["alpha-auto"]), "beta": _State(["beta-only"])}
    r._loaded = True
    r._last_refresh = 9e18
    r._ttl = 3600.0
    import threading

    r._lock = threading.Lock()
    r._clock = lambda: 1000.0
    r._layer = type("L", (), {"PROVIDERS": {"alpha": _Spec("alpha", ["alpha-auto"]), "beta": _Spec("beta", ["beta-only"])}, "PROVIDER_ORDER": ["alpha", "beta"]})()

    pairs = r.candidates(model="free:alpha:alpha-auto")
    assert [spec.name for spec, _ in pairs] == ["alpha"], "a pinned spec must not widen"

    # ...while an unpinned `auto` request does route across providers.
    pairs_auto = r.candidates(model="auto")
    assert len(pairs_auto) == 2, [s.name for s, _ in pairs_auto]
