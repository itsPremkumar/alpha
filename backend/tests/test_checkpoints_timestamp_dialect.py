"""The checkpoint plane must speak ISO 8601, and must say so in its schema.

This plane is different from the others in one specific way, and that is why it
is worth its own test.

``CheckpointResponse.created_at`` was declared ``float``. Everywhere else in the
Gateway a float timestamp is an *accident* - a dataclass ``to_dict()`` leaking
through an untyped ``dict`` response, invisible in the schema. Here it was
**declared**, which made this the only plane with a written exception to the
ISO 8601 convention... and no record anywhere that the exception existed. The
declaration read like an intentional contract when it was really
``CodeCheckpoint.created_at`` (a ``time.time()`` float written by
``create_shadow_checkpoint``) leaking through a model nobody enforced.

So this test pins two things:

* the routes emit ISO 8601, and
* the declared type matches what is emitted - a declared schema that disagrees
  with the wire is how the exception survived in the first place.

``WorkflowCheckpoint.created_at`` (runtime/checkpoint/engine.py) has the same
float shape but no Gateway route reaches it, so it is harness API rather than
this wire; the assertion below pins that fact too, so the day someone routes it
the change is deliberate.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.config import paths as paths_module
from alpha.config.paths import Paths
from alpha.runtime.user_context import reset_current_user, set_current_user
from app.gateway.routers import checkpoints as checkpoints_router

_USER_ID = "iso-checkpoints-user"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A checkpoint-router-only app with an isolated data root and one caller.

    Mirrors ``test_sec_audit_checkpoints.py``: ``Paths(base_dir=tmp_path)`` plus a
    request-scoped user, which is what ``_caller_area()`` resolves through.
    """
    monkeypatch.setattr(paths_module, "_paths", Paths(base_dir=str(tmp_path)), raising=False)
    token = set_current_user(SimpleNamespace(id=_USER_ID, system_role="user"))
    app = FastAPI()
    app.include_router(checkpoints_router.router)
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        reset_current_user(token)


@pytest.fixture(autouse=True)
def _clean_registry():
    """`_ACTIVE_CHECKPOINTS` is process-global; never leak rows between tests."""
    from alpha.tools.builtins import code_agentic_core

    before = set(code_agentic_core._ACTIVE_CHECKPOINTS)
    yield
    for key in set(code_agentic_core._ACTIVE_CHECKPOINTS) - before:
        code_agentic_core._ACTIVE_CHECKPOINTS.pop(key, None)


def _assert_iso(value: object, where: str) -> None:
    assert isinstance(value, str), f"{where} is {type(value).__name__}, not a string: {value!r}"
    assert value, f"{where} is an empty string"
    assert datetime.fromisoformat(value).tzinfo is not None, f"{where} is not an aware ISO timestamp: {value!r}"


