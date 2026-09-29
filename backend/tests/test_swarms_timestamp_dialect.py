"""The swarm plane must speak one timestamp dialect: ISO 8601, everywhere.

The bug
-------
``SwarmPlan.created_at`` / ``completed_at`` are strings
(``%Y-%m-%dT%H:%M:%SZ``), but the task node's ``lease_expires_at`` /
``started_at`` / ``completed_at`` / ``next_attempt_at`` are ``time.time()``
floats, and ``SwarmTaskNode.to_dict()`` emitted them raw. One live response
carried ``created_at='2026-09-29T01:21:02Z'`` in the same document as
``tasks.task-map-1.started_at=1790644866.30809`` - same field name, two types.

A client that parses the field as a date cannot read a float, so the row
renders as undated. That is exactly what happened to the project event feed,
whose fix (``routers/projects.py``) is the reference model for this one.

The same class of leak was in the sibling routes of this same file and is fixed
here too, because leaving them would just move the bug:
``SwarmTaskLease.expires_at``, ``SwarmBudget.started_at``,
``SwarmMessage.created_at``, and the blackboard's ``timestamp``.

The fix
-------
Coerce at the API boundary only. The plan JSON on disk and the message journal
keep their floats, so ``SwarmPlan.from_dict`` / ``SwarmTaskNode.from_dict`` /
``SwarmMessage.from_dict`` still load every existing record and nothing is
rewritten. These tests pin both halves of that contract.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import alpha.swarm.coordinator as coord_mod
from alpha.swarm.coordinator import get_swarm_coordinator
from alpha.swarm.models import SwarmMode, SwarmTaskNode


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    coord_mod._GLOBAL_COORDINATOR = None
    yield
    coord_mod._GLOBAL_COORDINATOR = None


def _admin(user_id: str = "alice"):
    return SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=user_id, system_role="admin")))


def _assert_iso(value: object, where: str) -> None:
    """A wire timestamp must be an ISO 8601 *string*, not merely a string."""
    assert isinstance(value, str), f"{where} is {type(value).__name__}, not a string: {value!r}"
    assert value, f"{where} is an empty string"
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, f"{where} is not an aware ISO timestamp: {value!r}"


def _assert_no_raw_epoch(payload: object, where: str = "payload") -> None:
    """No bare JSON number may sit where a timestamp field name is expected.

    This is the assertion that actually pins the regression: it fails on the
    float itself, not on a formatting detail.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and key.endswith(("_at", "_at_")):
                # No timestamp field may still be a number anywhere in the tree.
                raise AssertionError(f"{where}.{key} is still a raw epoch number: {value!r}")
            _assert_no_raw_epoch(value, f"{where}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            _assert_no_raw_epoch(item, f"{where}[{index}]")


# ---------------------------------------------------------------------------
# SwarmTaskNode inside the plan document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_task_timestamps_are_iso_on_the_wire():
    """The regression: `tasks.<id>.started_at` must parse as a date."""
    from app.gateway.routers import swarms

    admin = _admin()
    created = await swarms.create_and_spawn_swarm(swarms.SwarmCreateRequest(goal="ISO dialect plan", mode=SwarmMode.PARALLEL), admin)
    swarm_id = created["swarm_id"]

    # A real tick so a task actually has started_at / lease_expires_at set.
    coordinator = get_swarm_coordinator()
    coordinator.step(swarm_id)

    plan = await swarms.get_swarm_details(swarm_id, admin)
    assert plan["tasks"], "no tasks were created, so nothing was asserted"

    for task_id, task in plan["tasks"].items():
        for field in ("lease_expires_at", "started_at", "completed_at", "next_attempt_at"):
            value = task[field]
            where = f"tasks.{task_id}.{field}"
            if value is None:
                continue
            _assert_iso(value, where)

    _assert_no_raw_epoch(plan, "plan")


@pytest.mark.asyncio
async def test_plan_and_its_tasks_agree_on_the_timestamp_type():
    """One document, one dialect - the exact shape that produced the bug."""
    from app.gateway.routers import swarms

    admin = _admin()
    swarm_id = (await swarms.create_and_spawn_swarm(swarms.SwarmCreateRequest(goal="Agreement probe", mode=SwarmMode.PARALLEL), admin))["swarm_id"]
    get_swarm_coordinator().step(swarm_id)
    plan = await swarms.get_swarm_details(swarm_id, admin)

    assert isinstance(plan["created_at"], str), "plan.created_at stopped being a string"
    for task in plan["tasks"].values():
        for field in ("started_at", "lease_expires_at"):
            if task[field] is None:
                continue
            assert type(task[field]) is type(plan["created_at"]), f"tasks.*.{field} and created_at disagree on the timestamp type"


@pytest.mark.asyncio
async def test_list_and_create_agree_with_get_details():
    """The list and create routes are separate call sites of the same shape."""
    from app.gateway.routers import swarms

    admin = _admin()
    created = await swarms.create_and_spawn_swarm(swarms.SwarmCreateRequest(goal="List parity", mode=SwarmMode.PARALLEL), admin)
    swarm_id = created["swarm_id"]
    get_swarm_coordinator().step(swarm_id)

    listed = await swarms.list_swarms(20, request=admin)
    row = next(item for item in listed if item["swarm_id"] == swarm_id)
    detail = await swarms.get_swarm_details(swarm_id, admin)

    _assert_iso(created["created_at"], "create.created_at")
    _assert_iso(row["created_at"], "list.created_at")
    for source, label in ((created, "create"), (row, "list"), (detail, "detail")):
        for task_id, task in source["tasks"].items():
            for field in ("started_at", "completed_at", "lease_expires_at", "next_attempt_at"):
                if task[field] is None:
                    continue
                _assert_iso(task[field], f"{label}.tasks.{task_id}.{field}")


@pytest.mark.asyncio
async def test_step_result_budget_timestamp_is_iso():
    """`coordinator.step` inlines `SwarmBudget.check()`, which carries a float."""
    from app.gateway.routers import swarms

    admin = _admin()
    swarm_id = (await swarms.create_and_spawn_swarm(swarms.SwarmCreateRequest(goal="Budget dialect", mode=SwarmMode.PARALLEL), admin))["swarm_id"]
    get_swarm_coordinator().step(swarm_id)

    result = await swarms.step_swarm(swarm_id, admin)
    if "budget" in result:
        assert result["budget"]["started_at"] is None or isinstance(result["budget"]["started_at"], str), result["budget"]
        if result["budget"]["started_at"] is not None:
            _assert_iso(result["budget"]["started_at"], "step.budget.started_at")


@pytest.mark.asyncio
async def test_metrics_budget_timestamp_is_iso():
    from app.gateway.routers import swarms

    admin = _admin()
    swarm_id = (await swarms.create_and_spawn_swarm(swarms.SwarmCreateRequest(goal="Metrics dialect", mode=SwarmMode.PARALLEL), admin))["swarm_id"]
    metrics = await swarms.get_swarm_metrics(swarm_id, admin)
    started = metrics["budget"]["started_at"]
    if started is not None:
        _assert_iso(started, "metrics.budget.started_at")


# ---------------------------------------------------------------------------
# The standalone task / lease routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_task_route_coerces_task_and_lease():
    from app.gateway.routers import swarms

    admin = _admin()
    swarm_id = (await swarms.create_and_spawn_swarm(swarms.SwarmCreateRequest(goal="Single task dialect", mode=SwarmMode.PARALLEL), admin))["swarm_id"]
    task_id = next(iter(get_swarm_coordinator().get_swarm(swarm_id).tasks))

    body = await swarms.get_swarm_task(swarm_id, task_id, admin)
    for field in ("lease_expires_at", "started_at", "completed_at", "next_attempt_at"):
        if body["task"][field] is not None:
            _assert_iso(body["task"][field], f"task.{field}")
    if body["lease"]["expires_at"] is not None:
        _assert_iso(body["lease"]["expires_at"], "lease.expires_at")
    _assert_no_raw_epoch(body, "task-route")


@pytest.mark.asyncio
async def test_claimed_lease_expires_at_is_iso():
    from app.gateway.routers import swarms

    admin = _admin()
    coordinator = get_swarm_coordinator()
    swarm_id = (await swarms.create_and_spawn_swarm(swarms.SwarmCreateRequest(goal="Lease dialect", mode=SwarmMode.PARALLEL), admin))["swarm_id"]
    # Claim without stepping first: the scheduler dispatches on `step`, and a
    # dispatched task is no longer "ready" to claim.
    task_id = next(iter(coordinator.get_swarm(swarm_id).tasks))

    lease = await swarms.claim_swarm_task(swarm_id, task_id, swarms.SwarmClaimRequest(owner="external-worker", expected_revision=coordinator.get_swarm(swarm_id).revision), admin)
    _assert_iso(lease["expires_at"], "lease.expires_at")
    _assert_no_raw_epoch(lease, "lease")


@pytest.mark.asyncio
async def test_completed_task_route_coerces_too():
    from app.gateway.routers import swarms

    admin = _admin()
    coordinator = get_swarm_coordinator()
    swarm_id = (await swarms.create_and_spawn_swarm(swarms.SwarmCreateRequest(goal="Complete dialect", mode=SwarmMode.PARALLEL), admin))["swarm_id"]
    step = coordinator.step(swarm_id)
    task_id = step["dispatched"][0]
    lease_id = coordinator.get_swarm(swarm_id).tasks[task_id].lease_id

    done = await swarms.complete_swarm_task(swarm_id, task_id, swarms.SwarmTaskCompleteRequest(result_summary="done", lease_id=lease_id, expected_revision=coordinator.get_swarm(swarm_id).revision), admin)
    for field in ("started_at", "completed_at", "lease_expires_at", "next_attempt_at"):
        if done[field] is not None:
            _assert_iso(done[field], f"completed.{field}")
    _assert_no_raw_epoch(done, "completed")


# ---------------------------------------------------------------------------
# Messages and blackboard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_created_at_is_iso():
    from app.gateway.routers import swarms

    admin = _admin()
    swarm_id = (await swarms.create_and_spawn_swarm(swarms.SwarmCreateRequest(goal="Message dialect", mode=SwarmMode.PARALLEL), admin))["swarm_id"]

    published = await swarms.publish_swarm_message(swarm_id, swarms.SwarmMessageRequest(sender="operator", content="hello"), admin)
    _assert_iso(published["created_at"], "published.created_at")

    listed = await swarms.get_swarm_messages(swarm_id, request=admin)
    assert listed
    for message in listed:
        _assert_iso(message["created_at"], "listed.created_at")


@pytest.mark.asyncio
async def test_blackboard_timestamps_are_iso(monkeypatch):
    import alpha.swarm.memory as memory_mod
    from app.gateway.routers import swarms

    admin = _admin()
    swarm_id = (await swarms.create_and_spawn_swarm(swarms.SwarmCreateRequest(goal="Blackboard dialect", mode=SwarmMode.PARALLEL), admin))["swarm_id"]

    manager = memory_mod.get_swarm_memory_manager()
    manager.record_artifact(swarm_id, "artifact://one")
    manager.record_task_result(swarm_id, "task-1", summary="did a thing")

    payload = await swarms.get_swarm_memory(swarm_id, admin)
    assert payload["artifacts"], "no artifacts were recorded"
    assert payload["task_results"], "no task results were recorded"
    for row in payload["artifacts"]:
        _assert_iso(row["timestamp"], "artifacts[].timestamp")
    for row in payload["task_results"]:
        _assert_iso(row["timestamp"], "task_results[].timestamp")


@pytest.mark.asyncio
async def test_degraded_blackboard_section_is_not_mistaken_for_rows(monkeypatch):
    """The coercion must not resurrect the `{"error": ...}` degradation marker."""
    import alpha.swarm.memory as memory_mod
    from app.gateway.routers import swarms

    admin = _admin()
    swarm_id = (await swarms.create_and_spawn_swarm(swarms.SwarmCreateRequest(goal="Degradation intact", mode=SwarmMode.PARALLEL), admin))["swarm_id"]

    class _Failing:
        def get_facts(self, _swarm_id):
            return {}

        def get_artifacts(self, _swarm_id):
            raise RuntimeError("store offline")

        def get_task_results(self, _swarm_id):
            return []

    monkeypatch.setattr(memory_mod, "get_swarm_memory_manager", lambda: _Failing())
    payload = await swarms.get_swarm_memory(swarm_id, admin)
    assert payload["artifacts"] == {"error": "RuntimeError"}
    assert payload["degraded"] == ["artifacts"]


# ---------------------------------------------------------------------------
# Persistence is untouched: the fix is an API-boundary concern
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_stored_plan_json_keeps_the_floats(tmp_path: Path):
    """Rewriting the stored type would break every plan already on disk."""
    from app.gateway.routers import swarms

    admin = _admin()
    coordinator = get_swarm_coordinator()
    swarm_id = (await swarms.create_and_spawn_swarm(swarms.SwarmCreateRequest(goal="Persistence probe", mode=SwarmMode.PARALLEL), admin))["swarm_id"]
    coordinator.step(swarm_id)

    plans = list((tmp_path / "swarms").rglob("*.json")) or list(tmp_path.rglob("*.json"))
    assert plans, "the coordinator wrote no plan file to check"

    found_float = False
    for path in plans:
        for key, node in (json.loads(path.read_text(encoding="utf-8")).get("tasks") or {}).items():
            for field in ("started_at", "lease_expires_at", "completed_at", "next_attempt_at"):
                if isinstance(node.get(field), float):
                    found_float = True
    assert found_float, "the stored task timestamps were rewritten to strings; existing plan files would no longer round-trip"


def test_a_legacy_plan_with_float_timestamps_still_loads(tmp_path: Path):
    """A plan written before the fix must still construct from disk."""
    from alpha.swarm.models import SwarmPlan, SwarmTaskNode

    node = SwarmTaskNode(task_id="t", objective="legacy")
    node.started_at = 1790644866.30809
    node.lease_expires_at = 1790644866.30809 + 60.0
    plan = SwarmPlan(swarm_id="swm-legacy", goal="legacy", mode=SwarmMode.PARALLEL, tasks={"t": node})

    restored = SwarmPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert isinstance(restored.tasks["t"].started_at, float), "the stored timestamp changed shape"
    assert restored.tasks["t"].started_at == pytest.approx(1790644866.30809)
    assert restored.created_at == plan.created_at


def test_the_dataclass_still_defaults_to_a_float():
    """Guards the storage contract from being 'fixed' in the wrong layer."""
    node = SwarmTaskNode(task_id="t", objective="o")
    assert node.started_at is None
    assert node.lease_expires_at is None
    assert isinstance(node.to_dict()["started_at"], (type(None), float)), "to_dict must not have been changed to emit ISO in the model"


# ---------------------------------------------------------------------------
# The zero case: this is the part most likely to regress
# ---------------------------------------------------------------------------


def test_the_zero_epoch_is_not_a_swarm_sentinel():
    """Why there is no zero-translation here, stated as an executable claim.

    Every nullable swarm timestamp defaults to ``None``, and ``None`` means
    "has not happened". Nothing in this plane uses ``0.0`` to mean "never", so
    a stored ``0.0`` is coerced as the real epoch it claims to be rather than
    being silently turned into a null. If someone later introduces a
    ``0.0``-means-never field here, THIS test is what should change - and it
    should be changed deliberately, with the field named.
    """
    from app.gateway.routers.swarms import _wire_ts

    assert SwarmTaskNode(task_id="t", objective="o").started_at is None
    assert SwarmTaskNode(task_id="t", objective="o").lease_expires_at is None
    assert SwarmTaskNode(task_id="t", objective="o").next_attempt_at is None
    assert SwarmTaskNode(task_id="t", objective="o").completed_at is None

    # Absent stays absent, on every optional swarm timestamp.
    assert _wire_ts(None) is None
    # A stored 0.0 is the epoch it says it is, not a lie and not a null.
    assert _wire_ts(0.0) == "1970-01-01T00:00:00+00:00"
    # A real epoch renders as a real, parseable date.
    assert _wire_ts(1790644866.30809) == "2026-09-29T01:21:06.308090+00:00"


@pytest.mark.asyncio
async def test_an_unstarted_task_reports_null_not_a_number():
    """The null case on the wire: absent must not become 1970 or ""."""
    from app.gateway.routers import swarms

    admin = _admin()
    swarm_id = (await swarms.create_and_spawn_swarm(swarms.SwarmCreateRequest(goal="Never started", mode=SwarmMode.PARALLEL), admin))["swarm_id"]
    plan = await swarms.get_swarm_details(swarm_id, admin)

    unstarted = [task for task in plan["tasks"].values() if task["state"] == "pending"]
    assert unstarted, "every task was already running, so the null case was not exercised"
    for task in unstarted:
        for field in ("started_at", "completed_at", "lease_expires_at", "next_attempt_at"):
            assert task[field] is None, f"an unstarted task reports {field}={task[field]!r}; expected null, never an epoch"
            assert task[field] != "1970-01-01T00:00:00+00:00"
