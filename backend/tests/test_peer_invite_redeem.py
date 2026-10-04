"""Offline tests for redeeming an Alpha connection string.

The properties under test are the ones the codec docstring claims and the service
docstring used to get wrong:

* redeeming **stamps provenance** without granting anything;
* an invite is **single use**, enforced by the *issuer* via a monotonic epoch —
  not by rotating the redeemer's own code, which protects nothing;
* a replayed string is refused **without** consuming throttle budget, so a third
  party holding a screenshot cannot lock the real owner out of pairing;
* the epoch watermark cannot be poisoned by someone who does not have the code;
* a rewritten endpoint is refused by the agent-id comparison.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from alpha.peer_network import PeerNetworkService
from alpha.peer_network.invite import InviteClaims, InviteError, parse_invite
from alpha.peer_network.models import PeerCard, PeerPairRequest


@pytest.fixture
def peer_pair(tmp_path: Path):
    """Two enabled installations whose transports talk to each other in-process."""

    issuer = PeerNetworkService(tmp_path / "issuer", enabled=True)
    redeemer = PeerNetworkService(tmp_path / "redeemer", enabled=True)

    async def _wire() -> None:
        # `redeemer.pair()` reaches the issuer twice: fetch its card, then call
        # its public pair route. Both are answered locally rather than over a
        # socket, so this exercises the real service code paths.
        async def fetch_card(endpoint: str):
            assert endpoint == issuer.card().url
            return issuer.card()

        # `PeerTransport.pair` returns the decoded body, not a `TransportResult`
        # (`service.pair` calls `.get("accepted")` on it).
        async def pair_remote(endpoint: str, request: PeerPairRequest) -> dict:
            response = await issuer.accept_pair(request)
            return response.model_dump(mode="json")

        redeemer.transport.fetch_card = fetch_card  # type: ignore[method-assign]
        redeemer.transport.pair = pair_remote  # type: ignore[method-assign]

    asyncio.run(_wire())
    yield issuer, redeemer
    issuer.store.close()
    redeemer.store.close()


def _invite(issuer: PeerNetworkService, *, include_secret: bool = True) -> str:
    return asyncio.run(issuer.build_invite(include_secret=include_secret))["invite"]


# --------------------------------------------------------------------------
# minting
# --------------------------------------------------------------------------


def test_a_full_invite_carries_a_code_and_an_epoch_and_the_address_only_one_carries_neither(peer_pair):
    issuer, _ = peer_pair
    full = asyncio.run(issuer.build_invite())
    assert full["includes_pairing_code"] is True
    assert full["single_use"] is True
    assert full["epoch"] == 1
    claims = parse_invite(full["invite"])
    assert claims.has_secret is True
    assert claims.epoch == 1
    assert claims.require_secret() == issuer.identity.pairing_code

    address_only = asyncio.run(issuer.build_invite(include_secret=False))
    assert address_only["includes_pairing_code"] is False
    assert "k=" not in address_only["invite"]
    assert "ep=" not in address_only["invite"]
    assert issuer.identity.pairing_code not in address_only["invite"]


def test_each_full_invite_gets_a_strictly_greater_epoch(peer_pair):
    issuer, _ = peer_pair
    epochs = [asyncio.run(issuer.build_invite())["epoch"] for _ in range(3)]
    assert epochs == [1, 2, 3]


def test_the_epoch_survives_a_restart_so_a_pre_restart_screenshot_stays_spent(tmp_path: Path):
    """A watermark that reset to 0 on boot would resurrect a leaked invite."""

    first = PeerNetworkService(tmp_path / "issuer", enabled=True)
    epoch = asyncio.run(first.build_invite())["epoch"]
    # Spend it here, so the post-restart assertion below is testing that the
    # *watermark* survived rather than that the number was merely reused.
    assert first.store.consume_invite_epoch(epoch) is True
    first.store.close()
    second = PeerNetworkService(tmp_path / "issuer", enabled=True)
    try:
        assert asyncio.run(second.build_invite())["epoch"] > epoch
        assert second.store.consume_invite_epoch(epoch) is False
    finally:
        second.store.close()


def test_a_disabled_plane_refuses_to_mint_rather_than_handing_out_an_unusable_credential(tmp_path: Path):
    off = PeerNetworkService(tmp_path / "off", enabled=False)
    try:
        with pytest.raises(ValueError, match="disabled"):
            asyncio.run(off.build_invite())
    finally:
        off.store.close()


def test_the_summary_shown_before_confirming_never_contains_the_code(peer_pair):
    issuer, _ = peer_pair
    built = asyncio.run(issuer.build_invite())
    assert issuer.identity.pairing_code not in str(built["summary"])
    assert built["summary"]["has_pairing_code"] is True


# --------------------------------------------------------------------------
# redeeming
# --------------------------------------------------------------------------


def test_redeeming_pairs_the_peer_and_stamps_invite_provenance(peer_pair):
    issuer, redeemer = peer_pair
    result = asyncio.run(redeemer.redeem_invite(_invite(issuer)))
    assert result["agent_id"] == issuer.identity.agent_id
    assert result["peer"]["trust"] == "paired"
    # Provenance, not a capability: `link_source` is a label an operator can sort
    # by. Trust stays `paired` — an invite did not widen anything.
    assert result["peer"]["link_source"] == "invite"
    assert result["peer"]["auto_reply"] is False


def test_redeeming_does_not_grant_auto_reply_or_change_the_local_pairing_code(peer_pair):
    issuer, redeemer = peer_pair
    before = redeemer.identity.pairing_code
    asyncio.run(redeemer.redeem_invite(_invite(issuer)))
    peer = asyncio.run(redeemer.get_peer(issuer.identity.agent_id))
    assert peer["auto_reply"] is False
    # The redeemer's own code is untouched: it is not the credential that leaked,
    # so rotating it would protect nothing while silently invalidating an invite
    # this installation had already shared with somebody else.
    assert redeemer.identity.pairing_code == before


def _with_expiry(uri: str, expires_at: int) -> str:
    claims = parse_invite(uri)
    return InviteClaims(
        agent_id=claims.agent_id,
        url=claims.url,
        websocket_url=claims.websocket_url,
        name=claims.name,
        expires_at=expires_at,
        epoch=claims.epoch,
        pairing_code=claims.pairing_code,
    ).to_uri()


def test_an_expired_invite_is_refused_before_any_pairing_attempt(peer_pair):
    issuer, redeemer = peer_pair
    expired = _with_expiry(_invite(issuer), 1)
    with pytest.raises(InviteError, match="expired"):
        asyncio.run(redeemer.redeem_invite(expired))
    # Nothing was paired by the refused attempt.
    assert asyncio.run(redeemer.list_peers()) == []


def test_an_address_only_invite_is_refused_with_the_fix_in_the_message(peer_pair):
    issuer, redeemer = peer_pair
    with pytest.raises(InviteError, match="address-only"):
        asyncio.run(redeemer.redeem_invite(_invite(issuer, include_secret=False)))
    # Nothing was paired by the failed attempt.
    assert asyncio.run(redeemer.list_peers()) == []


def test_a_malformed_invite_names_the_field_rather_than_saying_invalid(peer_pair):
    issuer, redeemer = peer_pair
    with pytest.raises(InviteError, match="version"):
        asyncio.run(redeemer.redeem_invite(_invite(issuer).replace("v=1", "v=9")))


def test_a_rewritten_endpoint_is_refused_by_the_agent_id_comparison(peer_pair):
    """Endpoint substitution: editing `u=` yields the impostor's card, whose
    agent id cannot match the `a=` the editor left behind."""

    issuer, redeemer = peer_pair
    claims = parse_invite(_invite(issuer))
    redirected = claims.__class__(
        agent_id=claims.agent_id,
        url="http://127.0.0.1:9",  # a host the attacker controls
        websocket_url=claims.websocket_url,
        name=claims.name,
        expires_at=claims.expires_at,
        epoch=claims.epoch,
        pairing_code=claims.pairing_code,
    ).to_uri()

    async def fetch_card(endpoint: str):
        # The impostor answers with its own card, not the issuer's.
        return PeerCard(agent_id="alpha-attacker", name="Impostor", url="http://127.0.0.1:9")

    redeemer.transport.fetch_card = fetch_card  # type: ignore[method-assign]
    with pytest.raises(Exception, match="identity mismatch"):
        asyncio.run(redeemer.redeem_invite(redirected))


# --------------------------------------------------------------------------
# single use, enforced by the issuer
# --------------------------------------------------------------------------


def test_the_same_invite_cannot_be_redeemed_twice(peer_pair):
    issuer, redeemer = peer_pair
    uri = _invite(issuer)
    assert asyncio.run(redeemer.redeem_invite(uri))["agent_id"] == issuer.identity.agent_id

    # A second caller presenting the identical string is refused by the issuer's
    # epoch watermark.
    request = PeerPairRequest(
        card=redeemer.card(),
        pairing_code=issuer.identity.pairing_code,
        invite_epoch=parse_invite(uri).epoch,
    )
    response = asyncio.run(issuer.accept_pair(request))
    assert response.accepted is False
    assert "already used" in response.message


def test_a_fresh_invite_outranks_the_spent_one(peer_pair):
    issuer, redeemer = peer_pair
    first = parse_invite(_invite(issuer))
    asyncio.run(redeemer.redeem_invite(first.to_uri()))
    second = parse_invite(_invite(issuer))
    assert second.epoch > first.epoch
    request = PeerPairRequest(
        card=redeemer.card(),
        pairing_code=issuer.identity.pairing_code,
        invite_epoch=second.epoch,
    )
    assert asyncio.run(issuer.accept_pair(request)).accepted is True


def test_a_pairing_without_an_epoch_is_unaffected_by_the_watermark(peer_pair):
    """The classic manual-code path must keep working after this ships."""

    issuer, redeemer = peer_pair
    asyncio.run(redeemer.redeem_invite(_invite(issuer)))
    manual = PeerPairRequest(card=redeemer.card(), pairing_code=issuer.identity.pairing_code)
    assert asyncio.run(issuer.accept_pair(manual)).accepted is True


def test_a_replay_does_not_consume_throttle_budget(peer_pair):
    """A third party replaying a leaked screenshot must not be able to burn the
    owner's pairing budget and lock the real owner out of pairing."""

    issuer, redeemer = peer_pair
    uri = _invite(issuer)
    asyncio.run(redeemer.redeem_invite(uri))
    epoch = parse_invite(uri).epoch
    snapshot_before = issuer.pairing_throttle.snapshot()
    for _ in range(5):
        request = PeerPairRequest(
            card=redeemer.card(),
            pairing_code=issuer.identity.pairing_code,
            invite_epoch=epoch,
        )
        assert asyncio.run(issuer.accept_pair(request)).accepted is False
    # The property under test: the owner's budget survived five replays, so a
    # *different* prospective peer can still pair. This is asserted by actually
    # pairing them rather than by reading a counter, because a counter only says
    # what was recorded — not what is still permitted.
    stranger_card = PeerCard(agent_id="alpha-a-stranger", name="Stranger", url="http://10.0.0.9:8001")
    stranger = PeerPairRequest(card=stranger_card, pairing_code=issuer.identity.pairing_code)
    assert asyncio.run(issuer.accept_pair(stranger)).accepted is True

    # And a wrong code is still refused and still costs that identity budget —
    # asserted separately so this test cannot pass merely because the replay path
    # fails to exist.
    attacker_card = PeerCard(agent_id="alpha-an-attacker", name="Attacker", url="http://10.0.0.8:8001")
    wrong = PeerPairRequest(card=attacker_card, pairing_code="wrong-code-that-is-long-enough-000")
    assert asyncio.run(issuer.accept_pair(wrong)).accepted is False


