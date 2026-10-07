"""The intelligence control plane (P0) and its P14 honesty contract.

What this pins
--------------
The master plan asks for one top-level answer to *Is Alpha actually becoming
more capable?* (``alpha.intelligence`` Phases A–H measure the loop; nothing
composed them into a single operational status). The control plane is that
composition — and the three failure modes it must never exhibit:

1. **A source it could not read must not become a zero.** An unreadable expert
   fabric is not "0 experts"; a degraded goal store is not "0 goals"; a corrupt
   journal is not "0 entries". Each must arrive as ``available: false`` with the
   real reason, and every metric derived from it must be ``basis:
   "unavailable"`` with ``value: None``.
2. **An unmeasured P14 metric must stay unmeasured.** Mission success rate,
   cost per success, regression rate and the rest have no aggregate owner yet.
   They are declared ``basis: "unowned"`` with the owning subsystem named —
   never defaulted to ``0``, ``0.0`` or ``false``.
3. **There is one loop-health composition, not two.** ``/health`` and the
   control plane must produce the same report for the same state; a second
   inline composition is how the two surfaces start disagreeing.

The router stays read-only: the intelligence API has no mutating method at all.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.intelligence import control_plane as control_plane_mod
from alpha.intelligence.control_plane import (
    SCHEMA_VERSION,
    UNOWNED_METRICS,
    VALID_BASIS,
    MetricReading,
    build_control_plane,
    build_loop_health_report,
)

VALID_BASIS_TUPLE = tuple(sorted(VALID_BASIS))
_METRIC_KEYS = {"name", "value", "unit", "basis", "reason", "source"}
_SECTION_KEYS = {"available", "reason", "data"}


@pytest.fixture()
def control_plane_env(tmp_path: Path) -> Iterator[Path]:
    """Point the intelligence layer at an isolated home with a real config.

    Mirrors ``test_intelligence_route_reachability.py``: the example config is
    copied (never mutated in place) so a config parse failure cannot be an
    accident of this test's environment.
    """
    import yaml

    home = tmp_path / "alpha-home"
    home.mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / "config.yaml"
    source = Path(__file__).resolve().parents[2] / "config.example.yaml"
    config_path.write_text(
        yaml.safe_dump(yaml.safe_load(source.read_text(encoding="utf-8")), allow_unicode=True),
        encoding="utf-8",
    )

    previous = {k: os.environ.get(k) for k in ("ALPHA_CONFIG_PATH", "ALPHA_HOME")}
    os.environ["ALPHA_CONFIG_PATH"] = str(config_path)
    os.environ["ALPHA_HOME"] = str(home)
    try:
        yield home
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _metrics(payload: dict) -> dict[str, dict]:
    return {m["name"]: m for m in payload["metrics"]}


class TestLoopHealthComposition:
    """``build_loop_health_report`` is the single composition both surfaces use."""

    def test_it_returns_the_contract_the_health_route_always_answered(self, control_plane_env: Path) -> None:
        result = build_loop_health_report()
        assert set(result) >= {"report", "required_subsystems", "ledger", "observed_events"}
        assert "regime" in result["report"], result["report"]
        # A real assessment (as opposed to the config-failure fallback) carries
        # the measured attempt count.
        assert "scored_attempts" in result["report"]

    def test_a_config_that_will_not_parse_is_disclosed_not_raised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom() -> None:
            raise RuntimeError("config parse failed")

        monkeypatch.setattr("alpha.intelligence.config.intelligence_config", boom)
        result = build_loop_health_report()
        # The fallback is regime=insufficient_data + the real reason. It must
        # not look like a measured assessment, which is why scored_attempts is
        # deliberately absent from this dict (build_control_plane keys on it).
        assert result["report"]["regime"] == "insufficient_data"
        assert "config parse failed" in result["report"]["reason"]
        assert "scored_attempts" not in result["report"]

    def test_an_unmeasured_loop_reports_insufficient_data_not_stable(self, control_plane_env: Path) -> None:
        # A fresh home has no scored attempts, so no regime may be claimed.
        report = build_loop_health_report()["report"]
        if report.get("scored_attempts", 0) == 0:
            assert report["regime"] == "insufficient_data"
            assert report.get("recommended_action"), "an insufficient-data verdict must still name an action"


class TestControlPlanePayload:
    def test_schema_and_sections_are_present(self, control_plane_env: Path) -> None:
        payload = build_control_plane()
        assert payload["schema_version"] == SCHEMA_VERSION
        assert SCHEMA_VERSION == "alpha.control-plane.v1"
        for key in ("mode", "loop_health", "ledger", "journal", "capability_fabric", "replay", "goals", "metrics", "summary"):
            assert key in payload, f"missing section {key!r}"

    def test_every_source_section_uses_the_disclosure_envelope(self, control_plane_env: Path) -> None:
        payload = build_control_plane()
        for key in ("mode", "loop_health", "ledger", "journal", "capability_fabric", "replay", "goals"):
            section = payload[key]
            assert _SECTION_KEYS <= set(section), f"{key} does not carry the availability envelope"
            assert isinstance(section["available"], bool)
            if not section["available"]:
                assert section["reason"], f"{key} is unavailable without naming a reason"

    def test_every_metric_declares_a_known_basis_and_a_source(self, control_plane_env: Path) -> None:
        payload = build_control_plane()
        assert payload["metrics"], "a control plane with no metrics answers nothing"
        for metric in payload["metrics"]:
            assert _METRIC_KEYS <= set(metric), metric
            assert metric["basis"] in VALID_BASIS_TUPLE, metric
            assert metric["source"], f"{metric['name']} does not name the subsystem that owns it"

    def test_a_metric_that_was_not_measured_carries_a_reason_and_a_null(self, control_plane_env: Path) -> None:
        payload = build_control_plane()
        for metric in payload["metrics"]:
            if metric["basis"] == "measured":
                continue
            assert metric["value"] is None, f"{metric['name']} ({metric['basis']}) must be null, got {metric['value']!r}"
            assert metric["reason"], f"{metric['name']} is {metric['basis']} without a reason"

    def test_summary_counts_match_the_metric_list(self, control_plane_env: Path) -> None:
        payload = build_control_plane()
        summary = payload["summary"]
        metrics = payload["metrics"]
        for basis in VALID_BASIS_TUPLE:
            expected = sum(1 for m in metrics if m["basis"] == basis)
            assert summary[basis] == expected, f"summary[{basis}]={summary.get(basis)} but metrics say {expected}"
        assert summary["total"] == len(metrics)

    def test_health_state_mirrors_the_loop_report_verbatim(self, control_plane_env: Path) -> None:
        payload = build_control_plane()
        report = payload["loop_health"]["data"]["report"] if payload["loop_health"]["available"] else None
        if report is None:
            # A loop-health composition that could not run must not invent a
            # state: "unknown" is the honest answer, not "stable".
            assert payload["summary"]["health_state"] == "unknown"
        else:
            assert payload["summary"]["health_state"] == report["regime"]

    def test_unavailable_sections_are_named_in_the_summary(self, monkeypatch: pytest.MonkeyPatch, control_plane_env: Path) -> None:
        import alpha.intelligence.expert_fabric as fabric_mod

        monkeypatch.setattr(fabric_mod, "get_expert_fabric", lambda: (_ for _ in ()).throw(RuntimeError("fabric file corrupt")))
        payload = build_control_plane()
        assert payload["summary"]["sources_unavailable"] == ["capability_fabric"]


class TestUnownedMetricsStayUnowned:
    """The P14 dashboard metrics that have no aggregate owner yet."""

    def test_the_plan_s_metrics_are_declared_rather_than_faked(self, control_plane_env: Path) -> None:
        payload = build_control_plane()
        unowned = {m["name"]: m for m in payload["metrics"] if m["basis"] == "unowned"}
        expected = {
            "mission_success_rate",
            "verified_completion_rate",
            "unverified_completion_rate",
            "recovery_success_rate",
            "cost_per_success",
            "learning_gain_per_replay",
            "regression_rate",
            "model_availability",
            "memory_health",
            "swarm_health",
            "stalled_runs",
        }
        assert expected <= set(unowned), f"missing unowned declarations: {sorted(expected - set(unowned))}"
        for name, metric in unowned.items():
            assert metric["value"] is None, name
            assert metric["reason"], name
            assert metric["source"], name

    def test_the_registry_itself_is_internally_consistent(self) -> None:
        names = [entry.name for entry in UNOWNED_METRICS]
        assert len(names) == len(set(names)), "duplicate unowned metric names"
        for entry in UNOWNED_METRICS:
            assert entry.reason and entry.source


class TestFailClosedSources:
    """Unreadable -> unavailable, never an empty/zero reading."""

    def test_an_unreadable_fabric_is_not_zero_experts(self, monkeypatch: pytest.MonkeyPatch, control_plane_env: Path) -> None:
        import alpha.intelligence.expert_fabric as fabric_mod

        def boom() -> None:
            raise RuntimeError("fabric file corrupt")

        monkeypatch.setattr(fabric_mod, "get_expert_fabric", boom)
        payload = build_control_plane()

        section = payload["capability_fabric"]
        assert section["available"] is False
        assert "fabric file corrupt" in section["reason"]

        active = _metrics(payload)["capability_active"]
        assert active["basis"] == "unavailable"
        assert active["value"] is None
        assert "fabric file corrupt" in active["reason"]

    def test_an_unreadable_journal_discloses_and_never_claims_chain_health(self, monkeypatch: pytest.MonkeyPatch, control_plane_env: Path) -> None:
        import alpha.intelligence.journal as journal_mod

        def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("journal unreadable")

        monkeypatch.setattr(journal_mod, "LearningJournal", boom)
        payload = build_control_plane()

        assert payload["journal"]["available"] is False
        assert "journal unreadable" in payload["journal"]["reason"]
        chain = _metrics(payload)["journal_chain_ok"]
        assert chain["value"] is None
        assert chain["basis"] == "unavailable"

    def test_a_degraded_goal_store_is_not_zero_goals(self, monkeypatch: pytest.MonkeyPatch, control_plane_env: Path) -> None:
        import alpha.harness.continuous.store as goal_store_mod

        class DegradedStore:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            is_degraded = True
            load_error = "goals.json is corrupt"

            def list_goals(self) -> list[object]:
                raise AssertionError("a degraded store must never be enumerated into counts")

        monkeypatch.setattr(goal_store_mod, "GoalStore", DegradedStore)
        payload = build_control_plane()

        assert payload["goals"]["available"] is False
        assert "corrupt" in payload["goals"]["reason"]
        tracked = _metrics(payload)["goals_tracked"]
        assert tracked["value"] is None
        assert tracked["basis"] == "unavailable"

    def test_one_dead_source_cannot_500_the_plane(self, monkeypatch: pytest.MonkeyPatch, control_plane_env: Path) -> None:
        """Every section is independently wrapped: one broken source discloses, the rest answer."""
        import alpha.intelligence.journal as journal_mod

        monkeypatch.setattr(journal_mod, "LearningJournal", lambda *a, **k: (_ for _ in ()).throw(ValueError("everything is broken")))
        payload = build_control_plane()
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["journal"]["available"] is False
        assert "everything is broken" in payload["journal"]["reason"]
        # Loop health reads the same journal, so it honestly reports the same
        # outage instead of pretending — but sections that do not read it still
        # answer, which is what keeps one dead source from 500ing the plane.
        assert payload["loop_health"]["available"] is False
        assert payload["mode"]["available"] is True
        assert payload["summary"]["health_state"] == "unknown"


class TestSingleComposition:
    """``/health`` and the control plane must not grow two loop-health answers."""

    def test_the_route_delegates_to_the_package_function(self) -> None:
        from app.gateway.routers import intelligence as router_mod

        source = inspect.getsource(router_mod)
        assert "build_loop_health_report" in source, "the health route must delegate, not re-compose"

    def test_the_route_and_the_plane_report_the_same_regime(self, control_plane_env: Path) -> None:
        from app.gateway.routers.intelligence import router as intelligence_router

        app = FastAPI()
        app.include_router(intelligence_router)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/intelligence/health")
        assert response.status_code == 200, response.text[:400]
        route_report = response.json()["report"]

        plane = build_control_plane()
        assert plane["loop_health"]["available"] is True
        plane_report = plane["loop_health"]["data"]["report"]
        assert route_report == plane_report, "one composition, one answer"


class TestReadOnlyRouter:
    def test_the_intelligence_router_exposes_no_mutating_method(self) -> None:
        from app.gateway.routers.intelligence import router as intelligence_router

        methods: set[str] = set()
        for route in intelligence_router.routes:
            methods |= set(getattr(route, "methods", None) or set())
        assert methods <= {"GET", "HEAD", "OPTIONS"}, f"mutating methods on the intelligence router: {sorted(methods)}"

    def test_the_control_plane_route_answers_with_its_schema(self, control_plane_env: Path) -> None:
        from app.gateway.routers.intelligence import router as intelligence_router

        app = FastAPI()
        app.include_router(intelligence_router)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/intelligence/control-plane")
        assert response.status_code == 200, response.text[:400]
        payload = response.json()
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["summary"]["health_state"] in (
            "improving",
            "stable",
            "saturating",
            "regressing",
            "insufficient_data",
            "unknown",
        )


class TestMetricReading:
    def test_a_measured_reading_serialises_its_reason_as_empty_not_missing(self) -> None:
        reading = MetricReading(name="x", value=1, unit="count", basis="measured", source="test")
        payload = reading.to_dict()
        assert payload["reason"] == ""
        assert payload["value"] == 1

    def test_an_unmeasured_reading_serialises_null(self) -> None:
        reading = MetricReading(name="x", value=0, unit="count", basis="unmeasured", reason="not measured", source="test")
        # Construction from a raw 0 must not smuggle the zero through: the
        # dataclass normalises any non-measured basis to None.
        assert reading.to_dict()["value"] is None


class TestPackageExports:
    """The module surface and the package-root lazy exports cannot drift.

    ``alpha.intelligence`` installs ``_EXPORTS`` lazily, so a name declared in
    this module's ``__all__`` but absent from the table would raise
    ``AttributeError`` for ``from alpha.intelligence import <name>`` — a
    discoverable-surface claim that is simply false at runtime.
    """

    def test_every_declared_export_resolves_through_the_package_root(self) -> None:
        import alpha.intelligence as pkg

        assert control_plane_mod.__all__, "the module declares no public surface"
        for name in control_plane_mod.__all__:
            assert name in pkg.__all__, f"{name} missing from the package's lazy export table"
            assert getattr(pkg, name) is getattr(control_plane_mod, name), f"{name} resolves to two different objects"
