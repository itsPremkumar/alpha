"""Settings for the behaviour-trace writer. Default-off, every key has a reader.

Why a second config class
-------------------------
:mod:`alpha.observability.config` already owns the span/event spine, and its
``enabled`` key is the master gate the brief asks us to *turn on* rather than
duplicate. Adding keys to it would mean editing a module whose
:data:`~alpha.observability.config.READERS` table and whose closed
``ObservabilityConfig`` shape are asserted by another test file that this work
does not own. So the behaviour trace's own knobs live here, in a new class with
its own reader table, and **inherit the gate** rather than declaring a second
one:

    writer = BehaviourTraceWriter(recorder)          # enabled == recorder.config.enabled

That is the whole contract for "one switch". There is no state in which the
behaviour trace is on and the spine is off, because the writer reads
``recorder.config.enabled`` and nothing else decides the gate. Two independent
switches for one feature is how a deployment ends up with tracing it believes is
on and is not.

The reader-table discipline
---------------------------
This package holds a rule: *a configuration key nobody reads is a lie told in
YAML* (see :mod:`alpha.observability.config`). :data:`READERS` is the same
mechanism applied to this class, and ``test_behaviour_trace.py`` asserts it
covers exactly :func:`read_all_keys` with no extra and no missing key and that
flipping each key changes an observable behaviour. A key added without a reader
fails; a reader added without a key fails.

No new dependency
-----------------
Every field is a primitive. The optional-extras rule from the brief is satisfied
trivially: this module adds nothing to the import graph beyond the stdlib and the
sibling modules it already lives beside.

Why the default redaction policy is ``standard``, not ``strict``
---------------------------------------------------------------
This is a measured decision, not a preference, and it is the one place where
copying the spine's default would have quietly destroyed the substrate.

The ``strict`` policy adds a bare-blob rule: a string of at least 32 characters
with no whitespace and high character entropy is treated as an opaque
credential. In a *span attribute* that is a cheap win. In a behaviour trace it
is destructive, and measurably so -- running the existing
:class:`~alpha.observability.redaction.Redactor` over representative values:

==========================================  ==========  ==========
value                                        ``strict``  ``standard``
==========================================  ==========  ==========
``a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4``       redacted    kept
(32-hex -- i.e. **a run id**)                            (and a span id)
``/home/user/projects/.../contract.py``      redacted    kept
``https://example.com/.../doc.html``         redacted    kept
``sk-proj-0123...``                          redacted    redacted
``Authorization: Bearer eyJ...``             redacted    redacted
``postgres://u:pw@host/db``                  redacted    redacted
``api_key=9f8e...``                          redacted    redacted
``AKIAIOSFODNN7EXAMPLE``                     redacted    redacted
==========================================  ==========  ==========

Every row marked "redacted" in the ``strict`` column *and* "kept" in the
``standard`` column is a row the ``strict`` blob rule takes. Three of the seven
are exactly the fields the correlation spine, layer 8 (search result URLs) and
layer 11 (filesystem paths) exist to carry: redacting them would leave a log
that can no longer be joined, quoted or re-read.

The credential families that actually appear in this system's highest-risk
payloads -- a user prompt, tool arguments, a tool result -- are all **pattern**
families (``openai_sk_key``, ``github_token``, ``aws_access_key_id``,
``bearer_token``, ``jwt_compact_token``, ``connection_string_credentials``,
``env_file_line``, ``stripe_secret_key``, ``google_api_key``, ``npm_token``,
``azure_storage_account_key``, ``slack_*``) and every one of them fires under
``standard`` too, because they are plugged in as value patterns rather than as
policy extensions. The strict policy's *additional* coverage is the bare blob
plus the extra key-name segments, and that is what costs the ids.

So the default is ``standard`` and ``strict`` is one config key away, for a
deployment that would rather over-redact than under-redact. The test
``test_default_policy_keeps_ids_paths_and_urls_but_not_credentials`` pins both
halves of that table, so a future change to the underlying heuristic has to
update this module's reasoning rather than silently changing what a trace holds.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .contract import MIN_PAYLOAD_CAP_BYTES
from .redaction import REDACTION_POLICIES, STANDARD

__all__ = [
    "DEFAULT_BEHAVIOUR_TRACE",
    "READERS",
    "BehaviourTraceConfig",
    "read_all_keys",
    "resolve_all",
]

#: The shipped default. ``max_payload_bytes`` is the single most consequential
#: number in this file: it is the cap that every layer-2/3/8 payload (a user
#: prompt, tool arguments, a tool result) has to fit inside, and it is
#: deliberately larger than the spine's per-attribute
#: ``max_attribute_value_chars`` (1024) because a payload is many attributes and
#: truncating a whole model's output to 1 KB would make layer 2 useless. 64 KiB
#: is about one page of prose; anything larger belongs in the tool's own output
#: externalisation, which already exists.
DEFAULT_BEHAVIOUR_TRACE: Final[dict[str, Any]] = {
    "max_payload_bytes": 64 * 1024,
    "tail_maxlen": 4096,
    "max_tracked_runs": 256,
    "emit_spans": True,
    "include_reasoning": True,
    "redaction_policy": STANDARD,
}


class BehaviourTraceConfig(BaseModel):
    """Bounds and switches for the behaviour-trace writer.

    There is deliberately no ``enabled`` field: the gate is
    :attr:`alpha.observability.config.ObservabilityConfig.enabled`, read from
    the injected recorder, so there is exactly one switch and it is the one that
    already defaults to ``False``.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True, frozen=True)

    max_payload_bytes: int = Field(
        default=DEFAULT_BEHAVIOUR_TRACE["max_payload_bytes"],
        ge=MIN_PAYLOAD_CAP_BYTES,
        description="Byte ceiling for one event payload. Over it the payload is clamped and the envelope records truncated/truncated_count/payload_bytes/payload_sha256.",
    )
    tail_maxlen: int = Field(
        default=DEFAULT_BEHAVIOUR_TRACE["tail_maxlen"],
        ge=1,
        description="Records kept in the writer's in-process ring. Oldest evicted first; the eviction count is disclosed, never silent.",
    )
    max_tracked_runs: int = Field(
        default=DEFAULT_BEHAVIOUR_TRACE["max_tracked_runs"],
        ge=1,
        description="How many runs' per-run sequence counters are retained at once. The least recently seen is evicted and disclosed.",
    )
    emit_spans: bool = Field(
        default=DEFAULT_BEHAVIOUR_TRACE["emit_spans"],
        description=(
            "Make writer.span()/aspan() create real TraceRecorder spans, so nesting, the depth cap and the existing span record format are the spine's. "
            "False makes the writer event-only, the right posture for a test asserting envelopes without span noise."
        ),
    )
    include_reasoning: bool = Field(
        default=DEFAULT_BEHAVIOUR_TRACE["include_reasoning"],
        description=(
            "Carry a provider's reasoning/thinking content in the payload. The configured models (space-bunny, union-alpha) declare supports_thinking=false, so this is "
            "normally None; the field exists so the day a model does return reasoning it is recorded without another schema change."
        ),
    )
    redaction_policy: str = Field(
        default=DEFAULT_BEHAVIOUR_TRACE["redaction_policy"],
        description=(
            "Which Redactor policy applies to behaviour payloads. Defaults to 'standard', NOT the spine's 'strict': see the module docstring for the measured table "
            "showing that 'strict' redacts every 32-hex run id, every filesystem path and every search URL."
        ),
    )

    @field_validator("redaction_policy", mode="before")
    @classmethod
    def _reject_unknown_policy(cls, value: object) -> str:
        """Refuse a typo rather than silently downgrading the policy.

        A ``field_validator`` rather than ``__post_init__``: pydantic v2 does not
        run ``__post_init__`` on every construction path, and a config that
        silently accepted ``"lenient"`` would then be caught at the first write
        instead of at construction -- which is exactly the "configuration error
        discovered in production" shape this repo's config classes avoid.
        """
        if value not in REDACTION_POLICIES:
            raise ValueError(f"unknown redaction policy {value!r}; expected one of {sorted(REDACTION_POLICIES)}")
        return str(value)


