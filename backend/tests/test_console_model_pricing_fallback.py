"""The configured price table must actually price things.

Found by auditing, not by reading: `config.example.yaml` ships a `model_pricing:`
block with eight entries, `AppConfig` declares the field and
`ModelPriceEntry` validates it, and `backend/packages/harness/alpha/config/AGENTS.md`
documents it as "fallback per-1M prices for models that omit `models[].pricing`".

Nothing read it. `_build_pricing_map()` consulted only `models[*].pricing`, and no
model declared that, so every console route answered `total_cost: null` and
`currency: null` — with a full price table sitting in the config looking
authoritative. The Usage view showed no cost and no error.

A shipped key that silently does nothing is worse than an absent key, because it
tells the operator their configuration is in effect when it is not.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.gateway.routers import console


@pytest.fixture()
def fake_config(monkeypatch):
    """Install a config with a `model_pricing` table and no per-model `pricing`."""

    def install(table, models=()):
        cfg = SimpleNamespace(
            models=list(models),
            model_pricing={k: SimpleNamespace(input=v[0], output=v[1]) for k, v in table.items()},
        )
        monkeypatch.setattr(console, "get_app_config", lambda: cfg)
        return cfg

    return install


def test_a_model_pricing_table_alone_produces_a_price(fake_config):
    """The regression: this table is the only thing configured, and it must work."""
    fake_config({"gpt-4o": (2.5, 10.0)})
    pricing = console._build_pricing_map()
    assert "gpt-4o" in pricing, "a documented fallback table was ignored"
    entry = pricing["gpt-4o"]
    assert entry.input_per_million == 2.5
    assert entry.output_per_million == 10.0


def test_a_priced_model_is_costed_end_to_end(fake_config):
    """Not just present in the map — it must produce a number for a real run."""
    fake_config({"gpt-4o": (2.5, 10.0)})
    cost = console._run_cost(
        console._build_pricing_map(),
        model_name="gpt-4o",
        total_input_tokens=1_000_000,
        total_output_tokens=1_000_000,
        token_usage_by_model=None,
    )
    assert cost == pytest.approx(12.5), f"expected 2.5 + 10.0, got {cost}"


def test_models_pricing_still_wins_over_the_fallback_table(fake_config):
    """`models[].pricing` is authoritative; the table is a fallback, not a peer."""
    priced = SimpleNamespace(
        name="gpt-4o",
        model="gpt-4o-2024",
        pricing={"currency": "USD", "input_per_million": 99.0, "output_per_million": 99.0},
    )
    fake_config({"gpt-4o": (2.5, 10.0)}, models=[priced])
    pricing = console._build_pricing_map()
    assert pricing["gpt-4o"].input_per_million == 99.0, "the fallback overrode the per-model price"
    # The provider model id is keyed too, and carries the same authoritative entry.
    assert pricing["gpt-4o-2024"].input_per_million == 99.0


def test_the_fallback_adopts_the_configured_currency(fake_config):
    """`ModelPriceEntry` has no currency, so it must not invent a mixed one."""
    priced = SimpleNamespace(
        name="local-model",
        model="local-model",
        pricing={"currency": "CNY", "input_per_million": 8.0, "output_per_million": 32.0},
    )
    fake_config({"some-other-model": (1.0, 2.0)}, models=[priced])
    pricing = console._build_pricing_map()
    assert pricing["some-other-model"].currency == "CNY", "a fallback entry must not introduce a second currency"


def test_the_fallback_defaults_to_usd_when_nothing_else_is_priced(fake_config):
    fake_config({"gpt-4o": (2.5, 10.0)})
    assert console._build_pricing_map()["gpt-4o"].currency == "USD"


def test_a_fallback_entry_has_no_cache_hit_price_rather_than_free_cache_reads(fake_config):
    """`ModelPriceEntry` cannot express a hit price, so a hit must bill as a miss."""
    fake_config({"gpt-4o": (2.5, 10.0)})
    price = console._build_pricing_map()["gpt-4o"]
    assert price.input_cache_hit_per_million is None, "a fabricated hit price would under-bill cache reads"
    cost = console._token_cost(1_000_000, 0, price, cache_read_tokens=1_000_000)
    assert cost == pytest.approx(2.5), f"a fully cache-hit read cost {cost}, expected the miss price"


def test_an_all_zero_table_is_ignored_rather_than_pricing_everything_at_zero(fake_config):
    fake_config({"free-model": (0.0, 0.0), "paid-model": (3.0, 15.0)})
    pricing = console._build_pricing_map()
    assert "free-model" not in pricing, "a zero/zero entry is a placeholder, not a price"
    assert "paid-model" in pricing


def test_an_empty_table_is_not_an_error(fake_config):
    fake_config({})
    assert console._build_pricing_map() == {}


def test_the_key_is_looked_up_case_insensitively(fake_config):
    """`token_usage_by_model` buckets carry the provider-reported name."""
    fake_config({"GPT-4O": (2.5, 10.0)})
    pricing = console._build_pricing_map()
    assert console._lookup_pricing(pricing, "gpt-4o") is not None


def test_the_shipped_template_table_is_reachable_by_the_console(fake_config):
    """The regression, restated against the real template key.

    `config.example.yaml` declares `gpt-4o`, `claude-3-7-sonnet` and friends.
    Before the fix none of them produced a price, so an operator who copied the
    template saw `total_cost: null` forever.
    """
    from alpha.config import get_app_config

    table = get_app_config().model_pricing
    assert table, "the template must still declare a fallback table"
    # Every declared entry must expose the two fields the console reads.
    for name, entry in table.items():
        assert float(getattr(entry, "input", 0)) >= 0, name
        assert float(getattr(entry, "output", 0)) >= 0, name
