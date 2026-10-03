"""Per-bot LLM model configuration: declaration, validation, resolution.

Covers the module contract in ``alpha.bots.model_config``:

- fail-closed validation that collects *every* issue in one raise;
- secrets/egress refusal (a bot profile is API-writable JSON, so a
  credential-shaped key or ``base_url`` must never land in it);
- pure resolution with per-value provenance (the ``*_source`` fields are what
  makes the detail view honest rather than a rendering guess);
- the precedence ladder ``request > bot.model_config > bot.model > custom
  agent > default`` decided in exactly one place;
- epoch stability: an empty block must not churn an existing profile's
  capability fingerprint.
"""

from __future__ import annotations

import pytest

from alpha.bots.model_config import (
    MAX_COUNSEL_MEMBERS,
    MAX_FALLBACKS,
    MAX_MIXTURE_REFERENCES,
    BotModelConfig,
    BotModelConfigError,
    CounselConfig,
    MixtureConfig,
    describe_model_plan,
    fingerprint_surface,
    resolve_model_plan,
    validate_bot_model_config,
)

KNOWN = {"flagship", "cheap", "local-model"}


def _codes(exc: BotModelConfigError) -> set[str]:
    return {i.code for i in exc.issues}


# ── Validation: happy path ─────────────────────────────────────────────────


def test_valid_block_round_trips():
    cfg = validate_bot_model_config(
        {
            "primary": "flagship",
            "fallbacks": ["cheap", "local-model"],
            "counsel": {"enabled": True, "members": ["flagship", "cheap"], "rounds": 2},
            "mixture": {"enabled": True, "references": ["cheap"], "aggregator": "flagship"},
            "sampling": {"temperature": 0.2, "max_tokens": 4096},
        },
        known_models=KNOWN,
    )
    assert cfg.primary == "flagship"
    assert cfg.fallbacks == ("cheap", "local-model")
    assert cfg.counsel is not None and cfg.counsel.enabled
    assert cfg.mixture is not None and cfg.mixture.aggregator == "flagship"
    assert cfg.sampling == {"temperature": 0.2, "max_tokens": 4096}
    # The stored form is the canonical one, not the caller's spelling.
    assert BotModelConfig.from_dict(cfg.to_dict()).to_dict() == cfg.to_dict()


def test_empty_and_none_are_inherit_everything():
    assert validate_bot_model_config(None, known_models=KNOWN).is_empty
    assert validate_bot_model_config({}, known_models=KNOWN).is_empty
    assert fingerprint_surface(None) is None
    assert fingerprint_surface(BotModelConfig()) is None


# ── Validation: fail-closed, collect everything ────────────────────────────


def test_unknown_model_names_are_errors_not_fallbacks():
    with pytest.raises(BotModelConfigError) as exc:
        validate_bot_model_config(
            {"primary": "nonexistent", "fallbacks": ["also-missing"]},
            known_models=KNOWN,
        )
    # Both are reported at once — one save, one response, full list.
    assert {"unknown_model"} <= _codes(exc.value)
    fields = {i.field for i in exc.value.issues}
    assert "model_config.primary" in fields
    assert "model_config.fallbacks[0]" in fields
    assert len(exc.value.issues) >= 2


def test_every_problem_is_collected_in_one_raise():
    with pytest.raises(BotModelConfigError) as exc:
        validate_bot_model_config(
            {
                "primary": "ghost",
                "fallbacks": ["cheap", "cheap", "ghost"],  # dup + primary in chain
                "nonsense_key": 1,
                "counsel": {"enabled": True, "members": []},  # enabled with no members
                "sampling": {"api_key": "sk-123", "nested": {"a": 1}},
            },
            known_models=KNOWN,
        )
    codes = _codes(exc.value)
    assert {"unknown_model", "duplicate_fallback", "fallback_cycle", "unknown_key", "counsel_no_members", "secret_key", "non_scalar"} <= codes


def test_fallback_bounds():
    too_many = [f"m{i}" for i in range(MAX_FALLBACKS + 1)]
    with pytest.raises(BotModelConfigError) as exc:
        validate_bot_model_config({"fallbacks": too_many}, known_models=set(too_many))
    assert "too_many_fallbacks" in _codes(exc.value)


