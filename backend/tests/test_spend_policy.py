"""Tests for :mod:`alpha.budgets.spend` — the zero-cost spend policy.

The properties pinned here, and why each one is load-bearing:

1. **An unpriced model reports ``None``, never ``0.0``.** The two readings point
   opposite ways — "costs nothing" vs "we do not know" — and conflating them is
   how a zero-cost budget silently becomes an unlimited one.
2. **An unpriced model is never *assumed* free.** Absence of a price is not a
   price of zero.
3. **Zero on BOTH axes is free; free-input/paid-output is not.** Providers label
   the latter ``:free`` too, which is why ``models/discovery.py`` computes it
   rather than trusting the label.
4. **The refusal names the namespace that decided**, so an operator can resolve
   a config conflict rather than guess.
5. **The gate is inert by default.** With no ``spend:`` section the mode is
   ``budgeted``, which is today's behaviour — adding this package must change
   nothing for an existing deployment.
6. **A paid model is never constructed in ``zero_cost``**, verified through the
   real ``create_chat_model`` rather than by calling the predicate directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from alpha.budgets.spend import (
    FreeVerdict,
    PaidCallRefused,
    SpendMode,
    SpendPolicyConfig,
    estimate_cost_usd,
    is_model_free,
    refuse_paid_model,
    resolve_spend_policy,
)

# --------------------------------------------------------------------------
# Minimal AppConfig stand-ins
# --------------------------------------------------------------------------


@dataclass
class _Gateway:
    id: str
    documented_models: list[str] = field(default_factory=list)


@dataclass
class _Priced:
    name: str
    pricing: Any = None


@dataclass
class _Config:
    """Only the attributes `spend.py` reads.

    A real ``AppConfig`` is far larger; stubbing just these keeps the test
    about the policy rather than about config construction.
    """

    models: list[_Priced] = field(default_factory=list)
    model_pricing: dict[str, Any] = field(default_factory=dict)
    free_gateways: list[_Gateway] = field(default_factory=list)
    spend: Any = None


def _block(input_per_million: float, output_per_million: float) -> dict[str, float]:
    """``model_pricing:`` shape — per-million values under ``input``/``output``."""
    return {"input": input_per_million, "output": output_per_million}


def _inline_block(input_per_million: float, output_per_million: float) -> dict[str, float]:
    """``models[].pricing`` shape — per-million values under the ``*_per_million`` keys.

    Both namespaces are priced **per million** and differ only in key names.
    Reading one shape for both is how a configured price silently prices
    nothing, which is what these two helpers exist to keep pinned.
    """
    return {"input_per_million": input_per_million, "output_per_million": output_per_million}


# --------------------------------------------------------------------------
# 1. Pricing estimation: None is a real answer
# --------------------------------------------------------------------------


class TestEstimateCost:
    def test_unpriced_model_is_none_not_zero(self) -> None:
        """The single most important assertion in this file."""
        config = _Config(model_pricing={"gpt-4o": _block(2.5, 10.0)})
        assert estimate_cost_usd("some-unlisted-model", 1000, 1000, config=config) is None

    def test_config_pricing_is_per_million_converted_to_per_thousand(self) -> None:
        """`input: 3.0` means $3 per 1M, so 1k in + 1k out costs $0.018."""
        config = _Config(model_pricing={"claude-3-7-sonnet": _block(3.0, 15.0)})
        assert estimate_cost_usd("claude-3-7-sonnet", 1000, 1000, config=config) == pytest.approx(0.018)

    def test_model_entry_pricing_is_honoured(self) -> None:
        """The inline ``*_per_million`` keys are the other half of the trap.

        ``0.15/1M`` in and ``0.6/1M`` out for 1k each is $0.00075, not $0.75.
        """
        config = _Config(models=[_Priced(name="m1", pricing=_inline_block(0.15, 0.6))])
        assert estimate_cost_usd("m1", 1000, 1000, config=config) == pytest.approx(0.00075)

    def test_both_namespaces_agree_on_the_same_price(self) -> None:
        """The two key shapes must not produce two different answers."""
        inline = _Config(models=[_Priced(name="m", pricing=_inline_block(3.0, 15.0))])
        table = _Config(model_pricing={"m": _block(3.0, 15.0)})
        assert estimate_cost_usd("m", 1000, 1000, config=inline) == estimate_cost_usd("m", 1000, 1000, config=table)

    def test_malformed_pricing_is_unpriced_not_zero(self) -> None:
        config = _Config(model_pricing={"broken": {"input": "free", "output": None}})
        assert estimate_cost_usd("broken", 1000, 1000, config=config) is None

    def test_negative_pricing_is_refused(self) -> None:
        config = _Config(model_pricing={"weird": _block(-1.0, 5.0)})
        assert estimate_cost_usd("weird", 1000, 1000, config=config) is None

    def test_zero_priced_model_costs_zero(self) -> None:
        """Zero on both axes is a real price, not an absence."""
        config = _Config(model_pricing={"free-model": _block(0.0, 0.0)})
        assert estimate_cost_usd("free-model", 1000, 1000, config=config) == 0.0

    def test_an_empty_model_name_is_unpriced(self) -> None:
        assert estimate_cost_usd("", 1000, 1000, config=_Config()) is None

    def test_negative_token_counts_never_produce_a_negative_cost(self) -> None:
        config = _Config(model_pricing={"m": _block(3.0, 15.0)})
        assert estimate_cost_usd("m", -50, -50, config=config) == 0.0


# --------------------------------------------------------------------------
# 2. Free verdicts carry their source
# --------------------------------------------------------------------------


class TestFreeVerdict:
    def test_a_local_model_is_free(self) -> None:
        verdict = is_model_free("ollama/qwen3:32b", config=_Config())
        assert verdict.is_free is True
        assert verdict.source == "local_model"

    def test_a_documented_free_gateway_model_is_free(self) -> None:
        config = _Config(free_gateways=[_Gateway(id="kilo", documented_models=["poolside/laguna-s-2.1:free"])])
        verdict = is_model_free("poolside/laguna-s-2.1:free", config=config)
        assert verdict.is_free is True
        assert verdict.source == "free_gateways"
        assert "kilo" in verdict.reason

    def test_a_priced_model_is_not_free(self) -> None:
        config = _Config(model_pricing={"gpt-4o": _block(2.5, 10.0)})
        verdict = is_model_free("gpt-4o", config=config)
        assert verdict.is_free is False
        assert verdict.source == "priced"

    def test_an_unpriced_model_is_not_assumed_free(self) -> None:
        """Absence of a price is not a price of zero."""
        verdict = is_model_free("mystery-model", config=_Config())
        assert verdict.is_free is False
        assert verdict.source == "unpriced"
        assert "never assumed free" in verdict.reason

    def test_zero_price_on_both_axes_is_free(self) -> None:
        config = _Config(model_pricing={"free-thing": _block(0.0, 0.0)})
        verdict = is_model_free("free-thing", config=config)
        assert verdict.is_free is True
        assert verdict.source == "zero_price"

    def test_free_input_paid_output_is_not_free(self) -> None:
        """A `:free` label with a paid output tier is still a paid model.

        This is the rule ``models/discovery.py`` already applies when it computes
        "free is zero on *both* prices"; the label is not evidence.
        """
        config = _Config(model_pricing={"model-x:free": _block(0.0, 1.0)})
        verdict = is_model_free("model-x:free", config=config)
        assert verdict.is_free is False
        assert verdict.source == "priced"

    def test_the_free_suffix_resolves_only_through_a_declared_gateway(self) -> None:
        """A ``:free`` suffix with no gateway configured proves nothing."""
        verdict = is_model_free("some/model:free", config=_Config())
        assert verdict.is_free is False

    def test_an_unnamed_model_is_not_free(self) -> None:
        verdict = is_model_free("", config=_Config())
        assert verdict.is_free is False
        assert verdict.source == "unnamed"

    def test_the_verdict_is_truthy_so_a_bare_bool_still_works(self) -> None:
        config = _Config(model_pricing={"gpt-4o": _block(2.5, 10.0)})
        assert not is_model_free("gpt-4o", config=config)
        assert bool(FreeVerdict(True, "x", "y")) is True

    def test_the_check_order_prefers_local_over_price(self) -> None:
        """A local model that also appears in ``model_pricing`` is still free.

        Operator hardware costs nothing regardless of a stale price entry, and
        the local verdict is the more trustworthy of the two.
        """
        config = _Config(model_pricing={"ollama/m": _block(1.0, 2.0)})
        assert is_model_free("ollama/m", config=config).is_free is True


# --------------------------------------------------------------------------
# 3. Policy resolution
# --------------------------------------------------------------------------


class TestPolicyResolution:
    def test_no_section_means_budgeted_which_is_todays_behaviour(self) -> None:
        """Adding this package must change nothing for an existing install."""
        policy = resolve_spend_policy(_Config())
        assert policy.mode is SpendMode.BUDGETED
        assert policy.forbids_paid_models is False

    def test_zero_cost_forbids_paid_models(self) -> None:
        policy = resolve_spend_policy(_Config(spend=SpendPolicyConfig(mode="zero_cost")))
        assert policy.forbids_paid_models is True

    def test_unrestricted_is_permissive(self) -> None:
        policy = SpendPolicyConfig(mode="unrestricted_by_budget")
        assert policy.forbids_paid_models is False

    def test_an_unknown_mode_is_refused_not_defaulted(self) -> None:
        with pytest.raises(ValueError):
            SpendPolicyConfig(mode="free-please")

    def test_a_negative_ceiling_is_refused(self) -> None:
        with pytest.raises(ValueError):
            SpendPolicyConfig(mode="budgeted", max_cost_usd_per_run=-1.0)

    def test_a_bool_is_not_a_ceiling(self) -> None:
        with pytest.raises(ValueError):
            SpendPolicyConfig(mode="budgeted", max_daily_cost_usd=True)

    def test_a_zero_ceiling_is_a_real_ceiling(self) -> None:
        assert SpendPolicyConfig(mode="budgeted", max_cost_usd_per_run=0.0).max_cost_usd_per_run == 0.0


# --------------------------------------------------------------------------
# 4. The refusal itself
# --------------------------------------------------------------------------


class TestRefusal:
    def test_a_paid_model_is_refused_in_zero_cost(self) -> None:
        config = _Config(model_pricing={"gpt-4o": _block(2.5, 10.0)})
        policy = SpendPolicyConfig(mode="zero_cost")
        with pytest.raises(PaidCallRefused) as excinfo:
            refuse_paid_model("gpt-4o", policy=policy, config=config)
        assert excinfo.value.model_name == "gpt-4o"
        assert "priced" in excinfo.value.reason

    def test_a_free_model_passes_and_reports_why(self) -> None:
        """An allowed call carries its reason too.

        A permitted paid call with no recorded reason is indistinguishable from
        one that was never checked.
        """
        config = _Config(free_gateways=[_Gateway(id="kilo", documented_models=["m:free"])])
        verdict = refuse_paid_model("m:free", policy=SpendPolicyConfig(mode="zero_cost"), config=config)
        assert verdict.is_free is True

    def test_an_unpriced_model_is_refused_in_zero_cost(self) -> None:
        """The conservative default: not knowing is not permission."""
        policy = SpendPolicyConfig(mode="zero_cost")
        with pytest.raises(PaidCallRefused) as excinfo:
            refuse_paid_model("mystery", policy=policy, config=_Config())
        assert "unpriced" in excinfo.value.reason

    def test_budgeted_mode_permits_a_paid_model(self) -> None:
        config = _Config(model_pricing={"gpt-4o": _block(2.5, 10.0)})
        verdict = refuse_paid_model("gpt-4o", policy=SpendPolicyConfig(mode="budgeted"), config=config)
        assert verdict.is_free is False

    def test_the_refusal_is_a_distinct_type_from_a_missing_model(self) -> None:
        """An operator policy decision and a typo need different handling."""
        assert issubclass(PaidCallRefused, RuntimeError)
        assert not issubclass(PaidCallRefused, ValueError)


# --------------------------------------------------------------------------
# 5. Wired enforcement through the real factory
# --------------------------------------------------------------------------


class TestFactoryGate:
    """The predicate is only worth something if the factory honours it.

    These drive the **real** ``create_chat_model`` with a real ``AppConfig``.
    An earlier version used a dataclass stub and proved nothing: the factory
    resolves the fallback chain through ``AppConfig.get_model_config`` first, so
    the stub failed *before* the gate was reached and the test "passed" for the
    wrong reason. A test that cannot fail is worse than no test, because it
    occupies the slot where a real one belongs.
    """

    @staticmethod
    def _config(models: list[dict[str, Any]], *, mode: str | None, default: str) -> Any:
        """A real ``AppConfig``.

        ``models[].model`` and ``sandbox`` are both required by the schema, so a
        config built without them fails validation and never reaches the gate --
        exactly the trap the earlier stub fell into.
        """
        from alpha.config.app_config import AppConfig
        from alpha.config.model_config import ModelConfig
        from alpha.config.sandbox_config import SandboxConfig

        payload: dict[str, Any] = {
            "models": [ModelConfig(**entry) for entry in models],
            "sandbox": SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"),
            "default_model": default,
        }
        if mode is not None:
            payload["spend"] = {"mode": mode}
        return AppConfig(**payload)

    @staticmethod
    def _model(name: str, input_price: float, output_price: float) -> dict[str, Any]:
        return {
            "name": name,
            "display_name": name,
            "use": "langchain_openai:ChatOpenAI",
            "model": name,
            "pricing": {"input_per_million": input_price, "output_per_million": output_price},
        }

    def test_a_paid_model_is_never_constructed_in_zero_cost(self) -> None:
        """A refused call never reaches a provider."""
        from alpha.models.factory import create_chat_model

        config = self._config([self._model("probe-model", 2.5, 10.0)], mode="zero_cost", default="probe-model")
        with pytest.raises(PaidCallRefused) as excinfo:
            create_chat_model("probe-model", app_config=config)
        assert excinfo.value.model_name == "probe-model"

    def test_a_zero_priced_model_passes_the_gate_in_zero_cost(self) -> None:
        """The gate must not refuse everything — that is a DoS, not a policy.

        Construction still fails later for unrelated reasons (no API key, no
        network); what matters is that the failure is **not** a spend refusal.
        """
        from alpha.models.factory import create_chat_model

        config = self._config([self._model("probe-model", 0.0, 0.0)], mode="zero_cost", default="probe-model")
        with pytest.raises(Exception) as excinfo:
            create_chat_model("probe-model", app_config=config)
        assert not isinstance(excinfo.value, PaidCallRefused)

    def test_the_gate_is_inert_in_budgeted_mode(self) -> None:
        """The permissive mode changes nothing about construction."""
        from alpha.models.factory import create_chat_model

        config = self._config([self._model("probe-model", 2.5, 10.0)], mode="budgeted", default="probe-model")
        with pytest.raises(Exception) as excinfo:
            create_chat_model("probe-model", app_config=config)
        assert not isinstance(excinfo.value, PaidCallRefused)

    def test_the_gate_is_inert_when_no_spend_section_exists(self) -> None:
        """The default must be today's behaviour, or this changes every install."""
        from alpha.models.factory import create_chat_model

        config = self._config([self._model("probe-model", 2.5, 10.0)], mode=None, default="probe-model")
        with pytest.raises(Exception) as excinfo:
            create_chat_model("probe-model", app_config=config)
        assert not isinstance(excinfo.value, PaidCallRefused)

    def test_a_paid_fallback_member_is_refused_too(self) -> None:
        """The whole chain is checked, not just the primary.

        A free primary with a paid fallback spends nothing until the fallback
        fires; a gate inspecting only the primary would enforce nothing exactly
        when it matters most.
        """
        from alpha.models.factory import create_chat_model

        config = self._config([self._model("free-model", 0.0, 0.0), self._model("paid-model", 2.5, 10.0)], mode="zero_cost", default="free-model")
        with pytest.raises(PaidCallRefused) as excinfo:
            create_chat_model("free-model", app_config=config, fallbacks=["paid-model"])
        assert excinfo.value.model_name == "paid-model"
