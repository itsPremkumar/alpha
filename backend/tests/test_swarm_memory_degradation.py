"""Per-store degradation for the swarm memory endpoint.

`GET /api/swarms/{swarm_id}/memory` fans three unrelated store reads
(`get_facts`, `get_artifacts`, `get_task_results`) out to threads. They were
gathered without `return_exceptions=True`, so a single unavailable store
propagated its exception, returned a 500 for the whole endpoint, and discarded
the results the other two reads had already produced.

That is the wrong shape for a read endpoint assembled from independent parts.
An operator debugging a swarm wants the two stores that work; a 500 tells them
nothing except that one of three things is down. These tests pin the corrected
contract, and - importantly - pin that a failure is reported as a *distinct
fact* rather than as an empty result, because "this store has no facts" and
"this store is unreachable" are different answers.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import alpha.swarm.coordinator as coord_mod
import alpha.swarm.memory as memory_mod


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    coord_mod._GLOBAL_COORDINATOR = None
    yield
    coord_mod._GLOBAL_COORDINATOR = None


def _request(user_id: str, *, admin: bool = False):
    return SimpleNamespace(
        state=SimpleNamespace(
            user=SimpleNamespace(
                id=user_id,
                system_role="admin" if admin else "user",
            )
        )
    )


class _FakeManager:
    """Stands in for SwarmMemoryManager, failing the sections it is told to."""

    def __init__(self, failing: dict[str, BaseException], secret: str) -> None:
        self._failing = failing
        self._secret = secret

    def get_facts(self, swarm_id: str) -> dict[str, object]:
        if "facts" in self._failing:
            raise self._failing["facts"]
        return {"swarm_id": swarm_id, "observed": True}

    def get_artifacts(self, swarm_id: str) -> list[dict[str, object]]:
        if "artifacts" in self._failing:
            raise self._failing["artifacts"]
        return [{"kind": "report", "swarm_id": swarm_id}]

    def get_task_results(self, swarm_id: str) -> list[dict[str, object]]:
        if "task_results" in self._failing:
            raise self._failing["task_results"]
        return [{"task_id": "t-1", "swarm_id": swarm_id}]


async def _make_swarm() -> str:
    from app.gateway.routers import swarms

    payload = swarms.SwarmCreateRequest(goal="memory degradation probe", mode="parallel")
    created = await swarms.create_and_spawn_swarm(payload, _request("alice", admin=True))
    return created["swarm_id"]


def _install(monkeypatch, failing: dict[str, BaseException]) -> str:
    secret = "postgresql://svc:hunter2@internal-db.internal:5432/prod?sslmode=require"
    monkeypatch.setattr(
        memory_mod,
        "get_swarm_memory_manager",
        lambda: _FakeManager(failing, secret),
    )
    return secret


@pytest.mark.asyncio
async def test_healthy_stores_return_every_section_and_declare_no_degradation(monkeypatch):
    from app.gateway.routers import swarms

    swarm_id = await _make_swarm()
    _install(monkeypatch, {})

    payload = await swarms.get_swarm_memory(swarm_id, _request("alice", admin=True))

    assert payload["swarm_id"] == swarm_id
    assert payload["facts"] == {"swarm_id": swarm_id, "observed": True}
    assert payload["artifacts"] == [{"kind": "report", "swarm_id": swarm_id}]
    assert payload["task_results"] == [{"task_id": "t-1", "swarm_id": swarm_id}]
    # The healthy path must not invent a degradation report.
    assert "degraded" not in payload


@pytest.mark.asyncio
async def test_one_failing_store_does_not_discard_the_other_two(monkeypatch):
    """The regression: a single down store must not cost the operator the rest."""

    from app.gateway.routers import swarms

    swarm_id = await _make_swarm()
    _install(monkeypatch, {"artifacts": RuntimeError("store offline")})

    payload = await swarms.get_swarm_memory(swarm_id, _request("alice", admin=True))

    # The two healthy stores still delivered real data, not empty stand-ins.
    assert payload["facts"] == {"swarm_id": swarm_id, "observed": True}
    assert payload["task_results"] == [{"task_id": "t-1", "swarm_id": swarm_id}]

    # The failed section is reported as a failure, not as "no artifacts".
    assert payload["artifacts"] == {"error": "RuntimeError"}
    assert payload["degraded"] == ["artifacts"]


@pytest.mark.asyncio
async def test_failure_is_distinguishable_from_an_empty_result(monkeypatch):
    """An error marker must never be mistakable for legitimately empty data."""

    from app.gateway.routers import swarms

    swarm_id = await _make_swarm()
    _install(monkeypatch, {"facts": ConnectionError("refused")})

    payload = await swarms.get_swarm_memory(swarm_id, _request("alice", admin=True))

    assert isinstance(payload["facts"], dict)
    assert "error" in payload["facts"]
    # Critically: not an empty dict, which would read as "no facts recorded".
    assert payload["facts"] != {}


@pytest.mark.asyncio
async def test_two_failing_stores_are_both_reported(monkeypatch):
    from app.gateway.routers import swarms

    swarm_id = await _make_swarm()
    _install(
        monkeypatch,
        {"facts": TimeoutError("slow"), "task_results": OSError("disk gone")},
    )

    payload = await swarms.get_swarm_memory(swarm_id, _request("alice", admin=True))

    assert payload["facts"] == {"error": "TimeoutError"}
    assert payload["task_results"] == {"error": "OSError"}
    assert payload["artifacts"] == [{"kind": "report", "swarm_id": swarm_id}]
    assert sorted(payload["degraded"]) == ["facts", "task_results"]


@pytest.mark.asyncio
async def test_all_stores_failing_still_returns_a_body(monkeypatch):
    """Total store outage degrades the endpoint; it does not 500."""

    from app.gateway.routers import swarms

    swarm_id = await _make_swarm()
    _install(
        monkeypatch,
        {
            "facts": RuntimeError("a"),
            "artifacts": RuntimeError("b"),
            "task_results": RuntimeError("c"),
        },
    )

    payload = await swarms.get_swarm_memory(swarm_id, _request("alice", admin=True))

    assert sorted(payload["degraded"]) == ["artifacts", "facts", "task_results"]
    for section in ("facts", "artifacts", "task_results"):
        assert payload[section] == {"error": "RuntimeError"}


@pytest.mark.asyncio
async def test_store_internals_are_not_leaked_to_the_client(monkeypatch):
    """The exception type is useful in a browser; its message may hold a DSN."""

    from app.gateway.routers import swarms

    swarm_id = await _make_swarm()
    secret = _install(monkeypatch, {"facts": RuntimeError("postgresql://svc:hunter2@internal-db:5432/prod")})

    payload = await swarms.get_swarm_memory(swarm_id, _request("alice", admin=True))

    rendered = repr(payload)
    assert secret not in rendered
    assert "hunter2" not in rendered
    assert "internal-db" not in rendered
    # The type alone is still reported, so the client can branch on it.
    assert payload["facts"] == {"error": "RuntimeError"}


@pytest.mark.asyncio
async def test_a_base_exception_is_still_isolated(monkeypatch):
    """Cancellation from one probe must not cancel the sibling reads."""

    from app.gateway.routers import swarms

    swarm_id = await _make_swarm()
    _install(monkeypatch, {"artifacts": asyncio_cancelled()})

    payload = await swarms.get_swarm_memory(swarm_id, _request("alice", admin=True))

    assert payload["degraded"] == ["artifacts"]
    assert payload["facts"] == {"swarm_id": swarm_id, "observed": True}


def asyncio_cancelled() -> BaseException:
    import asyncio

    return asyncio.CancelledError()
