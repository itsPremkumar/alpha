"""Tests for ``/api/rsi/predictive`` — the read-only operator surface.

These are about the *HTTP* contract, not a re-proof of the library (that is
``test_rsi_predictive.py``): auth, the route inventory, payload shape, and above
all the degraded states.

The headline boundary is ``test_an_unreadable_ledger_is_503_not_an_empty_200``.
A store that could not be read and a store with nothing in it drive opposite
operator decisions and only one of them is true, so the first must never answer
like the second. A *missing* file is genuinely empty — absence is not
corruption — and the two stay distinguishable.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

#: The router's own prefix; requests go to the full path, exactly as an operator
#: types them.
BASE = "/api/rsi/predictive"


@pytest.fixture
def rsi_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "alpha_home"
    home.mkdir()
    monkeypatch.setenv("ALPHA_HOME", str(home))
    return home


def _client() -> TestClient:
    from app.gateway.deps import get_current_user_from_request
    from app.gateway.routers.predictive_rsi import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_from_request] = lambda: SimpleNamespace(id="user-1", system_role="member")
    return TestClient(app)


# --- auth ---------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/status", "/calibration"])
def test_both_routes_require_an_authenticated_caller(rsi_home, path):
    """An unauthenticated caller must not read the deployment's telemetry."""
    from app.gateway.routers.predictive_rsi import router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as unauthenticated:
        # No dependency override: the real auth dependency runs.
        assert unauthenticated.get(f"{BASE}{path}").status_code in (401, 403)


# --- status -------------------------------------------------------------------


def test_status_reports_the_posture_and_no_promotion_authority(rsi_home):
    payload = _client().get(f"{BASE}/status").json()

    assert payload["mode"] == "observe_only"
    assert payload["influences_selection"] is False
    assert payload["promotion_authority"] is False
    assert payload["predictor"]["tier"] == 0
    assert payload["accepted_backends"]


def test_status_reports_an_empty_ledger_as_empty_not_corrupt(rsi_home):
    """A missing file is a first run, and must not read as a fault."""
    payload = _client().get(f"{BASE}/status").json()
    assert payload["ledger"]["readable"] is True
    assert payload["ledger"]["forecast_count"] == 0
    assert payload["ledger"]["load_error"] is None


def test_status_is_read_only(rsi_home):
    """Nothing here mutates: the same call twice is the same answer."""
    client = _client()
    first = client.get(f"{BASE}/status").json()
    second = client.get(f"{BASE}/status").json()
    assert first == second
    assert first["ledger"]["record_count"] == second["ledger"]["record_count"]


# --- calibration --------------------------------------------------------------


def test_calibration_reports_null_not_a_good_score_when_unscored(rsi_home):
    """The single most important distinction on this surface.

    ``brier: null`` means *not measured yet*. Rendering it as ``0`` would read as
    a perfect forecast; the server must not hand a client a flattering number.
    """
    payload = _client().get(f"{BASE}/calibration").json()

    assert payload["brier"] is None
    assert payload["calibration_error"] is None
    assert payload["calibration_known"] is False
    assert payload["scored"] == 0
    assert payload["promotion_authority"] is False
    assert "UNKNOWN" in payload["detail"]


def test_calibration_reports_a_real_number_once_a_verdict_exists(rsi_home):
    """Forecast, then a genuinely measured verdict, then a real Brier score."""
    from alpha.rsi.engine import RSIEngine
    from alpha.rsi.predictive import ForecastLedger, record_holdout_verdict

    RSIEngine().run_rsi_cycle(bottleneck="Context bloat", target_component="compaction")
    candidate_id = (ForecastLedger().forecasts() or ())[-1].candidate_id
    record_holdout_verdict(candidate_id, {"evidence_kind": "measured", "passed": 5, "failed": 0})

    payload = _client().get(f"{BASE}/calibration").json()
    assert payload["calibration_known"] is True
    assert payload["scored"] == 1
    assert payload["brier"] == pytest.approx(0.25), "the 0.5 prior forecast against a success"


def test_calibration_excludes_an_unmeasured_verdict(rsi_home):
    """A simulated verdict must not make the surface look calibrated."""
    from alpha.rsi.predictive import record_holdout_verdict

    record_holdout_verdict("cand-1", {"evidence_kind": "simulated", "passed": 5, "failed": 0})
    payload = _client().get(f"{BASE}/calibration").json()
    assert payload["calibration_known"] is False
    assert payload["brier"] is None


# --- degraded states ----------------------------------------------------------


def test_an_unreadable_ledger_is_503_not_an_empty_200(rsi_home):
    """'Could not look' must never render as 'nothing recorded'."""
    blocked = rsi_home / "rsi" / "predictions.jsonl"
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.mkdir()  # a directory at the ledger path makes the read fail

    client = _client()
    for path in ("/status", "/calibration"):
        response = client.get(f"{BASE}{path}")
        assert response.status_code == 503, f"{path} answered a broken ledger like an empty one"
        assert "unknown" in response.json()["detail"].lower()


# --- route inventory ----------------------------------------------------------


def test_the_router_exposes_only_read_verbs(rsi_home):
    """A mutating verb on this surface would exceed its stated scope."""
    from app.gateway.routers.predictive_rsi import router

    methods = {method for route in router.routes for method in route.methods}
    assert methods == {"GET"}, f"this surface is read-only by construction; found {sorted(methods)}"


def test_the_routes_are_mounted_where_documented():
    """The prefix is part of the contract an operator types."""
    from app.gateway.routers.predictive_rsi import router

    assert router.prefix == "/api/rsi/predictive"
    assert {route.path for route in router.routes} == {"/api/rsi/predictive/status", "/api/rsi/predictive/calibration"}


def test_the_harness_boundary_holds_for_the_new_router():
    """`app.*` may import `alpha.*`; never the reverse."""
    import ast

    source = Path("app/gateway/routers/predictive_rsi.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("alpha.rsi.engine"), "the route must read the service, never drive a cycle"
