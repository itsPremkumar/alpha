"""Evaluator-surface manifest + integrity verification (RSI WP-A2, feature #2).

Before/after every RSI cycle a SHA-256 manifest over the *evaluator surface*
(the tests, benchmark suites, release gate, safety guards, reproduction gates
and policy engine that judge candidates) proves nothing tampered with the
evaluation itself.

Honesty semantics (implementation plan §3 WP-A2 + §5.6):
- every hash is computed from the actual file bytes via ``hashlib.sha256``
  and stored as ``"sha256:<hexdigest>"`` — never fabricated;
- an unhashable/missing/unreadable file marks ``state="incomplete"`` and the
  cycle must **fail closed** via :func:`manifest_allows_cycle` (mirrors spec
  §16 QUARANTINE — a hard reject, *not* the unverified-neutral 0.5 path);
- :func:`verify_manifest` never raises: malformed baselines, rebuild
  failures, hash changes and suite-version drift all come back as
  ``(False, changes)`` with the real mismatch;
- a missing or corrupt stored baseline yields ``None`` / ``(False, reason)``
  with the real error text — never a fabricated "verified".

``EVALUATOR_SURFACE`` is a constant *in code* (not a candidate-writable JSON
file), so a candidate cannot rewrite its own approval policy
(spec §15: "candidate must not rewrite the policy").

Wire-in: Phase A is internal — ``RSIEngine.run_rsi_cycle`` consumes these
results as evidence lines (WP-A4 integration, A4 owns ``rsi/engine.py``);
this module owns no gateway surface.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1

# Repo-root-relative globs covering the surface that evaluates candidates:
# tests, benchmark suites (incl. the release gate they re-export), safety
# guard sources, reproduction gates, and the policy engine constants.
EVALUATOR_SURFACE: tuple[str, ...] = (
    "backend/tests/**/*",
    "backend/packages/harness/alpha/benchmarks/**/*",
    "backend/packages/harness/alpha/benchmarks/release_gate.py",
    "backend/packages/harness/alpha/safety/**/*",
    "backend/packages/harness/alpha/reproduction/gates.py",
    "backend/packages/harness/alpha/policy/engine.py",
)

# Cycle ids must be a single safe path component: no separators, no traversal.
_CYCLE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _default_root() -> Path:
    """Repository root inferred from this file: <root>/backend/packages/harness/alpha/rsi/…."""
    return Path(__file__).resolve().parents[5]


def _iter_surface_files(base: Path, pattern: str) -> list[Path]:
    """Sorted regular files under ``base`` matching ``pattern`` (no ``__pycache__``).

    ``__pycache__`` is excluded because bytecode churn (interpreter version,
    pytest run) is not an evaluator change and would produce false tamper
    reports; source and fixture bytes are hashed instead.
    """
    candidates: list[Path] = []
    for path in base.glob(pattern):
        if not path.is_file():
            continue
        if "__pycache__" in path.relative_to(base).parts:
            continue
        candidates.append(path)
    return sorted(candidates)


def _collect_suite_versions() -> dict[str, dict[str, Any]]:
    """Return ``{suite_name: {"name", "version", "cases"}}`` from the benchmark runner.

    ``register_eval_suites()`` (idempotent, integration point) runs first so
    the nightly suites the gates depend on are present. Any failure
    propagates to :func:`build_manifest`, which records the real error as an
    ``incomplete`` state instead of fabricating suite versions.
    """
    from alpha.benchmarks.runner import get_benchmark_runner
    from alpha.benchmarks.suites import register_eval_suites

    register_eval_suites()
    entries = get_benchmark_runner().list_suites()
    versions = {str(entry["name"]): {"name": entry["name"], "version": entry["version"], "cases": entry["cases"]} for entry in entries}
    return dict(sorted(versions.items()))


def build_manifest(*, root: Path | None = None) -> dict[str, Any]:
    """Build the SHA-256 manifest of the evaluator surface.

    Returns ``{"version": 1, "files": {relpath: "sha256:…"},
    "suite_versions": {...}, "state": "complete"|"incomplete",
    "missing": [...]}`` where paths are repo-root-relative POSIX strings.
    Deterministic: two builds over an unchanged tree compare equal.

    ``root`` defaults to the repository root inferred from this module's
    location (cwd-independent); tests point it at a temp tree.
    """
    base = Path(root).resolve() if root is not None else _default_root()
    missing: list[str] = []
    files: dict[str, str] = {}

    try:
        suite_versions = _collect_suite_versions()
    except Exception as exc:
        suite_versions = {}
        missing.append(f"suite_versions unavailable: {type(exc).__name__}: {exc}")
    if not suite_versions:
        missing.append("no benchmark suites registered (evaluator surface incomplete)")

    if not (base / "backend").is_dir():
        missing.append(f"evaluator surface root invalid: no 'backend/' directory under {base}")
    else:
        for pattern in EVALUATOR_SURFACE:
            candidates = _iter_surface_files(base, pattern)
            if not candidates:
                missing.append(pattern)
                continue
            for path in candidates:
                rel = path.relative_to(base).as_posix()
                if rel in files:
                    continue
                try:
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError as exc:
                    entry = f"{rel} (unreadable: {exc})"
                    if entry not in missing:
                        missing.append(entry)
                    continue
                files[rel] = f"sha256:{digest}"

    return {
        "version": MANIFEST_VERSION,
        "files": dict(sorted(files.items())),
        "suite_versions": suite_versions,
        "state": "complete" if not missing else "incomplete",
        "missing": missing,
    }


def verify_manifest(baseline: dict[str, Any], *, root: Path | None = None) -> tuple[bool, list[str]]:
    """Verify ``baseline`` against a fresh build of the evaluator surface.

    Returns ``(True, [])`` only when every file hash and suite version matches
    exactly; otherwise ``(False, changes)`` naming each real mismatch. Never
    raises: any unexpected error is reported as a change (fail-closed).
    """
    try:
        return _verify_manifest(baseline, root=root)
    except Exception as exc:
        return False, [f"verify_manifest failed (fail-closed): {type(exc).__name__}: {exc}"]


def _verify_manifest(baseline: dict[str, Any], *, root: Path | None = None) -> tuple[bool, list[str]]:
    if not isinstance(baseline, dict):
        return False, [f"baseline manifest malformed: expected JSON object, got {type(baseline).__name__}"]
    required = ("version", "files", "suite_versions", "state", "missing")
    absent = [key for key in required if key not in baseline]
    if absent:
        return False, [f"baseline manifest malformed: missing keys {absent}"]
    if not isinstance(baseline["files"], dict) or not isinstance(baseline["suite_versions"], dict):
        return False, ["baseline manifest malformed: 'files'/'suite_versions' must be JSON objects"]
    if baseline["version"] != MANIFEST_VERSION:
        return False, [f"baseline manifest version {baseline['version']!r} != supported {MANIFEST_VERSION}"]
    if baseline["state"] != "complete":
        return False, [f"baseline manifest state={baseline['state']!r} — fail-closed: an incomplete baseline can never verify"]

    fresh = build_manifest(root=root)
    changes: list[str] = []

    base_files: dict[str, Any] = baseline["files"]
    fresh_files: dict[str, Any] = fresh["files"]
    for rel in sorted(set(base_files) | set(fresh_files)):
        if rel not in fresh_files:
            changes.append(f"missing file: {rel}")
        elif rel not in base_files:
            changes.append(f"unexpected new file: {rel}")
        elif base_files[rel] != fresh_files[rel]:
            changes.append(f"hash mismatch: {rel} (baseline {base_files[rel]!r}, current {fresh_files[rel]!r})")

    base_suites: dict[str, Any] = baseline["suite_versions"]
    fresh_suites: dict[str, Any] = fresh["suite_versions"]
    for name in sorted(set(base_suites) | set(fresh_suites)):
        if base_suites.get(name) != fresh_suites.get(name):
            changes.append(f"suite drift: {name}: baseline {base_suites.get(name)!r} -> current {fresh_suites.get(name)!r}")

    if fresh["state"] != "complete":
        changes.append(f"current evaluator surface state='incomplete': missing={fresh['missing']}")

    return (not changes, changes)


def manifest_allows_cycle(manifest: dict[str, Any]) -> tuple[bool, str]:
    """Documented fail-closed gate: a cycle proceeds only on a ``complete`` manifest.

    Mirrors spec §16 QUARANTINE per plan §3 WP-A2 — an incomplete manifest is
    a hard reject with the real reason, never an unverified-neutral pass.
    """
    if not isinstance(manifest, dict):
        return False, "fail-closed: evaluator manifest malformed (not a JSON object) — cycle refused"
    state = manifest.get("state")
    missing = manifest.get("missing") or []
    if state != "complete" or missing or manifest.get("version") != MANIFEST_VERSION:
        return False, f"fail-closed: evaluator manifest state={state!r} version={manifest.get('version')!r} missing={list(missing)} — cycle refused (spec §16 quarantine)"
    return True, "evaluator manifest complete — cycle may proceed"


def _manifest_path(name: str) -> Path:
    # Lazy import: ``alpha.config``'s package init eagerly loads/validates the
    # whole config tree (~seconds on cold start); keeping it out of module
    # import lets cycle wiring import this integrity helper cheaply.
    # ``runtime_home()`` is still called per invocation so the
    # ``AGENT_WORKSPACE_HOME`` isolation contract holds.
    from alpha.config.runtime_paths import runtime_home

    return runtime_home() / "rsi" / "manifests" / f"{name}.json"


def store_cycle_manifest(cycle_id: str, manifest: dict[str, Any]) -> Path:
    """Atomically persist ``manifest`` as ``runtime_home()/rsi/manifests/<cycle_id>.json``.

    Raises ``ValueError`` when ``cycle_id`` is not a single safe path
    component (no separators, no traversal) and propagates the real OS error
    when runtime home is unwritable — never returns a path that was not
    written.
    """
    if _CYCLE_ID_RE.fullmatch(cycle_id) is None:
        raise ValueError(f"invalid cycle_id {cycle_id!r}: must be a single path component matching {_CYCLE_ID_RE.pattern!r} (no separators, no traversal)")
    from alpha.evolution.identity import atomic_write_json

    path = _manifest_path(cycle_id)
    atomic_write_json(path, manifest)
    return path


def store_baseline(manifest: dict[str, Any]) -> Path:
    """Persist ``manifest`` as the verification baseline (``baseline.json``)."""
    return store_cycle_manifest("baseline", manifest)


def load_baseline() -> dict[str, Any] | None:
    """Load ``baseline.json``, or ``None`` when it is missing/corrupt/not an object.

    Honest absence: ``None`` never becomes a fabricated manifest, so callers
    cannot mistake "no baseline" for "verified".
    """
    path = _manifest_path("baseline")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def verify_baseline(*, root: Path | None = None) -> tuple[bool, list[str]]:
    """Verify against the stored baseline, honestly reporting why when it cannot.

    Missing → ``(False, ["baseline manifest missing: <path>"])``;
    corrupt → ``(False, ["baseline manifest unreadable: <real error>"])``.
    """
    path = _manifest_path("baseline")
    if not path.is_file():
        return False, [f"baseline manifest missing: {path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, [f"baseline manifest unreadable: {type(exc).__name__}: {exc}"]
    if not isinstance(payload, dict):
        return False, [f"baseline manifest malformed: expected JSON object, got {type(payload).__name__}"]
    return verify_manifest(payload, root=root)
