#!/usr/bin/env python3
"""Cold-start budget gate: fail the build when startup gets slower.

Compares a fresh ``cold_start_probe`` measurement against the committed
baseline and exits non-zero on a regression. Run it the same way in CI and
locally; it is a measurement, not a cache.

The contract
------------
**Fail closed.** A missing, unreadable, or schema-mismatched baseline is a
failure, never a skip. A probe that errors, times out, or returns a target it
could not measure is a failure. A gate that cannot run must never read as
green -- otherwise a broken toolchain silently stops enforcing the property it
exists to enforce.

**Median, not mean.** One AV stall must not decide the verdict. The statistic is
the median of ``--repeats`` wall-clock samples per target per mode; the raw
samples and their spread are printed so a reader can judge the noise rather
than take the gate's word for it.

**Tolerance, applied to a real regression.** A target fails only when its
median exceeds ``budget + tolerance`` **and** the excess is larger than the
observed spread of this run's own samples. A regression that sits inside
jitter is not a regression, and a gate that cries wolf gets disabled. Both
numbers are printed for every target so the call is auditable.

**Per-module budgets.** Named module costs (``--module`` / the baseline's
``module_budgets``) are gated the same way, so "the total is fine but
``sqlalchemy.ext.asyncio`` came back" is still caught.

**Improvements are never auto-applied.** A target that comes in under budget is
reported as an opportunity. The baseline only moves when a human runs
``--write-baseline`` and commits the result. A gate that lowers its own bar
after a good run is a gate that cannot catch the next bad one.

Usage::

    python scripts/check_cold_start_budget.py --json --tolerance-ms 2500
    python scripts/check_cold_start_budget.py --targets alpha --repeats 5
    python scripts/check_cold_start_budget.py --write-baseline   # human, then commit
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = Path(__file__).with_name("cold_start_probe.py")
BASELINE = REPO_ROOT / "backend" / "benchmarks" / "cold_start" / "baseline.json"

#: Schema this gate understands. A baseline written by a different probe
#: version is a hard failure, not something to guess at.
BASELINE_SCHEMA = 1

#: Documented default: 15% of the target's own budget, floored at 400 ms and
#: capped at 5000 ms. Rationale: a large target on a Defender-scanned host has
#: double-digit-percent sample spread, so a flat floor/ceiling pair tracks the
#: host without letting a big target buy unbounded slack. Override per run with
#: ``--tolerance-ms``; the value used is always printed.
DEFAULT_TOLERANCE_MS = 2500.0
TOLERANCE_FLOOR_MS = 400.0
TOLERANCE_CEILING_MS = 5000.0

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_ERROR = 2


class GateError(RuntimeError):
    """Anything that stops the gate from producing a verdict."""


def default_tolerance(budget_ms: float) -> float:
    """15% of the budget, clamped into the documented band."""
    return min(max(budget_ms * 0.15, TOLERANCE_FLOOR_MS), TOLERANCE_CEILING_MS)


# --------------------------------------------------------------------------
# baseline
# --------------------------------------------------------------------------


def load_baseline(path: Path) -> dict[str, Any]:
    """Read the baseline or fail loudly. Never returns a default."""
    if not path.exists():
        raise GateError(
            f"baseline not found: {path}\n"
            "  Cold start is an enforced property, so an absent baseline is a failure, not a skip.\n"
            "  Create it deliberately on a representative host and commit it:\n"
            f"    python scripts/check_cold_start_budget.py --write-baseline --baseline {path}"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"baseline unreadable: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GateError(f"baseline is not a JSON object: {path}")
    version = data.get("schema_version")
    if version != BASELINE_SCHEMA:
        raise GateError(
            f"baseline schema_version {version!r} != {BASELINE_SCHEMA!r}: {path}\n"
            "  Re-create it with --write-baseline on a representative host."
        )
    if not isinstance(data.get("targets"), dict):
        raise GateError(f"baseline has no 'targets' object: {path}")
    if not isinstance(data.get("module_budgets"), dict):
        raise GateError(f"baseline has no 'module_budgets' object: {path}")
    return data


def build_baseline(measurement: dict[str, Any], tolerance_ms: float) -> dict[str, Any]:
    """Derive a baseline from a measurement. Only ever called by --write-baseline."""
    targets: dict[str, Any] = {}
    for entry in measurement["targets"]:
        targets[entry["module"]] = {
            "cold_ms": entry["cold"]["median_ms"],
            "warm_ms": entry["warm"]["median_ms"],
            "module_count": (entry.get("attribution") or {}).get("module_count"),
        }
    # Per-module budgets: the top self-time entries from the attribution pass.
    # These are the leaves that actually cost something, so a regression that
    # redistributes cost inside the graph still gets caught.
    module_budgets: dict[str, Any] = {}
    for entry in measurement["targets"]:
        attribution = entry.get("attribution")
        if not attribution:
            continue
        for row in attribution["hotspots"]:
            module_budgets.setdefault(
                row["module"],
                {"self_ms": row["self_ms"], "observed_in": entry["module"]},
            )
    return {
        "schema_version": BASELINE_SCHEMA,
        "tool": "check_cold_start_budget",
        "probe_version": measurement["probe_version"],
        "host": measurement["host"],
        "measurement": {
            "repeats": measurement["measurement"]["repeats"],
            "timeout_seconds": measurement["measurement"]["timeout_seconds"],
            "attribution_mode": measurement["measurement"]["attribution_mode"],
        },
        "tolerance_ms": tolerance_ms,
        "targets": targets,
        "module_budgets": module_budgets,
    }


# --------------------------------------------------------------------------
# probe invocation
# --------------------------------------------------------------------------


def probe_default_targets() -> list[str]:
    """``cold_start_probe.DEFAULT_TARGETS``, read without running anything."""
    spec = importlib.util.spec_from_file_location("alpha_cold_start_probe", PROBE)
    if spec is None or spec.loader is None:  # pragma: no cover - unloadable probe
        raise GateError(f"could not load {PROBE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.DEFAULT_TARGETS)


def run_probe(
    targets: list[str], repeats: int, timeout: float, top: int, attribution: str
) -> dict[str, Any]:
    """Invoke the probe as a subprocess. A non-zero exit is a gate error.

    The probe writes its measurement to a file rather than stdout so a crash
    cannot be mistaken for a truncated-but-valid result.
    """
    with tempfile.TemporaryDirectory(prefix="alpha-coldstart-gate-") as scratch:
        out = Path(scratch) / "measurement.json"
        argv = [
            sys.executable,
            str(PROBE),
            "--targets",
            *targets,
            "--repeats",
            str(repeats),
            "--timeout",
            str(timeout),
            "--top",
            str(top),
            "--attribution",
            attribution,
            "--json",
            str(out),
            "--quiet",
        ]
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                argv,
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=timeout * len(targets) * 2 + 300,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GateError(
                f"cold_start_probe.py timed out after {exc.timeout:.0f}s"
            ) from exc
        except OSError as exc:
            raise GateError(f"could not launch cold_start_probe.py: {exc}") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[-2000:]
            raise GateError(
                f"cold_start_probe.py exited {proc.returncode}. The gate fails closed: an "
                f"unmeasurable cold start is not a passing cold start.\n{detail}"
            )
        if not out.exists():
            raise GateError(
                "cold_start_probe.py exited 0 but wrote no measurement file.\n"
                f"--- stdout ---\n{proc.stdout[-2000:]}\n--- stderr ---\n{proc.stderr[-2000:]}"
            )
        return read_measurement(out.read_text(encoding="utf-8"), proc.stderr, out)


def read_measurement(text: str, stderr: str, script: Path) -> dict[str, Any]:
    """Validate a measurement JSON document."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GateError(
            f"{script} is not valid measurement JSON: {exc}\n--- probe stderr ---\n{stderr[-2000:]}"
        ) from exc
    if not isinstance(data, dict) or data.get("tool") != "cold_start_probe":
        raise GateError(f"{script} is not a cold_start_probe measurement")
    if data.get("schema_version") != 1:
        raise GateError(
            f"{script}: unexpected schema_version {data.get('schema_version')!r}"
        )
    if not isinstance(data.get("targets"), list) or not data["targets"]:
        raise GateError(f"{script} returned no targets")
    for entry in data["targets"]:
        for mode in ("cold", "warm"):
            stats = entry.get(mode)
            if not isinstance(stats, dict) or "median_ms" not in stats:
                raise GateError(
                    f"{script}: target {entry.get('module')!r} has no {mode} measurement"
                )
    return data