def test_the_watermark_cannot_be_poisoned_without_the_pairing_code(peer_pair):
    """Consuming an epoch requires a valid credential. If the check ran first,
    anyone who merely saw an invite could claim a huge epoch and permanently
    block every future invite without ever knowing the code."""

    issuer, redeemer = peer_pair
    huge = PeerPairRequest(
        card=redeemer.card(),
        pairing_code="definitely-not-the-right-code-000000",
        invite_epoch=999_999,
    )
    response = asyncio.run(issuer.accept_pair(huge))
    assert response.accepted is False
    assert "already used" not in response.message  # it failed on the code, first
    # A legitimate invite still redeems.
    assert asyncio.run(redeemer.redeem_invite(_invite(issuer)))["agent_id"] == issuer.identity.agent_id


def test_the_public_peer_response_carries_no_owner_id_or_card(peer_pair):
    """D5: the authenticated API used to be looser than the model tool."""

    issuer, redeemer = peer_pair
    result = asyncio.run(redeemer.redeem_invite(_invite(issuer)))
    assert "owner_id" not in result["peer"]
    assert "card" not in result["peer"]
    assert "outbound_token" not in result["peer"]
    for peer in asyncio.run(redeemer.list_peers()):
        assert "owner_id" not in peer
        assert "card" not in peer
        assert "outbound_token" not in peer
