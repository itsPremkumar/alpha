"""A declared per-model price must actually be a declared price.

The sibling bug this is one level down from: ``config.example.yaml`` shipped
eight ``model_pricing:`` entries, documented them as authoritative, and every
cost in the product was ``null`` with no error anywhere. That was fixed by
making the console read the table. The INLINE table was still unvalidated, so
the same bug survived one level down::

    pricing:
      inpt_per_million: 1.0      # one typo

parsed cleanly (``ModelConfig`` is ``extra="allow"``), survived the load,
reached ``console._build_pricing_map``, matched none of the keys the consumer
reads, and produced ``total_cost: null``. A configured price that silently
prices nothing is worse than no price, because it tells the operator their
config is in effect when it is not.

The fix is a declared schema for the *value* of the one Alpha key hiding in the
free-form extras map, with ``extra="forbid"`` on that schema only.
``ModelConfig`` itself stays ``extra="allow"`` -- operators legitimately pass
provider kwargs Alpha cannot enumerate, and deny-by-default would break every
working install. Declared-and-validated where known, open where genuinely open.

These tests pin the four things that must all stay true at once, and they are
deliberately not equivalent to each other:

* a valid inline price PRICES A RUN (the consumer, not just the parse -- a
  schema that validates but is read by nobody is the original bug);
* a typo'd key fails AT LOAD, naming the model and the key;
* an unknown provider kwarg is STILL accepted (``extra="allow"` must survive);
* ``config.example.yaml`` itself validates, because ``make config`` produces an
  install from it and a template that fails its own schema is a disaster on
  first run.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from alpha.config.model_catalog_schema import ModelPriceEntry, ModelPricing
from alpha.config.model_config import ModelConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_EXAMPLE = REPO_ROOT / "config.example.yaml"

# A complete, valid inline block. `input`/`output` are deliberately NOT here:
# those are the `model_pricing:` fallback table's key names (see
# `ModelPriceEntry`), a different scope, and using them inline is the exact
# mistake `extra="forbid"` now refuses.
VALID_PRICING = {
    "currency": "CNY",
    "input_per_million": 8.0,
    "output_per_million": 32.0,
    "input_cache_hit_per_million": 0.8,
}


def _model(**overrides) -> ModelConfig:
    base = {
        "name": "probe-model",
        "display_name": "Probe",
        "use": "langchain_openai:ChatOpenAI",
        "model": "probe-model-1",
    }
    base.update(overrides)
    return ModelConfig(**base)


# ---------------------------------------------------------------------------
# 1. The consumer. A price that validates must PRICE something.
# ---------------------------------------------------------------------------


class TestAValidPriceActuallyPricesARun:
    """The regression, restated as a number rather than a parse.

    A schema that accepts the block but that no read path consults is the exact
    failure already fixed one level down. So this drives the real console cost
    functions against a config that went through real validation, and asserts a
    non-null cost -- not merely that ``ModelConfig`` parsed.
    """

    @pytest.fixture()
    def loaded(self, monkeypatch):
        """Install a config whose pricing came from the real schema."""

        def install(**model_overrides):
            from app.gateway.routers import console

            model = _model(name="gpt-4o", model="gpt-4o-2024", **model_overrides)
            table = {model.name: ModelPriceEntry(input=1.0, output=1.0)}
            monkeypatch.setattr(
                console,
                "get_app_config",
                lambda: type("Cfg", (), {"models": [model], "model_pricing": table})(),
            )
            return console

        return install

    def test_a_valid_inline_price_yields_a_non_null_run_cost(self, loaded):
        console = loaded(pricing=dict(VALID_PRICING))
        cost = console._run_cost(
            console._build_pricing_map(),
            model_name="gpt-4o",
            total_input_tokens=1_000_000,
            total_output_tokens=1_000_000,
            token_usage_by_model=None,
        )
        assert cost == pytest.approx(40.0), f"a validated price produced {cost}, not 8 + 32"
        assert cost is not None, "a validated price must not read as 'unpriced'"

    def test_the_per_model_breakdown_prices_too(self, loaded):
        """A multi-model run is priced from `token_usage_by_model`.

        This is the path a real run takes, and the one that returned `null`
        before the consumer was pinned to the schema.
        """
        console = loaded(pricing=dict(VALID_PRICING))
        cost = console._run_cost(
            console._build_pricing_map(),
            model_name="gpt-4o",
            total_input_tokens=None,
            total_output_tokens=None,
            token_usage_by_model={"gpt-4o-2024": {"input_tokens": 1_000_000, "output_tokens": 1_000_000}},
        )
        assert cost == pytest.approx(40.0)

    def test_the_cache_hit_price_survives_the_schema(self, loaded):
        """A key that only affects *how much* is still a key the consumer reads.

        `input_cache_hit_per_million` is the one optional field. A schema that
        declared the two required prices and dropped this one would still pass
        every parse test and quietly over-bill every cached prompt.
        """
        console = loaded(pricing=dict(VALID_PRICING))
        price = console._build_pricing_map()["gpt-4o"]
        assert price.input_cache_hit_per_million == pytest.approx(0.8)

    def test_the_consumer_and_the_schema_agree_on_the_key_names(self):
        """The failure mode a hand-written schema would reintroduce.

        The schema is only safe because it names the same fields the consumer
        reads. If the consumer ever read a key the schema did not declare, a
        config using it would fail at load; if the schema declared a key the
        consumer did not read, a config using it would load and price nothing.
        This asserts the reader's key set is a subset of the schema's, so the
        second failure cannot be reintroduced silently.
        """
        from app.gateway.routers import console

        reader_keys = {"input_per_million", "output_per_million", "input_cache_hit_per_million", "currency"}
        assert reader_keys <= set(ModelPricing.model_fields), f"the console reads {sorted(reader_keys - set(ModelPricing.model_fields))}, which the schema does not declare: such a config would load and price nothing"
        # And every declared key is one the consumer actually consults, so the
        # schema is not carrying decorative fields nobody reads.
        assert set(ModelPricing.model_fields) == reader_keys
        # The constructed entry exposes exactly the reader's names too.
        entry_fields = set(console._ModelPricing._fields)
        assert reader_keys - {"currency"} <= entry_fields


# ---------------------------------------------------------------------------
# 2. A typo fails AT LOAD, naming the model and the key.
# ---------------------------------------------------------------------------


class TestATypoFailsLoudlyAtConfigLoad:
    @pytest.mark.parametrize(
        "bad_key",
        [
            "inpt_per_million",
            "input_per_milliion",
            "input",
            "output",
            "cache_hit_per_million",
            "input_per_million_per_1m",
        ],
    )
    def test_a_typo_key_is_rejected(self, bad_key):
        with pytest.raises(ValidationError) as excinfo:
            _model(name="doubao-seed", pricing={bad_key: 1.0, "input_per_million": 1.0})
        assert bad_key in str(excinfo.value)

    def test_the_message_names_the_model(self):
        """A load error an operator cannot locate is barely better than silence.

        A config can carry dozens of model entries; "unknown key" alone does
        not say which one to open.
        """
        with pytest.raises(ValidationError, match="doubao-seed"):
            _model(name="doubao-seed", pricing={"inpt_per_million": 1.0})

    def test_the_message_names_the_offending_key_and_the_allowed_ones(self):
        with pytest.raises(ValidationError) as excinfo:
            _model(name="doubao-seed", pricing={"inpt_per_million": 1.0})
        message = str(excinfo.value)
        assert "inpt_per_million" in message
        # The fix is almost always a rename, so the allowed set belongs in the
        # message rather than in a doc the operator has to go and find.
        assert "input_per_million" in message

    def test_a_near_miss_typo_suggests_the_intended_key(self):
        """A confident suggestion is only emitted when there is one right answer."""
        with pytest.raises(ValidationError) as excinfo:
            _model(name="m", pricing={"inpt_per_million": 1.0})
        assert "did you mean 'input_per_million'?" in str(excinfo.value)

    def test_a_wildly_wrong_key_gets_no_confident_suggestion(self):
        """Suggesting a match for an unrelated key is worse than saying nothing."""
        with pytest.raises(ValidationError) as excinfo:
            _model(name="m", pricing={"bananas": 1.0})
        assert "did you mean" not in str(excinfo.value)

    def test_the_fallback_table_key_names_are_not_accepted_inline(self):
        """`input`/`output` belong to `model_pricing:`, not to an inline block.

        They are the same fact at two scopes with the same unit, so an operator
        copying one into the other is a very plausible mistake -- and it used to
        produce a silent `null` rather than an error.
        """
        with pytest.raises(ValidationError, match="unknown key 'input'"):
            _model(name="m", pricing={"input": 1.0, "output": 2.0})

    @pytest.mark.parametrize(
        ("block", "expected"),
        [
            ({"input_per_million": -1.0}, "greater than or equal to 0"),
            ({"input_per_million": "cheap"}, "valid number"),
            ({"currency": ""}, "non-empty ISO code"),
        ],
    )
    def test_a_malformed_value_is_rejected_too(self, block, expected):
        """A typo in the *key* is only half the class; a bad value is the other."""
        with pytest.raises(ValidationError, match=expected):
            _model(name="m", pricing=block)

    def test_the_load_failure_surfaces_through_the_real_app_config(self):
        """Not just `ModelConfig` in isolation -- through `AppConfig`, as shipped.

        `AppConfig` is what `config.yaml` actually becomes, so this is the path
        a `make config` install takes. If the error only appeared when the model
        was constructed by hand, the guard would not protect the product.
        """
        from alpha.config.app_config import AppConfig
        from alpha.config.sandbox_config import SandboxConfig

        with pytest.raises(ValidationError) as excinfo:
            AppConfig(
                models=[_model(name="broken", pricing={"inpt_per_million": 1.0})],
                sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"),
            )
        assert "broken" in str(excinfo.value)
        assert "inpt_per_million" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 3. `extra="allow"` must survive. This is the constraint that makes the fix hard.
# ---------------------------------------------------------------------------


class TestTheExtrasMapStaysOpen:
    """Declaring one key's schema must not become deny-by-default.

    `ModelConfig` is `extra="allow"` because operators pass real provider
    kwargs Alpha cannot enumerate. Making it strict would break every existing
    install, so the schema was applied to the *value* of the one known Alpha key
    and to nothing else.
    """

    def test_real_provider_kwargs_are_still_accepted(self):
        config = _model(api_key="$OPENAI_API_KEY", base_url="https://api.openai.com/v1", max_tokens=4096, temperature=0.2, max_retries=2)
        assert config.model_extra["api_key"] == "$OPENAI_API_KEY"
        assert config.model_extra["base_url"] == "https://api.openai.com/v1"
        assert config.model_extra["max_tokens"] == 4096
        assert config.model_extra["temperature"] == 0.2
        assert config.model_extra["max_retries"] == 2

    def test_a_genuinely_new_provider_kwarg_is_accepted(self):
        """The case a future LangChain release creates, which no allowlist can list."""
        config = _model(some_future_provider_kwarg="yes", reasoning_effort_backend="v2")
        assert config.model_extra["some_future_provider_kwarg"] == "yes"
        assert config.model_extra["reasoning_effort_backend"] == "v2"

    def test_an_unknown_key_and_a_pricing_key_coexist(self):
        """They are siblings in one mapping, so the guard must be per-key."""
        config = _model(pricing=dict(VALID_PRICING), base_url="https://example.invalid/v1", temperature=0.7)
        assert config.model_extra["base_url"] == "https://example.invalid/v1"
        assert config.model_extra["temperature"] == 0.7
        assert config.model_extra["pricing"]["currency"] == "CNY"

    def test_a_typo_in_a_provider_kwarg_is_still_not_caught_here(self):
        """Stated honestly: the guard is scoped to `pricing` and nothing else.

        This documents the limit of the fix rather than asserting a property it
        does not have. `maxx_tokens` is still accepted here and still fails at
        request time, via the factory's own `_warn_unknown_model_settings`
        divert-and-crash path. Narrowing that would mean deciding which keys are
        *not* provider kwargs, which is the deny-list that `extra="allow"`
        exists to avoid.
        """
        assert _model(maxx_tokens=100).model_extra["maxx_tokens"] == 100

    def test_the_model_config_extras_policy_is_unchanged(self):
        assert ModelConfig.model_config["extra"] == "allow"


# ---------------------------------------------------------------------------
# 4. The shipped template must pass its own schema.
# ---------------------------------------------------------------------------


class TestTheShippedTemplateValidates:
    @pytest.fixture(scope="class")
    def template(self) -> dict:
        return yaml.safe_load(CONFIG_EXAMPLE.read_text(encoding="utf-8")) or {}

    def test_every_model_entry_validates(self, template):
        assert template.get("models"), "the template must declare models"
        for entry in template["models"]:
            ModelConfig(**entry)  # raises on anything the loader would refuse

    def test_every_fallback_price_entry_validates(self, template):
        assert template.get("model_pricing"), "the template must still declare the fallback table"
        for name, entry in template["model_pricing"].items():
            ModelPriceEntry(**entry)

    def test_no_shipped_price_key_is_outside_its_schema(self, template):
        """The check that would have caught a bad edit to the template itself.

        Validating entry-by-entry also catches this, but stating it directly
        names the failure: a template key that no schema accepts, which would
        make `make config` produce an install that cannot boot.
        """
        for name, entry in template.get("model_pricing", {}).items():
            unknown = set(entry) - set(ModelPriceEntry.model_fields)
            assert not unknown, f"template model_pricing[{name}] ships {sorted(unknown)}, which the schema rejects"
        for entry in template.get("models", []):
            if "pricing" in entry:
                unknown = set(entry["pricing"]) - set(ModelPricing.model_fields)
                assert not unknown, f"model {entry['name']} ships inline pricing keys {sorted(unknown)}, which the schema rejects"

    def test_the_documented_example_block_in_the_template_is_valid(self):
        """The commented `pricing:` example is the thing an operator copies.

        It is a comment, so nothing loads it -- which is precisely why a typo
        there would sit unnoticed until someone pasted it into a real config and
        every cost came back `null`. So the example is extracted from the
        template and validated against the schema here rather than trusted.
        """
        example = _commented_pricing_example(CONFIG_EXAMPLE.read_text(encoding="utf-8"))
        assert example, f"config.example.yaml must still document an inline `pricing:` example (anchor {_PRICING_EXAMPLE_ANCHOR!r})"
        ModelPricing(**example)

    def test_the_documented_example_names_only_real_keys(self):
        """A key in the *example* the schema rejects is the worst place for one.

        Split out from validating the block because a rejected key and an
        unparseable block are different failures, and the message should say
        which one this is.
        """
        example = _commented_pricing_example(CONFIG_EXAMPLE.read_text(encoding="utf-8"))
        unknown = set(example) - set(ModelPricing.model_fields)
        assert not unknown, f"the documented pricing example teaches {sorted(unknown)}, which the schema rejects"

    def test_the_template_loads_through_the_real_loader(self, tmp_path, monkeypatch):
        """End to end: `config.example.yaml` copied to `config.yaml` and loaded.

        Entry-by-entry validation above proves the price blocks; this proves the
        whole document, which is what a fresh install actually does. A template
        that fails its own schema is a disaster on first run, and it is the one
        failure mode no unit test on the schema alone would catch.
        """
        from alpha.config.app_config import AppConfig

        project = tmp_path / "project"
        project.mkdir()
        shutil.copy(CONFIG_EXAMPLE, project / "config.yaml")
        monkeypatch.setenv("ALPHA_PROJECT_ROOT", str(project))
        monkeypatch.setenv("ALPHA_CONFIG_PATH", str(project / "config.yaml"))

        config = AppConfig.from_file(str(project / "config.yaml"))

        assert config.models, "the template loaded with no models"
        assert config.model_pricing, "the template's fallback price table vanished in the load"
        for name, entry in config.model_pricing.items():
            assert entry.input >= 0 and entry.output >= 0, f"{name} lost its price through the load"


#: The template's inline `pricing:` example is a YAML comment block whose header
#: line is exactly this, with each key commented beneath it as `#     key: value`.
_PRICING_EXAMPLE_ANCHOR = "#   pricing:"


def _commented_pricing_example(text: str) -> dict:
    """Extract the template's documented inline `pricing:` example.

    Read out of the comment block rather than hard-coded, so the test validates
    what the template actually teaches. If the example is ever reformatted this
    returns ``{}`` and the caller fails naming the anchor -- a silently empty
    result would leave a test that no longer checks anything, which is worse
    than a failing one.
    """
    lines = text.splitlines()
    anchor = _PRICING_EXAMPLE_ANCHOR.strip()
    start = next((i for i, line in enumerate(lines) if line.strip() == anchor), None)
    if start is None:
        return {}
    block: dict[str, str] = {}
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped.startswith("#"):
            break
        body = stripped.lstrip("#").strip()
        if not body:
            continue
        if ":" not in body:
            # A prose continuation (the cache-hit-price note) is the tail of the
            # block, not a key.
            break
        key, _, value = body.partition(":")
        value = value.split("#")[0].strip()
        if value:
            block[key.strip()] = value
    return {key: (float(value) if _is_number(value) else value) for key, value in block.items()}


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# 5. The fallback table: audited, and deliberately left alone.
# ---------------------------------------------------------------------------


class TestTheFallbackTableIsAlreadyValidated:
    """Task item 4: does `model_pricing:` have the same unvalidated-typo problem?

    No, and the reason is structural rather than lucky: it is a *declared*
    ``AppConfig`` field typed ``dict[str, ModelPriceEntry]`` with
    ``extra="forbid"`` and both prices required, so pydantic validates it on
    every load. The inline block had none of that because it was an extra. These
    tests pin that the asymmetry is intentional, so a future edit that loosens
    the table has to argue with them.
    """

    def test_a_typo_in_the_fallback_table_is_already_a_load_error(self):
        with pytest.raises(ValidationError, match="model_pricing"):
            AppConfigWithPricing({"gpt-4o": {"input": 1.0, "outputt": 2.0}})

    def test_the_table_requires_both_prices(self):
        with pytest.raises(ValidationError):
            AppConfigWithPricing({"gpt-4o": {"input": 1.0}})

    def test_the_table_rejects_a_negative_price(self):
        with pytest.raises(ValidationError):
            AppConfigWithPricing({"gpt-4o": {"input": -1.0, "output": 1.0}})

    def test_the_inline_schema_does_not_accept_the_fallback_table_keys(self):
        """The two schemas are disjoint on purpose.

        Documented rather than enforced by a shared base class, because making
        them share one would mean one of the two key sets is wrong.
        """
        assert not set(ModelPricing.model_fields) & set(ModelPriceEntry.model_fields)


def AppConfigWithPricing(table: dict):
    """Build an `AppConfig` carrying a `model_pricing:` table, for validation."""
    from alpha.config.app_config import AppConfig
    from alpha.config.sandbox_config import SandboxConfig

    return AppConfig(
        models=[_model(name="m", model="m-1")],
        model_pricing=table,
        sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"),
    )