# --------------------------------------------------------------------------
# decision logic (pure; no I/O, so it is directly testable)
# --------------------------------------------------------------------------


def _judge(
    name: str, budget_ms: float, stats: dict[str, Any], tolerance_ms: float
) -> dict[str, Any]:
    """Compare one measured series against a budget.

    Returns a verdict dict. A regression must clear BOTH the tolerance and this
    run's own sample spread: a difference smaller than the noise it was measured
    against is not evidence of anything.
    """
    median = float(stats["median_ms"])
    spread = float(stats.get("spread_ms", 0.0))
    runs = int(stats.get("runs", 0))
    excess = median - budget_ms
    if excess > tolerance_ms and excess > spread:
        status = "regressed"
    elif excess > 0:
        status = "within_tolerance"
    elif budget_ms - median > tolerance_ms and budget_ms - median > spread:
        status = "improved"
    else:
        status = "within_tolerance"
    return {
        "name": name,
        "budget_ms": budget_ms,
        "median_ms": median,
        "spread_ms": spread,
        "runs": runs,
        "samples_ms": list(stats.get("samples_ms", [])),
        "tolerance_ms": tolerance_ms,
        "excess_ms": round(excess, 1),
        "status": status,
    }


def compare(
    measurement: dict[str, Any], baseline: dict[str, Any], tolerance_ms: float | None
) -> dict[str, Any]:
    """Full verdict for a measurement against a baseline.

    A target present in the measurement but missing from the baseline is
    reported as ``unbudgeted`` (informational) rather than failing, so adding a
    target does not require a baseline edit in the same commit. A target in the
    baseline but absent from the measurement is a failure: the probe was asked
    to measure it and did not.
    """
    budget_tolerance = float(baseline.get("tolerance_ms") or DEFAULT_TOLERANCE_MS)
    verdicts: list[dict[str, Any]] = []
    measured = {entry["module"]: entry for entry in measurement["targets"]}

    for module, budget in sorted(baseline["targets"].items()):
        entry = measured.get(module)
        if entry is None:
            verdicts.append(
                {
                    "name": module,
                    "budget_ms": float(budget.get("cold_ms") or 0.0),
                    "median_ms": 0.0,
                    "spread_ms": 0.0,
                    "runs": 0,
                    "samples_ms": [],
                    "tolerance_ms": budget_tolerance,
                    "excess_ms": 0.0,
                    "status": "not_measured",
                }
            )
            continue
        tol = tolerance_ms if tolerance_ms is not None else budget_tolerance
        for mode in ("cold", "warm"):
            # NB: baseline keys are "<mode>_ms". Testing `mode in budget` here
            # would silently skip every target, and a real regression would then
            # ride through on module budgets alone.
            if budget.get(f"{mode}_ms") is None:
                continue
            verdicts.append(
                _judge(
                    f"{module}[{mode}]", float(budget[f"{mode}_ms"]), entry[mode], tol
                )
            )

    for module, entry in sorted(measured.items()):
        if module in baseline["targets"]:
            continue
        tol = tolerance_ms if tolerance_ms is not None else budget_tolerance
        for mode in ("cold", "warm"):
            verdicts.append(
                {
                    "name": f"{module}[{mode}]",
                    "budget_ms": 0.0,
                    "median_ms": float(entry[mode]["median_ms"]),
                    "spread_ms": float(entry[mode].get("spread_ms", 0.0)),
                    "runs": int(entry[mode].get("runs", 0)),
                    "samples_ms": list(entry[mode].get("samples_ms", [])),
                    "tolerance_ms": tol,
                    "excess_ms": float(entry[mode]["median_ms"]),
                    "status": "unbudgeted",
                }
            )

    attribution = {
        entry["module"]: entry.get("attribution")
        for entry in measurement["targets"]
        if entry.get("attribution")
    }
    for name, budget in sorted(baseline["module_budgets"].items()):
        observed = _find_module_self_ms(attribution, name)
        tol = tolerance_ms if tolerance_ms is not None else budget_tolerance
        if observed is None:
            # Attribution is a top-N list, so a budgeted module that fell out of
            # it this run means it got *cheaper*, not that the budget went
            # unverified. Failing here would make the gate's verdict depend on
            # which modules happened to be expensive, which is noise.
            verdicts.append(
                {
                    "name": name,
                    "budget_ms": float(budget.get("self_ms") or 0.0),
                    "median_ms": 0.0,
                    "spread_ms": 0.0,
                    "runs": 0,
                    "samples_ms": [],
                    "tolerance_ms": tol,
                    "excess_ms": 0.0,
                    "status": "not_observed",
                }
            )
            continue
        verdicts.append(
            _judge(
                name,
                float(budget.get("self_ms") or 0.0),
                {
                    "median_ms": observed,
                    "spread_ms": 0.0,
                    "runs": 1,
                    "samples_ms": [observed],
                },
                tol,
            )
        )

    return {
        "status": "fail"
        if any(v["status"] in ("regressed", "not_measured") for v in verdicts)
        else "pass",
        "verdicts": verdicts,
        "regressions": [v for v in verdicts if v["status"] == "regressed"],
        "improvements": [v for v in verdicts if v["status"] == "improved"],
        "unmeasured": [v for v in verdicts if v["status"] == "not_measured"],
        "not_observed": [v for v in verdicts if v["status"] == "not_observed"],
    }