def _assert_no_raw_epoch(payload: object, where: str = "payload") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and (key.endswith("_at") or key.endswith("_at_")):
                raise AssertionError(f"{where}.{key} is still a raw epoch number: {value!r}")
            _assert_no_raw_epoch(value, f"{where}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            _assert_no_raw_epoch(item, f"{where}[{index}]")


def _create(client: TestClient, label: str = "iso probe") -> dict:
    response = client.post("/api/checkpoints", json={"label": label, "root_path": ".", "target_files": ["note.txt"]})
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# The regression itself
# ---------------------------------------------------------------------------


def test_created_checkpoint_created_at_is_iso(client: TestClient) -> None:
    """`POST /api/checkpoints` returned `"created_at": cp.created_at` raw."""
    created = _create(client)
    _assert_iso(created["created_at"], "created.created_at")


def test_listed_checkpoint_created_at_is_iso(client: TestClient) -> None:
    """`GET /api/checkpoints` returned `get_all_checkpoints()` rows unfiltered."""
    _create(client)
    rows = client.get("/api/checkpoints").json()
    assert rows, "no checkpoints were returned"
    for row in rows:
        _assert_iso(row["created_at"], f"listed[{row['checkpoint_id']}].created_at")
    _assert_no_raw_epoch(rows, "checkpoints")


def test_create_and_list_agree_on_the_timestamp_type(client: TestClient) -> None:
    """Two routes, one `CodeCheckpoint`; they must not disagree."""
    created = _create(client)
    listed = next(r for r in client.get("/api/checkpoints").json() if r["checkpoint_id"] == created["checkpoint_id"])
    assert type(created["created_at"]) is type(listed["created_at"]), "create and list disagree on the timestamp type"
    assert created["created_at"] == listed["created_at"], "the same checkpoint reports two different creation times"


def test_the_created_value_is_a_plausible_current_date(client: TestClient) -> None:
    """Not just parseable - actually recent, so a wrong epoch cannot pass."""
    before = datetime.fromisoformat("2020-01-01T00:00:00+00:00")
    parsed = datetime.fromisoformat(_create(client)["created_at"])
    assert parsed > before, f"the checkpoint claims to predate 2020: {parsed.isoformat()}"


# ---------------------------------------------------------------------------
# The declared schema must match the wire
# ---------------------------------------------------------------------------


def test_the_declared_response_type_is_a_string() -> None:
    """The declared exception is gone; the schema now states the real contract.

    A declared `float` here is what made the leak look intentional. If a future
    edit puts the float back, THIS test fails - which is the point.
    """
    field = checkpoints_router.CheckpointResponse.model_fields["created_at"]
    assert field.annotation is str, f"CheckpointResponse.created_at is declared {field.annotation}, not str"


def test_the_declared_model_validates_the_emitted_value(client: TestClient) -> None:
    """The schema and the wire must accept each other's output."""
    created = _create(client)
    validated = checkpoints_router.CheckpointResponse(
        checkpoint_id=created["checkpoint_id"],
        label=created["label"],
        created_at=created["created_at"],
        files_count=created["files_count"],
        git_ref=created["git_ref"],
    )
    _assert_iso(validated.created_at, "CheckpointResponse.created_at")


def test_the_model_still_rejects_a_bare_float() -> None:
    """Pydantic would coerce a float to a str silently - so pin it explicitly."""
    with pytest.raises(Exception):
        # A float is NOT a valid ISO string, and Pydantic v2 in strict-ish
        # string mode rejects it rather than stringifying it.
        checkpoints_router.CheckpointResponse(
            checkpoint_id="chk_x",
            label="x",
            created_at=1790528973.1918123,  # type: ignore[arg-type]
            files_count=0,
        )


# ---------------------------------------------------------------------------
# Persistence / model contract
# ---------------------------------------------------------------------------


def test_the_harness_checkpoint_still_stores_a_float(tmp_path: Path) -> None:
    """`CodeCheckpoint.created_at` is the stored shape; the coercion is a route concern.

    The registry is process-global and in-memory, so nothing on disk changes -
    but the model contract still matters, because the `manage_code_checkpoint`
    tool reads the same registry.
    """
    from alpha.tools.builtins.code_agentic_core import create_shadow_checkpoint

    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    cp = create_shadow_checkpoint(label="stored shape", root_path=str(tmp_path), target_files=["file.txt"])
    assert isinstance(cp.created_at, float), "the harness model must keep its float; the fix belongs at the API boundary"
    assert cp.created_at > 1_600_000_000, "the checkpoint claims a pre-2020 creation time"


def test_workflow_checkpoint_is_not_on_this_wire() -> None:
    """`WorkflowCheckpoint.created_at` is a float too, but no route reaches it.

    Pinned so that the day someone mounts it, the choice is deliberate rather
    than another silent leak.
    """
    from alpha.runtime.checkpoint.engine import WorkflowCheckpoint

    assert WorkflowCheckpoint.__annotations__["created_at"] in (float, "float")
    source_paths = [
        Path(checkpoints_router.__file__),
    ]
    for path in source_paths:
        assert "runtime.checkpoint" not in path.read_text(encoding="utf-8"), "the checkpoints router must not import the workflow engine without also coercing it"


# ---------------------------------------------------------------------------
# The zero case
# ---------------------------------------------------------------------------


def test_the_zero_epoch_decision_for_this_plane() -> None:
    """Why there is no zero-translation here, stated as an executable claim.

    `CodeCheckpoint.created_at` is written by `create_shadow_checkpoint` as
    `time.time()` at the moment the snapshot is taken. There is no "never
    created" state for a checkpoint - it does not exist until it is created - so
    there is no `0.0` sentinel to translate, and inventing one would be less
    honest than rendering the real epoch the row claims.
    """
    from app.gateway.routers.checkpoints import _wire_ts

    assert _wire_ts(0.0) == "1970-01-01T00:00:00+00:00"
    assert _wire_ts(1790528973.1918123) == "2026-09-27T17:09:33.191812+00:00"
    assert datetime.fromisoformat(_wire_ts(1790528973.1918123)).tzinfo is not None
