"""Honesty proofs for evidence-disclosure fixes that had no existing coverage.

Covers:
- F2: the enterprise discovery endpoint serves an EMPTY latency profile list
  (no fabricated p50/p95/p99 baselines) because no runtime latency recorder
  exists for those components.
- F3: the council holdout benchmark router response carries evidence_kind and
  emits holdout_passed=None with an explicit reason instead of a boolean pass
  verdict derived from a simulated score.
- F4: a bad measured ROI velocity is preserved, never floored to a flattering
  constant.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.enterprise import get_discovery_and_optimization_engine
from alpha.enterprise.governance import DepartmentTokenTreasury
from app.gateway.routers.enterprise import router as enterprise_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(enterprise_router)
    return TestClient(app)


def test_discovery_endpoint_serves_no_fabricated_latency_profiles():
    """F2: profiles start empty; the endpoint must not invent p50/p95/p99 values."""
    client = _client()
    res = client.get("/api/enterprise/discovery")
    assert res.status_code == 200
    data = res.json()
    # No runtime latency source is wired, so the honest answer is an empty list.
    assert data["latency_profiles"] == []

    disc = get_discovery_and_optimization_engine()
    assert disc.profile_latencies() == []
    assert disc.get_latency_profiles() == []
    # Feature gaps and the AST security scan are real and still served.
    assert isinstance(data["feature_gaps"], list) and len(data["feature_gaps"]) >= 1
    assert data["latest_security_scan"] is not None


def test_holdout_benchmark_router_response_has_no_boolean_pass_verdict():
    """F3: a simulated score must never be turned into a holdout pass claim."""
    client = _client()
    version = "v9.9.9"
    release_id = "rel-v9-9-9"
    stage = client.post(
        "/api/enterprise/council/stage",
        json={
            "version": version,
            "component": "enterprise-core",
            "description": "evidence disclosure proof",
            "diff_content": "diff payload for evidence disclosure proof",
        },
    )
    assert stage.status_code == 201

    res = client.post(f"/api/enterprise/council/releases/{release_id}/benchmark")
    assert res.status_code == 200
    body = res.json()

    # The score field stays, but its basis is unmissable.
    assert isinstance(body["holdout_benchmark_score"], float)
    assert body["evidence_kind"] == "simulated"

    # No boolean pass verdict may be derived from a simulated score.
    assert body["holdout_passed"] is None
    reason = body["holdout_passed_reason"]
    assert "simulated" in reason
    assert "evidence_kind=simulated" in reason
    assert "no measured benchmark ran" in reason

    # The stored model gate remains the real untouched False.
    assert body["release"]["holdout_passed"] is False
    assert body["release"]["evidence_kind"] == "simulated"


def test_low_measured_roi_velocity_is_preserved_not_floored():
    """F4: a poor measured ROI is real information and must not be overwritten."""
    treasury = DepartmentTokenTreasury()
    alloc = treasury.get_allocation("dept-engineering")
    assert alloc is not None

    # Burn a large amount of tokens while completing zero tasks: the real ROI
    # for this window is 0 tasks / 10k tokens = 0.0.
    alloc, _tripped = treasury.record_token_burn(
        "dept-engineering", tokens_burned=50000, tasks_completed=0
    )
    assert alloc.spent_tokens > 0
    # HONESTY PIN: the old code floored any ROI < 0.1 up to the constant 0.85.
    assert alloc.roi_velocity == 0.0
    assert alloc.roi_velocity != 0.85

    # The roll-up consumes the real value without range assumptions.
    telemetry = treasury.get_overall_telemetry()
    assert telemetry["average_roi_velocity"] < 1.2
    assert isinstance(telemetry["average_roi_velocity"], float)
