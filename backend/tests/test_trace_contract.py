"""The event contract and the closed registry: versioned shape, bounds, redaction.

What is pinned here
-------------------
* The envelope is versioned, and a record whose ``schema_version`` this build does
  not know is **refused** rather than mis-read.
* Every event code fits the durable store's 32-character ``event_type`` bound, and
  the literal duplicated in :mod:`.codes` is asserted against the real constant.
* Every one of the eighteen layers has at least one registered code, so "the
  registry covers the layer map" is checkable.
* The four legacy error taxonomies are **reconciled, not extended**: the closure
  checks return empty, and adding a value to a legacy enum without a mapping is
  the failure they exist to catch.
* Redaction happens at construction, so there is no path to an unredacted record.
* An over-cap payload is truncated with the flag, the original byte count and the
  SHA-256 of the *full* payload -- never silently.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from alpha.constants import RUN_EVENT_TYPE_MAX_LENGTH
from alpha.observability.trace.codes import (
    DURABLE_CATEGORY,
    DURABLE_EVENT_TYPE_ALIASES,
    EVENT_CODES,
    EVENT_TYPES,
    LAYER_EVENT_CODES,
    LEGACY_ERROR_CODE_ALIASES,
    LEGACY_TAXONOMIES,
    MAX_EVENT_CODE_LENGTH,
    STATUS_SEVERITY_ALIASES,
    TraceLayer,
    TraceSeverity,
    UnknownTraceErrorCode,
    UnknownTraceEventTypeError,
    event_type,
    layer_of,
    require_event_type,
    resolve_error_code,
    unresolved_durable_types,
    unresolved_error_codes,
    unresolved_statuses,
)
from alpha.observability.trace.contract import (
    ENVELOPE_SCHEMA_VERSION,
    PAYLOAD_DROP_NOTICE_KEY,
    TraceBounds,
    TraceEnvelope,
    UnknownEnvelopeFieldError,
)

# Fabricated, split-literal so no whole credential-shaped token exists in this
# file. Same convention as ``alpha.observability.redaction``: a module that proves
# a secret is not written has to be able to name one.
_OPENAI_KEY = "sk-proj-" + "0123456789abcdefghijklmnopqrstuvwx"
_GITHUB_PAT = "ghp_" + "0123456789abcdefghijklmnopqrstuvwxyz"


def _envelope(**overrides):
    base = {
        "event_type": "model.call.completed",
        "run_id": "a" * 32,
        "trace_id": "trace-1",
        "seq": 1,
        "payload": {"provider": "space-bunny", "model": "space-bunny-free", "finish_reason": "stop", "latency_ms": 12.5},
    }
    base.update(overrides)
    return TraceEnvelope.build(**base)


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_every_layer_has_at_least_one_registered_code():
    for layer in TraceLayer:
        assert LAYER_EVENT_CODES[int(layer)], f"layer {layer!r} has no event code; an empty layer is a hole in the map, not an omission"


def test_every_code_fits_the_durable_store_bound():
    for code in sorted(EVENT_CODES):
        assert len(code) <= RUN_EVENT_TYPE_MAX_LENGTH, f"{code!r} cannot be persisted as a run event_type"


def test_the_duplicated_code_bound_matches_the_real_constant():
    """The literal in codes.py exists to keep that module import-light; this is
    the check that it has not drifted from the store's actual bound."""
    assert MAX_EVENT_CODE_LENGTH == RUN_EVENT_TYPE_MAX_LENGTH


def test_unknown_code_is_refused_with_the_offending_value_named():
    with pytest.raises(UnknownTraceEventTypeError) as excinfo:
        require_event_type("model.call.completedd")
    assert "model.call.completedd" in str(excinfo.value)
    with pytest.raises(UnknownTraceEventTypeError):
        event_type(42)


def test_layer_of_is_a_dict_lookup_not_a_prefix_convention():
    assert layer_of("node.transition") is TraceLayer.NODE
    assert layer_of("err.raised") is TraceLayer.ERROR
    assert layer_of("evo.verify.outcome") is TraceLayer.SELF_EVOLUTION


def test_required_payload_keys_are_declared_for_the_expensive_codes():
    """A code whose payload can be incomplete is a code the sentinel cannot trust."""
    for code in ("model.call.completed", "tool.select.decided", "skill.select.decided", "node.transition", "err.raised", "evo.verify.outcome"):
        assert EVENT_TYPES[code].required_payload, f"{code} declares no required payload keys"


