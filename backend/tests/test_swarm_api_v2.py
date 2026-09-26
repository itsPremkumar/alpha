"""API-level regression coverage for the owner-scoped swarm v2 surface."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import alpha.swarm.coordinator as coord_mod
from alpha.swarm.coordinator import get_swarm_coordinator
from alpha.swarm.models import SwarmMode, TaskNodeState


@pytest.fixture(autouse=True)
def _isolated_swarm_api(tmp_path, monkeypatch):
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


@pytest.mark.asyncio
async def test_swarm_api_idempotency_and_owner_isolation():
    from app.gateway.routers import swarms

    admin = _request("alice", admin=True)
    payload = swarms.SwarmCreateRequest(
        goal="Owner-scoped API plan",
        mode=SwarmMode.PARALLEL,
    )
    first = await swarms.create_and_spawn_swarm(
        payload,
        admin,
        idempotency_key="api-admission-1",
    )
    second = await swarms.create_and_spawn_swarm(
        payload,
        admin,
        idempotency_key="api-admission-1",
    )
    assert first["swarm_id"] == second["swarm_id"]
    assert first["revision"] == second["revision"]

    with pytest.raises(HTTPException) as exc_info:
        await swarms.get_swarm_details(first["swarm_id"], _request("bob"))
    assert exc_info.value.status_code == 404
    assert (await swarms.get_swarm_details(first["swarm_id"], admin))["swarm_id"] == first["swarm_id"]


@pytest.mark.asyncio
async def test_swarm_api_lease_completion_conflict_and_acceptance_projection():
    from app.gateway.routers import swarms

    coordinator = get_swarm_coordinator()
    plan = coordinator.create_swarm("Lease API work", mode=SwarmMode.PARALLEL, owner_id="alice")
    step = coordinator.step(plan.swarm_id)
    task_id = step["dispatched"][0]
    lease_id = plan.tasks[task_id].lease_id
    assert lease_id

    completed = await swarms.complete_swarm_task(
        plan.swarm_id,
        task_id,
        swarms.SwarmTaskCompleteRequest(
            result_summary="fresh result",
            lease_id=lease_id,
            expected_revision=plan.revision,
        ),
        _request("alice", admin=True),
    )
    assert completed["state"] == "completed"

    with pytest.raises(HTTPException) as exc_info:
        await swarms.complete_swarm_task(
            plan.swarm_id,
            task_id,
            swarms.SwarmTaskCompleteRequest(result_summary="stale", lease_id="wrong-lease"),
            _request("alice", admin=True),
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_swarm_api_messages_are_owner_derived_and_sse_ends():
    from app.gateway.routers import swarms

    coordinator = get_swarm_coordinator()
    plan = coordinator.create_swarm("Observable API work", mode=SwarmMode.PARALLEL, owner_id="alice")
    admin = _request("alice", admin=True)
    message = await swarms.publish_swarm_message(
        plan.swarm_id,
        swarms.SwarmMessageRequest(sender="spoofed", content="operator note", idempotency_key="api-msg-1"),
        admin,
    )
    assert message["sender"] == "alice"
    assert message["trust"] == "untrusted"
    assert (await swarms.get_swarm_messages(plan.swarm_id))[0]["content"] == "operator note"

    coordinator.cancel_swarm(plan.swarm_id, reason="test-complete")
    response = await swarms.stream_swarm_events(plan.swarm_id, admin, timeout_seconds=1)
    chunks = [chunk async for chunk in response.body_iterator]
    body = "".join(chunks)
    assert "event: end" in body
    assert "SWARM_CANCELLED" in body


@pytest.mark.asyncio
async def test_swarm_api_task_claim_is_explicit_and_bounded():
    from app.gateway.routers import swarms

    coordinator = get_swarm_coordinator()
    plan = coordinator.create_swarm("Claim API work", mode=SwarmMode.PARALLEL, owner_id="alice")
    task_id = next(iter(plan.tasks))
    lease = await swarms.claim_swarm_task(
        plan.swarm_id,
        task_id,
        swarms.SwarmClaimRequest(owner="external-worker", expected_revision=plan.revision),
        _request("alice", admin=True),
    )
    assert lease["task_id"] == task_id
    assert lease["owner"] == "external-worker"
    assert plan.tasks[task_id].state == TaskNodeState.RUNNING
