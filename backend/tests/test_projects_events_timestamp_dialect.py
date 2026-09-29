"""The project event feed must speak the same timestamp dialect as its siblings.

Found by rendering the Projects view against the live Gateway: every activity
row said "time not reported" while the server was clearly sending something.

The cause is a wire-format inconsistency, not a UI problem.
``ProjectEvent.created_at`` is declared ``float`` with a ``time.time()``
default and ``to_dict()`` is ``dataclasses.asdict``, so
``GET /api/projects/{id}/events`` was the only project read that emitted a raw
epoch float while ``/projects``, ``/projects/{id}/threads`` and
``/projects/{id}/approvals`` all emit ISO 8601 strings. A client that parses
``created_at`` as a date — which this repo's own frontend does — cannot read a
float, so the value is dropped and every event renders as undated.

The fix coerces at the API boundary only. The on-disk event log keeps the float
so ``ProjectEvent.from_dict`` still loads existing logs, and nothing is
rewritten. These tests pin both halves of that contract.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alpha.projects.events import EVENT_TYPES, ProjectEvent, ProjectEventBus, event_log_path, get_event_bus

# Real event types, sampled from the registry. `emit` validates against
# EVENT_TYPES and rejects anything unknown, so a made-up name would fail the
# seeding rather than the assertion under test.
_TYPES = sorted(EVENT_TYPES)[:3]
from test_projects_router import _build_projects_app

pytestmark = pytest.mark.anyio


def _anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
def client(tmp_path) -> Any:
    app = _build_projects_app(tmp_path)
    return TestClient(app)


def _seed_events(project_id: str, count: int = 3) -> None:
    bus = get_event_bus(project_id)
    for i in range(count):
        bus.emit(_TYPES[i % len(_TYPES)], "coder", {"i": i})


def test_event_timestamps_are_iso_strings_on_the_wire(client, tmp_path):
    """The regression itself: the wire value must parse as a date."""
    created = client.post("/api/projects", json={"name": "evt", "instructions": "x"})
    project_id = created.json()["id"]

    _seed_events(project_id, 3)
    body = client.get(f"/api/projects/{project_id}/events").json()
    assert body["events"], "no events were returned"

    for event in body["events"]:
        raw = event["created_at"]
        assert isinstance(raw, str), f"created_at is {type(raw).__name__}, not a string: {raw!r}"
        # Must actually be a parseable ISO timestamp, not just any string.
        assert datetime.fromisoformat(raw).tzinfo is not None, f"not an aware ISO timestamp: {raw!r}"


def test_event_timestamps_agree_with_the_other_project_routes(client):
    """One dialect across the plane, so a client can format them all the same way."""
    project_id = client.post("/api/projects", json={"name": "dialect", "instructions": "x"}).json()["id"]
    _seed_events(project_id, 1)
    event = client.get(f"/api/projects/{project_id}/events").json()["events"][0]
    project = client.get(f"/api/projects/{project_id}").json()
    thread = client.get(f"/api/projects/{project_id}/threads").json()

    def kind(value: object) -> str:
        return type(value).__name__

    assert kind(event["created_at"]) == kind(project["created_at"]), "events and the project row disagree on the timestamp type"
    # `thread` is a list; an empty one cannot be compared, so only compare when
    # the project actually has a conversation.
    for row in thread:
        assert kind(event["created_at"]) == kind(row["created_at"]), "events and the thread row disagree on the timestamp type"


def test_the_on_disk_event_log_keeps_the_float(client, tmp_path):
    """The coercion is an API-boundary concern; persistence must be untouched.

    Rewriting the stored value would break every event log already on disk:
    ``ProjectEvent.from_dict`` reads the same ``created_at`` field back.
    """
    project_id = client.post("/api/projects", json={"name": "persist", "instructions": "x"}).json()["id"]
    _seed_events(project_id, 2)

    log_path = event_log_path(project_id)
    assert log_path.exists(), f"the event log was not written at {log_path}"

    lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines, "the event log is empty"
    for line in lines:
        stored = json.loads(line)["created_at"]
        assert isinstance(stored, float), f"the stored timestamp changed shape to {type(stored).__name__}: {stored!r}"


def test_stored_events_still_round_trip_through_from_dict(client):
    """A log written before the fix must still load."""
    original = ProjectEvent(
        seq=1,
        event_id="ev-legacy",
        project_id="p",
        type=_TYPES[0],
        actor="system",
        payload={"a": 1},
        created_at=1790528973.1918123,
    )
    restored = ProjectEvent.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored.created_at == original.created_at
    assert restored.type == _TYPES[0]
    assert restored.payload == {"a": 1}
    assert restored.seq == 1
    assert restored.event_id == "ev-legacy"


def test_an_already_iso_stored_value_is_unchanged(client):
    """An event log that already holds ISO text is passed through, not double-coerced."""
    from alpha.utils.time import coerce_iso

    iso = "2026-09-27T17:09:23.798611+00:00"
    assert coerce_iso(iso) == iso


def test_search_results_are_coerced_too(client):
    """The `q` search path is a separate branch in the route and must match."""
    project_id = client.post("/api/projects", json={"name": "search", "instructions": "x"}).json()["id"]
    _seed_events(project_id, 3)
    body = client.get(f"/api/projects/{project_id}/events", params={"q": _TYPES[1]}).json()
    assert body["events"], "the search branch returned nothing"
    for event in body["events"]:
        assert isinstance(event["created_at"], str)
        assert datetime.fromisoformat(event["created_at"]).tzinfo is not None


def test_the_bus_still_uses_a_float_default(client):
    """Guards the storage contract from being 'fixed' in the wrong layer."""
    event = ProjectEvent(seq=1, event_id="ev-1", project_id="p", type="t", actor="a")
    assert isinstance(event.created_at, float), "the dataclass default must stay a float epoch"


def test_the_event_bus_reads_a_legacy_log(client, tmp_path):
    """A pre-existing float log is readable after the change."""
    project_id = "legacy-project"
    bus = ProjectEventBus(project_id)
    bus.emit(_TYPES[0], "a")
    rows = bus.read()
    assert len(rows) == 1
    assert isinstance(rows[0].created_at, float), "the bus must still return the stored float"

    # And it survives a reload from disk, which is what an existing log does.
    reloaded = ProjectEventBus(project_id)
    again = reloaded.read()
    assert len(again) == 1
    assert again[0].created_at == pytest.approx(rows[0].created_at)