# ---------------------------------------------------------------------------
# error taxonomy reconciliation
# ---------------------------------------------------------------------------


def test_all_four_legacy_taxonomies_are_named():
    assert len(LEGACY_TAXONOMIES) == 4
    assert {taxonomy.key for taxonomy in LEGACY_TAXONOMIES} == {"run_event_type", "trace_event_status", "gateway_auth_code", "config_validation_code"}


def test_every_legacy_error_value_reconciles_onto_the_surviving_registry():
    """The closure check. Empty is the pass condition; a non-empty result is a
    fifth taxonomy in the making and must fail the build."""
    from alpha.errors.registry import ERROR_CODES

    assert unresolved_error_codes() == ()
    for value, target in LEGACY_ERROR_CODE_ALIASES.items():
        assert target in ERROR_CODES, f"alias {value!r} points at {target!r}, which is not registered in alpha.errors.registry"


def test_the_three_legacy_enums_are_all_actually_reachable():
    """Proves the closure check is reading live values, not passing vacuously."""
    by_key = {taxonomy.key: taxonomy for taxonomy in LEGACY_TAXONOMIES}
    assert len(by_key["gateway_auth_code"].values()) == 9
    # One taxonomy, three enum classes: 9 + 3 + 5.
    assert len(by_key["config_validation_code"].values()) == 17
    assert len(by_key["trace_event_status"].values()) == 6


def test_every_legacy_status_maps_onto_the_error_severity_scale():
    assert unresolved_statuses() == ()
    assert set(STATUS_SEVERITY_ALIASES.values()) <= {member.value for member in TraceSeverity}


def test_trace_severity_is_the_same_scale_as_the_error_registry():
    from alpha.errors.registry import ErrorSeverity

    assert {member.value for member in TraceSeverity} == {member.value for member in ErrorSeverity}


def test_durable_event_types_map_two_ways_onto_trace_codes():
    assert unresolved_durable_types() == ()
    for durable_type, code in DURABLE_EVENT_TYPE_ALIASES.items():
        assert code in EVENT_CODES, f"{durable_type!r} maps to unknown trace code {code!r}"


def test_a_legacy_alias_resolves_to_the_surviving_code():
    assert resolve_error_code("invalid_credentials") == "AUTH_INVALID_CREDENTIALS"
    assert resolve_error_code("AUTH_INVALID_CREDENTIALS") == "AUTH_INVALID_CREDENTIALS"


def test_user_not_found_collapses_onto_invalid_credentials_on_purpose():
    """The anti-enumeration decision, pinned.

    A login that distinguishes "no such user" from "wrong password" is a user
    enumeration oracle. If this test ever fails because someone split the two
    codes apart, that is a *security* change and needs an explicit decision --
    not a tidy-up.
    """
    assert resolve_error_code("user_not_found") == resolve_error_code("invalid_credentials")


def test_an_unregistered_code_is_refused_rather_than_defaulted():
    """Falling back to INTERNAL_ERROR would erase the difference between 'this
    code is unknown' and 'this code means something else'."""
    with pytest.raises(UnknownTraceErrorCode) as excinfo:
        resolve_error_code("TOTALLY_MADE_UP")
    assert "TOTALLY_MADE_UP" in str(excinfo.value)
    with pytest.raises(UnknownTraceErrorCode):
        resolve_error_code("")


# ---------------------------------------------------------------------------
# envelope shape
# ---------------------------------------------------------------------------


def test_envelope_carries_the_whole_declared_field_set():
    record = _envelope().to_record()
    for field in (
        "schema_version",
        "event_id",
        "seq",
        "run_id",
        "trace_id",
        "thread_id",
        "correlation_id",
        "parent_span_id",
        "agent_name",
        "parent_agent_name",
        "agent_depth",
        "subagent_id",
        "span_id",
        "node",
        "from_node",
        "to_node",
        "severity",
        "ts_monotonic",
        "ts_wall",
        "event_type",
        "payload",
        "payload_bytes",
        "payload_sha256",
        "truncated",
        "dropped_items",
    ):
        assert field in record, f"{field} is missing from the persisted record"


