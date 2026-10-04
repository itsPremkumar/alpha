"""Offline tests for the Alpha-to-Alpha connection string codec.

``parse_invite`` is the only parser in the peer plane that consumes fully
attacker-controlled text, so this file is deliberately exhaustive about refusal:
every field gets a malformed case, every refusal is asserted to *name the field*,
and the endpoint cases assert the shared SSRF guard still applies.
"""

from __future__ import annotations

import re
import time

import pytest

from alpha.peer_network.invite import (
    CLOCK_SKEW_TOLERANCE_SECONDS,
    DEFAULT_TTL_SECONDS,
    MAX_TTL_SECONDS,
    InviteClaims,
    InviteError,
    build_invite,
    parse_invite,
)

CODE = "s3cret-pairing-code-value-0123456789"


def _full_invite(**overrides) -> str:
    kwargs = {
        "agent_id": "alpha-7f3a2b",
        "url": "http://192.168.1.20:8001",
        "websocket_url": "ws://192.168.1.20:8001/api/peer-network/ws",
        "name": "Priya's laptop",
        "pairing_code": CODE,
    }
    kwargs.update(overrides)
    return build_invite(**kwargs)


# --------------------------------------------------------------------------
# round trip
# --------------------------------------------------------------------------


def test_full_invite_round_trips_every_field():
    claims = parse_invite(_full_invite())
    assert claims.agent_id == "alpha-7f3a2b"
    assert claims.url == "http://192.168.1.20:8001"
    assert claims.websocket_url == "ws://192.168.1.20:8001/api/peer-network/ws"
    assert claims.name == "Priya's laptop"
    assert claims.pairing_code == CODE
    assert claims.has_secret is True
    assert claims.require_secret() == CODE
    assert claims.expires_at is not None


def test_round_trip_is_stable_so_copying_twice_yields_the_same_string():
    once = _full_invite(ttl_seconds=600, now=1_000_000)
    assert parse_invite(once).to_uri() == once


def test_address_only_invite_omits_the_secret_and_is_not_redeemable():
    uri = _full_invite(include_secret=False)
    assert "k=" not in uri
    claims = parse_invite(uri)
    assert claims.has_secret is False
    # The refusal explains the fix rather than surfacing later as a bare 401.
    with pytest.raises(InviteError, match="address-only"):
        claims.require_secret()


def test_a_full_invite_needs_a_code_and_an_address_only_one_refuses_none():
    with pytest.raises(InviteError, match="full invite needs one"):
        build_invite(agent_id="alpha-1", url="http://10.0.0.5:8001", include_secret=True)
    # include_secret=False with no code is the ordinary address-only mint.
    assert parse_invite(build_invite(agent_id="alpha-1", url="http://10.0.0.5:8001", include_secret=False)).agent_id == "alpha-1"


def test_summary_never_carries_the_pairing_code():
    """The paste-preview card is what gets screenshotted for support."""

    summary = parse_invite(_full_invite()).summary()
    assert CODE not in str(summary)
    assert summary["has_pairing_code"] is True
    assert summary["url"] == "http://192.168.1.20:8001"


# --------------------------------------------------------------------------
# version and shape
# --------------------------------------------------------------------------


def test_unknown_version_is_refused_rather_than_guessed():
    uri = _full_invite().replace("v=1", "v=2")
    with pytest.raises(InviteError, match="version"):
        parse_invite(uri)


def test_a_missing_version_is_refused():
    uri = _full_invite().replace("v=1&", "", 1)
    with pytest.raises(InviteError, match="version"):
        parse_invite(uri)


def test_a_foreign_scheme_is_refused_with_the_scheme_named():
    with pytest.raises(InviteError, match="alpha://"):
        parse_invite("https://connect?v=1&a=alpha-1&u=http://10.0.0.5:8001")
    with pytest.raises(InviteError, match="must start with"):
        parse_invite("just some pasted text")


def test_the_host_segment_must_be_connect():
    with pytest.raises(InviteError, match="alpha://connect"):
        parse_invite("alpha://somethingelse?v=1&a=alpha-1&u=http://10.0.0.5:8001")


