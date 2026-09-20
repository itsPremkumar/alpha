"""HTTP contracts for the skill-proposal queue (H4)."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.deps import get_config
from app.gateway.routers import skills
from alpha.skills.proposals import SkillProposalStore, proposals_root

CLEAN_MD = "---\nname: demo-skill\n---\n# Demo Skill\nDoes useful things safely.\n"
EVIL_MD = "---\nname: evil-skill\n---\n# Evil\n169.254.169.254 metadata endpoint.\n"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path / "home"))
    app = FastAPI()

    @app.middleware("http")
    async def identity(request, call_next):
        if request.headers.get("x-role") != "anonymous":
            request.state.user = User(
                id="00000000-0000-0000-0000-000000000000",
                email="test@example.com",
                password_hash="x",
                system_role=request.headers.get("x-role", "admin"),
            )
        request.state.auth_source = request.headers.get("x-auth-source")
        return await call_next(request)

    app.dependency_overrides[get_config] = lambda: SimpleNamespace()
    app.include_router(skills.router)
    return app


def _store() -> SkillProposalStore:
    return SkillProposalStore(proposals_root())


def test_propose_lists_and_rejects_flow(app):
    with TestClient(app) as client:
        created = client.post(
            "/api/skills/proposals",
            json={"name": "demo-skill", "description": "Does things", "skill_md": CLEAN_MD},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["status"] == "pending"
        assert body["created_by"] == "test-user-autouse"
        assert body["findings"] == []
        assert body["findings_summary"] == {}
        proposal_id = body["id"]

        mine = client.get("/api/skills/proposals")
        assert mine.status_code == 200
        assert [p["id"] for p in mine.json()["proposals"]] == [proposal_id]

        rejected = client.post(f"/api/skills/proposals/{proposal_id}/reject", json={"reason": "not needed"})
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "rejected"
        assert rejected.json()["reject_reason"] == "not needed"
        assert rejected.json()["reviewed_by"]

        again = client.post(f"/api/skills/proposals/{proposal_id}/approve")
        assert again.status_code == 409


def test_propose_blocked_content_never_stores(app):
    with TestClient(app) as client:
        response = client.post(
            "/api/skills/proposals",
            json={"name": "evil-skill", "description": "d", "skill_md": EVIL_MD},
        )
        assert response.status_code == 400, response.text
        assert _store().list_all() == []


def test_propose_validates_inputs(app):
    with TestClient(app) as client:
        bad_name = client.post(
            "/api/skills/proposals",
            json={"name": "BAD NAME", "description": "d", "skill_md": CLEAN_MD},
        )
        assert bad_name.status_code == 400
        missing = client.post("/api/skills/proposals", json={"name": "demo-skill"})
        assert missing.status_code == 422
        bad_scope = client.get("/api/skills/proposals", params={"scope": "everyone"})
        assert bad_scope.status_code == 400
        bad_status = client.get("/api/skills/proposals", params={"status": "ghost"})
        assert bad_status.status_code == 400
        unknown = client.post(f"/api/skills/proposals/{'ab' * 16}/approve")
        assert unknown.status_code == 404


def test_review_permissions(app, monkeypatch):
    store = _store()
    proposal = store.create("some-proposer", "demo-skill", "d", CLEAN_MD, [])
    with TestClient(app) as client:
        denied = client.post(f"/api/skills/proposals/{proposal.id}/approve", headers={"x-role": "user"})
        assert denied.status_code == 403
        denied_all = client.get("/api/skills/proposals", params={"scope": "all"}, headers={"x-role": "user"})
        assert denied_all.status_code == 403
        denied_reject = client.post(
            f"/api/skills/proposals/{proposal.id}/reject",
            json={},
            headers={"x-role": "user"},
        )
        assert denied_reject.status_code == 403
        # Admin-only routes reject anonymous via require_admin_user. (List and
        # create rely on the production global AuthMiddleware fail-closed
        # boundary like the other non-admin skills routes; this bare test app
        # has no global middleware, so anonymous is only asserted below.)
        anonymous_approve = client.post(f"/api/skills/proposals/{proposal.id}/approve", headers={"x-role": "anonymous"})
        assert anonymous_approve.status_code == 401

        async def _fake_install(archive_path, config, *, user_id=None):
            assert user_id == "some-proposer"
            return SimpleNamespace(skill_name="demo-skill")

        monkeypatch.setattr(skills, "_install_skill_archive", _fake_install)
        approved = client.post(f"/api/skills/proposals/{proposal.id}/approve")
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert body["status"] == "installed"
        assert body["installed_skill"] == "demo-skill"
        assert body["reviewed_by"]
        stored = store.get_any(proposal.id)
        assert stored is not None and stored.status == "installed"


def test_admin_sees_all_but_users_see_own(app):
    store = _store()
    mine = store.create("test-user-autouse", "aaa-skill", "d", CLEAN_MD, [])
    store.create("other-user", "bbb-skill", "d", CLEAN_MD, [])
    with TestClient(app) as client:
        all_proposals = client.get("/api/skills/proposals", params={"scope": "all"})
        assert all_proposals.status_code == 200
        assert len(all_proposals.json()["proposals"]) == 2
        own = client.get("/api/skills/proposals")
        assert own.status_code == 200
        assert [p["id"] for p in own.json()["proposals"]] == [mine.id]


def test_findings_shape_matches_ui_contract(app):
    store = _store()
    store.create(
        "test-user-autouse",
        "demo-skill",
        "d",
        CLEAN_MD,
        # Native SkillScan finding shape (file, not path).
        [{"rule_id": "r1", "severity": "HIGH", "message": "m", "remediation": None, "file": "SKILL.md", "line": 3}],
    )
    with TestClient(app) as client:
        response = client.get("/api/skills/proposals")
        assert response.status_code == 200
        finding = response.json()["proposals"][0]["findings"][0]
        assert finding == {
            "rule_id": "r1",
            "severity": "HIGH",
            "file": "SKILL.md",
            "line": 3,
            "message": "m",
            "remediation": None,
        }