def test_secrets_and_egress_are_refused():
    """A bot profile serialises to JSON, rides exports and is API-writable."""
    for key in ("api_key", "apiKey", "authorization_token", "password", "headers", "base_url", "endpoint"):
        with pytest.raises(BotModelConfigError) as exc:
            validate_bot_model_config({"sampling": {key: "SUPERSECRET-VALUE"}}, known_models=KNOWN)
        assert "secret_key" in _codes(exc.value), key
        # The value must never echo back in the message.
        assert "SUPERSECRET-VALUE" not in str(exc.value)


def test_factory_metadata_keys_are_refused_as_sampling():
    for key in ("name", "model", "use", "provider", "fallbacks", "pricing"):
        with pytest.raises(BotModelConfigError) as exc:
            validate_bot_model_config({"sampling": {key: "y"}}, known_models=KNOWN)
        assert "metadata_key" in _codes(exc.value), key


def test_sampling_is_scalar_only_and_effort_is_canonicalised():
    with pytest.raises(BotModelConfigError) as exc:
        validate_bot_model_config({"sampling": {"temperature": [1, 2]}}, known_models=KNOWN)
    assert "non_scalar" in _codes(exc.value)

    cfg = validate_bot_model_config({"sampling": {"reasoning_effort": "HIGH"}}, known_models=KNOWN)
    assert cfg.sampling["reasoning_effort"] == "high"

    with pytest.raises(BotModelConfigError) as exc:
        validate_bot_model_config({"sampling": {"reasoning_effort": "ultra-mega"}}, known_models=KNOWN)
    assert "bad_effort" in _codes(exc.value)


def test_mixture_and_counsel_bounds():
    with pytest.raises(BotModelConfigError) as exc:
        validate_bot_model_config(
            {
                "mixture": {
                    "enabled": True,
                    "references": [f"m{i}" for i in range(MAX_MIXTURE_REFERENCES + 1)],
                    "strategy": "sideways",
                },
                "counsel": {"members": [f"m{i}" for i in range(MAX_COUNSEL_MEMBERS + 1)], "rounds": 99},
            },
            known_models={f"m{i}" for i in range(MAX_MIXTURE_REFERENCES + 2)},
        )
    assert {"too_many_references", "bad_strategy", "too_many_members", "bad_rounds"} <= _codes(exc.value)


def test_mixture_requires_an_aggregator_when_it_has_references():
    with pytest.raises(BotModelConfigError) as exc:
        validate_bot_model_config(
            {"mixture": {"enabled": True, "references": ["cheap"]}},
            known_models=KNOWN,
        )
    assert "mixture_no_aggregator" in _codes(exc.value)


def test_not_a_mapping_is_refused():
    with pytest.raises(BotModelConfigError) as exc:
        validate_bot_model_config(["not", "a", "block"], known_models=KNOWN)
    assert "not_mapping" in _codes(exc.value)


# ── Tolerant reads (legacy profiles must never crash a reader) ─────────────


def test_from_dict_is_tolerant_of_junk():
    assert BotModelConfig.from_dict(None).is_empty
    assert BotModelConfig.from_dict("junk").is_empty
    assert BotModelConfig.from_dict({"fallbacks": "not-a-list"}).fallbacks == ()
    assert BotModelConfig.from_dict({"primary": "  flagship  "}).primary == "flagship"
    assert BotModelConfig.from_dict({"counsel": "junk"}).counsel is None
    assert BotModelConfig.from_dict({"mixture": 7}).mixture is None


def test_counsel_and_mixture_from_dict_defaults():
    counsel = CounselConfig.from_dict({})
    assert counsel.enabled is False and counsel.rounds == 1 and counsel.quorum == 0
    mixture = MixtureConfig.from_dict({"enabled": True})
    assert mixture.strategy == "parallel" and mixture.max_workers == 4


# ── Resolution: precedence decided once, with provenance ───────────────────


def _plan(**kwargs):
    return resolve_model_plan(**kwargs)