class _Reader:
    """One key's reader: how to read it, and which code depends on it."""

    __slots__ = ("consumer", "read")

    def __init__(self, read: Callable[[BehaviourTraceConfig], Any], consumer: str) -> None:
        self.read = read
        self.consumer = consumer


#: ``key -> reader``. The audit surface: a test asserts this covers exactly
#: :func:`read_all_keys` and that flipping each key changes an observable
#: behaviour, so a key cannot be declared and left unread.
READERS: Final[Mapping[str, _Reader]] = {
    "max_payload_bytes": _Reader(lambda c: c.max_payload_bytes, "BehaviourTraceWriter.emit -> contract.clamp_payload cap"),
    "tail_maxlen": _Reader(lambda c: c.tail_maxlen, "BehaviourTraceWriter._resolve_tail InMemorySink(max_records=...)"),
    "max_tracked_runs": _Reader(lambda c: c.max_tracked_runs, "BehaviourTraceWriter._trim_runs LRU bound on per-run seq counters"),
    "emit_spans": _Reader(lambda c: c.emit_spans, "BehaviourTraceWriter.span/aspan -> TraceRecorder.span delegation"),
    "include_reasoning": _Reader(lambda c: c.include_reasoning, "BehaviourTraceWriter.model_call reasoning field inclusion"),
    "redaction_policy": _Reader(lambda c: c.redaction_policy, "BehaviourTraceWriter Redactor construction policy"),
}


def read_all_keys(config: BehaviourTraceConfig | None = None) -> frozenset[str]:
    """Return the canonical schema keys (or *config*'s own keys)."""
    if config is None:
        return frozenset(BehaviourTraceConfig.model_fields)
    return frozenset(type(config).model_fields)


def resolve_all(config: BehaviourTraceConfig | None = None) -> dict[str, Any]:
    """Read every key through :data:`READERS` and return the resolved map."""
    target = config if config is not None else BehaviourTraceConfig()
    return {key: reader.read(target) for key, reader in READERS.items()}
