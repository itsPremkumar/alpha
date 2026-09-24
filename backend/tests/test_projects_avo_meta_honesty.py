"""Honesty contract tests for the projects AVO-iterate and meta-compiler
benchmark request bodies (wave-2 audit finding F12).

``AVOIterateBody`` and ``MetaBenchmarkBody`` must carry NO defaults for
measured values: a ``POST {}`` fails with 422 instead of committing a
``VersionRecord`` (or a regression gate) built from invented numbers
(previously correctness=True / performance=0.88 / quality=0.92 /
baseline=0.80 were silently substituted by the server), and values the
caller does provide flow through to the committed record unchanged.

Harness mirrors ``test_projects_router.py`` (imported helpers: real
SQL project repo on a temp sqlite engine + TestClient + stub auth).
The AVO runner writes under ``AGENT_WORKSPACE_PROJECTS_DIR``, which is
redirected to a per-test temp dir so no repo state is touched.
"""

from __future__ import annotations

import anyio
import pytest
from fastapi.testclient import TestClient
from test_projects_router import _build_projects_app

from alpha.metacompiler import get_meta_compiler_lineage
from alpha.persistence.engine import close_engine


@pytest.fixture(autouse=True)
def _close_engine_after_test():
    yield
    anyio.run(close_engine)


def _client_with_project(tmp_path, monkeypatch, name: str):
    """Stub-authed app + client + a freshly created project id."""
    monkeypatch.setenv("AGENT_WORKSPACE_PROJECTS_DIR", str(tmp_path / "avo_projects"))
    app = _build_projects_app(tmp_path)
    client = TestClient(app)
    client.__enter__()
    created = client.post("/api/projects", json={"name": name})
    assert created.status_code == 201, created.text
    return client, created.json()["id"]


def test_avo_iterate_empty_body_is_rejected_with_422(tmp_path, monkeypatch):
    client, pid = _client_with_project(tmp_path, monkeypatch, "avo-empty")
    try:
        empty = client.post(f"/api/projects/{pid}/avo/iterate", json={})
        assert empty.status_code == 422
        locs = " ".join(str(item.get("loc")) for item in empty.json()["detail"])
        for field in ("hypothesis", "modification", "correctness", "performance_score", "quality_score"):
            assert field in locs, f"{field} accepted without a value: {locs}"

        # The pre-fix caller shape (hypothesis + modification only) must also
        # fail: the server may not default the measurements.
        partial = client.post(
            f"/api/projects/{pid}/avo/iterate",
            json={"hypothesis": "h", "modification": "m"},
        )
        assert partial.status_code == 422

        # Rejected calls committed nothing.
        lineage = client.get(f"/api/projects/{pid}/avo/lineage")
        assert lineage.status_code == 200
        assert lineage.json()["versions"] == []
    finally:
        client.__exit__(None, None, None)


def test_avo_iterate_measurements_flow_through_unchanged(tmp_path, monkeypatch):
    client, pid = _client_with_project(tmp_path, monkeypatch, "avo-flow")
    try:
        payload = {
            "hypothesis": "Reduce latency by fusing kernels",
            "modification": "matmul -> fused_kernel",
            "correctness": True,
            "performance_score": 0.41,
            "quality_score": 0.23,
        }
        created = client.post(f"/api/projects/{pid}/avo/iterate", json=payload)
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["committed"] is True
        # composite = 0.5*correctness(1.0) + 0.3*0.41 + 0.2*0.23 = 0.669.
        # It must derive from the caller's numbers alone — not from any
        # server-side default (the old defaults would have produced
        # 0.5 + 0.3*0.88 + 0.2*0.92 = 0.948).
        assert body["composite_score"] == pytest.approx(0.669)

        lineage = client.get(f"/api/projects/{pid}/avo/lineage")
        assert lineage.status_code == 200
        versions = lineage.json()["versions"]
        assert len(versions) == 1
        version = versions[0]
        assert version["version_id"] == body["version_id"]
        assert version["hypothesis"] == payload["hypothesis"]
        assert version["modification"] == payload["modification"]
        assert version["correctness"] is True
        assert version["performance_score"] == pytest.approx(0.41)
        assert version["quality_score"] == pytest.approx(0.23)
        assert version["composite_score"] == pytest.approx(0.669)
    finally:
        client.__exit__(None, None, None)


def test_avo_iterate_failed_correctness_still_requires_measurements(tmp_path, monkeypatch):
    """A failing verdict is a measurement too — it must be stated, not defaulted."""
    client, pid = _client_with_project(tmp_path, monkeypatch, "avo-fail")
    try:
        rejected = client.post(
            f"/api/projects/{pid}/avo/iterate",
            json={
                "hypothesis": "h",
                "modification": "m",
                "correctness": False,
                "performance_score": 0.1,
                "quality_score": 0.05,
            },
        )
        assert rejected.status_code == 200, rejected.text
        body = rejected.json()
        assert body["committed"] is False
        assert body["composite_score"] == 0.0

        still_missing = client.post(
            f"/api/projects/{pid}/avo/iterate",
            json={"hypothesis": "h", "modification": "m", "correctness": False},
        )
        assert still_missing.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_meta_benchmark_requires_explicit_baseline(tmp_path, monkeypatch):
    client, pid = _client_with_project(tmp_path, monkeypatch, "meta-missing")
    try:
        compiled = client.post(f"/api/projects/{pid}/meta-compiler/compile", json={})
        assert compiled.status_code == 200, compiled.text
        bp_id = compiled.json()["blueprint_id"]

        missing = client.post(
            f"/api/projects/{pid}/meta-compiler/benchmark",
            json={"blueprint_id": bp_id},
        )
        assert missing.status_code == 422
        assert "baseline_score" in str(missing.json()["detail"])

        # The rejected call recorded no scorecard against an invented gate.
        store = get_meta_compiler_lineage(pid)
        assert store.get_scorecard(bp_id) is None
    finally:
        client.__exit__(None, None, None)


def test_meta_benchmark_baseline_flows_into_regression_gate(tmp_path, monkeypatch):
    client, pid = _client_with_project(tmp_path, monkeypatch, "meta-flow")
    try:
        compiled = client.post(f"/api/projects/{pid}/meta-compiler/compile", json={})
        assert compiled.status_code == 200, compiled.text
        bp_id = compiled.json()["blueprint_id"]

        # Baseline 0.0: every non-negative composite passes the gate.
        low = client.post(
            f"/api/projects/{pid}/meta-compiler/benchmark",
            json={"blueprint_id": bp_id, "baseline_score": 0.0},
        )
        assert low.status_code == 200, low.text
        assert low.json()["preview_passed"] is True

        # Baseline 1.0: the synthetic composite (< 1.0) must fail. The gate
        # flips purely with the caller-supplied baseline — the server never
        # substitutes one.
        high = client.post(
            f"/api/projects/{pid}/meta-compiler/benchmark",
            json={"blueprint_id": bp_id, "baseline_score": 1.0},
        )
        assert high.status_code == 200, high.text
        assert high.json()["preview_passed"] is False

        stored = get_meta_compiler_lineage(pid).get_scorecard(bp_id)
        assert stored is not None
        assert stored.preview_passed is False
    finally:
        client.__exit__(None, None, None)
