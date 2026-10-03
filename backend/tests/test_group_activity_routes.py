"""The activity/claims routes: ordering, honesty, and refusal shapes.

Route order is pinned first. This file already declares `GET /tree` before
`GET /{name}` because Starlette matches in registration order and a literal
declared after a catch-all answers "Room 'x' not found" — indistinguishable from
an absent feature. The activity and claims cluster sits above the catch-all for
the same reason.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import app.gateway.routers.groups as groups

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Isolate every store these routes read or write.

    A sync fixture on purpose: this suite runs under `asyncio_mode = strict`,
    where an `async def` fixture is not awaited by pytest, so an async fixture
    would silently never create its room.
    """
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))

    import alpha.bots.registry as bot_reg
    import alpha.groups.activity as activity_mod
    import alpha.groups.claims as claims_mod
    import alpha.groups.service as grp_svc

    for module, names in (
        (activity_mod, ("_ledger", "_ledger_path")),
        (claims_mod, ("_claims", "_claims_path")),
        (bot_reg, ("_global_registry", "_global_registry_path")),
        (grp_svc, ("_global_groups", "_global_groups_path")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, None, raising=False)
    yield


async def _room() -> None:
    """One room with two members, for tests that read or mutate it."""
    if groups._service().get_room("crew") is None:
        await groups.create_room(groups.RoomCreateRequest(name="crew", members=["coder", "frontend"]))


def _paths() -> list[str]:
    return [r.path for r in groups.router.routes]


def _ledger():
    import alpha.groups.activity as activity_mod

    return activity_mod.get_activity_ledger()


def _claims():
    from alpha.groups.claims import get_claim_store

    return get_claim_store()


# ---------------------------------------------------------------------------
# Route order
# ---------------------------------------------------------------------------


class TestRouteOrder:
    def test_tree_still_precedes_the_catch_all(self):
        paths = _paths()
        assert paths.index("/api/groups/tree") < paths.index("/api/groups/{name}")

    @pytest.mark.parametrize(
        "path",
        [
            "/api/groups/{name}/activity",
            "/api/groups/{name}/activity/{bot_name}",
            "/api/groups/{name}/activity/heartbeat",
            "/api/groups/{name}/activity/reconcile",
            "/api/groups/{name}/claims",
            "/api/groups/{name}/claims/{claim_id}",
            "/api/groups/{name}/claims/{claim_id}/reclaim",
        ],
    )
    def test_the_activity_cluster_is_registered_above_the_catch_all(self, path):
        paths = _paths()
        assert path in paths
        assert paths.index(path) < paths.index("/api/groups/{name}")


# ---------------------------------------------------------------------------
# Room activity
# ---------------------------------------------------------------------------


class TestRoomActivity:
    async def test_every_member_appears_with_evidence(self):
        await _room()
        result = await groups.room_activity("crew", request=None)
        assert [a["bot_name"] for a in result["agents"]] == ["coder", "frontend"]
        for entry in result["agents"]:
            assert entry["evidence"]["reason"]
            assert entry["evidence"]["detail"]

    async def test_a_member_with_no_record_is_unknown_not_idle(self):
        await _room()
        result = await groups.room_activity("crew", request=None)
        assert result["agents"][0]["activity"] == "unknown"

    async def test_by_activity_sums_to_count(self):
        await _room()
        result = await groups.room_activity("crew", request=None)
        assert sum(result["by_activity"].values()) == result["count"] == 2

    async def test_both_membership_counts_travel_together(self):
        """A header claiming "3 members" over six visible bots is a fabricated
        count, so `direct_count` and `effective_count` always ship as a pair."""
        await _room()
        result = await groups.room_activity("crew", request=None)
        assert result["direct_count"] is not None
        assert result["effective_count"] == len(result["effective_members"])

    async def test_it_survives_a_request_with_no_run_manager(self):
        """Outside a Gateway request there is no run store to read; the route
        degrades rather than 500."""
        await _room()
        assert (await groups.room_activity("crew", request=None))["count"] == 2

    async def test_a_missing_room_is_404_not_an_empty_roster(self):
        with pytest.raises(HTTPException) as excinfo:
            await groups.room_activity("no-such-room", request=None)
        assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# One agent
# ---------------------------------------------------------------------------


class TestAgentActivity:
    async def test_one_agent_returns_its_own_row(self):
        await _room()
        result = await groups.agent_activity("crew", "coder", request=None)
        assert result["bot_name"] == "coder"
        assert result["evidence"]["reason"]

    async def test_a_non_member_is_404(self):
        await _room()
        with pytest.raises(HTTPException) as excinfo:
            await groups.agent_activity("crew", "stranger", request=None)
        assert excinfo.value.status_code == 404

    async def test_an_invalid_name_is_422_before_any_room_lookup(self):
        with pytest.raises(HTTPException) as excinfo:
            await groups.agent_activity("crew", "bad name!", request=None)
        assert excinfo.value.status_code == 422

    async def test_a_missing_room_is_404(self):
        with pytest.raises(HTTPException) as excinfo:
            await groups.agent_activity("no-such-room", "coder", request=None)
        assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat:
    async def test_a_member_can_report_its_state(self):
        await _room()
        recorded = await groups.heartbeat_activity("crew", groups.ActivityHeartbeatRequest(bot_name="coder", activity="working", detail="editing routes"))
        assert recorded["recorded"] is True
        result = await groups.room_activity("crew", request=None)
        coder = next(a for a in result["agents"] if a["bot_name"] == "coder")
        assert coder["activity"] == "working"
        assert coder["detail"] == "editing routes"

    async def test_an_unknown_state_is_422(self):
        """A typo must not become a state word nothing can interpret."""
        await _room()
        with pytest.raises(HTTPException) as excinfo:
            await groups.heartbeat_activity("crew", groups.ActivityHeartbeatRequest(bot_name="coder", activity="finishd"))
        assert excinfo.value.status_code == 422

    async def test_a_non_member_cannot_report(self):
        await _room()
        with pytest.raises(HTTPException) as excinfo:
            await groups.heartbeat_activity("crew", groups.ActivityHeartbeatRequest(bot_name="stranger"))
        assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------


class TestClaims:
    async def test_a_claim_is_recorded_and_announced(self):
        await _room()
        result = await groups.create_claim("crew", groups.ClaimRequest(bot_name="coder", subject="src/api.py"))
        assert result["claim"]["subject"] == "src/api.py"
        room = await groups.get_room("crew")
        assert any(m["intent"] == "status" for m in room["messages"])

    async def test_a_second_claim_on_the_same_file_is_not_refused(self):
        """Advisory by design: the overlap is reported, not blocked."""
        await _room()
        await groups.create_claim("crew", groups.ClaimRequest(bot_name="coder", subject="src/api.py"))
        result = await groups.create_claim("crew", groups.ClaimRequest(bot_name="frontend", subject="./src/api.py"))
        assert result["conflicts"], "normalised paths must still collide"

    async def test_an_unknown_kind_is_422(self):
        await _room()
        with pytest.raises(HTTPException) as excinfo:
            await groups.create_claim("crew", groups.ClaimRequest(bot_name="coder", kind="socket", subject="x"))
        assert excinfo.value.status_code == 422

    async def test_an_unknown_intent_is_422(self):
        await _room()
        with pytest.raises(HTTPException) as excinfo:
            await groups.create_claim("crew", groups.ClaimRequest(bot_name="coder", subject="x", intent="vandalising"))
        assert excinfo.value.status_code == 422

    async def test_a_non_member_cannot_claim(self):
        await _room()
        with pytest.raises(HTTPException) as excinfo:
            await groups.create_claim("crew", groups.ClaimRequest(bot_name="stranger", subject="x.py"))
        assert excinfo.value.status_code == 404

    async def test_the_holder_may_release(self):
        await _room()
        created = await groups.create_claim("crew", groups.ClaimRequest(bot_name="coder", subject="x.py"))
        released = await groups.release_claim("crew", created["claim"]["claim_id"], "coder")
        assert released["released"] is True

    async def test_another_bot_cannot_release(self):
        await _room()
        created = await groups.create_claim("crew", groups.ClaimRequest(bot_name="coder", subject="x.py"))
        with pytest.raises(HTTPException) as excinfo:
            await groups.release_claim("crew", created["claim"]["claim_id"], "frontend")
        assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# Reclaim
# ---------------------------------------------------------------------------


class TestReclaim:
    async def test_a_missing_claim_is_404(self):
        await _room()
        with pytest.raises(HTTPException) as excinfo:
            await groups.reclaim_claim("crew", "cl-nope", groups.ReclaimRequest(bot_name="frontend"))
        assert excinfo.value.status_code == 404

    async def test_a_live_claim_is_409_not_404(self):
        """The two are different mistakes: one does not exist, the other is
        held by somebody who has not died."""
        await _room()
        created = await groups.create_claim("crew", groups.ClaimRequest(bot_name="coder", subject="x.py"))
        with pytest.raises(HTTPException) as excinfo:
            await groups.reclaim_claim("crew", created["claim"]["claim_id"], groups.ReclaimRequest(bot_name="frontend"))
        assert excinfo.value.status_code == 409

    async def test_an_orphaned_claim_can_be_taken_over(self):
        await _room()
        created = await groups.create_claim("crew", groups.ClaimRequest(bot_name="coder", subject="x.py"))
        _claims().orphan_for_bot("crew", "coder", evidence={"reason": "orphan_recovered"})
        result = await groups.reclaim_claim("crew", created["claim"]["claim_id"], groups.ReclaimRequest(bot_name="frontend"))
        assert result["claim"]["holder"] == "frontend"

    async def test_a_non_member_cannot_reclaim(self):
        await _room()
        created = await groups.create_claim("crew", groups.ClaimRequest(bot_name="coder", subject="x.py"))
        _claims().orphan_for_bot("crew", "coder", evidence={"reason": "orphan_recovered"})
        with pytest.raises(HTTPException) as excinfo:
            await groups.reclaim_claim("crew", created["claim"]["claim_id"], groups.ReclaimRequest(bot_name="stranger"))
        assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# Crash reconcile
# ---------------------------------------------------------------------------


class TestReconcile:
    async def _crash(self, bot: str = "coder") -> None:
        _ledger().record_run_terminal(bot, run_id="r1", status="error", stop_reason="orphan_recovered", room_name="crew")

    async def test_a_room_with_nothing_crashed_returns_an_empty_receipt(self):
        """An unverifiable "reconciled" is what this layer exists to avoid, so
        it reports what it found rather than a quiet success."""
        await _room()
        receipt = await groups.reconcile_activity("crew")
        assert receipt["crashed"] == []
        assert receipt["orphaned"] == []
        assert receipt["announced"] == 0

    async def test_a_crash_orphans_the_dead_agents_claims_and_announces_it(self):
        await _room()
        await groups.create_claim("crew", groups.ClaimRequest(bot_name="coder", subject="src/api.py"))
        await self._crash()
        receipt = await groups.reconcile_activity("crew")
        assert receipt["crashed"] == ["coder"]
        assert len(receipt["orphaned"]) == 1
        assert receipt["announced"] == 1
        room = await groups.get_room("crew")
        intents = {m["intent"] for m in room["messages"]}
        assert "blocker" in intents and "handoff" in intents

    async def test_it_is_idempotent(self):
        await _room()
        await groups.create_claim("crew", groups.ClaimRequest(bot_name="coder", subject="src/api.py"))
        await self._crash()
        await groups.reconcile_activity("crew")
        assert (await groups.reconcile_activity("crew"))["orphaned"] == []

    async def test_an_unresponsive_agent_is_never_orphaned(self):
        """Silence is not proof of death; an agent inside a long tool call
        still holds its work."""
        import alpha.groups.activity as activity_mod

        await _room()
        await groups.create_claim("crew", groups.ClaimRequest(bot_name="coder", subject="src/api.py"))
        _ledger().record_run_started("coder", run_id="r1")
        ledger = _ledger()
        row = ledger.raw_row("coder")
        row["last_heartbeat_at"] = 1.0
        ledger._rows["coder"] = row
        activity_mod._ledger = ledger
        assert (await groups.reconcile_activity("crew"))["crashed"] == []

    async def test_a_missing_room_is_404(self):
        with pytest.raises(HTTPException) as excinfo:
            await groups.reconcile_activity("no-such-room")
        assert excinfo.value.status_code == 404
