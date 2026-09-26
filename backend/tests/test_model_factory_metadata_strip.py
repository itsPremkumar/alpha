"""``ModelConfig`` metadata must never reach the provider completion request.

The shipped bug this file exists to prevent
--------------------------------------------
``ModelConfig`` declares ``capabilities: list[str]`` with ``default_factory=list``,
so the key is *always* present in ``model_dump()``. ``_build_single_model`` popped a
hand-maintained deny-list of Alpha metadata before calling the provider
constructor, and ``capabilities`` was missing from it. langchain-openai does not
reject an unknown constructor kwarg: it emits a ``UserWarning`` and transfers the
key into ``model_kwargs``, which is then spread into every
``Completions.create()`` call and rejected by the OpenAI SDK at *request* time:

    TypeError: Completions.create() got an unexpected keyword argument 'capabilities'

The factory's own ``_warn_unknown_model_settings`` message already predicted it
("will be forwarded as-is; this may raise at request time"), which makes this a
silent-until-runtime failure, not a loud one.

Why the deny-list is not enough
------------------------------
Adding ``capabilities`` to the deny-list fixes one field. The next
``ModelConfig`` field re-breaks it, because a deny-list is only as complete as
the last person to remember to update it. So the strip set is now *derived*:
``ModelConfig.model_fields`` minus an explicit allowlist of the declared fields
that are genuine provider-constructor kwargs. A new field is stripped by
default. These tests pin that property so the derivation cannot silently rot:

* every declared field is accounted for exactly once (strip or passthrough);
* every passthrough field really is a constructor field of the target class;
* **no declared field's value ever reaches the completion request** -- the
  empirical form of the invariant, asserted per field with a poison value and
  driven through the real factory and a real ``ChatOpenAI``;
* the exact reported ``TypeError`` is reproduced and shown to be gone.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

# ``alpha.models`` cannot be the first ``alpha.*`` module this process imports.
# The chain is ``alpha.models.__init__`` -> ``factory`` -> ``alpha.config`` ->
# ... -> ``alpha.runtime.goal`` -> ``from alpha.models import create_chat_model``,
# which closes a cycle and raises ``ImportError: cannot import name
# 'create_chat_model' from partially initialized module 'alpha.models'`` whenever
# ``alpha.models`` is still mid-initialisation. It is survivable only if
# something has already initialised it, and ``alpha.client`` pulls the same graph
# in an order that closes the cycle -- so importing it first is what makes this
# file collectable regardless of which test file pytest reaches first.
#
# This is a test-side workaround, NOT a fix: the underlying cycle is a
# pre-existing product defect (`python -c "import alpha.models.factory"` fails on
# its own) and lives in files this change does not own.
import alpha.client  # noqa: F401  # imported for import-order side effect only
from alpha.config.app_config import AppConfig
from alpha.config.model_config import ModelConfig, ProviderConfig
from alpha.config.sandbox_config import SandboxConfig
from alpha.models import factory as factory_module
from alpha.models.factory import (
    _EXTRA_NON_CONSTRUCTOR_MODEL_KEYS,
    _MODEL_CONFIG_PROVIDER_PASSTHROUGH,
    _NON_CONSTRUCTOR_MODEL_KEYS,
    _build_single_model,
    create_chat_model,
)

_DECLARED_FIELDS = frozenset(ModelConfig.model_fields)

# A value no provider field could legitimately hold, so finding it anywhere in
# the request kwargs is unambiguous evidence of a leak.
_POISON = "P0-LEAKED-METADATA"

# A declared provider profile, so the ``provider`` field can be exercised with a
# value the factory will actually resolve instead of failing closed.
_PROBE_PROVIDER = "probe-provider"


def _app_config(model: ModelConfig) -> AppConfig:
    return AppConfig(
        models=[model],
        providers={_PROBE_PROVIDER: ProviderConfig(name=_PROBE_PROVIDER, use="langchain_openai:ChatOpenAI", temperature=0.1)},
        sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"),
    )


def _model_config(**overrides) -> ModelConfig:
    base = {
        "name": "probe-model",
        "display_name": "Probe",
        "description": None,
        "use": "langchain_openai:ChatOpenAI",
        "model": "probe-model-1",
        "api_key": "test-key-not-a-secret",
    }
    base.update(overrides)
    return ModelConfig(**base)


def _build(model_config: ModelConfig, **kwargs):
    return _build_single_model(model_config, False, _app_config(model_config), False, None, dict(kwargs))


def _build_with_spy(monkeypatch, model_config: ModelConfig, **kwargs) -> dict:
    """Build the model and return the exact kwargs the constructor received.

    Spying on ``resolve_class`` is what makes the per-field test uniform. Two
    declared fields -- ``use`` and ``provider`` -- are *consumed* during factory
    resolution (class path, provider profile) and raise before a client exists
    when given a bogus value, so they cannot be exercised through a real build.
    Replacing the class resolver makes the assertion about exactly one thing: the
    kwargs the provider constructor is handed.
    """
    captured: dict = {}

    class _Recorder(ChatOpenAI):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: _Recorder)
    _build_single_model(model_config, False, _app_config(model_config), False, None, dict(kwargs))
    return captured


#: A type-valid, non-default value per declared field. ``ModelConfig`` validates
#: several of these (``capabilities`` against a known set, ``context_window`` as a
#: positive int), so a single sentinel type cannot be used for every field. The
#: leak is keyed on the field *name*; the value only has to differ from the
#: default so the field is genuinely exercised.
def _poison_value(field_name: str):
    if field_name in {"supports_thinking", "supports_reasoning_effort", "supports_vision", "use_responses_api"}:
        return True
    if field_name == "context_window":
        return 123456
    if field_name == "fallbacks":
        return ["no-such-fallback"]
    if field_name == "capabilities":
        # Valid entries, but non-empty: the field's default is [], so a
        # default-populated payload is distinguishable from an absent one.
        return ["vision", "stt"]
    if field_name in {"when_thinking_enabled", "when_thinking_disabled", "thinking"}:
        # A genuine provider kwarg, not a sentinel: the factory *deliberately*
        # expands these dicts into the settings (the when_thinking_* /
        # thinking-shortcut transforms), so a sentinel here would legitimately
        # reach the constructor. The invariant under test is that the field
        # *name* is stripped, which the key assertion covers.
        return {"max_tokens": 4242}
    if field_name == "use":
        # A resolution *input*, consumed by ``_resolve_effective_use`` before the
        # constructor exists. A bogus value fails closed with an ImportError, so
        # it cannot be poisoned; the key assertion still applies.
        return "langchain_openai:ChatOpenAI"
    if field_name == "provider":
        # Likewise consumed by ``_effective_model_settings`` / class-path
        # resolution, and fail-closed on an unknown profile name.
        return _PROBE_PROVIDER
    return _POISON


def _passthrough_value(field_name: str):
    if field_name == "model":
        return f"{_POISON}-model"
    if field_name == "use_responses_api":
        return True
    if field_name == "stream_chunk_timeout":
        return 12.5
    return "v1"


# ---------------------------------------------------------------------------
# The strip set is complete by construction
# ---------------------------------------------------------------------------


def test_every_declared_model_config_field_is_stripped_or_declared_a_passthrough():
    """No declared field may fall through both halves of the split.

    This is the property the one-line fix did not have. Add a field to
    ``ModelConfig`` and the derived strip set picks it up automatically; hand-edit
    the strip set instead and this fails, because the field is in neither half.
    """
    accounted = _NON_CONSTRUCTOR_MODEL_KEYS | _MODEL_CONFIG_PROVIDER_PASSTHROUGH
    unaccounted = _DECLARED_FIELDS - accounted
    assert not unaccounted, f"ModelConfig field(s) can reach the constructor undecided: {sorted(unaccounted)}"

    overlap = _NON_CONSTRUCTOR_MODEL_KEYS & _MODEL_CONFIG_PROVIDER_PASSTHROUGH
    assert not overlap, f"field(s) declared both stripped and passed through: {sorted(overlap)}"


def test_strip_set_is_derived_from_the_model_config_field_metadata():
    """The strip set is ``declared fields - passthrough`` plus the extra-metadata set.

    Pinned so the derivation cannot be replaced by a hand-maintained list that
    happens to agree today.
    """
    expected = (_DECLARED_FIELDS - _MODEL_CONFIG_PROVIDER_PASSTHROUGH) | _EXTRA_NON_CONSTRUCTOR_MODEL_KEYS
    assert _NON_CONSTRUCTOR_MODEL_KEYS == expected


def test_capabilities_is_stripped():
    """The reported field specifically. Named so a regression is self-explanatory."""
    assert "capabilities" in _NON_CONSTRUCTOR_MODEL_KEYS


def test_every_passthrough_field_is_a_real_provider_constructor_kwarg():
    """The allowlist cannot become a back door for metadata.

    A passthrough entry that the target class does not declare is diverted into
    ``model_kwargs`` by langchain-openai -- the exact failure this file exists to
    prevent, just via the allowlist instead of the deny-list. Aliases count:
    ``ChatOpenAI`` declares the provider's model id as ``model_name`` with
    ``model`` as its alias, exactly as ``_warn_unknown_model_settings`` assumes.
    """
    client_fields = set(ChatOpenAI.model_fields)
    aliases = {field.alias for field in ChatOpenAI.model_fields.values() if field.alias}
    for name in sorted(_MODEL_CONFIG_PROVIDER_PASSTHROUGH):
        assert name in _DECLARED_FIELDS, f"{name!r} is not a ModelConfig field"
        assert name in client_fields or name in aliases, f"{name!r} is not a ChatOpenAI constructor field or alias, so it would be diverted into model_kwargs"


# ---------------------------------------------------------------------------
# The empirical invariant: nothing declared reaches the completion request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field_name", sorted(_DECLARED_FIELDS))
def test_no_declared_model_config_field_reaches_the_provider_constructor(monkeypatch, field_name):
    """The invariant, one declared field at a time.

    The failure mode is not "the constructor rejected it" -- the OpenAI client
    never rejects anything. It warns, transfers the key into ``model_kwargs``,
    and the OpenAI SDK rejects it later at ``Completions.create()`` time. So the
    assertion is on the kwargs the constructor actually receives: a declared
    metadata field present there is a leak, whatever the value is.

    Values are type-valid (``capabilities`` is validated against a known set,
    ``supports_thinking`` is a bool) and differ from the field's default, so the
    field is genuinely exercised rather than short-circuited by its own default.
    """
    if field_name in _MODEL_CONFIG_PROVIDER_PASSTHROUGH:
        pytest.skip(f"{field_name!r} is a declared provider passthrough; covered by test_passthrough_field_actually_reaches_the_client")

    value = _poison_value(field_name)
    poisoned = _model_config(**{field_name: value})
    assert poisoned.model_dump()[field_name] == value, f"field {field_name!r} did not retain the test value, so the test is not exercising it"

    captured = _build_with_spy(monkeypatch, poisoned)

    assert field_name not in captured, f"ModelConfig.{field_name} reached the provider constructor and will be diverted into model_kwargs -> Completions.create()"
    if isinstance(value, str):
        assert value not in captured.values(), f"the value of ModelConfig.{field_name} reached the constructor: {captured}"


@pytest.mark.parametrize("field_name", sorted(_DECLARED_FIELDS))
def test_no_declared_model_config_field_reaches_the_request_payload(monkeypatch, field_name):
    """Same invariant, observed at the other end: the built client's ``model_kwargs``.

    The constructor test proves the key was handed over; this proves the built
    object really would send it. ``model_kwargs`` is spread into every
    ``Completions.create()`` call, so its presence *is* the request-time failure.
    """
    if field_name in _MODEL_CONFIG_PROVIDER_PASSTHROUGH:
        pytest.skip(f"{field_name!r} is a declared provider passthrough; covered by test_passthrough_field_actually_reaches_the_client")

    instance = _build(_model_config(**{field_name: _poison_value(field_name)}))
    request_kwargs = dict(getattr(instance, "model_kwargs", None) or {})
    assert field_name not in request_kwargs, f"ModelConfig.{field_name} was diverted into model_kwargs and will be sent to Completions.create()"
    assert _POISON not in request_kwargs.values()


@pytest.mark.parametrize("field_name", sorted(_MODEL_CONFIG_PROVIDER_PASSTHROUGH))
def test_passthrough_field_actually_reaches_the_client(field_name):
    """The allowlist is real, not a no-op the strip test would pass against.

    Asserts the *key* is absent from ``model_kwargs`` (i.e. it was consumed as a
    declared field rather than diverted) and, for the fields the client exposes
    readably, that the configured value actually arrived.
    """
    value = _passthrough_value(field_name)
    instance = _build(_model_config(**{field_name: value}))

    request_kwargs = dict(getattr(instance, "model_kwargs", None) or {})
    assert field_name not in request_kwargs, f"{field_name!r} is declared a passthrough but was diverted into model_kwargs"
    if field_name == "output_version":
        # Enum-validated: the sentinel is rejected, so presence is the assertion.
        assert "output_version" in ChatOpenAI.model_fields
        assert hasattr(instance, "output_version")
        return
    if field_name == "model":
        assert instance.model_name == value
        return
    if field_name == "use_responses_api":
        assert instance.use_responses_api is True
        return
    assert instance.stream_chunk_timeout == value


def test_extra_metadata_key_pricing_is_stripped():
    """``pricing`` is an *extra* key (``extra="allow"``), so it needs its own entry.

    Extras cannot be deny-by-default -- they carry the operator's real provider
    kwargs -- so presentation metadata has to be listed explicitly. Asserted
    separately because the derived half of the strip set cannot cover it.
    """
    assert "pricing" in _EXTRA_NON_CONSTRUCTOR_MODEL_KEYS
    instance = _build(_model_config(pricing={"input": 1.0}))
    request_kwargs = dict(getattr(instance, "model_kwargs", None) or {})
    assert "pricing" not in request_kwargs


def test_operator_extras_still_reach_the_client():
    """Default-deny must not become default-drop-the-operator's-settings.

    The strip set only covers *declared* metadata fields and the explicitly
    listed extras. A genuine provider kwarg written inline in ``config.yaml``
    still has to arrive, or the fix would break every working configuration.
    """
    instance = _build(_model_config(max_tokens=1234, temperature=0.25, base_url="https://example.invalid/v1"))
    assert instance.max_tokens == 1234
    assert instance.temperature == 0.25
    assert str(instance.openai_api_base) == "https://example.invalid/v1"


# ---------------------------------------------------------------------------
# The exact reported failure, driven through the real client
# ---------------------------------------------------------------------------


def test_model_with_capabilities_completes_without_unexpected_keyword_error(monkeypatch):
    """Reproduce the shipped ``TypeError`` and show it is gone.

    ``Completions.create`` is the exact call the OpenAI SDK rejected, so patching
    it reproduces the real failure mode rather than a proxy for it. The assertion
    is on the recorded request kwargs, which is where an unstripped key would
    land.
    """
    recorded: list[dict] = []

    def _fake_create(self, **kwargs):  # noqa: ANN001 - signature mirrors the SDK
        recorded.append(dict(kwargs))
        from openai.types.chat import ChatCompletion
        from openai.types.chat.chat_completion import Choice
        from openai.types.chat.chat_completion_message import ChatCompletionMessage

        completion = ChatCompletion(
            id="chatcmpl-p0",
            choices=[
                Choice(
                    finish_reason="stop",
                    index=0,
                    message=ChatCompletionMessage(role="assistant", content="banana"),
                )
            ],
            created=0,
            model="probe-model-1",
            object="chat.completion",
        )

        class _RawResponse:
            """Stands in for ``LegacyAPIResponse``: langchain calls ``.parse()`` on it."""

            headers: dict = {}

            def parse(self):
                return completion

        return _RawResponse()

    from openai.resources.chat.completions import Completions

    monkeypatch.setattr(Completions, "create", _fake_create, raising=True)

    model = create_chat_model(
        "probe-model",
        thinking_enabled=False,
        attach_tracing=False,
        app_config=_app_config(_model_config(capabilities=["vision", "stt"])),
    )
    model.invoke([HumanMessage(content="hi")])

    assert len(recorded) == 1, "the completion request never reached Completions.create()"
    assert "capabilities" not in recorded[0], f"capabilities leaked into the request: {sorted(recorded[0])}"


def test_build_single_model_strips_capabilities_from_effective_settings(monkeypatch):
    """The strip happens on the merged settings, before the constructor call."""
    model_config = _model_config(capabilities=["vision"])
    captured = _build_with_spy(monkeypatch, model_config)
    assert "capabilities" not in captured