def test_event_id_is_monotonic_per_run_and_unique():
    first = _envelope(seq=1)
    second = _envelope(seq=2)
    assert first.event_id < second.event_id, "event_id must sort in emission order within a run"
    assert first.event_id.startswith(first.run_id)
    other = _envelope(seq=1, run_id="b" * 32)
    assert other.event_id != first.event_id


def test_both_clocks_are_recorded():
    envelope = _envelope(ts_monotonic=12.5, ts_wall=1_700_000_000.25)
    assert envelope.ts_monotonic == 12.5
    assert envelope.ts_wall == 1_700_000_000.25


def test_round_trip_through_the_stable_record():
    original = _envelope(agent_name="lead-agent", agent_depth=0, thread_id="t-1")
    restored = TraceEnvelope.from_record(original.to_record())
    assert restored == original


def test_an_unknown_schema_version_is_refused_not_guessed():
    record = _envelope().to_record()
    record["schema_version"] = ENVELOPE_SCHEMA_VERSION + 1
    with pytest.raises(UnknownEnvelopeFieldError) as excinfo:
        TraceEnvelope.from_record(record)
    assert str(ENVELOPE_SCHEMA_VERSION + 1) in str(excinfo.value)


def test_a_malformed_record_is_refused_with_the_reason():
    record = _envelope().to_record()
    del record["event_type"]
    with pytest.raises(UnknownEnvelopeFieldError):
        TraceEnvelope.from_record(record)


def test_a_missing_required_payload_key_is_refused_at_write_time():
    with pytest.raises(ValueError, match="requires payload key"):
        TraceEnvelope.build(
            event_type="tool.select.decided",
            run_id="a" * 32,
            trace_id="t",
            seq=1,
            payload={"chosen": "bash"},
        )


# ---------------------------------------------------------------------------
# redaction at write time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("secret", [_OPENAI_KEY, _GITHUB_PAT])
def test_a_secret_in_a_prompt_never_reaches_the_record(secret):
    envelope = _envelope(payload={"provider": "p", "model": "m", "finish_reason": "stop", "latency_ms": 1, "prompt": f"please use {secret} to authenticate"})
    serialized = json.dumps(envelope.to_record())
    assert secret not in serialized
    assert "REDACTED" in envelope.payload["prompt"]


def test_a_key_named_key_is_credential_shaped_under_the_strict_policy():
    """Over-redaction is the accepted cost of the strict policy, and this pins
    which way it goes. ``key_000`` has segments ("key", "000") and "key" is in
    the strict segment set, so the value is replaced even though it is 40 x's.
    A run that complains about this should complain about the *policy*, not about
    a bug: the alternative is a trace that keeps a credential because its key
    happened to be spelled unusually.
    """
    envelope = _envelope(payload={"provider": "p", "model": "m", "finish_reason": "stop", "latency_ms": 1, "key_000": "x" * 40})
    assert envelope.payload["key_000"] == "[REDACTED:secret_named_key]"


def test_the_standard_policy_keeps_a_key_named_key_visible():
    """The escape hatch is a policy choice, not a per-value override: a caller
    who would rather keep a high-entropy build id readable in a trace asks for
    ``standard`` and gets a different decision for the whole run."""
    envelope = TraceEnvelope.build(
        event_type="model.call.completed",
        run_id="a" * 32,
        trace_id="t",
        seq=1,
        payload={"provider": "p", "model": "m", "finish_reason": "stop", "latency_ms": 1, "key_000": "x" * 40},
        bounds=TraceBounds(redaction_policy="standard"),
    )
    assert envelope.payload["key_000"] == "x" * 40


def test_a_secret_under_a_credential_named_key_is_replaced():
    """A non-string value under a secret-named key is gap 1 in the redactor's own
    table, and it is exactly the shape a tool argument takes."""
    envelope = _envelope(payload={"provider": "p", "model": "m", "finish_reason": "stop", "latency_ms": 1, "api_key": 1234567890123456})
    assert envelope.payload["api_key"] == "[REDACTED:secret_named_key]"


