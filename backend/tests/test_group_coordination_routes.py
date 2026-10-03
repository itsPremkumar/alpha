"""Gateway routes for group coordination: activity, claims, conflicts, orphans.

Route-boundary coverage for the layer that joins `groups/activity.py` to
`groups/claims.py`. The sharp edges live in the Gateway half, because this is
the only place live `RunEvidence` can be read at all — `RunManager` is owned by
`app.gateway.deps` and `packages/harness/` may never import `app.*`.

Three invariants are pinned here, each of which is a claim the UI depends on:

1. **Route ordering.** These routes share `/api/groups` with `groups.py`'s
   single-segment `/{name}` catch-all.
2. **`crashed` is not `unresponsive`.** Only a hard crash verdict makes a
   claim reclaimable; a slow agent's work must not be handed away.
3. **A crash is evidence; a hand-off is a decision.** `reconcile` records the
   orphan, `reclaim` is where a peer acts on it — the two are not one route.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.gateway.app import create_app
from app.gateway.routers import group_coordination, groups

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    import alpha.bots.registry as bot_reg
    import alpha.groups.claims as claims_mod
    import alpha.groups.service as grp_svc

    monkeypatch.setattr(bot_reg, "_global_registry", None)
    monkeypatch.setattr(bot_reg, "_global_registry_path", None)
    monkeypatch.setattr(grp_svc, "_global_groups", None)
    monkeypatch.setattr(grp_svc, "_global_groups_path", None)
    # The claim store and the activity ledger are process-globals rooted at
    # ALPHA_HOME. Reset them so one test's claims cannot leak into the next.
    monkeypatch.setattr(claims_mod, "_global_claim_store", None, raising=False)
    yield


class _FakeRequest:
    """The minimum `get_run_manager` and the auth decorator need."""

    def __init__(self, manager=None):
        self.state = type("S", (), {})()
        self._alpha_test_bypass_auth = True
        self.state.run_manager = manager


class _FakeRecord:
    def __init__(self, run_id, status, stop_reason=None, error=None):
        self.run_id = run_id
        self.status = type("S", (), {"value": status})()
        self.stop_reason = stop_reason
        self.error = error


class _FakeManager:
    def __init__(self, rows):
        self._rows = rows

    async def get(self, run_id):
        return self._rows.get(run_id)


# ---------------------------------------------------------------------------
# Route mounting and ordering
# ---------------------------------------------------------------------------


async def test_gateway_mounts_the_coordination_routes() -> None:
    paths = {route.path for route in create_app().routes}
    for expected in (
        "/api/groups/{name}/coordination",
        "/api/groups/{name}/claims",
        "/api/groups/{name}/claims/{claim_id}/release",
        "/api/groups/{name}/claims/{claim_id}/reclaim",
        "/api/groups/{name}/reconcile",
    ):
        assert expected in paths, f"{expected} is not mounted"


async def test_the_coordination_router_precedes_the_name_catchall() -> None:
    """Starlette matches in registration order.

    Same trap as `GET /tree` in `groups.py`: a coordination route mounted after
    the single-segment catch-all would answer `Room 'coordination' not found`,
    which is indistinguishable from the route not existing.
    """
    paths = [route.path for route in create_app().routes if route.path.startswith("/api/groups")]
    assert "/api/groups/{name}/coordination" in paths
    assert paths.index("/api/groups/{name}/coordination") < paths.index("/api/groups/{name}")


async def test_the_coordination_route_really_answers() -> None:
    """Not just mounted — reachable, and not swallowed by the catch-all."""
    await groups.create_room(groups.RoomCreateRequest(name="warroom", members=["architect"]))
    result = await group_coordination.get_room_coordination("warroom", _FakeRequest(_FakeManager({})))
    assert result["ok"] is True
    assert result["room"] == "warroom"
    assert result["count"] == 1


# ---------------------------------------------------------------------------
# Activity needs the live run store, and absence is a distinct claim
# ---------------------------------------------------------------------------


async def test_a_terminal_failure_run_resolves_to_crashed() -> None:
    """Without the live run read this resolves to `idle` — the exact
    conflation `activity.py` exists to eliminate."""
    await groups.create_room(groups.RoomCreateRequest(name="warroom", members=["architect"]))
    from alpha.groups.activity import get_activity_ledger

    get_activity_ledger().record_run_started("architect", run_id="run-1", room_name="warroom")

    manager = _FakeManager({"run-1": _FakeRecord("run-1", "error", error="boom")})
    result = await group_coordination.get_room_coordination("warroom", _FakeRequest(manager))
    architect = next(a for a in result["agents"] if a["bot_name"] == "architect")
    assert architect["activity"] == "crashed"
    assert architect["evidence"]["run"]["stop_reason"] is None


async def test_a_recoverable_stop_reason_is_not_a_crash() -> None:
    """`gateway_shutdown` and friends are named recoveries, not failures."""
    await groups.create_room(groups.RoomCreateRequest(name="warroom", members=["architect"]))
    from alpha.groups.activity import get_activity_ledger

    get_activity_ledger().record_run_started("architect", run_id="run-2", room_name="warroom")

    manager = _FakeManager({"run-2": _FakeRecord("run-2", "interrupted", stop_reason="gateway_shutdown")})
    result = await group_coordination.get_room_coordination("warroom", _FakeRequest(manager))
    architect = next(a for a in result["agents"] if a["bot_name"] == "architect")
    assert architect["activity"] != "crashed"


async def test_a_missing_run_row_is_reported_as_unresponsive_not_dropped() -> None:
    """`absent=True` is a distinct claim from a row that is still running."""
    await groups.create_room(groups.RoomCreateRequest(name="warroom", members=["architect"]))
    from alpha.groups.activity import get_activity_ledger

    get_activity_ledger().record_run_started("architect", run_id="run-3", room_name="warroom")

    result = await group_coordination.get_room_coordination("warroom", _FakeRequest(_FakeManager({})))
    architect = next(a for a in result["agents"] if a["bot_name"] == "architect")
    assert architect["evidence"]["run"]["absent"] is True
    assert architect["activity"] == "unresponsive"


async def test_one_unreadable_run_does_not_blank_the_room() -> None:
    """A store read that raises must degrade that one bot, not the response."""

    class _Exploding:
        async def get(self, run_id):
            raise RuntimeError("store offline")

    await groups.create_room(groups.RoomCreateRequest(name="warroom", members=["architect"]))
    from alpha.groups.activity import get_activity_ledger

    get_activity_ledger().record_run_started("architect", run_id="run-4", room_name="warroom")

    result = await group_coordination.get_room_coordination("warroom", _FakeRequest(_Exploding()))
    assert result["count"] == 1
    architect = next(a for a in result["agents"] if a["bot_name"] == "architect")
    assert architect["evidence"]["run"]["absent"] is True


# ---------------------------------------------------------------------------
# Both membership counts travel together
# ---------------------------------------------------------------------------


async def test_direct_and_effective_counts_are_both_reported() -> None:
    """A client rendering only `direct_count` would say "3 members" over six
    visible bots — so both numbers ship, always."""
    await groups.create_room(groups.RoomCreateRequest(name="warroom", members=["architect"]))
    result = await group_coordination.get_room_coordination("warroom", _FakeRequest(_FakeManager({})))
    assert result["direct_count"] == 1
    assert result["effective_count"] == 1
    assert result["count"] == 1


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------


async def test_a_claim_is_never_refused_for_contested_work() -> None:
    """The point of the layer: two agents claiming one file both succeed, and
    the overlap surfaces as a soft conflict rather than an error."""
    await groups.create_room(groups.RoomCreateRequest(name="warroom", members=["architect", "auditor"]))
    first = await group_coordination.create_claim(
        "warroom",
        group_coordination.ClaimRequest(holder="architect", kind="file", subject="./src/api.py"),
    )
    second = await group_coordination.create_claim(
        "warroom",
        group_coordination.ClaimRequest(holder="auditor", kind="file", subject="src/api.py"),
    )
    assert first["claim"]["live"] is True
    assert second["claim"]["live"] is True

    result = await group_coordination.get_room_coordination("warroom", _FakeRequest(_FakeManager({})))
    assert result["live_claim_count"] == 2
    # "./src/api.py" and "src/api.py" normalise to the same subject, so this is
    # one conflict and not two.
    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert set(conflict["holders"]) == {"architect", "auditor"}
    assert conflict["subject"] == "src/api.py"


async def test_an_empty_or_unknown_claim_is_a_422_naming_the_cause() -> None:
    with pytest.raises(HTTPException) as bad_kind:
        await group_coordination.create_claim("warroom", group_coordination.ClaimRequest(holder="architect", kind="nonsense", subject="a.py"))
    assert bad_kind.value.status_code == 422
    assert "kind must be one of" in str(bad_kind.value.detail)

    with pytest.raises(HTTPException) as bad_intent:
        await group_coordination.create_claim("warroom", group_coordination.ClaimRequest(holder="architect", subject="a.py", intent="yelling"))
    assert bad_intent.value.status_code == 422
    assert "intent must be one of" in str(bad_intent.value.detail)


async def test_reclaiming_the_same_subject_renews_rather_than_duplicates() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="warroom", members=["architect"]))
    first = await group_coordination.create_claim("warroom", group_coordination.ClaimRequest(holder="architect", subject="a.py"))
    second = await group_coordination.create_claim("warroom", group_coordination.ClaimRequest(holder="architect", subject="a.py"))
    assert first["claim"]["claim_id"] == second["claim"]["claim_id"]

    result = await group_coordination.get_room_coordination("warroom", _FakeRequest(_FakeManager({})))
    assert result["live_claim_count"] == 1


async def test_release_refuses_a_non_holder_and_says_so() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="warroom", members=["architect"]))
    taken = await group_coordination.create_claim("warroom", group_coordination.ClaimRequest(holder="architect", subject="a.py"))
    claim_id = taken["claim"]["claim_id"]

    with pytest.raises(HTTPException) as denied:
        await group_coordination.release_claim("warroom", claim_id, group_coordination.ReleaseRequest(holder="auditor"))
    assert denied.value.status_code == 409

    released = await group_coordination.release_claim("warroom", claim_id, group_coordination.ReleaseRequest(holder="architect"))
    assert released["released"] is True


async def test_an_operator_release_is_reported_as_an_operator_release() -> None:
    """Forced and voluntary hand-backs are different events in room history."""
    await groups.create_room(groups.RoomCreateRequest(name="warroom", members=["architect"]))
    taken = await group_coordination.create_claim("warroom", group_coordination.ClaimRequest(holder="architect", subject="a.py"))
    claim_id = taken["claim"]["claim_id"]

    released = await group_coordination.release_claim("warroom", claim_id, group_coordination.ReleaseRequest(force=True))
    assert released["by"] == "operator"
    assert released["owner_was"] == "architect"


async def test_releasing_a_claim_in_another_room_is_a_404() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="warroom", members=["architect"]))
    taken = await group_coordination.create_claim("warroom", group_coordination.ClaimRequest(holder="architect", subject="a.py"))
    with pytest.raises(HTTPException) as missing:
        await group_coordination.release_claim("other", taken["claim"]["claim_id"], group_coordination.ReleaseRequest(holder="architect"))
    assert missing.value.status_code == 404


async def test_a_traversing_claim_id_is_refused_rather_than_sanitised() -> None:
    with pytest.raises(HTTPException) as bad:
        await group_coordination.release_claim("warroom", "../escape", group_coordination.ReleaseRequest())
    assert bad.value.status_code == 400


# ---------------------------------------------------------------------------
# Reclaim: crashed only, and orphaned only
# ---------------------------------------------------------------------------


async def test_reclaiming_a_live_claim_is_refused_even_when_the_holder_crashed() -> None:
    """A crash is evidence; a hand-off is a decision. They are two routes."""
    await groups.create_room(groups.RoomCreateRequest(name="warroom", members=["architect"]))
    taken = await group_coordination.create_claim("warroom", group_coordination.ClaimRequest(holder="architect", subject="a.py"))
    claim_id = taken["claim"]["claim_id"]

    from alpha.groups.activity import get_activity_ledger

    ledger = get_activity_ledger()
    ledger.record_run_started("architect", run_id="run-crash", room_name="warroom")
    ledger.record_run_terminal("architect", run_id="run-crash", status="error", error="boom")

    with pytest.raises(HTTPException) as refused:
        await group_coordination.reclaim_claim("warroom", claim_id, group_coordination.ReclaimRequest(holder="auditor"))
    assert refused.value.status_code == 409
    assert "reconcile" in str(refused.value.detail)


async def test_an_unresponsive_holder_keeps_its_claim() -> None:
    """The load-bearing refusal. A slow agent may be mid-tool-call, and
    handing its work to a peer loses work rather than saving it."""
    await groups.create_room(groups.RoomCreateRequest(name="warroom", members=["architect"]))
    taken = await group_coordination.create_claim("warroom", group_coordination.ClaimRequest(holder="architect", subject="a.py"))
    claim_id = taken["claim"]["claim_id"]

    from alpha.groups.claims import get_claim_store

    # Orphan it, but leave the holder merely unresponsive rather than crashed.
    get_claim_store().orphan_for_bot("warroom", "architect", evidence={"reason": "test"})

    with pytest.raises(HTTPException) as refused:
        await group_coordination.reclaim_claim("warroom", claim_id, group_coordination.ReclaimRequest(holder="auditor"))
    assert refused.value.status_code == 409
    assert "not crashed" in str(refused.value.detail)


async def test_reconcile_then_reclaim_hands_the_work_over() -> None:
    await groups.create_room(groups.RoomCreateRequest(name="warroom", members=["architect", "auditor"]))
    taken = await group_coordination.create_claim("warroom", group_coordination.ClaimRequest(holder="architect", subject="a.py"))
    claim_id = taken["claim"]["claim_id"]

    from alpha.groups.activity import get_activity_ledger

    ledger = get_activity_ledger()
    ledger.record_run_started("architect", run_id="run-x", room_name="warroom")
    ledger.record_run_terminal("architect", run_id="run-x", status="error", error="boom")

    receipt = await group_coordination.reconcile_room("warroom", group_coordination.ReconcileRequest())
    assert receipt["crashed"] == ["architect"]
    assert len(receipt["orphaned"]) == 1
    # Announced into the transcript, and reported as announced — never as
    # "delivered" on a claim about delivery.
    assert receipt["announced"] >= 0

    moved = await group_coordination.reclaim_claim("warroom", claim_id, group_coordination.ReclaimRequest(holder="auditor"))
    assert moved["reclaimed"] is True
    assert moved["from"] == "architect"
    assert moved["to"] == "auditor"
    assert moved["claim"]["holder"] == "auditor"


async def test_reconcile_with_nothing_to_do_is_an_empty_receipt() -> None:
    """ "Reconciled" as a silent success is the claim this layer exists to avoid."""
    await groups.create_room(groups.RoomCreateRequest(name="warroom", members=["architect"]))
    receipt = await group_coordination.reconcile_room("warroom", group_coordination.ReconcileRequest())
    assert receipt["crashed"] == []
    assert receipt["orphaned"] == []
    assert receipt["announced"] == 0


async def test_reconciling_a_missing_room_is_a_404() -> None:
    with pytest.raises(HTTPException) as missing:
        await group_coordination.reconcile_room("no-such-room", group_coordination.ReconcileRequest())
    assert missing.value.status_code == 404
