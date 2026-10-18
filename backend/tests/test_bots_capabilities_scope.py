"""Capability scoping (2.3): one checked write path, refusals that stay refusals.

Pinned here:

1. ``GET /api/bots/{name}/capabilities`` reports the grant the way the mode
   contract resolves it — requested (stored) vs effective (ceiling-admitted),
   with the denied difference disclosed — plus the ceiling, the server's
   known-capability catalog, and the recorded ``last_rescope``.
2. ``PUT /api/bots/{name}/capabilities`` is the ONE write path: a request over
   the ceiling (or unknown, or empty) is REFUSED with every violation and the
   profile is left untouched — never silently trimmed.
3. ``PATCH /api/bots/{name}`` refuses ``capabilities`` and ``toolsets``, so
   the scoping flow has no side door, while the rest of the PATCH surface
   still works.
4. The save records what changed: the previous grant, the version bump, and
   the honest epoch answer — ``capabilities`` is deliberately not
   epoch-bearing (pinned by ``test_bots_selfservice``), so a grant-only save
   reports ``epoch_moved: false`` with the reason instead of implying a new
   epoch.
"""

from __future__ import annotations

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

import alpha.bots.registry as bot_reg
from alpha.bots.authority_ceiling import RANK_REASON, AuthorityCeiling
from alpha.bots.registry import BotRegistry
from app.gateway.routers import bots as bots_router


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """No test in this file may touch the real ALPHA_HOME roster."""

    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    bot_reg._global_registry = None
    bot_reg._global_registry_path = None
    yield
    bot_reg._global_registry = None
    bot_reg._global_registry_path = None


async def _noop_admin(*_args, **_kwargs):
    return None


@pytest.fixture()
def registry(tmp_path) -> BotRegistry:
    return BotRegistry(tmp_path / "roster.json")


@pytest.fixture()
def client(monkeypatch, registry) -> TestClient:
    monkeypatch.setattr(bots_router, "require_admin_user", _noop_admin)
    monkeypatch.setattr(bots_router, "_registry", lambda: registry)
    app = make_authed_test_app()
    app.include_router(bots_router.router)
    return TestClient(app)


def _seed(registry: BotRegistry, name: str = "quill", **kwargs):
    return registry.get_or_create(name, display_name="Quill", role="Write scheduled social posts", **kwargs)


def test_the_scoping_view_reports_the_grant_ceiling_and_catalog(client, registry):
    _seed(registry, capabilities=["reason", "observe"])

    body = client.get("/api/bots/quill/capabilities").json()

    assert body["bot"] == "quill"
    assert body["requested_capabilities"] == ["observe", "reason"]
    assert body["effective_capabilities"] == ["observe", "reason"]
    assert body["denied_capabilities"] == []
    ceiling = body["ceiling"]
    assert isinstance(ceiling["max_capability_rank"], int)
    assert isinstance(ceiling["allowed_capabilities"], list)
    known = {entry["name"]: entry["rank"] for entry in body["known_capabilities"]}
    assert known["observe"] == 10
    assert known["reason"] == 20
    assert isinstance(body["declared"]["toolsets"], list)
    assert isinstance(body["declared"]["skills"], list)
    assert isinstance(body["declared"]["mcp_servers"], list)
    assert isinstance(body["epoch"], str) and body["epoch"]
    assert isinstance(body["version"], int)
    assert "dispatch admission" in body["enforcement"]["capabilities"]
    assert "declared only" in body["enforcement"]["toolsets"]


def test_a_tightened_ceiling_discloses_denied_capabilities(client, registry):
    """The difference between requested and effective is disclosed, not folded away."""
    _seed(registry, capabilities=["reason", "process_exec"])
    registry.set_ceiling(AuthorityCeiling(max_capability_rank=RANK_REASON))

    body = client.get("/api/bots/quill/capabilities").json()

    assert body["requested_capabilities"] == ["process_exec", "reason"]
    assert body["effective_capabilities"] == ["reason"]
    assert body["denied_capabilities"] == ["process_exec"]


