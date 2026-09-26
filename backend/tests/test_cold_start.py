"""Cold-start budget: the measurement tool, the gate's contract, and the lazy conversions.

Two rules shape this file.

**No test asserts a wall-clock duration.** This host's sample spread on a single
import is measured in seconds, and an AV-scanned Windows host is worse. A test
that says "import X takes under N ms" is a time bomb that detonates on somebody
else's CI runner. What is asserted instead is *structure* -- that the probe
parses, attributes and labels correctly, and that the gate *decides* correctly
-- driven by measurements injected as data. Exactly one opt-in live measurement
test exists (``test_live_cold_start_measurement``, skipped unless
``AGENT_WORKSPACE_RUN_LIVE_TESTS=1``) for a human to run deliberately.

**The gate must be able to fail.** Every safety property in
``check_cold_start_budget.py`` is tested by feeding it a broken input and
asserting a non-zero exit: a missing baseline, a corrupt one, a probe that
crashes. A gate that cannot demonstrate its own failure is a gate whose green
means nothing.

The lazy-conversion tests are the other half: they prove the public import
surface of every converted module is unchanged, by comparing it against the
names the pre-conversion module exported.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = REPO_ROOT / "scripts" / "cold_start_probe.py"
GATE_PATH = REPO_ROOT / "scripts" / "check_cold_start_budget.py"
BASELINE_PATH = REPO_ROOT / "backend" / "benchmarks" / "cold_start" / "baseline.json"


def _load(path: Path, name: str) -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _in_fresh_interpreter(body: str, env: dict[str, str] | None = None) -> str:
    """Run ``body`` in a brand-new interpreter and return its stdout.

    "Module X does not import Y" is only meaningful in an interpreter where
    nothing has been imported yet. Inside a pytest session an earlier test has
    usually already pulled the very modules in question into ``sys.modules``,
    so the assertion would be testing test ordering, not the import graph.
    """
    child_env = dict(os.environ)
    if env is not None:
        # An explicit env replaces the ambient one wholesale, so an inherited
        # AGENT_WORKSPACE_* can never leak into the measurement.
        child_env = env
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", body],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env,
        check=False,
    )
    assert proc.returncode == 0, f"fresh interpreter failed:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout.strip()


#: The minimum AppConfig accepts (``sandbox`` and one ``models`` entry are the
#: required fields; the shape mirrors tests/test_app_config_reload.py). Written
#: to tmp_path by the tests that need a resolvable config, so they stay hermetic
#: and never touch the developer's real config.yaml.
_MINIMAL_CONFIG = """\
sandbox:
  use: alpha.sandbox.local:LocalSandboxProvider
models:
  - name: test-model
    use: langchain_openai:ChatOpenAI
    model: gpt-test
