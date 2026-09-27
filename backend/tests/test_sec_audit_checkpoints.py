"""Security audit: the HTTP checkpoint surface must not let a client point a
host-filesystem snapshot / diff / rollback at a directory outside its own area.

``routers/checkpoints.py`` is the only Gateway route that hands a raw,
client-supplied ``root_path`` to harness functions that read, diff and **write**
host files (``code_agentic_core.create_shadow_checkpoint`` /
``get_checkpoint_diff`` / ``rollback_to_checkpoint`` all do
``Path(root_path).resolve()`` with no confinement). ``rollback_to_checkpoint``
additionally does ``(root / rel_path).write_text(...)`` and
``target.parent.mkdir(parents=True, exist_ok=True)``, so an unconfined
``root_path`` is an arbitrary-path write primitive, and ``create_shadow_checkpoint``
additionally runs ``git update-ref`` inside whatever directory it was pointed at.

Every other Gateway filesystem route confines the caller's path to the
authenticated user's own bucket (``path_utils.resolve_outputs_confined_path``,
``Paths.user_dir``), per ``backend/docs/AUTH_DESIGN.md`` ("Isolation by Default:
Repositories, filesystems, memory ... resolve against the authenticated user by
default"). These tests pin that the checkpoint routes hold the same line.

The tests assert the HTTP-visible security property (a rejected request), not the
presence of a particular helper, so they stay honest if the confinement is later
implemented differently.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.config import paths as paths_module
from alpha.config.paths import Paths
from alpha.runtime.user_context import reset_current_user, set_current_user
from app.gateway.routers import checkpoints as checkpoints_router

_USER_ID = "sec-audit-checkpoints-user"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A checkpoint-router-only app with an isolated data root and one caller."""
    monkeypatch.setattr(paths_module, "_paths", Paths(base_dir=str(tmp_path)), raising=False)
    user = SimpleNamespace(id=_USER_ID, system_role="user")
    token = set_current_user(user)
    app = FastAPI()
    app.include_router(checkpoints_router.router)
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        reset_current_user(token)


def _outside_dir(tmp_path: Path) -> Path:
    """A real directory outside the caller's own data root."""
    outside = tmp_path.parent / f"outside-{uuid4().hex}"
    outside.mkdir(parents=True, exist_ok=True)
    return outside


def test_create_checkpoint_refuses_a_root_path_outside_the_callers_area(client: TestClient, tmp_path: Path):
    """An absolute host path outside the caller's bucket must be refused (400)."""
    outside = _outside_dir(tmp_path)
    (outside / "secret.txt").write_text("host secret", encoding="utf-8")

    response = client.post("/api/checkpoints", json={"label": "x", "root_path": str(outside), "target_files": ["secret.txt"]})

    assert response.status_code == 400, response.text
    assert response.json()["detail"] != ""


def test_create_checkpoint_refuses_a_parent_directory_escape(client: TestClient, tmp_path: Path):
    """A relative ``..`` escape out of the caller's area must be refused (400)."""
    client.post("/api/checkpoints", json={"label": "seed"})

    response = client.post("/api/checkpoints", json={"label": "escape", "root_path": ".."})

    assert response.status_code == 400, response.text


def test_create_checkpoint_refuses_a_target_file_that_escapes_the_root(client: TestClient, tmp_path: Path):
    """A ``target_files`` entry may not read outside the confined root."""
    outside = _outside_dir(tmp_path)
    (outside / "secret.txt").write_text("host secret", encoding="utf-8")

    response = client.post(
        "/api/checkpoints",
        json={
            "label": "traverse",
            "root_path": ".",
            "target_files": [(Path("..") / outside.name / "secret.txt").as_posix()],
        },
    )

    assert response.status_code == 400, response.text