def test_a_secret_in_a_nested_tool_result_is_replaced():
    """redact_attributes is the depth-1 entry point; a tool result is routinely a
    list of dicts whose own values carry credentials, and the recursion is what
    makes that safe without 200 call sites each remembering."""
    envelope = _envelope(
        payload={
            "provider": "p",
            "model": "m",
            "finish_reason": "stop",
            "latency_ms": 1,
            "tool_result": {"headers": {"Authorization": f"Bearer {_GITHUB_PAT}"}},
        }
    )
    assert _GITHUB_PAT not in json.dumps(envelope.to_record())


# ---------------------------------------------------------------------------
# payload bounds: truncated, disclosed, verifiable
# ---------------------------------------------------------------------------


def test_an_over_cap_payload_is_truncated_with_flag_size_and_hash():
    bounds = TraceBounds(max_payload_bytes=300)
    payload = {"model_config_sha256": "h", "config_version": "7", **{f"item_{index:03d}": "x" * 40 for index in range(50)}}
    envelope = TraceEnvelope.build(
        event_type="env.run.opened",
        run_id="a" * 32,
        trace_id="t",
        seq=1,
        payload=payload,
        bounds=bounds,
    )

    assert envelope.truncated is True
    assert envelope.dropped_items > 0
    assert PAYLOAD_DROP_NOTICE_KEY in envelope.payload
    assert envelope.payload_bytes > envelope.stored_payload_bytes, "the disclosure size must describe the full payload, not the stored prefix"
    full = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    assert envelope.payload_sha256 == hashlib.sha256(full.encode()).hexdigest()
    assert envelope.payload_bytes == len(full.encode())


def test_a_payload_under_the_cap_is_not_truncated():
    envelope = _envelope(payload={"provider": "p", "model": "m", "finish_reason": "stop", "latency_ms": 1, "small": "v"}, bounds=TraceBounds(max_payload_bytes=10_000))
    assert envelope.truncated is False
    assert envelope.dropped_items == 0
    assert envelope.payload_bytes == envelope.stored_payload_bytes


def test_truncation_is_not_inferred_from_the_notice_key():
    """A caller may legitimately pass a payload that already contains
    ``_dropped_items``. The flag is computed from the sealing step, not sniffed,
    so it cannot be spoofed into being wrong."""
    envelope = TraceEnvelope.build(
        event_type="env.run.opened",
        run_id="a" * 32,
        trace_id="t",
        seq=1,
        payload={"model_config_sha256": "h", "config_version": "1", PAYLOAD_DROP_NOTICE_KEY: "caller said so"},
        bounds=TraceBounds(max_payload_bytes=10_000),
    )
    assert envelope.truncated is False
    assert envelope.dropped_items == 0


def test_the_first_key_alone_can_exceed_the_cap_and_is_still_disclosed():
    envelope = TraceEnvelope.build(
        event_type="env.run.opened",
        run_id="a" * 32,
        trace_id="t",
        seq=1,
        payload={"model_config_sha256": "h" * 500, "config_version": "1"},
        bounds=TraceBounds(max_payload_bytes=64),
    )
    assert envelope.truncated is True
    assert envelope.dropped_items == 2
    assert PAYLOAD_DROP_NOTICE_KEY in envelope.payload


def test_two_independently_built_events_of_the_same_payload_hash_the_same():
    """The digest is a content identity, so key insertion order must not change
    it -- otherwise a reader comparing two envelopes would see a false mismatch."""
    first = _envelope(payload={"provider": "p", "model": "m", "finish_reason": "stop", "latency_ms": 1, "a": 1, "b": 2})
    second = _envelope(payload={"b": 2, "a": 1, "latency_ms": 1, "finish_reason": "stop", "model": "m", "provider": "p"})
    assert first.payload_sha256 == second.payload_sha256


def test_an_unrecognised_severity_is_refused_rather_than_defaulted():
    with pytest.raises(ValueError, match="unknown trace severity"):
        _envelope(severity="catastrophic")


def test_the_durable_row_shape_is_the_existing_store_contract():
    row = _envelope().to_run_event()
    assert row["category"] == DURABLE_CATEGORY
    assert set(row) == {"event_type", "category", "content", "metadata"}
    assert "schema_version" in row["metadata"]
    assert row["event_type"] in EVENT_CODES


def test_an_envelope_rebuilt_from_a_durable_row_equals_the_original():
    original = _envelope(thread_id="t-9", agent_name="lead-agent")
    assert TraceEnvelope.from_run_event(original.to_run_event()) == original