def test_over_ceiling_is_refused_not_trimmed(client, registry):
    _seed(registry, capabilities=["observe"])
    registry.set_ceiling(AuthorityCeiling(max_capability_rank=RANK_REASON))

    response = client.put("/api/bots/quill/capabilities", json={"capabilities": ["observe", "process_exec"]})

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "authority_ceiling_violation"
    assert any("process_exec" in v for v in detail["violations"])
    # The refusal left the profile exactly as it was: NOT trimmed to [observe].
    assert registry.get_bot("quill").capabilities == ["observe"]


def test_an_unknown_or_empty_grant_is_refused(client, registry):
    _seed(registry, capabilities=["observe"])

    unknown = client.put("/api/bots/quill/capabilities", json={"capabilities": ["observe", "teleport"]})
    assert unknown.status_code == 422, unknown.text
    assert any("teleport" in v for v in unknown.json()["detail"]["violations"])

    empty = client.put("/api/bots/quill/capabilities", json={"capabilities": []})
    assert empty.status_code == 422, empty.text

    assert registry.get_bot("quill").capabilities == ["observe"], "a refused request never writes"


def test_a_within_ceiling_rescope_records_the_change(client, registry):
    _seed(registry, capabilities=["reason"])
    before = registry.get_bot("quill")
    version_before = before.version
    epoch_before = before.capability_fingerprint()

    body = client.put("/api/bots/quill/capabilities", json={"capabilities": ["observe", "reason"]}).json()

    assert body["effective_capabilities"] == ["observe", "reason"]
    changed = body["changed"]
    assert changed["previous_capabilities"] == ["reason"]
    assert changed["version_before"] == version_before
    assert body["version"] == version_before + 1
    # The honest epoch answer: a grant-only save does not move the fingerprint,
    # and the response says why instead of implying a new epoch.
    assert changed["epoch_before"] == epoch_before
    assert changed["epoch_moved"] is False
    assert "not epoch-bearing" in changed["note"]
    rescope = body["last_rescope"]
    assert rescope["by"] == "server"
    assert rescope["previous"] == ["reason"]
    assert registry.get_bot("quill").capabilities == ["observe", "reason"]


def test_the_reason_is_recorded_verbatim(client, registry):
    _seed(registry, capabilities=["observe"])

    client.put(
        "/api/bots/quill/capabilities",
        json={"capabilities": ["observe"], "reason": "Tightening during a security review"},
    )

    assert registry.get_bot("quill").metadata["last_rescope"]["reason"] == "Tightening during a security review"


def test_patch_refuses_capabilities_and_toolsets_but_the_rest_still_works(client, registry):
    _seed(registry, capabilities=["observe"])

    for body in ({"capabilities": ["reason"]}, {"toolsets": ["all"]}):
        response = client.patch("/api/bots/quill", json=body)
        assert response.status_code == 422, (body, response.text)
        assert "PUT /api/bots/quill/capabilities" in response.json()["detail"]

    assert registry.get_bot("quill").capabilities == ["observe"], "a refused PATCH never writes"

    ok = client.patch("/api/bots/quill", json={"display_name": "Quill the Second", "skills": ["copywriting"]})
    assert ok.status_code == 200, ok.text
    assert registry.get_bot("quill").display_name == "Quill the Second"
    assert registry.get_bot("quill").skills == ["copywriting"]


def test_the_write_path_is_admin_gated(registry, monkeypatch):
    _seed(registry, capabilities=["observe"])
    monkeypatch.setattr(bots_router, "_registry", lambda: registry)
    app = make_authed_test_app()
    app.include_router(bots_router.router)
    client = TestClient(app)
    assert client.put("/api/bots/quill/capabilities", json={"capabilities": ["reason"]}).status_code == 403


def test_unknown_bot_is_404_on_both_read_and_write(client):
    assert client.get("/api/bots/ghost/capabilities").status_code == 404
    assert client.put("/api/bots/ghost/capabilities", json={"capabilities": ["observe"]}).status_code == 404