def test_rollback_refuses_a_root_path_outside_the_callers_area(client: TestClient, tmp_path: Path):
    """Rollback must not write host files into a directory the caller does not own.

    The snapshot is taken from the caller's own area (an absolute path inside it
    is a legitimate request both before and after the fix) so the pre-fix
    behaviour is a real write into an unowned directory, not an empty rollback.
    """
    base = Paths(base_dir=str(tmp_path)).user_dir(_USER_ID)
    base.mkdir(parents=True, exist_ok=True)
    (base / "owned.txt").write_text("owned", encoding="utf-8")

    created = client.post("/api/checkpoints", json={"label": "owned", "root_path": str(base), "target_files": ["owned.txt"]})
    assert created.status_code == 200, created.text
    assert created.json()["files_count"] == 1, created.text
    checkpoint_id = created.json()["checkpoint_id"]

    outside = _outside_dir(tmp_path)
    response = client.post(f"/api/checkpoints/{checkpoint_id}/rollback", params={"root_path": str(outside)})

    assert response.status_code == 400, response.text
    assert not any(outside.iterdir()), f"rollback wrote into an unowned directory: {list(outside.iterdir())}"


def test_diff_refuses_a_root_path_outside_the_callers_area(client: TestClient, tmp_path: Path):
    """Diff must not read host files outside the caller's own area."""
    base = Paths(base_dir=str(tmp_path)).user_dir(_USER_ID)
    base.mkdir(parents=True, exist_ok=True)
    (base / "owned.txt").write_text("owned", encoding="utf-8")
    created = client.post("/api/checkpoints", json={"label": "owned", "root_path": str(base), "target_files": ["owned.txt"]})
    assert created.status_code == 200, created.text

    response = client.get(f"/api/checkpoints/{created.json()['checkpoint_id']}/diff", params={"root_path": str(_outside_dir(tmp_path))})

    assert response.status_code == 400, response.text


def test_list_checkpoints_does_not_disclose_another_areas_checkpoints(client: TestClient, tmp_path: Path):
    """The process-global registry must not leak foreign roots to this caller."""
    from alpha.tools.builtins import code_agentic_core

    outside = _outside_dir(tmp_path)
    foreign = code_agentic_core.create_shadow_checkpoint(label="someone-elses", root_path=str(outside))
    try:
        rows = client.get("/api/checkpoints")
        assert rows.status_code == 200, rows.text
        ids = {row["checkpoint_id"] for row in rows.json()}
        assert foreign.checkpoint_id not in ids
    finally:
        code_agentic_core._ACTIVE_CHECKPOINTS.pop(foreign.checkpoint_id, None)


def test_own_area_checkpoint_round_trip_still_works(client: TestClient, tmp_path: Path):
    """The feature is preserved: snapshot -> diff -> rollback inside the caller's area."""
    base = Paths(base_dir=str(tmp_path)).user_dir(_USER_ID)
    base.mkdir(parents=True, exist_ok=True)
    (base / "note.txt").write_text("v1", encoding="utf-8")

    created = client.post("/api/checkpoints", json={"label": "mine", "root_path": str(base), "target_files": ["note.txt"]})
    assert created.status_code == 200, created.text
    checkpoint_id = created.json()["checkpoint_id"]
    assert created.json()["files_count"] == 1

    (base / "note.txt").write_text("v2", encoding="utf-8")
    diff = client.get(f"/api/checkpoints/{checkpoint_id}/diff", params={"root_path": str(base)})
    assert diff.status_code == 200, diff.text
    assert "note.txt" in diff.text

    rolled_back = client.post(f"/api/checkpoints/{checkpoint_id}/rollback", params={"root_path": str(base)})
    assert rolled_back.status_code == 200, rolled_back.text
    assert (base / "note.txt").read_text(encoding="utf-8") == "v1"


def test_the_default_root_path_is_the_callers_own_area(client: TestClient, tmp_path: Path):
    """``root_path="."`` resolves to the caller's own bucket, not the process CWD."""
    base = Paths(base_dir=str(tmp_path)).user_dir(_USER_ID)
    base.mkdir(parents=True, exist_ok=True)
    (base / "note.txt").write_text("mine", encoding="utf-8")

    created = client.post("/api/checkpoints", json={"label": "default", "root_path": ".", "target_files": ["note.txt"]})

    assert created.status_code == 200, created.text
    assert created.json()["files_count"] == 1, created.text
    listed = client.get("/api/checkpoints").json()
    assert any(row["checkpoint_id"] == created.json()["checkpoint_id"] for row in listed)