"""


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    """A config path and runtime home that cannot reach outside ``tmp_path``.

    Only ``AGENT_WORKSPACE_CONFIG_PATH`` is set: naming an extensions path
    asserts that file exists, and leaving it unset exercises the ordinary
    unconfigured fallback instead.
    """
    config = tmp_path / "config.yaml"
    config.write_text(_MINIMAL_CONFIG, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if key != "AGENT_WORKSPACE_EXTENSIONS_CONFIG_PATH"}
    env.update({"AGENT_WORKSPACE_CONFIG_PATH": str(config), "AGENT_WORKSPACE_HOME": str(home)})
    return env


@pytest.fixture(scope="module")
def probe() -> Any:
    return _load(PROBE_PATH, "alpha_cold_start_probe_under_test")


@pytest.fixture(scope="module")
def gate() -> Any:
    return _load(GATE_PATH, "alpha_cold_start_gate_under_test")


# --------------------------------------------------------------------------
# fixtures: synthetic measurements, injected as data
# --------------------------------------------------------------------------


def _stats(samples: list[float]) -> dict[str, Any]:
    ordered = sorted(samples)
    median = ordered[len(ordered) // 2] if len(ordered) % 2 else (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2
    return {
        "runs": len(ordered),
        "min_ms": ordered[0],
        "median_ms": median,
        "max_ms": ordered[-1],
        "spread_ms": ordered[-1] - ordered[0],
        "samples_ms": list(ordered),
    }


def _measurement(
    targets: dict[str, tuple[list[float], list[float]]] | None = None,
    attribution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    targets = targets or {"alpha": ([100.0, 100.0, 100.0], [80.0, 80.0, 80.0])}
    return {
        "schema_version": 1,
        "probe_version": "1.0.0",
        "tool": "cold_start_probe",
        "measurement": {"repeats": 3, "timeout_seconds": 900.0, "attribution_top_n": 25, "attribution_mode": "cold"},
        "host": {"platform": "test", "python_version": "3.12.0", "is_windows": False, "operator_label": ""},
        "targets": [
            {
                "module": name,
                "cold": _stats(cold),
                "warm": _stats(warm),
                "cold_minus_warm_ms": (sum(cold) / len(cold)) - (sum(warm) / len(warm)),
                "attribution": attribution,
                "attribution_mode": "cold" if attribution else "none",
            }
            for name, (cold, warm) in targets.items()
        ],
    }


def _baseline(
    targets: dict[str, tuple[float, float]] | None = None,
    module_budgets: dict[str, float] | None = None,
    tolerance_ms: float = 100.0,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "tool": "check_cold_start_budget",
        "probe_version": "1.0.0",
        "host": {},
        "measurement": {"repeats": 3, "timeout_seconds": 900.0, "attribution_mode": "cold"},
        "tolerance_ms": tolerance_ms,
        "targets": {name: {"cold_ms": cold, "warm_ms": warm, "module_count": None} for name, (cold, warm) in (targets or {"alpha": (100.0, 80.0)}).items()},
        "module_budgets": {name: {"self_ms": value, "observed_in": "alpha"} for name, value in (module_budgets or {}).items()},
    }


# --------------------------------------------------------------------------
# probe: parsing and attribution structure
# --------------------------------------------------------------------------


class TestProbeParsing:
    def test_importtime_rows_parse_to_self_and_cumulative(self, probe: Any) -> None:
        # Indentation carries the import-stack depth, which is NOT a tree.
        text = "\n".join(
            [
                "import time: self [us] | cumulative | imported package",
                "import time:      100 |       100 | winreg",
                "import time:       50 |      450 |   alpha",
                "import time:      200 |      200 |     alpha.config",
                "import time:      100 |      100 |   other",
            ]
        )
        timings = probe.parse_importtime(text)
        assert timings["winreg"] == (100, 100)
        assert timings["alpha"] == (50, 450)
        assert timings["alpha.config"] == (200, 200)
        assert timings["other"] == (100, 100)

    def test_builtin_and_frozen_suffixes_are_stripped(self, probe: Any) -> None:
        text = "import time:       10 |        10 |   _frozen_importlib (frozen)\nimport time:       20 |        20 | zipimport (built-in)"
        timings = probe.parse_importtime(text)
        assert "_frozen_importlib" in timings
        assert "zipimport" in timings

    def test_non_importtime_lines_are_ignored(self, probe: Any) -> None:
        text = "Traceback (most recent call last):\nValueError: boom\nimport time:       7 |        7 | alpha\n"
        assert probe.parse_importtime(text) == {"alpha": (7, 7)}

    def test_edges_round_trip_through_the_sentinel(self, probe: Any) -> None:
        payload = json.dumps([["alpha.config", "alpha.config.app_config"], [None, "alpha.config.memory_config"]])
        stdout = f"some noise\n{probe._EDGES_SENTINEL}{payload}\n"
        assert probe.parse_edges(stdout) == [
            ("alpha.config", "alpha.config.app_config"),
            (None, "alpha.config.memory_config"),
        ]

    def test_missing_sentinel_yields_no_edges_rather_than_raising(self, probe: Any) -> None:
        assert probe.parse_edges("nothing here") == []

    def test_malformed_edge_payload_yields_no_edges(self, probe: Any) -> None:
        assert probe.parse_edges(f"{probe._EDGES_SENTINEL}{{not json") == []


class TestProbeGraphHelpers:
    def test_reachable_is_closed_over_real_edges(self, probe: Any) -> None:
        edges = [("a", "a.b"), ("a.b", "a.b.c"), ("x", "y")]
        assert probe._reachable("a", edges) == {"a", "a.b", "a.b.c"}

    def test_reachable_survives_a_cycle(self, probe: Any) -> None:
        edges = [("a", "a.b"), ("a.b", "a")]
        assert probe._reachable("a", edges) == {"a", "a.b"}

    def test_ancestors_are_outermost_first(self, probe: Any) -> None:
        edges = [("a", "a.b"), ("a.b", "a.b.c")]
        assert probe._ancestors("a.b.c", edges) == ["a", "a.b"]

    def test_ancestors_empty_for_a_root(self, probe: Any) -> None:
        assert probe._ancestors("a", [("a", "a.b")]) == []


class TestProbeKinds:
    @pytest.mark.parametrize(
        ("module", "kind"),
        [
            ("alpha", "harness"),
            ("alpha.config.memory_config", "harness"),
            ("app.gateway.app", "app"),
            ("os", "stdlib"),
            ("sqlalchemy.orm", "third_party"),
            ("pydantic", "third_party"),
        ],
    )
    def test_module_kind_classification(self, probe: Any, module: str, kind: str) -> None:
        assert probe.module_kind(module) == kind

    def test_json_shape_is_stable_across_two_identical_runs(self, probe: Any) -> None:
        """Same numbers must serialize to byte-identical JSON.

        Timings are allowed to differ run to run; the *shape* is not, so a diff
        between two runs is readable instead of noisy.
        """
        measurement = _measurement(attribution={"module_count": 3, "hotspots": []})
        first = json.dumps(measurement, indent=2)
        second = json.dumps(_measurement(attribution={"module_count": 3, "hotspots": []}), indent=2)
        assert first == second

    def test_probe_declares_a_schema_version(self, probe: Any) -> None:
        assert isinstance(probe.SCHEMA_VERSION, int)

    def test_rounding_keeps_sub_tenth_of_a_millisecond_precision(self, probe: Any) -> None:
        assert probe.round_ms(1.23456) == 1.2
        assert probe.round_ms(51717.638) == 51717.6


# --------------------------------------------------------------------------
# gate: decision logic against injected measurements
# --------------------------------------------------------------------------


class TestGateDecisionLogic:
    def test_identical_measurement_passes(self, gate: Any) -> None:
        result = gate.compare(_measurement(), _baseline(), None)
        assert result["status"] == "pass"
        assert not result["regressions"]

    def test_regression_beyond_tolerance_fails(self, gate: Any) -> None:
        # budget 100, tolerance 100 -> 400 is a genuine regression
        result = gate.compare(_measurement({"alpha": ([400.0, 400.0, 400.0], [80.0, 80.0, 80.0])}), _baseline(), None)
        assert result["status"] == "fail"
        assert [v["name"] for v in result["regressions"]] == ["alpha[cold]"]

    def test_regression_inside_jitter_does_not_fail(self, gate: Any) -> None:
        """A big excess, but smaller than this run's own spread, is noise."""
        noisy = _measurement({"alpha": ([100.0, 200.0, 400.0], [80.0, 80.0, 80.0])})
        result = gate.compare(noisy, _baseline(tolerance_ms=100.0), None)
        assert noisy["targets"][0]["cold"]["median_ms"] == 200.0
        assert result["status"] == "pass"
        assert not result["regressions"]

    def test_jitter_exactly_at_spread_does_not_fail(self, gate: Any) -> None:
        # median 200, budget 100 -> excess 100; spread 100. Needs excess > spread.
        samples = [100.0, 200.0, 300.0]
        result = gate.compare(_measurement({"alpha": (samples, [80.0, 80.0, 80.0])}), _baseline(tolerance_ms=100.0), None)
        assert result["status"] == "pass"

    def test_regression_outside_jitter_fails_even_with_spread(self, gate: Any) -> None:
        samples = [100.0, 900.0, 2000.0]  # median 900, spread 1900
        result = gate.compare(_measurement({"alpha": (samples, [80.0, 80.0, 80.0])}), _baseline(tolerance_ms=100.0), None)
        # median 900 > 100+100 and 800 > 1900 is false -> not a regression.
        assert result["status"] == "pass"

    def test_clear_regression_with_some_spread_fails(self, gate: Any) -> None:
        samples = [800.0, 850.0, 900.0]  # median 850, spread 100
        result = gate.compare(_measurement({"alpha": (samples, [80.0, 80.0, 80.0])}), _baseline(tolerance_ms=100.0), None)
        assert result["status"] == "fail"
        assert result["regressions"][0]["excess_ms"] == 750.0

    def test_cli_tolerance_overrides_the_baseline(self, gate: Any) -> None:
        result = gate.compare(_measurement(), _baseline(tolerance_ms=10.0), 1000.0)
        assert result["status"] == "pass"
        assert all(v["tolerance_ms"] == 1000.0 for v in result["verdicts"])

    def test_improvement_is_reported_not_auto_applied(self, gate: Any) -> None:
        result = gate.compare(_measurement({"alpha": ([10.0, 10.0, 10.0], [5.0, 5.0, 5.0])}), _baseline(), 10.0)
        assert result["status"] == "pass"
        assert [v["name"] for v in result["improvements"]] == ["alpha[cold]", "alpha[warm]"]
        # The baseline is not part of the verdict payload and was not mutated.
        assert "targets" not in result

    def test_an_improvement_inside_tolerance_is_not_reported_as_one(self, gate: Any) -> None:
        """90 ms better than a 100 ms budget under a 100 ms tolerance: not news."""
        result = gate.compare(_measurement({"alpha": ([10.0, 10.0, 10.0], [5.0, 5.0, 5.0])}), _baseline(), 100.0)
        assert result["status"] == "pass"
        assert not result["improvements"]

    def test_warm_mode_is_gated_independently_of_cold(self, gate: Any) -> None:
        result = gate.compare(_measurement({"alpha": ([100.0, 100.0, 100.0], [900.0, 900.0, 900.0])}), _baseline(), None)
        assert [v["name"] for v in result["regressions"]] == ["alpha[warm]"]

    def test_spread_and_samples_are_reported_for_every_verdict(self, gate: Any) -> None:
        result = gate.compare(_measurement(), _baseline(), None)
        for verdict in result["verdicts"]:
            assert "spread_ms" in verdict
            assert "samples_ms" in verdict
            assert "tolerance_ms" in verdict

    def test_unmeasured_baseline_target_fails_closed(self, gate: Any) -> None:
        """A budgeted target the probe did not measure is a failure."""
        result = gate.compare(_measurement({"alpha": ([100.0, 100.0, 100.0], [80.0, 80.0, 80.0])}), _baseline({"alpha": (100.0, 80.0), "alpha.tools.builtins": (10.0, 10.0)}), None)
        assert result["status"] == "fail"
        assert [v["name"] for v in result["unmeasured"]] == ["alpha.tools.builtins"]

    def test_unbudgeted_target_is_reported_but_does_not_fail(self, gate: Any) -> None:
        result = gate.compare(
            _measurement(
                {
                    "alpha": ([100.0, 100.0, 100.0], [80.0, 80.0, 80.0]),
                    "brand.new": ([100.0, 100.0, 100.0], [80.0, 80.0, 80.0]),
                }
            ),
            _baseline(),
            None,
        )
        assert result["status"] == "pass"
        assert [v["name"] for v in result["verdicts"] if v["status"] == "unbudgeted"] == [
            "brand.new[cold]",
            "brand.new[warm]",
        ]

    def test_per_module_budget_regression_fails(self, gate: Any) -> None:
        attribution = {
            "module_count": 2,
            "hotspots": [{"module": "sqlalchemy.orm", "self_ms": 900.0, "cumulative_ms": 900.0, "kind": "third_party"}],
            "modules": [{"module": "sqlalchemy.orm", "self_ms": 900.0, "cumulative_ms": 900.0, "kind": "third_party"}],
        }
        result = gate.compare(
            _measurement(attribution=attribution),
            _baseline(module_budgets={"sqlalchemy.orm": 100.0}, tolerance_ms=100.0),
            None,
        )
        assert result["status"] == "fail"
        assert "sqlalchemy.orm" in [v["name"] for v in result["regressions"]]

    def test_per_module_budget_within_tolerance_passes(self, gate: Any) -> None:
        attribution = {
            "module_count": 2,
            "hotspots": [{"module": "sqlalchemy.orm", "self_ms": 150.0, "cumulative_ms": 150.0, "kind": "third_party"}],
            "modules": [{"module": "sqlalchemy.orm", "self_ms": 150.0, "cumulative_ms": 150.0, "kind": "third_party"}],
        }
        result = gate.compare(
            _measurement(attribution=attribution),
            _baseline(module_budgets={"sqlalchemy.orm": 100.0}, tolerance_ms=100.0),
            None,
        )
        assert result["status"] == "pass"

    def test_module_budget_outside_the_top_n_is_informational_not_a_failure(self, gate: Any) -> None:
        """A budgeted module that fell out of the attribution top-N got cheaper.

        Failing on it would make the verdict depend on which modules happened to
        be expensive this run, which is noise, not a regression.
        """
        result = gate.compare(
            _measurement(attribution={"module_count": 1, "hotspots": [], "modules": []}),
            _baseline(module_budgets={"sqlalchemy.orm": 100.0}, tolerance_ms=100.0),
            None,
        )
        assert result["status"] == "pass"
        assert [v["name"] for v in result["not_observed"]] == ["sqlalchemy.orm"]
        assert not result["unmeasured"]

    def test_every_budgeted_target_and_mode_produces_a_verdict(self, gate: Any) -> None:
        """Regression: a key-name mismatch once skipped every target verdict, so a
        real target regression rode through on module budgets alone."""
        result = gate.compare(_measurement(), _baseline(), None)
        names = {v["name"] for v in result["verdicts"]}
        assert "alpha[cold]" in names
        assert "alpha[warm]" in names

    def test_target_regression_is_caught_even_with_no_attribution(self, gate: Any) -> None:
        """The target verdict must stand on its own, with no module budgets."""
        result = gate.compare(
            _measurement({"alpha": ([900.0, 900.0, 900.0], [80.0, 80.0, 80.0])}),
            _baseline(module_budgets={}, tolerance_ms=100.0),
            None,
        )
        assert result["status"] == "fail"
        assert [v["name"] for v in result["regressions"]] == ["alpha[cold]"]


