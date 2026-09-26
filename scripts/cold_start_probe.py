#!/usr/bin/env python3
"""Cold-start import cost probe: who pays, and for what.

Alpha's startup is slow on some hosts (notably Defender-scanned Windows, where
every source/bytecode open is re-scanned) and slow by architecture (eager
package ``__init__``s that pull whole subtrees). Those two causes look the same
from a stopwatch, so this probe separates them and attributes the rest.

Design
------
Every measurement runs in a **fresh subprocess** (``sys.executable``), so the
probe never warms a cache for the thing it measures. Two modes are reported and
labelled separately:

``cold``
    ``PYTHONPYCACHEPREFIX`` points at an *empty* directory, so CPython finds no
    ``.pyc`` and must re-read + re-compile every source file. This is the
    first-run-on-a-new-machine cost. It scales with how many modules and how
    much source you import, and it is what an on-access AV scanner multiplies.
``warm``
    ``PYTHONPYCACHEPREFIX`` points at the directory the cold pass just filled,
    so only ``.pyc`` files are read. This is the ordinary restart cost.

``cold - warm`` is therefore the source-read/compile component, and ``warm`` is
the import-machinery + module-execution component. Redirecting the cache
(PEP 3147) means the probe **never writes ``__pycache__`` into the checkout**.

Attribution
-----------
Wall time says *that* startup is slow. Three more views say *why*:

* per-module ``self_ms`` / ``cumulative_ms`` from ``-X importtime``. These are
  authoritative: CPython times each import as it completes.
* the **real** import edges, captured in-process by a ``sys.meta_path``
  observer that records ``(importer, module)`` for every import actually
  performed. This is necessary because ``-X importtime`` prints *nothing* for a
  module that was already in ``sys.modules``, so its indentation is the live
  import-stack depth with holes in it -- it is not a tree, and reading it as one
  attributes edges to whatever module happened to be printed at that column.
* self time grouped by kind (``harness`` / ``app`` / ``third_party`` / ``stdlib``).
  Self time is exclusive, so it sums to the total without double counting;
  cumulative time does double count and is only ever used per node.

The attribution pass runs its own subprocess and its bookkeeping is inside the
measurement, so attribution numbers are labelled as attribution and are never
the gate's regression signal. That signal is the wall clock from *clean* runs.

Output
------
JSON with a fixed ``schema_version`` and fixed key order. Timings vary run to
run; the *shape* does not, so two runs diff cleanly. Run
``scripts/check_cold_start_budget.py`` to gate a measurement against a
committed baseline.

Usage::

    python scripts/cold_start_probe.py --json out.json
    python scripts/cold_start_probe.py --targets alpha --repeats 5 --no-attribution
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PROBE_VERSION = "1.0.0"

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
HARNESS = BACKEND / "packages" / "harness"

# Cheapest-first: a broken/slow early target is reported before the expensive
# tail has been waited on.
DEFAULT_TARGETS: tuple[str, ...] = (
    "alpha",
    "alpha.config.memory_config",
    "alpha.memory",
    "alpha.agents.lead_agent.prompt",
    "alpha.tools.builtins",
    "app.gateway.app",
)

IMPORTTIME_RE = re.compile(r"^import time:\s+(?P<self>\d+)\s*\|\s*(?P<cumulative>\d+)\s*\|(?P<rest>.*)$")
_MODULE_SUFFIX_RE = re.compile(r"\s+\((?:built-in|frozen|builtin)\)\s*$")
_EDGES_SENTINEL = "@@ALPHA_COLD_START_EDGES@@"

# Attribution is read off the cold pass by default: the module *set* a target
# pulls in is identical in both modes, only the per-module cost differs, so one
# attribution pass answers "what is expensive here" without doubling launches.
DEFAULT_ATTRIBUTION_MODE = "cold"

# Run inside the attribution child. Records the real (importer, module) edge for
# every import CPython actually performs, then prints them as one JSON line.
# Returning None from find_spec means "I only observe"; the rest of meta_path
# resolves the module exactly as it would have.
_TRACER_SRC = r'''
import json
import sys

_EDGES = []


def _caller_module():
    """Name of the module whose code triggered this import."""
    frame = sys._getframe(2)
    while frame is not None:
        name = frame.f_globals.get("__name__")
        if name and not name.startswith("importlib.") and name != "__main__":
            return name
        frame = frame.f_back
    return None


class _EdgeTracer:
    def find_spec(self, fullname, path=None, target=None):
        _EDGES.append([_caller_module(), fullname])
        return None


def _install():
    sys.meta_path.insert(0, _EdgeTracer())


def _dump():
    sys.stdout.write("\n" + "@@ALPHA_COLD_START_EDGES@@" + json.dumps(_EDGES))
    sys.stdout.flush()
'''

_CHILD_SRC = (
    "import sys\n"
    f"sys.path.insert(0, {str(BACKEND)!r})\n"
    f"sys.path.insert(0, {str(HARNESS)!r})\n"
    f"_SRC = {(_TRACER_SRC)!r}\n"
    "_ns = {}\n"
    "exec(compile(_SRC, '<cold-start-tracer>', 'exec'), _ns)\n"
    "import os as _os\n"
    "if _os.environ.get('ALPHA_COLD_START_TRACE'):\n"
    "    _ns['_install']()\n"
    "try:\n"
    "    import @TARGET@\n"
    "finally:\n"
    "    if _os.environ.get('ALPHA_COLD_START_TRACE'):\n"
    "        _ns['_dump']()\n"
)


# --------------------------------------------------------------------------
# importtime parsing
# --------------------------------------------------------------------------


def parse_importtime(text: str) -> dict[str, tuple[int, int]]:
    """Map module name -> (self_us, cumulative_us) from ``-X importtime``.

    Deliberately a flat map, not a tree. CPython omits the line for any module
    already in ``sys.modules``, so the indentation is a depth with holes and
    cannot be walked as a parent chain; the real edges come from the in-process
    observer instead.
    """
    found: dict[str, tuple[int, int]] = {}
    for raw in text.splitlines():
        match = IMPORTTIME_RE.match(raw)
        if match is None:
            continue
        name = _MODULE_SUFFIX_RE.sub("", match.group("rest").strip())
        if not name:
            continue
        found.setdefault(name, (int(match.group("self")), int(match.group("cumulative"))))
    return found


def parse_edges(stdout: str) -> list[tuple[str | None, str]]:
    marker = stdout.rfind(_EDGES_SENTINEL)
    if marker < 0:
        return []
    try:
        raw = json.loads(stdout[marker + len(_EDGES_SENTINEL) :])
    except json.JSONDecodeError:
        return []
    return [(parent, child) for parent, child in raw if isinstance(child, str)]


def module_kind(module: str) -> str:
    """Classify a module for honest cost attribution.

    ``harness``/``app`` are this repository and therefore architectural.
    ``stdlib`` is interpreter cost. Anything else is a third-party dependency:
    its cost is a dependency fact, amplified or suppressed by whatever the host
    does to file opens (AV scanning) -- never report it as ours.
    """
    if module == "alpha" or module.startswith("alpha."):
        return "harness"
    if module == "app" or module.startswith("app."):
        return "app"
    root = module.split(".", 1)[0]
    if root in getattr(sys, "stdlib_module_names", frozenset()):
        return "stdlib"
    return "third_party"


def round_ms(value: float) -> float:
    """Round to 0.1 ms.

    Keeps the JSON readable and byte-diffable while staying orders of magnitude
    below any tolerance the gate uses.
    """
    return round(value + 0.0, 1)


# --------------------------------------------------------------------------
# running a target
# --------------------------------------------------------------------------


class TargetError(RuntimeError):
    """A target could not be imported or timed. Always fatal to the run."""


def _child_env(home: Path, pycache_prefix: Path, trace: bool) -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    parts = [str(BACKEND), str(HARNESS)]
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONPYCACHEPREFIX"] = str(pycache_prefix)
    # An isolated home keeps config/runtime discovery out of the measurement and
    # out of the developer's real tree. Never let an inherited value win.
    env["AGENT_WORKSPACE_HOME"] = str(home)
    if trace:
        env["ALPHA_COLD_START_TRACE"] = "1"
    else:
        env.pop("ALPHA_COLD_START_TRACE", None)
    return env


def _run(module: str, env: dict[str, str], timeout: float, importtime: bool) -> tuple[str, str]:
    """Import ``module`` in a fresh interpreter. Returns (stdout, stderr)."""
    argv = [sys.executable]
    if importtime:
        argv += ["-X", "importtime"]
    argv += ["-c", _CHILD_SRC.replace("@TARGET@", module)]
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TargetError(f"{module}: import exceeded {timeout:.0f}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-5:]
        raise TargetError(f"{module}: exit {proc.returncode}: " + " | ".join(tail))
    return proc.stdout or "", proc.stderr or ""


def time_once(module: str, env: dict[str, str], timeout: float) -> float:
    """Wall time of a clean import, measured in the parent (subprocess.run)."""
    start = time.perf_counter()
    _run(module, env, timeout, importtime=False)
    return time.perf_counter() - start


def _stats(samples: list[float]) -> dict[str, Any]:
    """Spread, in a fixed key order, so a reader can judge noise themselves."""
    ordered = sorted(samples)
    return {
        "runs": len(ordered),
        "min_ms": round_ms(ordered[0] * 1000.0),
        "median_ms": round_ms(statistics.median(ordered) * 1000.0),
        "max_ms": round_ms(ordered[-1] * 1000.0),
        "spread_ms": round_ms((ordered[-1] - ordered[0]) * 1000.0),
        "samples_ms": [round_ms(value * 1000.0) for value in ordered],
    }


def _reachable(start: str, edges: list[tuple[str | None, str]]) -> set[str]:
    """Modules reachable from ``start`` over real import edges, plus itself."""
    children: dict[str, list[str]] = {}
    for parent, child in edges:
        if parent is not None:
            children.setdefault(parent, []).append(child)
    seen = {start}
    queue = [start]
    while queue:
        current = queue.pop()
        for child in children.get(current, ()):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return seen


def _ancestors(module: str, edges: list[tuple[str | None, str]]) -> list[str]:
    """Importers of ``module`` among this run's edges, outermost first.

    Importing ``a.b.c`` necessarily executes ``a`` and ``a.b`` first, so a
    target's cost is rarely its own. Reporting only the target's subtree would
    attribute a package ``__init__``'s whole cost to a leaf that merely named
    it, which is exactly the mistake this split exists to prevent.
    """
    parents: dict[str, str] = {}
    for parent, child in edges:
        if parent is not None and child not in parents:
            parents[child] = parent
    chain: list[str] = []
    seen = {module}
    current = module
    while current in parents:
        current = parents[current]
        if current in seen:  # defensive: never loop on a malformed edge cycle
            break
        seen.add(current)
        chain.append(current)
    chain.reverse()
    return chain


def attribute(module: str, env: dict[str, str], timeout: float, top: int) -> dict[str, Any]:
    """Run one traced importtime pass and turn it into attributable cost."""
    stdout, stderr = _run(module, env, timeout, importtime=True)
    timings = parse_importtime(stderr)
    if module not in timings:
        raise TargetError(f"{module}: importtime produced no record for the target")
    edges = parse_edges(stdout)
    if not edges:
        raise TargetError(f"{module}: the edge tracer produced no edges (ALPHA_COLD_START_TRACE lost?)")

    # A child edge into an already-cached module is a real edge with no cost of
    # its own in this run, so restrict every cost view to modules importtime
    # actually timed.
    loaded = set(timings)
    ancestors = [name for name in _ancestors(module, edges) if name in loaded]
    own = _reachable(module, edges) & loaded
    closure = _reachable(ancestors[0], edges) & loaded if ancestors else set(own)
    inherited = closure - own

    def _self_ms(names: set[str]) -> float:
        return sum(timings[name][0] for name in names) / 1000.0

    def _kinds(names: set[str]) -> dict[str, float]:
        buckets: dict[str, float] = {
            "app": 0.0,
            "harness": 0.0,
            "stdlib": 0.0,
            "third_party": 0.0,
        }
        for name in names:
            buckets[module_kind(name)] += timings[name][0] / 1000.0
        return {key: round_ms(buckets[key]) for key in sorted(buckets)}

    self_us, cumulative_us = timings[module]
    # "Who pulls in the world" for the statement actually run: the children of
    # the outermost package that had to be initialized first.
    chain_root = ancestors[0] if ancestors else module
    children: dict[str, list[str]] = {}
    for parent, child in edges:
        if parent is not None:
            children.setdefault(parent, []).append(child)

    total = cumulative_us or 1
    chains: list[dict[str, Any]] = []
    for child in sorted(set(children.get(chain_root, ())), key=lambda n: (-timings.get(n, (0, 0))[1], n)):
        child_cumulative, child_self = timings.get(child, (0, 0))
        subtree = _reachable(child, edges) & closure
        worst_name = max(subtree, key=lambda n: (timings[n][0], n)) if subtree else child
        chains.append(
            {
                "child": child,
                "cumulative_ms": round_ms(child_cumulative / 1000.0),
                "self_ms": round_ms(child_self / 1000.0),
                "share": round(child_cumulative / total, 4),
                "kind": module_kind(child),
                "modules": len(subtree),
                "deepest_cost_module": worst_name,
                "deepest_cost_ms": round_ms(timings[worst_name][0] / 1000.0),
                "deepest_cost_kind": module_kind(worst_name),
            }
        )

    def _row(name: str) -> dict[str, Any]:
        return {
            "module": name,
            "self_ms": round_ms(timings[name][0] / 1000.0),
            "cumulative_ms": round_ms(timings[name][1] / 1000.0),
            "kind": module_kind(name),
        }

    by_cumulative = sorted(closure, key=lambda n: (-timings[n][1], n))
    by_self = sorted(closure, key=lambda n: (-timings[n][0], n))
    return {
        "target_self_ms": round_ms(self_us / 1000.0),
        "target_cumulative_ms": round_ms(cumulative_us / 1000.0),
        "chain_root": chain_root,
        "ancestors": [_row(name) for name in ancestors],
        "module_count": len(closure),
        "self_ms_by_kind": _kinds(closure),
        "cost_split": {
            "own_subtree": {
                "modules": len(own),
                "self_ms": round_ms(_self_ms(own)),
                "self_ms_by_kind": _kinds(own),
            },
            "enclosing_packages": {
                "modules": len(inherited),
                "self_ms": round_ms(_self_ms(inherited)),
                "self_ms_by_kind": _kinds(inherited),
            },
        },
        "chains": chains[:top],
        "modules": [_row(name) for name in by_cumulative[:top]],
        "hotspots": [_row(name) for name in by_self[:top]],
    }


def measure_target(
    module: str,
    repeats: int,
    timeout: float,
    top: int,
    attribution: str,
    cache_dir: Path,
    home: Path,
) -> dict[str, Any]:
    """Measure one target cold and warm, with attribution on the chosen mode."""
    cold_prefix = cache_dir / "cold"
    cold_prefix.mkdir(parents=True, exist_ok=True)
    cold_env = _child_env(home, cold_prefix, trace=False)
    # The first cold pass fills the redirected cache; later passes then read
    # .pyc. Median-of-repeats keeps one Defender stall from deciding the verdict.
    cold_samples = [time_once(module, cold_env, timeout) for _ in range(repeats)]
    warm_samples = [time_once(module, cold_env, timeout) for _ in range(repeats)]

    entry: dict[str, Any] = {
        "module": module,
        "cold": _stats(cold_samples),
        "warm": _stats(warm_samples),
        "cold_minus_warm_ms": round_ms(statistics.median(cold_samples) * 1000.0 - statistics.median(warm_samples) * 1000.0),
    }
    if attribution == "none":
        entry["attribution"] = None
        entry["attribution_mode"] = "none"
    else:
        trace_env = _child_env(home, cold_prefix, trace=True)
        entry["attribution"] = attribute(module, trace_env, timeout, top)
        entry["attribution_mode"] = attribution
    return entry


# --------------------------------------------------------------------------
# host conditions
# --------------------------------------------------------------------------


def host_conditions() -> dict[str, Any]:
    """Facts a reader needs to tell architecture from environment.

    Deliberately conservative: reports what can be observed without running
    anything privileged or slow, and says so rather than guessing whether an AV
    scanner is active. ``operator_label`` carries the operator's own label when
    set via ``ALPHA_COLD_START_HOST_LABEL``.
    """
    conditions: dict[str, Any] = {
        "platform": sys.platform,
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "venv": sys.prefix != sys.base_prefix,
        "is_windows": os.name == "nt",
        "cpu_count": os.cpu_count(),
        "operator_label": os.environ.get("ALPHA_COLD_START_HOST_LABEL", ""),
    }
    if os.name == "nt":
        conditions["defender_note"] = (
            "Windows on-access AV scanning is expected to inflate per-file open cost "
            "(and therefore cold cost) versus an unsanned host. Treat absolute cold "
            "numbers as host-specific; the module graph, the chain attribution and the "
            "harness/third-party split are not."
        )
    return conditions


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def build_measurement(targets: list[str], repeats: int, timeout: float, top: int, attribution: str) -> dict[str, Any]:
    cache_dir = Path(tempfile.mkdtemp(prefix="alpha-coldstart-cache-"))
    home = Path(tempfile.mkdtemp(prefix="alpha-coldstart-home-"))
    try:
        entries = [measure_target(module, repeats, timeout, top, attribution, cache_dir, home) for module in targets]
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "probe_version": PROBE_VERSION,
        "tool": "cold_start_probe",
        "measurement": {
            "repeats": repeats,
            "timeout_seconds": timeout,
            "attribution_top_n": top,
            "attribution_mode": attribution,
        },
        "host": host_conditions(),
        "targets": entries,
    }


def print_report(measurement: dict[str, Any]) -> None:
    host = measurement["host"]
    print("cold-start probe")
    print(f"  python     {host['python_version']} ({host['platform']})")
    print(f"  executable {host['python_executable']}")
    if host.get("operator_label"):
        print(f"  host label {host['operator_label']}")
    if host.get("is_windows"):
        print("  NOTE       Defender-scanned Windows host: absolute cold numbers are")
        print("             environment-specific. Module graph and chain attribution are not.")
    print()
    header = f"{'target':<38} {'cold ms':>10} {'warm ms':>10} {'c-w ms':>9} {'spread':>9} {'mods':>6}"
    print(header)
    print("-" * len(header))
    for entry in measurement["targets"]:
        attribution = entry.get("attribution")
        modules = attribution["module_count"] if attribution else 0
        print(f"{entry['module']:<38} {entry['cold']['median_ms']:>10.1f} {entry['warm']['median_ms']:>10.1f} {entry['cold_minus_warm_ms']:>9.1f} {entry['cold']['spread_ms']:>9.1f} {modules:>6}")
    print()
    for entry in measurement["targets"]:
        attribution = entry.get("attribution")
        if not attribution:
            print(f"{entry['module']}  (attribution skipped)")
            print()
            continue
        print(f"{entry['module']}  ({entry['attribution_mode']} attribution)")
        print(f"  chain root {attribution['chain_root']}   ancestors: " + " < ".join(row["module"] for row in attribution["ancestors"]) or " <none>")
        split = attribution["cost_split"]
        own = split["own_subtree"]
        enclosing = split["enclosing_packages"]
        print(f"  own subtree      {own['modules']:>5} modules  {own['self_ms']:>9.1f} ms self   ({', '.join(f'{k}={v:.0f}' for k, v in own['self_ms_by_kind'].items() if v > 0)})")
        print(f"  enclosing pkgs   {enclosing['modules']:>5} modules  {enclosing['self_ms']:>9.1f} ms self   ({', '.join(f'{k}={v:.0f}' for k, v in enclosing['self_ms_by_kind'].items() if v > 0)})")
        for chain in attribution["chains"][:5]:
            print(f"  chain {chain['child']:<44} {chain['cumulative_ms']:>9.1f} ms ({chain['share'] * 100:4.1f}%)  -> {chain['deepest_cost_module']} {chain['deepest_cost_ms']:.0f} ms")
        print("  top self-time modules:")
        for row in attribution["hotspots"][:5]:
            print(f"    {row['module']:<50} {row['self_ms']:>8.1f} ms  [{row['kind']}]")
        print()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure and attribute cold-start import cost.")
    parser.add_argument(
        "--targets",
        nargs="+",
        default=list(DEFAULT_TARGETS),
        help="dotted module paths",
    )
    parser.add_argument("--repeats", type=int, default=3, help="wall-time runs per target per mode")
    parser.add_argument("--timeout", type=float, default=900.0, help="per-subprocess timeout in seconds")
    parser.add_argument("--top", type=int, default=25, help="entries per attribution list")
    parser.add_argument(
        "--attribution",
        choices=("cold", "warm", "none"),
        default=DEFAULT_ATTRIBUTION_MODE,
        help="which mode to run the traced -X importtime pass on (module set is identical in both)",
    )
    parser.add_argument("--json", type=Path, default=None, help="write the measurement JSON here")
    parser.add_argument("--quiet", action="store_true", help="suppress the human-readable report")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.repeats < 1:
        print("--repeats must be >= 1", file=sys.stderr)
        return 2
    measurement = build_measurement(list(args.targets), args.repeats, args.timeout, args.top, args.attribution)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(measurement, indent=2) + "\n", encoding="utf-8")
        if not args.quiet:
            print(f"wrote {args.json}")
    if not args.quiet:
        print_report(measurement)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TargetError as exc:
        print(f"cold-start probe FAILED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