def test_an_unrecognised_field_is_refused_not_ignored():
    """A newer build may depend on a field this one drops silently."""

    uri = _full_invite() + "&future_flag=1"
    with pytest.raises(InviteError, match="unrecognised field"):
        parse_invite(uri)


def test_a_duplicated_field_is_refused():
    uri = _full_invite() + f"&a=alpha-elsewhere"
    with pytest.raises(InviteError, match="more than once"):
        parse_invite(uri)


def test_a_queryless_invite_says_the_query_part_is_missing():
    with pytest.raises(InviteError, match="query part"):
        parse_invite("alpha://connect")


def test_empty_and_non_text_input_is_refused():
    with pytest.raises(InviteError, match="nothing to parse"):
        parse_invite("   ")
    with pytest.raises(InviteError, match="must be text"):
        parse_invite(None)  # type: ignore[arg-type]
    with pytest.raises(InviteError, match="must be text"):
        parse_invite(12345)  # type: ignore[arg-type]


def test_an_over_long_string_is_refused_before_parsing():
    with pytest.raises(InviteError, match="longer than"):
        parse_invite("alpha://connect?v=1&n=" + "x" * 5000)


def test_angle_wrapped_pastes_are_accepted_as_a_paste_convenience():
    claims = parse_invite(f"<{_full_invite()}>")
    assert claims.agent_id == "alpha-7f3a2b"


# --------------------------------------------------------------------------
# per-field validation, each naming its own field
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutation", "field"),
    [
        (lambda uri: uri.replace("a=alpha-7f3a2b", "a="), "agent id"),
        (lambda uri: uri.replace("a=alpha-7f3a2b", "a=has%20space"), "agent id"),
        (lambda uri: uri.replace("a=alpha-7f3a2b", f"a={'x' * 200}"), "agent id"),
        (lambda uri: uri.replace("u=http%3A%2F%2F192.168.1.20%3A8001", "u="), "gateway address"),
        (lambda uri: uri.replace("u=http%3A%2F%2F192.168.1.20%3A8001", "u=ftp%3A%2F%2F10.0.0.5"), "gateway address"),
        (lambda uri: uri.replace("w=ws%3A%2F%2F192.168.1.20%3A8001%2Fapi%2Fpeer-network%2Fws", "w=http%3A%2F%2F10.0.0.5"), "websocket address"),
        (lambda uri: re.sub(r"e=[^&]*", "e=notanumber", uri), "expiry"),
    ],
)
def test_each_malformed_field_is_refused_and_names_itself(mutation, field):
    with pytest.raises(InviteError, match=field):
        parse_invite(mutation(_full_invite()))


def test_the_shared_ssrf_guard_still_applies_to_an_invite_endpoint():
    """An invite must not smuggle in an endpoint the transport would reject."""

    for hostile in ("http://169.254.169.254", "http://metadata.google.internal", "http://0.0.0.0:8001", "http://user:pw@10.0.0.5:8001"):
        uri = build_invite(agent_id="alpha-1", url="http://10.0.0.5:8001", include_secret=False)
        uri = uri.replace("u=http%3A%2F%2F10.0.0.5%3A8001", "u=" + hostile.replace(":", "%3A").replace("/", "%2F"))
        with pytest.raises(InviteError, match="gateway address"):
            parse_invite(uri)


def test_build_invite_applies_the_same_guards_as_parse():
    with pytest.raises(InviteError, match="gateway address"):
        build_invite(agent_id="alpha-1", url="http://169.254.169.254", include_secret=False)
    with pytest.raises(InviteError, match="websocket address"):
        build_invite(agent_id="alpha-1", url="http://10.0.0.5:8001", websocket_url="http://10.0.0.5:8001", include_secret=False)
    with pytest.raises(InviteError, match="agent id"):
        build_invite(agent_id="has space", url="http://10.0.0.5:8001", include_secret=False)


