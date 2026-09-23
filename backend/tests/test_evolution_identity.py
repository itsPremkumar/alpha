"""Phase 1 — repository awareness: manifest, identity, release check, routes.

Covers the end-to-end wiring: config/project-manifest.json -> manifest loader
-> runtime identity / release check -> Gateway routes -> lead-prompt section,
plus ledger persistence and the honest-failure contracts (no fabricated
success, real error messages in-body).
"""

from __future__ import annotations

import json
import re

import pytest

from alpha.evolution import release_check
from alpha.evolution.engine import EvolutionEngine
from alpha.evolution.identity import WIRED_CAPABILITIES, get_runtime_identity, resolve_alpha_version
from alpha.evolution.manifest import find_project_manifest_path, load_project_manifest
from alpha.evolution.release_check import _is_newer_release, _parse_semver


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    """Point runtime_home() at a per-test tmp dir (AGENT_WORKSPACE_HOME)."""
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(home))
    return home


def _evolution_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.gateway.routers.evolution import router as evolution_router

    # Bare router app: the evolution routes carry no permission decorators, and
    # production auth stays the untouched default AuthMiddleware. Mirrors the
    # router-level test pattern documented in tests/_router_auth_helpers.py.
    app = FastAPI()
    app.include_router(evolution_router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Deliverable 1/2 — project manifest
# ---------------------------------------------------------------------------


def test_project_manifest_is_single_source_of_truth():
    manifest = load_project_manifest()
    assert manifest["projectId"] == "alpha"
    assert manifest["name"]
    repository = manifest["repository"]
    assert repository["provider"] == "github"
    assert repository["owner"] == "itsPremkumar"
    assert repository["name"] == "alpha"
    assert repository["url"] == "https://github.com/itsPremkumar/alpha"
    assert repository["defaultBranch"] == "main"
    assert manifest["release"]["channel"] == "stable"
    # No fifth version source: versions live in the four files that
    # scripts/verify_versions.sh gates.
    assert "version" not in manifest


def test_missing_manifest_raises_honest_runtime_error(tmp_path, monkeypatch):
    missing = tmp_path / "config" / "project-manifest.json"
    monkeypatch.setenv("ALPHA_PROJECT_MANIFEST", str(missing))
    with pytest.raises(RuntimeError) as excinfo:
        load_project_manifest()
    message = str(excinfo.value)
    assert str(missing) in message
    assert "ALPHA_PROJECT_MANIFEST" in message


def test_invalid_manifest_json_raises_honest_runtime_error(tmp_path, monkeypatch):
    broken = tmp_path / "project-manifest.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("ALPHA_PROJECT_MANIFEST", str(broken))
    with pytest.raises(RuntimeError, match="not valid JSON"):
        load_project_manifest()


def test_manifest_missing_fields_named_in_error(tmp_path, monkeypatch):
    partial = tmp_path / "project-manifest.json"
    partial.write_text(
        json.dumps({"projectId": "alpha", "name": "Alpha", "repository": {"owner": "x"}, "release": {"channel": "stable"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ALPHA_PROJECT_MANIFEST", str(partial))
    with pytest.raises(RuntimeError) as excinfo:
        load_project_manifest()
    assert "repository.defaultBranch" in str(excinfo.value)


def test_manifest_with_version_field_rejected(tmp_path, monkeypatch):
    """A version inside the manifest would be a fifth version source — refuse it."""
    data = json.loads(find_project_manifest_path().read_text(encoding="utf-8"))
    data["version"] = "9.9.9"
    override = tmp_path / "project-manifest.json"
    override.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("ALPHA_PROJECT_MANIFEST", str(override))
    with pytest.raises(RuntimeError, match="must not carry a 'version' field"):
        load_project_manifest()


# ---------------------------------------------------------------------------
# Deliverable 3 — runtime identity
# ---------------------------------------------------------------------------


def test_agent_id_generated_once_and_persisted_atomically(isolated_home):
    first = get_runtime_identity()["agentId"]
    assert re.fullmatch(r"alpha-[0-9a-f]{8}", first)
    identity_file = isolated_home / "identity.json"
    assert identity_file.is_file()
    on_disk = json.loads(identity_file.read_text(encoding="utf-8"))
    assert on_disk["agentId"] == first
    assert on_disk["identityVersion"] == 1
    assert on_disk["createdAt"]
    # Atomic write leaves no staging file behind.
    assert list(isolated_home.glob("*.tmp")) == []
    # Never regenerated on subsequent reads.
    assert get_runtime_identity()["agentId"] == first


def test_existing_agent_id_never_overwritten(isolated_home):
    isolated_home.mkdir(parents=True)
    identity_file = isolated_home / "identity.json"
    identity_file.write_text(
        json.dumps({"agentId": "alpha-deadbeef", "identityVersion": 1, "createdAt": "2026-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    identity = get_runtime_identity()
    assert identity["agentId"] == "alpha-deadbeef"
    assert identity["createdAt"] == "2026-01-01T00:00:00+00:00"
    assert json.loads(identity_file.read_text(encoding="utf-8"))["agentId"] == "alpha-deadbeef"


def test_runtime_identity_reports_honest_facts(isolated_home):
    identity = get_runtime_identity()
    for key in (
        "agentId",
        "alphaVersion",
        "gitCommit",
        "gitCommitSource",
        "gitCommitNote",
        "os",
        "architecture",
        "runtime",
        "repository",
        "releaseChannel",
        "updateState",
        "capabilities",
    ):
        assert key in identity
    assert identity["repository"]["url"] == "https://github.com/itsPremkumar/alpha"
    assert identity["repository"]["defaultBranch"] == "main"
    assert identity["releaseChannel"] == "stable"
    assert identity["updateState"] in release_check.STATES
    # Honest capability list: only what is actually wired in this build.
    assert identity["capabilities"] == list(WIRED_CAPABILITIES)
    assert set(identity["capabilities"]) == {"identity", "release_check", "evolution_ledger"}
    # Git: either a real probe result, or "unknown" with an honest source+note.
    if identity["gitCommit"] == "unknown":
        assert identity["gitCommitSource"] == "unavailable"
        assert identity["gitCommitNote"]
    else:
        assert identity["gitCommitSource"] == "git"
        assert identity["gitCommit"]


def test_git_commit_failure_is_honest_not_fabricated(tmp_path, monkeypatch, isolated_home):
    # Point the manifest at a directory outside any git checkout: the probe
    # must fail with "unknown" + the real reason instead of inventing a commit.
    data = json.loads(find_project_manifest_path().read_text(encoding="utf-8"))
    override = tmp_path / "project-manifest.json"
    override.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("ALPHA_PROJECT_MANIFEST", str(override))

    identity = get_runtime_identity()
    assert identity["gitCommit"] == "unknown"
    assert identity["gitCommitSource"] == "unavailable"
    assert identity["gitCommitNote"]


def test_identity_version_chain_cannot_drift_from_ops(isolated_home):
    from app.gateway.routers.ops import _resolve_gateway_version

    assert resolve_alpha_version() == _resolve_gateway_version()
    assert get_runtime_identity()["alphaVersion"] == _resolve_gateway_version()


# ---------------------------------------------------------------------------
# Deliverable 4 — release check: semver + state machine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1.2.3", (1, 2, 3)),
        ("v1.2.3", (1, 2, 3)),
        ("V2.0", (2, 0)),
        ("  1.10.0 ", (1, 10, 0)),
        ("0", (0,)),
        ("", None),
        ("v", None),
        ("banana-release", None),
        ("1.2.3-beta.1", None),
        ("1.-2.3", None),
        ("1.2.3.4x", None),
    ],
)
def test_parse_semver_edge_cases(value, expected):
    assert _parse_semver(value) == expected


@pytest.mark.parametrize(
    "latest,installed,expected",
    [
        ("v2.0.0", "1.9.9", True),
        ("1.10.0", "1.9.9", True),
        ("v1.2.3", "1.2.3", False),
        ("1.2", "1.2.0", False),
        ("1.2.1", "1.2", True),
        ("1.0.0", "2.0.0", False),
        ("garbage", "1.0.0", None),
        ("v1.0.0", "unknown", None),
        ("1.0.0", "", None),
    ],
)
def test_is_newer_release_edge_cases(latest, installed, expected):
    assert _is_newer_release(latest, installed) is expected


def test_update_state_starts_idle(isolated_home):
    state = release_check.load_update_state()
    assert state["state"] == release_check.IDLE
    assert state["checkedAt"] is None
    assert state["latestTag"] is None
    assert state["error"] is None
    assert state["installedVersion"] == resolve_alpha_version()


def test_update_check_detects_available_update(isolated_home, monkeypatch):
    monkeypatch.setattr(release_check, "resolve_alpha_version", lambda: "1.0.0")
    monkeypatch.setattr(release_check, "_fetch_latest_release", lambda owner, name: {"tag_name": "v9.9.9"})

    state = release_check.check_for_update()

    assert state["state"] == release_check.UPDATE_AVAILABLE
    assert state["latestTag"] == "v9.9.9"
    assert state["installedVersion"] == "1.0.0"
    assert state["checkedAt"]
    assert state["error"] is None
    assert release_check.load_update_state() == state  # persisted


def test_update_check_up_to_date(isolated_home, monkeypatch):
    monkeypatch.setattr(release_check, "resolve_alpha_version", lambda: "2.1.0")
    monkeypatch.setattr(release_check, "_fetch_latest_release", lambda owner, name: {"tag_name": "v2.1.0"})

    state = release_check.check_for_update()

    assert state["state"] == release_check.UP_TO_DATE
    assert state["error"] is None


def test_update_check_upstream_failure_reports_real_error(isolated_home, monkeypatch):
    def _explode(owner, name):
        raise RuntimeError("GitHub latest-release request refused with HTTP 403 (rate limited)")

    monkeypatch.setattr(release_check, "_fetch_latest_release", _explode)

    state = release_check.check_for_update()

    assert state["state"] == release_check.CHECK_FAILED
    assert "rate limited" in state["error"]
    assert release_check.load_update_state()["state"] == release_check.CHECK_FAILED


def test_update_check_unparseable_tag_never_guesses(isolated_home, monkeypatch):
    monkeypatch.setattr(release_check, "resolve_alpha_version", lambda: "2.1.0")
    monkeypatch.setattr(release_check, "_fetch_latest_release", lambda owner, name: {"tag_name": "banana-release"})

    state = release_check.check_for_update()

    assert state["state"] == release_check.CHECK_FAILED
    assert "banana-release" in state["error"]
    assert state["latestTag"] == "banana-release"


def test_update_check_unknown_installed_version_never_guesses(isolated_home, monkeypatch):
    monkeypatch.setattr(release_check, "resolve_alpha_version", lambda: "unknown")
    monkeypatch.setattr(release_check, "_fetch_latest_release", lambda owner, name: {"tag_name": "v1.2.3"})

    state = release_check.check_for_update()

    assert state["state"] == release_check.CHECK_FAILED
    assert "'unknown'" in state["error"]


def test_corrupt_update_state_file_reports_check_failed(isolated_home):
    isolated_home.mkdir(parents=True)
    (isolated_home / "update_state.json").write_text("{broken", encoding="utf-8")

    state = release_check.load_update_state()

    assert state["state"] == release_check.CHECK_FAILED
    assert "unreadable" in state["error"]


# ---------------------------------------------------------------------------
# Deliverable 6 — ledger persistence
# ---------------------------------------------------------------------------


def test_ledger_appends_and_reloads_roundtrip(isolated_home):
    engine = EvolutionEngine()
    candidate = engine.propose("skill", "demo-skill", {"v": 1})
    engine.record_benchmark(candidate.candidate_id, {"passed": 3, "failed": 0})

    ledger_file = isolated_home / "evolution" / "ledger.jsonl"
    assert ledger_file.is_file()
    persisted = [json.loads(line) for line in ledger_file.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in persisted] == ["proposed", "benchmarked"]

    reloaded = EvolutionEngine()
    events = reloaded.ledger(limit=10)
    assert [event["event"] for event in events] == ["proposed", "benchmarked"]
    assert events[0]["candidate_id"] == candidate.candidate_id
    assert events == persisted  # full round-trip, not just counts


def test_ledger_skips_corrupt_lines_with_warning(isolated_home, caplog):
    engine = EvolutionEngine()
    engine.propose("prompt", "planner", {})
    ledger_file = isolated_home / "evolution" / "ledger.jsonl"
    with ledger_file.open("a", encoding="utf-8") as handle:
        handle.write("{truncated json\n")
        handle.write('"a bare string is not an event object"\n')

    with caplog.at_level("WARNING", logger="alpha.evolution.engine"):
        reloaded = EvolutionEngine()

    events = reloaded.ledger(limit=10)
    assert len(events) == 1
    assert events[0]["event"] == "proposed"
    assert "Skipped 2 corrupt/partial evolution ledger line(s)" in caplog.text


def test_ledger_write_failure_never_crashes_evolution(isolated_home, caplog):
    ledger_dir = isolated_home / "evolution"
    ledger_dir.mkdir(parents=True)
    (ledger_dir / "ledger.jsonl").mkdir()  # a directory where the JSONL file belongs

    with caplog.at_level("WARNING", logger="alpha.evolution.engine"):
        engine = EvolutionEngine()
        candidate = engine.propose("skill", "demo", {})
        engine.record_benchmark(candidate.candidate_id, {"passed": 9, "failed": 0})
        promoted, _reason = engine.gate(candidate.candidate_id, {"passed": 7, "failed": 0}, human_approved=True)
        assert promoted is True
        assert engine.rollback(candidate.candidate_id, "smoke") is True

    assert engine.ledger()  # in-memory ledger keeps working despite write failures
    assert "Could not persist evolution ledger event" in caplog.text


# ---------------------------------------------------------------------------
# Deliverable 5 — Gateway routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_mounts_evolution_identity_routes() -> None:
    from app.gateway.app import create_app

    paths = {route.path for route in create_app().routes}
    assert "/api/evolution/candidates" in paths
    assert "/api/evolution/identity" in paths
    assert "/api/evolution/update-check" in paths
    assert "/api/evolution/update-state" in paths


def test_identity_route_returns_runtime_identity(isolated_home):
    response = _evolution_client().get("/api/evolution/identity")
    assert response.status_code == 200
    body = response.json()
    assert re.fullmatch(r"alpha-[0-9a-f]{8}", body["agentId"])
    assert body["repository"]["url"] == "https://github.com/itsPremkumar/alpha"
    assert body["releaseChannel"] == "stable"
    assert body["capabilities"] == list(WIRED_CAPABILITIES)


def test_update_state_route_serves_persisted_state_without_network(isolated_home):
    response = _evolution_client().get("/api/evolution/update-state")
    assert response.status_code == 200
    assert response.json()["state"] == release_check.IDLE


def test_update_check_route_success_and_failure_contract(isolated_home, monkeypatch):
    client = _evolution_client()

    monkeypatch.setattr(release_check, "resolve_alpha_version", lambda: "1.0.0")
    monkeypatch.setattr(release_check, "_fetch_latest_release", lambda owner, name: {"tag_name": "v3.0.0"})
    ok = client.post("/api/evolution/update-check")
    assert ok.status_code == 200
    assert ok.json()["state"] == release_check.UPDATE_AVAILABLE

    def _explode(owner, name):
        raise RuntimeError("GitHub rate limited the request (HTTP 429)")

    monkeypatch.setattr(release_check, "_fetch_latest_release", _explode)
    failed = client.post("/api/evolution/update-check")
    # Established precedent: failed checks answer 200 with CHECK_FAILED and
    # the real error in-body — not a fabricated success, not a 500.
    assert failed.status_code == 200
    body = failed.json()
    assert body["state"] == release_check.CHECK_FAILED
    assert "rate limited" in body["error"]

    persisted = client.get("/api/evolution/update-state")
    assert persisted.status_code == 200
    assert persisted.json()["state"] == release_check.CHECK_FAILED


# ---------------------------------------------------------------------------
# Deliverable 7 — lead-prompt project identity section
# ---------------------------------------------------------------------------


def test_project_identity_section_carries_repo_awareness():
    from alpha.agents.lead_agent import prompt as prompt_module

    section = prompt_module._build_project_identity_section()
    assert "https://github.com/itsPremkumar/alpha" in section
    assert "`main` branch is the host/source-of-truth" in section
    assert "propose -> benchmark -> gate -> promote" in section
    assert "evidence -> issue -> PR -> CI -> merge" in section
    assert "NEVER push to, force, or directly modify `main`" in section
    assert "branch protection" in section
    assert "verified" in section
    assert len(section.splitlines()) <= 15
    assert "{project_identity_section}" in prompt_module.SYSTEM_PROMPT_TEMPLATE


def test_project_identity_section_never_crashes_prompt_build(monkeypatch):
    from alpha.agents.lead_agent import prompt as prompt_module
    from alpha.evolution import manifest as manifest_module

    def _broken():
        raise RuntimeError("manifest unavailable")

    monkeypatch.setattr(manifest_module, "load_project_manifest", _broken)
    assert prompt_module._build_project_identity_section() == ""