class TestGateTolerancePolicy:
    def test_default_tolerance_is_fifteen_percent_within_the_documented_band(self, gate: Any) -> None:
        # 15% of 10 s is 1500 ms, inside [400, 5000].
        assert gate.default_tolerance(10_000.0) == 1500.0
        # 15% of 1 s is 150 ms, below the floor, so the floor wins.
        assert gate.default_tolerance(1_000.0) == gate.TOLERANCE_FLOOR_MS
        assert gate.default_tolerance(0.0) == gate.TOLERANCE_FLOOR_MS

    def test_default_tolerance_is_monotonic_across_the_band(self, gate: Any) -> None:
        values = [gate.default_tolerance(float(b)) for b in (0, 1_000, 10_000, 33_000, 1_000_000)]
        assert values == sorted(values)

    def test_default_tolerance_is_capped(self, gate: Any) -> None:
        assert gate.default_tolerance(10_000_000.0) == gate.TOLERANCE_CEILING_MS

    def test_documented_default_is_positive(self, gate: Any) -> None:
        assert gate.DEFAULT_TOLERANCE_MS > 0


# --------------------------------------------------------------------------
# gate: fail-closed behaviour
# --------------------------------------------------------------------------


class TestGateFailsClosed:
    def test_missing_baseline_raises(self, gate: Any, tmp_path: Path) -> None:
        with pytest.raises(gate.GateError) as excinfo:
            gate.load_baseline(tmp_path / "nope.json")
        assert "not found" in str(excinfo.value)

    def test_corrupt_baseline_raises(self, gate: Any, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(gate.GateError) as excinfo:
            gate.load_baseline(path)
        assert "unreadable" in str(excinfo.value)

    def test_wrong_schema_version_raises(self, gate: Any, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps({**_baseline(), "schema_version": 99}), encoding="utf-8")
        with pytest.raises(gate.GateError) as excinfo:
            gate.load_baseline(path)
        assert "schema_version" in str(excinfo.value)

    def test_baseline_without_targets_raises(self, gate: Any, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        data = _baseline()
        del data["targets"]
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(gate.GateError):
            gate.load_baseline(path)

    def test_non_measurement_json_raises(self, gate: Any, tmp_path: Path) -> None:
        path = tmp_path / "m.json"
        path.write_text(json.dumps({"tool": "something-else"}), encoding="utf-8")
        with pytest.raises(gate.GateError) as excinfo:
            gate.read_measurement(path.read_text(encoding="utf-8"), "", path)
        assert "cold_start_probe" in str(excinfo.value)

    def test_measurement_without_targets_raises(self, gate: Any, tmp_path: Path) -> None:
        payload = json.dumps({**_measurement(), "targets": []})
        path = tmp_path / "m.json"
        path.write_text(payload, encoding="utf-8")
        with pytest.raises(gate.GateError) as excinfo:
            gate.read_measurement(payload, "", path)
        assert "no targets" in str(excinfo.value)

    def test_measurement_missing_a_mode_raises(self, gate: Any, tmp_path: Path) -> None:
        payload = json.dumps(_measurement())
        data = json.loads(payload)
        del data["targets"][0]["warm"]["median_ms"]
        path = tmp_path / "m.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(gate.GateError) as excinfo:
            gate.read_measurement(json.dumps(data), "", path)
        assert "warm" in str(excinfo.value)

    @pytest.mark.parametrize("bad", [b"", b"not json at all", b"[1, 2, 3]"])
    def test_unparseable_measurement_raises(self, gate: Any, bad: bytes, tmp_path: Path) -> None:
        path = tmp_path / "m.json"
        path.write_bytes(bad)
        with pytest.raises(gate.GateError):
            gate.read_measurement(path.read_text(encoding="utf-8", errors="replace"), "", path)

    def test_cli_exits_two_when_the_measurement_is_unreadable(self, tmp_path: Path) -> None:
        """End-to-end: a measurement the gate cannot parse must not read as green."""
        measurement = tmp_path / "measurement.json"
        measurement.write_text(json.dumps(_measurement()), encoding="utf-8")
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps(_baseline()), encoding="utf-8")
        ok = subprocess.run(
            [sys.executable, str(GATE_PATH), "--baseline", str(baseline), "--measurement", str(measurement), "--tolerance-ms", "100"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=False,
        )
        assert ok.returncode == 0, ok.stdout + ok.stderr

        broken = tmp_path / "broken.json"
        broken.write_text("{ truncated", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(GATE_PATH), "--baseline", str(baseline), "--measurement", str(broken)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=False,
        )
        assert proc.returncode == 2
        assert "FAILED CLOSED" in proc.stderr

    def test_cli_exits_two_on_missing_baseline(self, tmp_path: Path) -> None:
        measurement = tmp_path / "measurement.json"
        measurement.write_text(json.dumps(_measurement()), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(GATE_PATH), "--baseline", str(tmp_path / "absent.json"), "--measurement", str(measurement)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=False,
        )
        assert proc.returncode == 2
        assert "FAILED CLOSED" in proc.stderr
        assert "baseline not found" in proc.stderr

    def test_cli_exits_one_on_injected_regression(self, tmp_path: Path) -> None:
        """The negative demonstration, as an automated check."""
        measurement = tmp_path / "measurement.json"
        measurement.write_text(json.dumps(_measurement({"alpha": ([900.0, 900.0, 900.0], [80.0, 80.0, 80.0])})), encoding="utf-8")
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps(_baseline()), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(GATE_PATH), "--baseline", str(baseline), "--measurement", str(measurement), "--tolerance-ms", "100"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=False,
        )
        assert proc.returncode == 1
        assert "REGRESSED" in proc.stdout
        assert "alpha[cold]" in proc.stdout

    def test_write_baseline_does_not_run_on_a_plain_check(self, tmp_path: Path) -> None:
        """A check must never rewrite the baseline it is checking against."""
        measurement = tmp_path / "measurement.json"
        measurement.write_text(json.dumps(_measurement()), encoding="utf-8")
        baseline = tmp_path / "baseline.json"
        original = json.dumps(_baseline())
        baseline.write_text(original, encoding="utf-8")
        subprocess.run(
            [sys.executable, str(GATE_PATH), "--baseline", str(baseline), "--measurement", str(measurement), "--tolerance-ms", "100"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=False,
        )
        assert baseline.read_text(encoding="utf-8") == original


class TestBaselineBuild:
    def test_build_baseline_records_target_medians(self, gate: Any) -> None:
        built = gate.build_baseline(_measurement({"alpha": ([100.0, 100.0, 100.0], [80.0, 80.0, 80.0])}), 2500.0)
        assert built["schema_version"] == gate.BASELINE_SCHEMA
        assert built["targets"]["alpha"]["cold_ms"] == 100.0
        assert built["targets"]["alpha"]["warm_ms"] == 80.0
        assert built["tolerance_ms"] == 2500.0

    def test_build_baseline_collects_module_budgets_from_hotspots(self, gate: Any) -> None:
        attribution = {
            "module_count": 2,
            "hotspots": [{"module": "sqlalchemy.orm", "self_ms": 42.0, "cumulative_ms": 42.0, "kind": "third_party"}],
        }
        built = gate.build_baseline(_measurement(attribution=attribution), 2500.0)
        assert built["module_budgets"]["sqlalchemy.orm"]["self_ms"] == 42.0

    def test_build_baseline_is_json_serializable_and_reloadable(self, gate: Any, tmp_path: Path) -> None:
        built = gate.build_baseline(_measurement(), 2500.0)
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps(built, indent=2), encoding="utf-8")
        reloaded = gate.load_baseline(path)
        assert reloaded["targets"].keys() == built["targets"].keys()


# --------------------------------------------------------------------------
# the committed baseline itself
# --------------------------------------------------------------------------


class TestCommittedBaseline:
    def test_baseline_exists_and_is_loadable(self, gate: Any) -> None:
        assert BASELINE_PATH.exists(), "backend/benchmarks/cold_start/baseline.json is the committed reference; without it the gate cannot run and the property is not enforced"
        baseline = gate.load_baseline(BASELINE_PATH)
        assert baseline["targets"], "baseline has no targets"

    def test_baseline_covers_every_required_target(self, probe: Any, gate: Any) -> None:
        baseline = gate.load_baseline(BASELINE_PATH)
        for module in probe.DEFAULT_TARGETS:
            assert module in baseline["targets"], f"baseline does not budget {module}"

    def test_baseline_budgets_are_positive_numbers(self, gate: Any) -> None:
        baseline = gate.load_baseline(BASELINE_PATH)
        for name, budget in baseline["targets"].items():
            for mode in ("cold_ms", "warm_ms"):
                value = budget.get(mode)
                assert isinstance(value, (int, float)) and value > 0, f"{name}.{mode} = {value!r}"

    def test_baseline_records_the_host_it_was_taken_on(self, gate: Any) -> None:
        """A baseline without host conditions cannot be read honestly."""
        baseline = gate.load_baseline(BASELINE_PATH)
        assert baseline.get("host"), "baseline must record the host conditions it was measured on"
        assert "platform" in baseline["host"]


# --------------------------------------------------------------------------
# --validate: the deterministic half of a measurement gate
# --------------------------------------------------------------------------


class TestBaselineValidation:
    """``--validate`` is what runs on the blocking path, so it is the part that
    has to be airtight: it must catch a baseline that cannot produce a verdict,
    and it must never be a way to move a budget."""

    def test_the_committed_baseline_validates(self, probe: Any, gate: Any) -> None:
        assert gate.validate(gate.load_baseline(BASELINE_PATH), list(probe.DEFAULT_TARGETS)) == []

    def test_validation_catches_a_target_the_baseline_forgot(self, gate: Any) -> None:
        problems = gate.validate(_baseline(), ["alpha", "alpha.brand.new"])
        assert any("alpha.brand.new" in problem for problem in problems)

    def test_validation_catches_a_target_the_probe_stopped_measuring(self, gate: Any) -> None:
        problems = gate.validate(_baseline({"retired": (10.0, 10.0)}), ["alpha"])
        assert any("retired" in problem for problem in problems)

    @pytest.mark.parametrize("key", ["cold_ms", "warm_ms"])
    def test_validation_catches_a_non_positive_budget(self, key: str, gate: Any) -> None:
        broken = _baseline()
        broken["targets"]["alpha"][key] = 0
        problems = gate.validate(broken, ["alpha"])
        assert any(key in problem and "positive" in problem for problem in problems)

    def test_validation_catches_an_empty_module_budget_set(self, gate: Any) -> None:
        problems = gate.validate(_baseline(module_budgets={}), ["alpha"])
        assert any("module budgets" in problem for problem in problems)

    def test_validation_catches_a_non_positive_module_budget(self, gate: Any) -> None:
        problems = gate.validate(_baseline(module_budgets={"sqlalchemy.orm": 0.0}), ["alpha"])
        assert any("sqlalchemy.orm" in problem for problem in problems)

    def test_validation_catches_a_missing_host_record(self, gate: Any) -> None:
        broken = _baseline()
        broken["host"] = {}
        assert any("host" in problem for problem in gate.validate(broken, ["alpha"]))

    def test_validation_catches_a_non_positive_tolerance(self, gate: Any) -> None:
        problems = gate.validate(_baseline(tolerance_ms=0.0), ["alpha"])
        assert any("tolerance" in problem for problem in problems)

    def test_a_zero_tolerance_baseline_is_refused_at_load(self, gate: Any, tmp_path: Path) -> None:
        """0.0 is falsy, so every `or DEFAULT_TOLERANCE_MS` read would widen it."""
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps(_baseline(tolerance_ms=0.0)), encoding="utf-8")
        with pytest.raises(gate.GateError, match="tolerance_ms"):
            gate.load_baseline(path)

    def test_validate_never_mutates_the_baseline(self, gate: Any) -> None:
        """A validation pass must not be able to loosen anything."""
        before = _baseline()
        snapshot = json.dumps(before, sort_keys=True)
        gate.validate(before, ["alpha"])
        assert json.dumps(before, sort_keys=True) == snapshot

    def test_cli_validate_exits_zero_on_the_committed_baseline(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(GATE_PATH), "--validate"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "baseline OK" in proc.stdout
        assert "cold median" not in proc.stdout, "--validate must not measure anything"

    def test_cli_validate_exits_two_on_a_broken_baseline(self, tmp_path: Path) -> None:
        broken = tmp_path / "baseline.json"
        payload = _baseline()
        payload["targets"]["alpha"]["cold_ms"] = 0
        broken.write_text(json.dumps(payload), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(GATE_PATH), "--baseline", str(broken), "--validate"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=False,
        )
        assert proc.returncode == 2
        assert "FAILED VALIDATION" in proc.stderr
        assert "cold_ms" in proc.stderr

    def test_cli_validate_exits_two_on_a_missing_baseline(self, tmp_path: Path) -> None:
        proc = subprocess.run(
            [sys.executable, str(GATE_PATH), "--baseline", str(tmp_path / "absent.json"), "--validate"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=False,
        )
        assert proc.returncode == 2
        assert "FAILED CLOSED" in proc.stderr


# --------------------------------------------------------------------------
# lazy conversions: the public import surface is unchanged
# --------------------------------------------------------------------------


class TestLazyConversionPublicSurface:
    """These fail if a conversion breaks an import any existing caller performs.

    Each expected name list is the package's *documented* surface, and each is
    exercised with the real import forms callers use: ``from pkg import name``,
    ``from pkg import submodule``, and ``getattr``.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "get_app_config",
            "SkillEvolutionConfig",
            "Paths",
            "get_paths",
            "SkillsConfig",
            "ExtensionsConfig",
            "get_extensions_config",
            "LoopDetectionConfig",
            "MemoryConfig",
            "get_memory_config",
            "get_tracing_config",
            "get_explicitly_enabled_tracing_providers",
            "get_enabled_tracing_providers",
            "is_monocle_tracing_enabled",
            "is_tracing_enabled",
            "validate_enabled_tracing_providers",
        ],
    )
    def test_alpha_config_still_exports_its_documented_names(self, name: str) -> None:
        module = importlib.import_module("alpha.config")
        assert name in module.__all__, f"alpha.config.__all__ lost {name}"
        assert getattr(module, name) is not None
        # The form callers actually write.
        assert getattr(importlib.import_module("alpha.config"), name) is not None

    @pytest.mark.parametrize(
        "submodule",
        [
            "app_config",
            "extensions_config",
            "loop_detection_config",
            "memory_config",
            "paths",
            "skill_evolution_config",
            "skills_config",
            "tracing_config",
        ],
    )
    def test_alpha_config_still_imports_its_former_eager_submodules(self, submodule: str) -> None:
        """`from alpha.config import paths` must keep working.

        Callers use this form. It resolved before because the eager
        `from .paths import ...` bound the submodule as a side effect, and it
        must still resolve now that the eager import is gone.
        """
        imported = importlib.import_module(f"alpha.config.{submodule}")
        assert imported.__name__ == f"alpha.config.{submodule}"
        # And through the from-import form a caller would write. This is the
        # exact mechanism CPython uses for `from pkg import submodule`: try the
        # attribute, then import the submodule. It resolves for siblings the old
        # eager imports never bound, which is why this must not depend on them.
        resolved = importlib.__import__("alpha.config", fromlist=[submodule])
        assert getattr(resolved, submodule) is imported

    def test_alpha_config_lazy_install_does_not_widen_the_namespace(self) -> None:
        """A sibling the old eager imports never bound stays unbound as an attribute.

        `from alpha.config import acp_config` still works, but only through
        CPython's own from-list submodule fallback, which is unchanged by this
        work. Must run in a fresh interpreter: once anything in the session has
        imported `acp_config`, Python binds it on the parent package and the
        attribute would exist for reasons unrelated to this package.
        """
        out = _in_fresh_interpreter("import alpha.config as c\nprint('bound', hasattr(c, 'acp_config'))\n")
        assert out == "bound False", f"alpha.config.acp_config became a bound attribute, widening the package namespace beyond the pre-conversion surface (got: {out})"

    def test_alpha_config_from_import_still_works_for_never_bound_siblings(self) -> None:
        """The from-import form must keep working for every sibling, bound or not."""
        out = _in_fresh_interpreter("from alpha.config import acp_config, paths, app_config\nprint(acp_config.__name__, paths.__name__, app_config.__name__)\n")
        assert out == "alpha.config.acp_config alpha.config.paths alpha.config.app_config"

    def test_alpha_config_leaf_import_does_not_drag_the_shared_schema(self) -> None:
        """The whole point: a leaf no longer pays for AppConfig and the ORM.

        This is the regression that would undo the conversion. Asserted in a
        fresh interpreter so the answer is about the import graph and not about
        what an earlier test happened to load.
        """
        out = _in_fresh_interpreter(
            "import sys\n"
            "import alpha.config.memory_config\n"
            "print('app_config', 'alpha.config.app_config' in sys.modules)\n"
            "print('sqlalchemy', any(m == 'sqlalchemy' or m.startswith('sqlalchemy.') for m in sys.modules))\n"
            "print('config_modules', sum(1 for m in sys.modules if m.startswith('alpha.config.')))\n"
        )
        assert "app_config False" in out, f"leaf import pulled in app_config:\n{out}"
        assert "sqlalchemy False" in out, f"leaf import pulled in the ORM:\n{out}"
        assert "config_modules 1" in out, f"leaf import pulled in extra config modules:\n{out}"

    def test_alpha_config_get_app_config_still_works_end_to_end(self, tmp_path: Path) -> None:
        """The resolver must not just exist; it must return a real AppConfig."""
        out = _in_fresh_interpreter(
            "from alpha.config import get_app_config\ncfg = get_app_config()\nprint('type', type(cfg).__name__)\nprint('model', cfg.models[0].name)\n",
            env=_isolated_env(tmp_path),
        )
        assert "type AppConfig" in out, out
        assert "model test-model" in out, out

    def test_alpha_config_memory_config_resolver_still_works_end_to_end(self, tmp_path: Path) -> None:
        out = _in_fresh_interpreter(
            "from alpha.config import get_memory_config\nprint('enabled', get_memory_config().enabled)\n",
            env=_isolated_env(tmp_path),
        )
        assert out.startswith("enabled "), out

    def test_unknown_attribute_still_raises_attribute_error(self) -> None:
        module = importlib.import_module("alpha.config")
        with pytest.raises(AttributeError):
            module.definitely_not_a_real_export  # noqa: B018 - asserting the raise

    def test_alpha_config_dir_advertises_the_public_names(self) -> None:
        module = importlib.import_module("alpha.config")
        listed = set(dir(module))
        assert set(module.__all__) <= listed


class TestExtensionLoaderCycleBreak:
    """`alpha.extensions.loader` no longer imports the ORM at module scope."""

    def test_loader_does_not_import_sqlalchemy_at_module_scope(self) -> None:
        out = _in_fresh_interpreter("import sys\nimport alpha.extensions.loader\nprint('sqlalchemy', any(m == 'sqlalchemy' or m.startswith('sqlalchemy.') for m in sys.modules))\n")
        assert out == "sqlalchemy False", f"importing alpha.extensions.loader pulled in SQLAlchemy; the extensions -> persistence -> sqlalchemy chain is back (got: {out})"

    def test_loader_does_not_import_the_persistence_package_at_module_scope(self) -> None:
        out = _in_fresh_interpreter("import sys\nimport alpha.extensions.loader\nprint('persistence', 'alpha.persistence' in sys.modules)\n")
        assert out == "persistence False", f"importing alpha.extensions.loader pulled in alpha.persistence (got: {out})"

    def test_register_extension_table_prefix_is_resolved_at_call_time(self) -> None:
        """The name is no longer a module attribute; it resolves when called.

        This is the deliberate public-surface change: nothing imports it from
        this module (every caller imports it from `_env_filters`, its canonical
        home), so binding it here would only re-create the eager edge.
        """
        loader = importlib.import_module("alpha.extensions.loader")
        assert not hasattr(loader, "register_extension_table_prefix")
        # The canonical import path is untouched.
        canonical = importlib.import_module("alpha.persistence.migrations._env_filters")
        assert callable(canonical.register_extension_table_prefix)

    def test_load_extensions_still_registers_the_prefix(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The end-to-end behaviour the deferred import must preserve."""
        filters = importlib.import_module("alpha.persistence.migrations._env_filters")
        saved = set(filters.EXTENSION_TABLE_PREFIXES)
        filters.EXTENSION_TABLE_PREFIXES.clear()
        try:
            loader = importlib.import_module("alpha.extensions.loader")
            spec = loader.ExtensionSpec(use="x:y", table_prefix="acme_ext_")
            loader.load_extensions([spec])
            assert "acme_ext_" in filters.EXTENSION_TABLE_PREFIXES
        finally:
            filters.EXTENSION_TABLE_PREFIXES.clear()
            filters.EXTENSION_TABLE_PREFIXES.update(saved)

    def test_load_extensions_still_rejects_a_colliding_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The ValueError path must survive the deferral -- it is a startup abort."""
        filters = importlib.import_module("alpha.persistence.migrations._env_filters")
        saved = set(filters.EXTENSION_TABLE_PREFIXES)
        filters.EXTENSION_TABLE_PREFIXES.clear()
        try:
            loader = importlib.import_module("alpha.extensions.loader")
            spec = loader.ExtensionSpec(use="x:y", table_prefix="run")  # host table prefix
            with pytest.raises(loader.ExtensionLoadError):
                loader.load_extensions([spec])
        finally:
            filters.EXTENSION_TABLE_PREFIXES.clear()
            filters.EXTENSION_TABLE_PREFIXES.update(saved)

    def test_env_filters_module_stays_import_light(self) -> None:
        """`_env_filters` defers the ORM itself; the deferral must not undo that."""
        filters = importlib.import_module("alpha.persistence.migrations._env_filters")
        assert filters.__name__.endswith("_env_filters")


# --------------------------------------------------------------------------
# the one opt-in live measurement
# --------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("AGENT_WORKSPACE_RUN_LIVE_TESTS") != "1",
    reason="live wall-clock measurement; opt in with AGENT_WORKSPACE_RUN_LIVE_TESTS=1",
)
def test_live_cold_start_measurement(tmp_path: Path) -> None:
    """HUMAN-INVOKED ONLY. Prints real numbers; asserts nothing about them.

    Run with:
        AGENT_WORKSPACE_RUN_LIVE_TESTS=1 python -m pytest \\
            backend/tests/test_cold_start.py::test_live_cold_start_measurement -q -s

    This is the single place wall-clock is observed. It is a report, not a
    gate: a slow host must not fail a build, and a fast host must not make it
    pass. The enforcing mechanism is ``check_cold_start_budget.py`` against the
    committed baseline, and this test exists so a human can regenerate the
    numbers that baseline was taken from.
    """
    out = tmp_path / "live.json"
    proc = subprocess.run(
        [sys.executable, str(PROBE_PATH), "--targets", "alpha", "--repeats", "1", "--json", str(out)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    entry = data["targets"][0]
    print(f"\nLIVE cold start: {entry['module']} cold median {entry['cold']['median_ms']:.0f} ms, warm median {entry['warm']['median_ms']:.0f} ms ({data['host']['platform']})")


# --------------------------------------------------------------------------
# the opt-in contract: a skip that cannot quietly become permanent
# --------------------------------------------------------------------------


LIVE_ENV_VAR = "AGENT_WORKSPACE_RUN_LIVE_TESTS"
THIS_FILE = Path(__file__)
BACKEND_MAKEFILE = REPO_ROOT / "backend" / "Makefile"

#: The one test in this module that is allowed to be skipped, and the one
#: reason it may be skipped. Both are pinned below: a second skip, a bare
#: ``@pytest.mark.skip``, or an xfail would all be silent losses of coverage
#: that a green suite would not surface.
LIVE_TEST_NAME = "test_live_cold_start_measurement"
_DECORATORS = [
    node
    for node in THIS_FILE.read_text(encoding="utf-8").splitlines()
    if node.lstrip().startswith("@pytest.mark.")
]
_SKIP_DECORATORS = [
    line for line in _DECORATORS if any(mark in line for mark in ("skip", "xfail"))
]


def _live_test_function() -> Any:
    """The live test, fetched from the imported module rather than re-parsed."""
    module = sys.modules[__name__]
    return getattr(module, LIVE_TEST_NAME)


def _skip_marks(function: Any) -> list[Any]:
    return [mark for mark in function.pytestmark if mark.name in ("skip", "skipif", "xfail")]


def _live_test_node() -> Any:
    """The live test's AST node, so the skip condition can be read as written.

    pytest evaluates a ``skipif`` condition at import time, so the applied mark
    holds a bool, not the lambda. Reading the decorator from the source is the
    only way to assert *how* the condition is written -- which is the thing
    that would rot.
    """
    import ast

    tree = ast.parse(THIS_FILE.read_text(encoding="utf-8"), filename=str(THIS_FILE))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == LIVE_TEST_NAME:
            return node
    raise AssertionError(f"{LIVE_TEST_NAME} is gone; this file's skip contract moved with it")


def _skipif_decorator(node: Any) -> Any:
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call) and ast.unparse(decorator.func) == "pytest.mark.skipif":
            return decorator
    raise AssertionError(f"{LIVE_TEST_NAME} no longer carries a pytest.mark.skipif")


def _evaluate_condition(monkeypatch: pytest.MonkeyPatch, value: str | None) -> bool:
    """Run the decorator's condition with ``$AGENT_WORKSPACE_RUN_LIVE_TESTS`` set."""
    import ast

    condition = _skipif_decorator(_live_test_node()).args[0]
    if value is None:
        monkeypatch.delenv(LIVE_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(LIVE_ENV_VAR, value)
    return bool(eval(compile(ast.Expression(body=condition), "<skipif>", "eval"), {"os": os}))  # noqa: S307 - the expression is this file's own source


class TestTheLiveSkipIsOptInAndCannotGoPermanent:
    """The single reported skip is environmental, and it stays reversible.

    A skip is invisible in a green suite: nobody reads "1 skipped" as a
    regression. So the conditions under which this module skips anything at all
    are asserted rather than trusted.
    """

    def test_the_live_test_is_marked_live(self) -> None:
        """`make test` deselects it, so CI never spends a runner on it."""
        marks = {ast.unparse(decorator) for decorator in _live_test_node().decorator_list}
        assert any(mark == "pytest.mark.live" for mark in marks), f"the live marker is gone: {sorted(marks)}"

    def test_the_live_test_is_skipped_only_by_the_named_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The condition is the env var and nothing else, in both directions."""
        assert _evaluate_condition(monkeypatch, None) is True, "unset must skip"
        assert _evaluate_condition(monkeypatch, "1") is False, "'1' must un-skip"
        # Any other value is still a skip: the opt-in is exactly one string, so
        # a typo or an inherited 'true' cannot quietly start running the probe.
        for value in ("0", "true", "True", "", "yes", " 1"):
            assert _evaluate_condition(monkeypatch, value) is True, f"{value!r} must not enable the live test"

    def test_the_skip_condition_names_the_env_var_it_reads(self) -> None:
        condition = ast.unparse(_skipif_decorator(_live_test_node()).args[0])
        assert LIVE_ENV_VAR in condition, f"the skip condition no longer reads {LIVE_ENV_VAR}: {condition!r}"
        assert condition.count("environ") == 1, f"the skip reads more than the opt-in: {condition!r}"

    def test_the_skip_reason_discloses_the_opt_in(self) -> None:
        decorator = _skipif_decorator(_live_test_node())
        keywords = {keyword.arg: keyword.value for keyword in decorator.keywords}
        assert "reason" in keywords, "a skip with no reason is a skip nobody can act on"
        reason = ast.literal_eval(keywords["reason"])
        assert "opt in" in reason.lower(), f"the skip reason does not say how to opt in: {reason!r}"
        assert LIVE_ENV_VAR in reason

    def test_this_module_declares_exactly_one_skip_and_no_xfails(self) -> None:
        """The skip count cannot grow quietly: one declaration, one reason."""
        assert len(_SKIP_DECORATORS) == 1, f"unexpected skip/xfail decorators: {_SKIP_DECORATORS}"
        assert "skipif" in _SKIP_DECORATORS[0], "the one skip must stay conditional, never a bare skip"
        assert "xfail" not in "\n".join(_SKIP_DECORATORS)
        # And pytest agrees: one applied skip condition on the one live test.
        assert len(_skip_marks(_live_test_function())) == 1

    def test_the_live_test_really_passes_when_it_is_opted_into(self) -> None:
        """The decisive check: the skip is conditional, not a permanent pass.

        Runs the live test in a child pytest with the opt-in set, and asserts
        it *ran and passed* rather than being skipped. Without this,
        "1 skipped" and "the opt-in silently stopped working" are the same
        green -- which is exactly how a permanent skip hides.
        """
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                f"{THIS_FILE}::{LIVE_TEST_NAME}",
                "-q",
                "--no-header",
                "-p",
                "no:cacheprovider",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, LIVE_ENV_VAR: "1"},
            cwd=str(REPO_ROOT),
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "1 passed" in proc.stdout, proc.stdout
        assert "skipped" not in proc.stdout, f"the opt-in did not un-skip the live test:\n{proc.stdout}"

    @pytest.mark.parametrize("recipe", ["test:", "test-shard:"])
    def test_the_suite_never_runs_live_tests_by_default(self, recipe: str) -> None:
        """`make test` / `make test-shard` deselect them, so CI is never charged."""
        body = _make_recipe_body(recipe)
        assert '-m "not live"' in body, f"`make {recipe % ':'}` does not exclude live tests"


def _make_recipe_body(recipe: str) -> str:
    """The lines of a backend/Makefile recipe, up to the next target."""
    lines = BACKEND_MAKEFILE.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith(recipe))
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line[0].isspace() and not line.startswith("\t"):
            break
        body.append(line)
    return "\n".join(body)


# --------------------------------------------------------------------------
# CI wiring: a script nobody runs, or a failure nobody sees, is not a gate
# --------------------------------------------------------------------------


WORKFLOWS = REPO_ROOT / ".github" / "workflows"
COLD_START_WORKFLOW = "cold-start-budget.yml"
IMPORT_GATE_PATH = REPO_ROOT / "scripts" / "check_cold_start_imports.py"
IMPORT_BUDGET_PATH = REPO_ROOT / "backend" / "benchmarks" / "cold_start" / "import_budget.json"


def _workflow(name: str) -> dict[str, Any]:
    import yaml

    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _job(name: str, job: str) -> dict[str, Any]:
    return _workflow(name)["jobs"][job]


def _runs(name: str, job: str) -> str:
    return "\n".join(str(step.get("run", "")) for step in _job(name, job)["steps"])


def test_the_cold_start_gate_is_referenced_by_a_workflow_at_all() -> None:
    """The reported gap, pinned: the wall-clock gate must be wired, somewhere.

    Whether it blocks is a measurement question answered in the job comments;
    being unreferenced is not a defensible state for either half.
    """
    references = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(WORKFLOWS.glob("*.y*ml"))
    }
    wall_clock = [name for name, text in references.items() if "check_cold_start_budget.py" in text]
    assert wall_clock, "no workflow runs scripts/check_cold_start_budget.py"
    assert COLD_START_WORKFLOW in wall_clock
    eager = [name for name, text in references.items() if "check_cold_start_imports.py" in text]
    assert eager, "no workflow runs scripts/check_cold_start_imports.py"
    assert COLD_START_WORKFLOW in eager


class TestColdStartWorkflowWiring:
    def test_the_deterministic_gate_blocks_on_pull_request_and_push(self) -> None:
        workflow = _workflow(COLD_START_WORKFLOW)
        triggers = workflow[True] if True in workflow else workflow["on"]
        for event in ("push", "pull_request"):
            assert event in triggers, f"{COLD_START_WORKFLOW} does not run on {event}"
        for required in (
            "backend/packages/harness/alpha/**",
            "backend/app/gateway/**",
            "scripts/check_cold_start_imports.py",
            "backend/benchmarks/cold_start/**",
            f".github/workflows/{COLD_START_WORKFLOW}",
        ):
            for event in ("push", "pull_request"):
                assert required in triggers[event]["paths"], f"{event} paths omit {required}"

    def test_the_blocking_job_runs_both_checks_and_can_fail(self) -> None:
        job = _job(COLD_START_WORKFLOW, "cold-start-imports")
        runs = _runs(COLD_START_WORKFLOW, "cold-start-imports")
        assert "python scripts/check_cold_start_imports.py" in runs
        assert "python scripts/check_cold_start_budget.py --validate" in runs
        assert "continue-on-error" not in job
        assert "|| true" not in runs
        assert "if: always()" not in job, "the gate must gate, not report"
        assert job.get("timeout-minutes"), "a gate that hangs is not a gate"
        workflow = _workflow(COLD_START_WORKFLOW)
        assert workflow["permissions"] == {"contents": "read"}, "the gate must not need write access"

    def test_the_blocking_gate_needs_no_dependency_install(self) -> None:
        """The one cold-start check that must survive a broken environment.

        A job that cannot install its dependencies cannot report, and a report
        that cannot run reads as nothing happened. The eager-import gate is an
        AST scan over the standard library, so it deliberately has no
        `uv sync`: if that ever changes, the reason must be in the diff.
        """
        runs = _runs(COLD_START_WORKFLOW, "cold-start-imports")
        assert "uv sync" not in runs and "pip install" not in runs
        steps = _job(COLD_START_WORKFLOW, "cold-start-imports")["steps"]
        assert any("checkout" in str(step.get("uses", "")) for step in steps)

    def test_the_wall_clock_measurement_is_scheduled_not_blocking(self) -> None:
        """A contended runner's median is a report, so it lives in the nightly.

        Pinned in both directions: the measurement must be present, and it must
        not have crept onto the pull-request path.
        """
        blocking = _runs(COLD_START_WORKFLOW, "cold-start-imports")
        assert "check_cold_start_budget.py --json" not in blocking, (
            "the 42-launch wall-clock measurement must not block a pull request"
        )
        assert "--validate" in blocking, (
            "the baseline's *configuration* is deterministic and must block"
        )
        nightly = _runs("nightly.yaml", "cold-start-budget")
        assert "python scripts/check_cold_start_budget.py --json" in nightly
        assert "python scripts/check_cold_start_budget.py --validate" in nightly
        job = _job("nightly.yaml", "cold-start-budget")
        assert "continue-on-error" not in job
        assert "|| true" not in nightly
        assert job.get("timeout-minutes")

    def test_the_nightly_cold_start_job_is_independent_of_the_image_publish(self) -> None:
        """A flaky measurement must not withhold a nightly image."""
        job = _job("nightly.yaml", "cold-start-budget")
        assert "needs" not in job
        for name, other in _workflow("nightly.yaml")["jobs"].items():
            if name == "cold-start-budget":
                continue
            needs = other.get("needs")
            names = [needs] if isinstance(needs, str) else list(needs or [])
            assert "cold-start-budget" not in names, f"{name} waits on the cold-start measurement"

    def test_no_workflow_mentions_cold_start_with_a_swallowed_failure(self) -> None:
        for name in (COLD_START_WORKFLOW, "nightly.yaml"):
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            for line in text.splitlines():
                if "check_cold_start" in line:
                    assert "continue-on-error" not in line
                    assert "|| true" not in line

    def test_the_budget_and_the_import_budget_are_both_committed(self) -> None:
        """Two budgets, two files, both on disk. Neither is generated at run time."""
        assert BASELINE_PATH.exists(), f"missing {BASELINE_PATH}"
        assert IMPORT_BUDGET_PATH.exists(), f"missing {IMPORT_BUDGET_PATH}"

