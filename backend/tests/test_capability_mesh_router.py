"""Tests for ``/api/capability-mesh`` — the HTTP mirror of the mesh tool.

Both surfaces call ``alpha.capability_mesh.service.CapabilityMeshService``, so
these tests are about the *HTTP* contract rather than re-proving the library:
auth, the route inventory, the shape of each payload, and above all the
degraded states.

The headline boundary is ``test_a_corrupt_store_is_503_not_an_empty_200``: a
store that could not be read must never answer like a store with nothing in
it, because those two facts drive opposite operator decisions. A *missing*
file is genuinely empty, and the two stay distinguishable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from alpha.capability_mesh.config import CapabilityMeshConfig
from alpha.capability_mesh.memory import CapabilityMemory, CapabilityRecord, OutcomeKind
from alpha.capability_mesh.models import CapabilityEstimate, CapabilityPassport, EntityKind, EvidenceLevel
from alpha.capability_mesh.store import PassportStore

PV = "v1"
TASK = "Debug the urgent production latency regression before the release."


@pytest.fixture
def mesh_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "capability_mesh"
    directory.mkdir()
    return directory


def _enable(monkeypatch, directory: Path, **overrides) -> None:
    """Point the router at a temp mesh directory through its own config seam."""
    from app.gateway.routers import capability_mesh as router_module

    config = CapabilityMeshConfig(directory=str(directory), **{"enabled": True, **overrides})
    monkeypatch.setattr(router_module, "get_app_config", lambda: SimpleNamespace(capability_mesh=config))


def _client() -> TestClient:
    from app.gateway.deps import get_current_user_from_request
    from app.gateway.routers.capability_mesh import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_from_request] = lambda: SimpleNamespace(id="user-1", system_role="member")
    return TestClient(app)


def _seed_passport(directory: Path, *, entity_id: str = "agent:planner", measured: bool = False) -> None:
    estimate = (
        CapabilityEstimate(capability="debug", task_category="*", quality_mean=0.8, uncertainty=0.05, sample_count=12, evidence_level=EvidenceLevel.BENCHMARKED, profile_version=PV)
        if measured
        else CapabilityEstimate(capability="debug", task_category="*", sample_count=0, evidence_level=EvidenceLevel.UNMEASURED)
    )
    PassportStore(directory).upsert(CapabilityPassport(entity_id=entity_id, entity_type=EntityKind.AGENT, version="v1", capabilities=(estimate,)))


def _seed_record(directory: Path, *, record_id: str, entity_id: str = "agent:planner", quality: float | None = None) -> None:
    CapabilityMemory(directory).record(
        CapabilityRecord(
            record_id=record_id,
            task_fingerprint=f"fp_{record_id}",
            task_category="general",
            entity_id=entity_id,
            capability="debug",
            outcome=OutcomeKind.SELECTED,
            recorded_at=datetime.now(UTC).isoformat(),
            quality=quality,
            profile_version=PV,
        )
    )


# ------------------------------------------------------------------ route inventory


class TestRouteInventory:
    def test_router_owns_its_own_prefix(self) -> None:
        from app.gateway.routers.capability_mesh import router

        assert router.prefix == "/api/capability-mesh"

    def test_every_declared_route_is_present(self) -> None:
        from app.gateway.routers.capability_mesh import router

        declared = {(route.path, tuple(sorted(route.methods))) for route in router.routes if hasattr(route, "methods")}
        expected = {
            ("/api/capability-mesh/profiles", ("GET",)),
            ("/api/capability-mesh/memory", ("GET",)),
            ("/api/capability-mesh/calibration", ("GET",)),
            ("/api/capability-mesh/drift", ("GET",)),
            ("/api/capability-mesh/policy", ("GET",)),
            ("/api/capability-mesh/independence", ("GET",)),
            ("/api/capability-mesh/shadow", ("GET",)),
            ("/api/capability-mesh/route", ("POST",)),
            ("/api/capability-mesh/team", ("POST",)),
            ("/api/capability-mesh/compose", ("POST",)),
            ("/api/capability-mesh/records", ("POST",)),
        }
        assert expected <= declared

    def test_router_is_mounted_in_the_gateway_app(self) -> None:
        source = Path("app/gateway/app.py").read_text(encoding="utf-8")
        assert "app.include_router(capability_mesh.router)" in source

    def test_router_is_declared_in_the_manifest(self) -> None:
        from alpha.workflow.registry.manifest_source import load_feature_manifest

        ids = {entry["id"] for entry in load_feature_manifest().get("routers", [])}
        assert "app.gateway.routers.capability_mesh" in ids


# ---------------------------------------------------------------------- auth


class TestAuth:
    def test_every_route_requires_authentication(self, mesh_dir: Path) -> None:
        from app.gateway.routers.capability_mesh import router

        routes = [route for route in router.routes if hasattr(route, "dependant")]
        assert routes, "the router declares no routes"
        for route in routes:
            names = {getattr(dependency.call, "__name__", None) for dependency in route.dependant.dependencies}
            assert "get_current_user_from_request" in names, route.path

    def test_an_unauthenticated_caller_is_refused(self, monkeypatch, mesh_dir: Path) -> None:
        from app.gateway.deps import get_current_user_from_request
        from app.gateway.routers.capability_mesh import router

        _enable(monkeypatch, mesh_dir)

        def _deny() -> None:
            raise HTTPException(status_code=401, detail="Not authenticated")

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user_from_request] = _deny
        with TestClient(app, raise_server_exceptions=False) as client:
            assert client.get("/api/capability-mesh/profiles").status_code == 401
            assert client.post("/api/capability-mesh/route", json={"task": TASK}).status_code == 401


# ---------------------------------------------------------------------- reads


class TestReads:
    def test_profiles_reports_the_count_with_its_evidence_level(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        _seed_passport(mesh_dir)
        with _client() as client:
            payload = client.get("/api/capability-mesh/profiles").json()
        assert payload["count"] == 1
        assert payload["evidence"]["strongest"] == "unmeasured"
        assert payload["passports"][0]["entity_id"] == "agent:planner"

    def test_profiles_reports_a_measured_level_when_there_is_one(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        _seed_passport(mesh_dir, measured=True)
        with _client() as client:
            payload = client.get("/api/capability-mesh/profiles").json()
        assert payload["evidence"]["strongest"] == "benchmarked"
        assert payload["evidence"]["unmeasured_count"] == 0

    def test_an_empty_store_is_an_empty_answer_not_a_degraded_one(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        with _client() as client:
            response = client.get("/api/capability-mesh/profiles")
        assert response.status_code == 200
        assert response.json()["count"] == 0

    def test_memory_filters_by_entity_id(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        _seed_record(mesh_dir, record_id="r1", entity_id="agent:planner")
        _seed_record(mesh_dir, record_id="r2", entity_id="agent:other")
        with _client() as client:
            payload = client.get("/api/capability-mesh/memory", params={"entity_id": "agent:planner"}).json()
        assert payload["record_count"] == 1
        assert payload["records"][0]["record_id"] == "r1"
        assert "agent:planner" in payload["credit"]

    def test_memory_bounds_its_page_and_says_it_truncated(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        for index in range(3):
            _seed_record(mesh_dir, record_id=f"r{index}")
        with _client() as client:
            payload = client.get("/api/capability-mesh/memory", params={"limit": 1}).json()
        assert payload["record_count"] == 3
        assert payload["returned"] == 1
        assert payload["truncated"] is True

    def test_calibration_reports_unresolved_over_nothing(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        with _client() as client:
            payload = client.get("/api/capability-mesh/calibration").json()
        assert payload["well_calibrated"] is None
        assert payload["brier_score"] is None
        assert "unresolved" in payload["verdict_note"].lower()

    def test_drift_reports_and_declares_it_does_not_act(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        with _client() as client:
            payload = client.get("/api/capability-mesh/drift").json()
        assert payload["acts"] is False
        assert payload["degraded_entities"] == []

    def test_policy_returns_the_whole_table_and_the_resolved_row(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        with _client() as client:
            payload = client.get("/api/capability-mesh/policy", params={"policy": "deep"}).json()
        assert payload["resolved"]["policy"] == "deep"
        assert len(payload["policies"]) >= 8
        assert payload["exploration_note"].startswith("declared, not spent")

    def test_shadow_reports_no_agreement_rate_over_nothing(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        with _client() as client:
            payload = client.get("/api/capability-mesh/shadow").json()
        assert payload["agreement_rate"] is None
        assert payload["spends_budget"] is False

    def test_independence_scores_a_known_pair(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        _seed_passport(mesh_dir, entity_id="agent:a", measured=True)
        _seed_passport(mesh_dir, entity_id="agent:b", measured=True)
        with _client() as client:
            response = client.get("/api/capability-mesh/independence", params={"entity_a": "agent:a", "entity_b": "agent:b"})
        assert response.status_code == 200
        assert response.json()["known"] is False

    def test_independence_refuses_the_same_entity_on_both_sides(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        _seed_passport(mesh_dir)
        with _client() as client:
            response = client.get("/api/capability-mesh/independence", params={"entity_a": "agent:planner", "entity_b": "agent:planner"})
        assert response.status_code == 400
        assert "distinct" in response.json()["detail"].lower()


# -------------------------------------------------------------------- previews


class TestPreviews:
    @pytest.mark.parametrize("path", ["/api/capability-mesh/route", "/api/capability-mesh/team", "/api/capability-mesh/compose"])
    def test_preview_routes_are_labelled_and_dispatch_nothing(self, monkeypatch, mesh_dir: Path, path: str) -> None:
        _enable(monkeypatch, mesh_dir)
        _seed_passport(mesh_dir, measured=True)
        with _client() as client:
            payload = client.post(path, json={"task": TASK, "required_capabilities": ["debug"]}).json()
        assert payload["preview"] is True, path
        assert payload["dispatched"] is False, path
        assert "nothing was dispatched" in payload["preview_note"].lower(), path

    def test_route_carries_the_heuristic_decomposition_disclosure(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        _seed_passport(mesh_dir, measured=True)
        with _client() as client:
            payload = client.post("/api/capability-mesh/route", json={"task": TASK, "required_capabilities": ["debug"]}).json()
        assert payload["decomposition"]["decomposer"] == "heuristic_baseline"

    def test_team_reports_its_stop_reason_and_independence_note(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        _seed_passport(mesh_dir, measured=True)
        with _client() as client:
            plan = client.post("/api/capability-mesh/team", json={"task": TASK, "required_capabilities": ["debug"]}).json()["plan"]
        assert plan["stops"]
        assert plan["independence_note"]

    def test_compose_accepts_supplied_artifacts_and_persists_none(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        _seed_passport(mesh_dir, measured=True)
        artifact = {
            "artifact_id": "a1",
            "entity_id": "agent:planner",
            "microtask_id": "mt-1",
            "content": "a bounded fix",
            "mime_type": "text/plain",
            "capability_coverage": ["debug"],
            "evidence_level": "observed",
            "quality": 0.9,
            "passed_hard_gate": True,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        with _client() as client:
            response = client.post("/api/capability-mesh/compose", json={"task": TASK, "required_capabilities": ["debug"], "artifacts": [artifact]})
        assert response.status_code == 200
        assert response.json()["composition"]["profile_version"] == PV
        assert not (mesh_dir / "artifacts.json").exists()

    @pytest.mark.parametrize("path", ["/api/capability-mesh/route", "/api/capability-mesh/team", "/api/capability-mesh/compose"])
    def test_preview_routes_require_task_text(self, monkeypatch, mesh_dir: Path, path: str) -> None:
        _enable(monkeypatch, mesh_dir)
        with _client() as client:
            assert client.post(path, json={}).status_code == 422


# --------------------------------------------------------------------- writes


class TestRecords:
    def test_records_appends_one_validated_record(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        with _client() as client:
            response = client.post("/api/capability-mesh/records", json={"entity_id": "agent:planner", "capability": "debug", "outcome": "selected", "task_fingerprint": "fp_1", "quality": 0.8})
        assert response.status_code == 200
        assert len(CapabilityMemory(mesh_dir).records()) == 1

    def test_records_refuses_a_score_without_a_profile_version(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir, default_profile_version="")
        with _client() as client:
            response = client.post("/api/capability-mesh/records", json={"entity_id": "agent:planner", "capability": "debug", "outcome": "selected", "task_fingerprint": "fp_1", "quality": 0.8})
        assert response.status_code == 400
        assert "profile_version" in response.json()["detail"]
        assert CapabilityMemory(mesh_dir).records() == []

    def test_records_rejects_a_truthy_string_hard_gate(self, monkeypatch, mesh_dir: Path) -> None:
        """``"yes"`` must never become a silent pass.

        The request model declares ``StrictBool``, so pydantic refuses the
        coercion and FastAPI answers ``422`` at the schema boundary — one layer
        before the mesh's own constructor would have refused it too. Either way
        the row is not written, which is the property that matters.
        """
        _enable(monkeypatch, mesh_dir)
        with _client() as client:
            response = client.post("/api/capability-mesh/records", json={"entity_id": "agent:planner", "capability": "debug", "outcome": "selected", "task_fingerprint": "fp_1", "passed_hard_gate": "yes"})
        assert response.status_code == 422
        assert CapabilityMemory(mesh_dir).records() == []

    def test_records_never_stores_raw_task_text(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        with _client() as client:
            response = client.post("/api/capability-mesh/records", json={"entity_id": "agent:planner", "capability": "debug", "outcome": "selected", "task_text": "a secret prompt body"})
        assert response.status_code == 200
        stored = CapabilityMemory(mesh_dir).records()[0].to_dict()
        assert stored["task_fingerprint"].startswith("fp_")
        assert "secret" not in str(stored)


# -------------------------------------------------------------- degraded states


class TestDegradedStates:
    def test_a_corrupt_passport_store_is_503_not_an_empty_200(self, monkeypatch, mesh_dir: Path) -> None:
        """The headline boundary: "could not read" must never answer like "nothing there"."""
        _enable(monkeypatch, mesh_dir)
        (mesh_dir / "passports.json").write_text("{ corrupt", encoding="utf-8")
        with _client() as client:
            response = client.get("/api/capability-mesh/profiles")
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "unreadable" in detail.lower()
        assert str(mesh_dir) in detail

    def test_a_corrupt_memory_is_503_not_an_empty_200(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        (mesh_dir / "capability_memory.jsonl").write_text("{ corrupt\n", encoding="utf-8")
        with _client() as client:
            assert client.get("/api/capability-mesh/memory").status_code == 503

    def test_a_corrupt_shadow_store_is_503_not_an_empty_200(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        (mesh_dir / "shadow.json").write_text("{ corrupt", encoding="utf-8")
        with _client() as client:
            assert client.get("/api/capability-mesh/shadow").status_code == 503

    def test_a_corrupt_store_degrades_a_preview_route_too(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        (mesh_dir / "passports.json").write_text("{ corrupt", encoding="utf-8")
        with _client() as client:
            assert client.post("/api/capability-mesh/route", json={"task": TASK}).status_code == 503

    def test_a_disabled_mesh_is_a_503_naming_the_setting(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir, enabled=False)
        with _client() as client:
            response = client.get("/api/capability-mesh/profiles")
        assert response.status_code == 503
        assert "capability_mesh.enabled" in response.json()["detail"]

    def test_an_unknown_policy_is_a_400_naming_the_valid_ones(self, monkeypatch, mesh_dir: Path) -> None:
        _enable(monkeypatch, mesh_dir)
        with _client() as client:
            response = client.get("/api/capability-mesh/policy", params={"policy": "nope"})
        assert response.status_code == 400
        assert "balanced" in response.json()["detail"]


# --------------------------------------------------------------- event loop I/O


class TestNoBlockingIo:
    def test_store_reads_do_not_run_on_the_request_loop(self, monkeypatch, mesh_dir: Path) -> None:
        """Every route is ``async``, so an inline store read stalls the process."""
        import threading

        _enable(monkeypatch, mesh_dir)
        _seed_passport(mesh_dir, measured=True)
        _seed_record(mesh_dir, record_id="r1", quality=0.8)
        loop_thread = threading.get_ident()
        offenders: list[str] = []
        real_open = Path.open

        def _tracking_open(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            if threading.get_ident() == loop_thread:
                offenders.append(str(self))
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", _tracking_open)
        with _client() as client:
            client.get("/api/capability-mesh/profiles")
            client.get("/api/capability-mesh/memory")
            client.get("/api/capability-mesh/calibration")
            client.get("/api/capability-mesh/drift")
            client.get("/api/capability-mesh/shadow")
            client.post("/api/capability-mesh/route", json={"task": TASK})
        assert offenders == [], f"store I/O ran on the request loop: {offenders}"