def test_precedence_request_wins_over_everything():
    plan = _plan(
        request_model="flagship",
        bot=type("B", (), {"model": "cheap", "model_config": {"primary": "local-model"}})(),
        custom_agent_model="cheap",
        default_model="cheap",
    )
    assert plan.primary == "flagship"
    assert plan.primary_source == "request"


def test_precedence_bot_model_config_beats_bot_model_and_agent():
    plan = _plan(
        bot=type("B", (), {"model": "cheap", "model_config": {"primary": "local-model"}})(),
        custom_agent_model="flagship",
        default_model="cheap",
    )
    assert plan.primary == "local-model"
    assert plan.primary_source == "bot.model_config"


def test_precedence_bot_model_beats_custom_agent():
    plan = _plan(
        bot=type("B", (), {"model": "cheap", "model_config": {}})(),
        custom_agent_model="flagship",
        default_model="local-model",
    )
    assert plan.primary == "cheap"
    assert plan.primary_source == "bot.model"


def test_precedence_custom_agent_then_default():
    plan = _plan(custom_agent_model="flagship", default_model="cheap")
    assert (plan.primary, plan.primary_source) == ("flagship", "custom_agent")
    plan = _plan(default_model="cheap")
    assert (plan.primary, plan.primary_source) == ("cheap", "default")
    plan = _plan()
    assert plan.primary is None and plan.primary_source == "default"


def test_bot_declared_fallbacks_replace_the_primary_own_chain():
    plan = _plan(bot_model_config=BotModelConfig(fallbacks=("cheap", "local-model")))
    assert plan.fallbacks == ("cheap", "local-model")
    assert plan.fallbacks_source == "bot.model_config"

    # Nothing declared → the primary's own chain applies (factory default).
    plan = _plan()
    assert plan.fallbacks == () and plan.fallbacks_source == "primary_model"


def test_sources_are_exposed_for_the_detail_view():
    plan = _plan(request_model="flagship")
    assert plan.primary_source in plan.SOURCES
    assert list(plan.SOURCES).index("request") < list(plan.SOURCES).index("bot.model_config")
    assert list(plan.SOURCES).index("bot.model_config") < list(plan.SOURCES).index("default")


def test_plan_serialises_every_source():
    cfg = BotModelConfig(
        primary="flagship",
        fallbacks=("cheap",),
        counsel=CounselConfig(enabled=True, members=["flagship"]),
        mixture=MixtureConfig(enabled=True, references=["cheap"], aggregator="flagship"),
        sampling={"temperature": 0.1},
    )
    data = _plan(bot_model_config=cfg).to_dict()
    for key in ("primary_source", "fallbacks_source", "counsel_source", "mixture_source", "sampling_source"):
        assert key in data, key
    assert data["sampling_source"] == "bot.model_config"
    assert data["counsel_source"] == "bot.model_config"


def test_has_bot_override_flags_bot_owned_values():
    assert _plan(request_model="flagship").has_bot_override
    assert _plan(bot_model_config=BotModelConfig(primary="flagship")).has_bot_override
    assert not _plan(custom_agent_model="flagship").has_bot_override


# ── describe_model_plan: the full-detail payload ───────────────────────────


def test_describe_carries_limits_and_precedence():
    described = describe_model_plan(_plan(), bot_name="reviewer")
    assert described["bot"] == "reviewer"
    assert "precedence" in described and "plan" in described
    limits = described["limits"]
    assert limits["max_fallbacks"] == MAX_FALLBACKS
    assert limits["max_mixture_references"] == MAX_MIXTURE_REFERENCES
    assert limits["mixture_strategies"] == ["parallel", "sequential"]


# ── Epoch stability ────────────────────────────────────────────────────────


def test_fingerprint_surface_only_appears_when_non_empty():
    surface = fingerprint_surface(BotModelConfig.from_dict({"primary": "flagship"}))
    assert surface is not None and surface["primary"] == "flagship"
    # Deterministic: same input, same bytes (it feeds a sha256).
    assert surface == fingerprint_surface(BotModelConfig.from_dict({"primary": "flagship"}))
