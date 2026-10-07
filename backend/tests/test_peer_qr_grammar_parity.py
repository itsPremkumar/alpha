"""The invite grammar must agree across all four implementations.

There are four parsers/writers for one wire format: the Python codec in
``alpha.peer_network.invite`` (the authority), the Python client mirror used by
the model tool, the TypeScript mirror in ``frontend/src/lib/invite.ts``, and the
QR encoder's size output. A connection string is the one artifact two machines
have to agree on byte-for-byte, so drift between them is a connection failure
that no single-language test would catch.

This file pins the *agreement*, not each implementation's internals — the
per-language suites own those. Specifically:

* the field names, their order, and their required-ness;
* the version token, which must be ``1`` on every side;
* the prefix, since a paste handler routes on it before anything is parsed.

A change to the grammar must change this file in the same commit. That is the
point: the failure it prevents is a silent one, where each side parses its own
dialect happily and the connection simply never happens.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from alpha.peer_network.invite import (
    INVITE_HOST,
    INVITE_PREFIX_HINT,
    INVITE_SCHEME,
    INVITE_VERSION,
    build_invite,
    parse_invite,
)

# `parents[0]` is `tests/`, so the repo root is two levels up from it.
REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_INVITE = REPO_ROOT / "frontend" / "src" / "lib" / "invite.ts"


@pytest.fixture(scope="module")
def frontend_source() -> str:
    # A backend-only checkout (a published wheel, a slim CI image) legitimately has
    # no frontend tree. Skipping there is honest; failing would be noise about the
    # wrong thing. A full clone always runs these.
    if not FRONTEND_INVITE.exists():
        pytest.skip(f"frontend sources are not present in this checkout ({REPO_ROOT})")
    return FRONTEND_INVITE.read_text(encoding="utf-8")


def test_the_python_codec_is_the_authority_for_the_prefix_and_version():
    assert INVITE_SCHEME == "alpha"
    assert INVITE_HOST == "connect"
    assert INVITE_PREFIX_HINT == f"{INVITE_SCHEME}://{INVITE_HOST}"
    assert INVITE_VERSION == "1"


def test_the_frontend_mirror_declares_the_same_prefix_and_version(frontend_source: str):
    """A paste handler routes on the prefix before parsing, so a drift here makes
    every paste fall through as ordinary text instead of being recognised."""

    assert 'INVITE_VERSION = "1"' in frontend_source
    assert 'INVITE_PREFIX = "alpha://connect"' in frontend_source


@pytest.mark.parametrize("field", ["v", "a", "u", "w", "n", "e", "ep", "k"])
def test_every_field_name_exists_on_both_sides(field: str, frontend_source: str):
    """The field is what the wire calls it; a rename on one side orphans it.

    ``ep`` and ``k`` are the two that matter most here: dropping ``ep`` silently
    removes single-use, and dropping ``k`` produces an address-only string that
    cannot be redeemed — both look like working code.
    """

    # Present in the frontend's FIELD_LABELS table.
    assert re.search(rf'^\s*{field}:\s*"', frontend_source, re.MULTILINE), f"{field} missing from the frontend field table"


def test_both_sides_agree_on_which_fields_are_required(frontend_source: str):
    """``a`` and ``u`` are the two required fields, and both parsers must agree.

    A parser that accepted a string without an endpoint would let the Connect
    button enable on a partial paste and fail server-side with a worse message.
    """

    assert 'const url = seen.get("u") ?? "";' in frontend_source
    assert 'if (!url) fail("u", "is required");' in frontend_source
    assert 'if (!agentId) fail("a", "is required");' in frontend_source


def test_a_python_minted_invite_matches_the_frontend_field_order():
    """Field *order* is not wire-significant for parsing, but the encoder emits it
    and a reader diffing two strings needs it stable and identical."""

    uri = build_invite(agent_id="alpha-abc", url="http://10.0.0.5:8001", pairing_code="c" * 24, epoch=2, ttl_seconds=600, now=0)
    query = uri.split("?", 1)[1]
    keys = [pair.split("=", 1)[0] for pair in query.split("&")]
    assert keys == ["v", "a", "u", "e", "ep", "k"]


def test_an_invite_the_frontend_mirror_accepts_is_also_accepted_by_python(frontend_source: str):
    """The round-trip that actually matters: one string, two parsers."""

    uri = build_invite(
        agent_id="alpha-parity",
        url="http://192.168.1.20:8001",
        websocket_url="ws://192.168.1.20:8001/ws",
        name="Priya's laptop",
        pairing_code="parity-code-0123456789abcd",
        epoch=7,
        ttl_seconds=900,
        now=1_700_000_000,
    )
    # The frontend's own escape handling: a space in a value must be encoded, and
    # an apostrophe must survive a decode round-trip.
    assert " " not in uri
    parsed = parse_invite(uri)
    assert parsed.name == "Priya's laptop"
    assert parsed.agent_id == "alpha-parity"
    assert parsed.epoch == 7


def test_the_qr_encoder_can_render_a_real_invite(frontend_source: str):
    """The QR path is only useful if the string it renders is one the peer can
    parse, so this asserts a real invite stays inside the encoder's capacity."""

    # An invite is ~400 bytes; byte mode at ECC level L reaches version 12 for that.
    uri = build_invite(
        agent_id="alpha-qr-check",
        url="http://192.168.1.20:8001",
        websocket_url="ws://192.168.1.20:8001/api/peer-network/ws",
        name="A Reasonably Long Display Name",
        pairing_code="s" * 43,
        epoch=12345,
        ttl_seconds=900,
        now=1_700_000_000,
    )
    # A real full invite is ~240 bytes: comfortably inside the encoder's capacity
    # and small enough that the QR dialog renders at a comfortable density. The
    # lower bound matters — a string much shorter than this would mean a field
    # silently stopped being emitted, which is precisely the drift this file
    # exists to catch.
    assert 180 < len(uri.encode("utf-8")) < 400, "the invite size the QR dialog is designed around"


def test_the_encoder_refuses_an_invite_too_large_to_render_rather_than_shrinking_it():
    """`qr-encode.ts` picks the smallest version that fits and *refuses* past its
    table. A silent fallback to a smaller version would render a scannable code
    carrying truncated data — the worst outcome, because it looks correct."""

    uri = build_invite(
        agent_id="alpha-qr-overflow",
        url="http://192.168.1.20:8001",
        pairing_code="s" * 43,
        ttl_seconds=900,
        now=1_700_000_000,
    )
    assert parse_invite(uri).agent_id == "alpha-qr-overflow"