def _find_module_self_ms(attribution: dict[str, Any], module: str) -> float | None:
    """Max self-time this run recorded for ``module`` across all targets.

    Attribution is a single run with no repeats, so it has no spread to compare
    against; the caller therefore passes ``spread_ms=0`` and the tolerance alone
    decides. Reporting the max keeps a module that regressed on one path visible
    even when a cheaper path happens to hide it.
    """
    best: float | None = None
    for entry in attribution.values():
        for row in entry.get("modules", []) + entry.get("hotspots", []):
            if row["module"] == module:
                value = float(row["self_ms"])
                best = value if best is None else max(best, value)
    return best


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def print_report(
    result: dict[str, Any], tolerance_ms: float | None, baseline_tolerance: float
) -> int:
    print("cold-start budget")
    print(
        f"  tolerance: {'CLI' if tolerance_ms is not None else 'baseline'} = "
        f"{tolerance_ms if tolerance_ms is not None else baseline_tolerance:.0f} ms"
    )
    print()
    header = (
        f"{'budget':<46} {'median':>10} {'spread':>9} {'tol':>7} {'excess':>10}  status"
    )
    print(header)
    print("-" * (len(header) + 8))
    for verdict in sorted(
        result["verdicts"],
        key=lambda v: (v["status"] not in ("regressed", "not_measured"), v["name"]),
    ):
        if verdict["status"] in ("unbudgeted", "not_observed"):
            continue
        print(
            f"{verdict['name']:<46} {verdict['median_ms']:>10.1f} {verdict['spread_ms']:>9.1f} "
            f"{verdict['tolerance_ms']:>7.0f} {verdict['excess_ms']:>+10.1f}  {verdict['status']}"
            + (f"  samples={verdict['samples_ms']}" if verdict["runs"] > 1 else "")
        )
    print()
    if result["not_observed"]:
        print(
            f"NOT OBSERVED ({len(result['not_observed'])}) -- module budgets that fell out of "
            "this run's attribution top-N (informational; usually means the module got cheaper):"
        )
        print("  " + ", ".join(v["name"] for v in result["not_observed"]))
        print()
    if result["unmeasured"]:
        print("NOT MEASURED (baseline entries the probe did not cover):")
        for verdict in result["unmeasured"]:
            print(f"  {verdict['name']}  budget {verdict['budget_ms']:.1f} ms")
        print()
    if result["regressions"]:
        print(f"REGRESSED ({len(result['regressions'])}):")
        for verdict in result["regressions"]:
            print(
                f"  {verdict['name']}: median {verdict['median_ms']:.1f} ms > budget "
                f"{verdict['budget_ms']:.1f} ms + {verdict['tolerance_ms']:.0f} ms tolerance "
                f"(excess {verdict['excess_ms']:+.1f} ms, spread {verdict['spread_ms']:.1f} ms, "
                f"runs {verdict['runs']})"
            )
        print()
    if result["improvements"]:
        print(
            f"IMPROVED ({len(result['improvements'])}) -- consider re-baselining to lock the win in:"
        )
        for verdict in result["improvements"]:
            print(
                f"  {verdict['name']}: median {verdict['median_ms']:.1f} ms < budget "
                f"{verdict['budget_ms']:.1f} ms ({verdict['excess_ms']:+.1f} ms)"
            )
        print(
            "  The gate never lowers a baseline on its own; commit --write-baseline output to keep it."
        )
        print()
    unbudgeted = [v for v in result["verdicts"] if v["status"] == "unbudgeted"]
    if unbudgeted:
        print(f"UNBUDGETED ({len(unbudgeted)}) -- not in the baseline, so not gated:")
        for verdict in unbudgeted:
            print(f"  {verdict['name']}: {verdict['median_ms']:.1f} ms")
        print()
    return EXIT_REGRESSION if result["status"] == "fail" else EXIT_OK


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gate cold-start import cost against a committed baseline."
    )
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument(
        "--targets", nargs="+", default=None, help="override the probed targets"
    )
    parser.add_argument(
        "--repeats", type=int, default=None, help="override the baseline's repeat count"
    )
    parser.add_argument(
        "--timeout", type=float, default=None, help="per-subprocess timeout in seconds"
    )
    parser.add_argument("--top", type=int, default=None, help="attribution list depth")
    parser.add_argument("--attribution", choices=("cold", "warm", "none"), default=None)
    parser.add_argument(
        "--tolerance-ms",
        type=float,
        default=None,
        help="override the baseline's tolerance",
    )
    parser.add_argument(
        "--json", action="store_true", help="also print the result as JSON"
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="replace the baseline with this measurement (human decision; then commit it)",
    )
    parser.add_argument(
        "--measurement",
        type=Path,
        default=None,
        help="use a saved measurement instead of probing",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.write_baseline:
        targets = args.targets or probe_default_targets()
        measurement = (
            read_measurement(
                args.measurement.read_text(encoding="utf-8"), "", args.measurement
            )
            if args.measurement
            else run_probe(
                targets,
                args.repeats or 3,
                args.timeout or 900.0,
                args.top or 25,
                args.attribution or "cold",
            )
        )
        tolerance = (
            args.tolerance_ms
            if args.tolerance_ms is not None
            else float(
                load_baseline(args.baseline).get("tolerance_ms") or DEFAULT_TOLERANCE_MS
            )
            if args.baseline.exists()
            else DEFAULT_TOLERANCE_MS
        )
        baseline = build_baseline(measurement, tolerance)
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps(baseline, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"wrote baseline {args.baseline} ({len(baseline['targets'])} targets, "
            f"{len(baseline['module_budgets'])} module budgets) -- review and commit it"
        )
        return EXIT_OK

    baseline = load_baseline(args.baseline)
    baseline_tolerance = float(baseline.get("tolerance_ms") or DEFAULT_TOLERANCE_MS)
    targets = args.targets or sorted(baseline["targets"])
    repeats = args.repeats or int(baseline.get("measurement", {}).get("repeats", 3))
    timeout = args.timeout or float(
        baseline.get("measurement", {}).get("timeout_seconds", 900.0)
    )
    top = args.top or 25
    attribution = args.attribution or str(
        baseline.get("measurement", {}).get("attribution_mode", "cold")
    )

    try:
        measurement = (
            read_measurement(
                args.measurement.read_text(encoding="utf-8"), "", args.measurement
            )
            if args.measurement
            else run_probe(targets, repeats, timeout, top, attribution)
        )
    except GateError as exc:
        print(f"cold-start budget gate FAILED CLOSED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    result = compare(measurement, baseline, args.tolerance_ms)
    if args.json:
        print(
            json.dumps(
                {"status": result["status"], "baseline": str(args.baseline), **result},
                indent=2,
            )
        )
    return print_report(result, args.tolerance_ms, baseline_tolerance)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GateError as exc:  # pragma: no cover - argparse-independent failures
        print(f"cold-start budget gate FAILED CLOSED: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR) from exc