def test_an_over_long_display_name_is_refused_with_the_bound_named():
    with pytest.raises(InviteError, match="at most 120"):
        build_invite(agent_id="alpha-1", url="http://10.0.0.5:8001", name="n" * 121, include_secret=False)
    with pytest.raises(InviteError, match="at most 120"):
        parse_invite(_full_invite(name="n" * 121))


def test_an_over_long_pairing_code_is_refused():
    with pytest.raises(InviteError, match="at most 256"):
        build_invite(agent_id="alpha-1", url="http://10.0.0.5:8001", pairing_code="k" * 257)


# --------------------------------------------------------------------------
# ttl bounds and expiry
# --------------------------------------------------------------------------


def test_a_non_positive_or_over_ceiling_ttl_is_refused_with_the_bound_named():
    for bad in (0, -1):
        with pytest.raises(InviteError, match="must be positive"):
            build_invite(agent_id="alpha-1", url="http://10.0.0.5:8001", include_secret=False, ttl_seconds=bad)
    with pytest.raises(InviteError, match=f"at most {MAX_TTL_SECONDS}"):
        build_invite(agent_id="alpha-1", url="http://10.0.0.5:8001", include_secret=False, ttl_seconds=MAX_TTL_SECONDS + 1)


def test_a_non_integer_ttl_is_refused_rather_than_coerced():
    with pytest.raises(InviteError, match="whole number"):
        build_invite(agent_id="alpha-1", url="http://10.0.0.5:8001", include_secret=False, ttl_seconds=90.5)  # type: ignore[arg-type]
    with pytest.raises(InviteError, match="whole number"):
        build_invite(agent_id="alpha-1", url="http://10.0.0.5:8001", include_secret=False, ttl_seconds=True)  # type: ignore[arg-type]


def test_a_fresh_invite_is_not_expired_and_the_default_is_fifteen_minutes():
    now = 1_700_000_000.0
    claims = parse_invite(build_invite(agent_id="alpha-1", url="http://10.0.0.5:8001", include_secret=False, now=now))
    assert claims.expires_at == int(now) + DEFAULT_TTL_SECONDS
    assert claims.is_expired(now=now) is False


def test_expiry_refuses_past_the_bound_plus_clock_skew_tolerance():
    now = 1_700_000_000.0
    claims = parse_invite(build_invite(agent_id="alpha-1", url="http://10.0.0.5:8001", include_secret=False, ttl_seconds=60, now=now))
    just_inside = claims.expires_at + CLOCK_SKEW_TOLERANCE_SECONDS - 1
    assert claims.is_expired(now=just_inside) is False
    assert claims.is_expired(now=claims.expires_at + CLOCK_SKEW_TOLERANCE_SECONDS + 1) is True


def test_expiry_defaults_to_the_real_clock_when_no_now_is_given():
    claims = parse_invite(build_invite(agent_id="alpha-1", url="http://10.0.0.5:8001", include_secret=False))
    assert claims.is_expired() is False


def test_a_claim_without_an_expiry_never_expires_on_its_own():
    """A pre-`e=` artifact is stale, but the codec must not invent a verdict.

    The service refuses a claim with no expiry separately; making `is_expired`
    return True here would report a *time* problem for a field that was simply
    absent, which sends an operator looking at the wrong thing.
    """

    claims = InviteClaims(agent_id="alpha-1", url="http://10.0.0.5:8001")
    assert claims.expires_at is None
    assert claims.is_expired(now=time.time()) is False


def test_a_non_positive_expiry_epoch_is_refused():
    uri = re.sub(r"e=[^&]*", "e=0", _full_invite())
    with pytest.raises(InviteError, match="expiry"):
        parse_invite(uri)


def test_an_unknown_field_is_refused_before_a_later_field_is_parsed():
    """Ordering is deliberate: a field this build does not understand may carry
    meaning it depends on, so the refusal happens before anything else is read."""

    uri = re.sub(r"e=[^&]*", "e=notanumber&z=1", _full_invite())
    with pytest.raises(InviteError, match="unrecognised field"):
        parse_invite(uri)
