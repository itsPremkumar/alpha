"""Tests for the RSI evaluator-surface manifest (WP-A2, feature #2).

Covers: ``state="complete"`` over the live real surface, deterministic
double-builds over an immutable snapshot of that surface's bytes, one-byte
tamper detection (quarantine semantics), missing-file fail-closed behavior,
honest SHA-256 provenance (hashes equal ``hashlib.sha256`` over the actual
bytes), suite-version drift, and honest handling of missing/corrupt stored
baselines.

Every test pins ``AGENT_WORKSPACE_HOME`` to a temp dir (the environment does
not isolate it); tamper/determinism scenarios run over temp trees — the
repository's own surface is never written. (Concurrent Wave-1 siblings *do*
write ``backend/tests/**`` while this suite runs, which is why equal-dicts
assertions use a frozen snapshot of the real bytes rather than the live
tree.)
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from alpha.benchmarks.runner import BenchmarkRunner
from alpha.rsi.evaluator_manifest import (
    EVALUATOR_SURFACE,
    MANIFEST_VERSION,
    build_manifest,
    load_baseline,
    manifest_allows_cycle,
    store_baseline,
    store_cycle_manifest,
    verify_baseline,
    verify_manifest,
)

# Minimal repo-shaped surface: one file per EVALUATOR_SURFACE pattern.
_SURFACE_FILES = {
    "backend/tests/test_example.py": "def test_example():\n    assert True\n",
    "backend/packages/harness/alpha/benchmarks/release_gate.py": "DEFAULT_AUTONOMY_GATES = ()\n",
    "backend/packages/harness/alpha/benchmarks/extra_suite.py": "# additional benchmark source\n",
    "backend/packages/harness/alpha/safety/guard.py": "class SafetyGuard:  # test double\n    ...\n",
    "backend/packages/harness/alpha/reproduction/gates.py": "GATES: tuple = ()\n",
    "backend/packages/harness/alpha/policy/engine.py": "AUTONOMY_POLICY = 'supervised'\n",
}


@pytest.fixture(autouse=True)
def _isolate_runtime_home(tmp_path, monkeypatch):
    """Pin runtime state (manifest store) to a temp dir for every test."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path / "runtime_home"))


def _write_surface(root: Path) -> None:
    for rel, text in _SURFACE_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _snapshot_surface(src_root: Path, dst_root: Path) -> int:
    """Copy every EVALUATOR_SURFACE file from ``src_root`` into an immutable tree."""
    seen: set[Path] = set()
    copied = 0
    for pattern in EVALUATOR_SURFACE:
        for path in src_root.glob(pattern):
            if not path.is_file() or path in seen:
                continue
            rel_parts = path.relative_to(src_root).parts
            if "__pycache__" in rel_parts:
                continue
            seen.add(path)
            target = dst_root.joinpath(*rel_parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1
    return copied


def test_manifest_over_real_surface_is_complete_and_deterministic(tmp_path):
    assert isinstance(EVALUATOR_SURFACE, tuple)
    assert EVALUATOR_SURFACE and all(isinstance(pattern, str) for pattern in EVALUATOR_SURFACE)

    # Completeness (and shape) over the *live* real surface, per plan §3 WP-A2.
    live = build_manifest()
    assert live["version"] == MANIFEST_VERSION == 1
    assert live["state"] == "complete"
    assert live["missing"] == []
    assert all(value.startswith("sha256:") for value in live["files"].values())

    covered = set(live["files"])
    assert any(rel.startswith("backend/tests/") for rel in covered)
    assert any(rel.startswith("backend/packages/harness/alpha/benchmarks/") for rel in covered)
    assert any(rel.startswith("backend/packages/harness/alpha/safety/") for rel in covered)
    assert "backend/packages/harness/alpha/benchmarks/release_gate.py" in covered
    assert "backend/packages/harness/alpha/reproduction/gates.py" in covered
    assert "backend/packages/harness/alpha/policy/engine.py" in covered

    assert live["suite_versions"], "nightly eval suites must be registered and reported"
    for entry in live["suite_versions"].values():
        assert set(entry) == {"name", "version", "cases"}

    allowed, reason = manifest_allows_cycle(live)
    assert allowed is True, reason

    # Determinism ("two builds, equal dicts") over an immutable snapshot of
    # the real surface's bytes. Concurrent Wave-1 siblings create files under
    # backend/tests/** while this test runs (observed mid-run:
    # test_rsi_lineage.py / test_execution_mode.py / test_rsi_opportunity.py
    # saved by other agents during the build window), so equality over the
    # live tree would assert a race, not determinism. The snapshot freezes
    # the inputs: any difference between the two builds can then only come
    # from the manifest algorithm itself.
    repo_root = Path(__file__).resolve().parents[2]
    snapshot = tmp_path / "surface_snapshot"
    copied = _snapshot_surface(repo_root, snapshot)
    assert copied > 0

    first = build_manifest(root=snapshot)
    second = build_manifest(root=snapshot)
    assert first == second, "two builds over identical bytes must be equal"
    assert first["state"] == "complete"
    assert first["missing"] == []
    assert first["suite_versions"] == live["suite_versions"]


def test_one_byte_tamper_is_detected_and_file_is_named(tmp_path):
    _write_surface(tmp_path)
    baseline = build_manifest(root=tmp_path)
    assert baseline["state"] == "complete"

    rel = "backend/tests/test_example.py"
    target = tmp_path / rel
    data = bytearray(target.read_bytes())
    data[0] ^= 0x01  # flip one byte — quarantine semantics
    target.write_bytes(bytes(data))

    ok, changes = verify_manifest(baseline, root=tmp_path)
    assert ok is False
    assert any("hash mismatch" in line and rel in line for line in changes), changes
    # the fresh build really re-hashed the flipped bytes (no cached/fabricated verdict)
    fresh = build_manifest(root=tmp_path)
    assert fresh["files"][rel] != baseline["files"][rel]


def test_missing_evaluator_file_fails_closed(tmp_path):
    _write_surface(tmp_path)
    baseline = build_manifest(root=tmp_path)
    assert baseline["state"] == "complete"

    (tmp_path / "backend/packages/harness/alpha/policy/engine.py").unlink()

    rebuilt = build_manifest(root=tmp_path)
    assert rebuilt["state"] == "incomplete"
    assert "backend/packages/harness/alpha/policy/engine.py" in rebuilt["missing"]

    # documented fail-closed behavior: an incomplete manifest refuses the cycle
    allowed, reason = manifest_allows_cycle(rebuilt)
    assert allowed is False
    assert "fail-closed" in reason and "refused" in reason

    ok, changes = verify_manifest(baseline, root=tmp_path)
    assert ok is False
    assert any("backend/packages/harness/alpha/policy/engine.py" in line for line in changes), changes
    assert any("incomplete" in line for line in changes), changes

    # an incomplete baseline can never verify either (hard reject, no neutral pass)
    ok_against_incomplete, incomplete_changes = verify_manifest(rebuilt, root=tmp_path)
    assert ok_against_incomplete is False
    assert any("incomplete" in line for line in incomplete_changes), incomplete_changes


def test_manifest_hashes_equal_real_sha256_of_actual_bytes():
    """Honesty: provenance claims come only from real hashes of actual bytes."""
    manifest = build_manifest()
    repo_root = Path(__file__).resolve().parents[2]
    for rel in (
        "backend/packages/harness/alpha/policy/engine.py",
        "backend/tests/test_rsi_evaluator_manifest.py",
    ):
        expected = "sha256:" + hashlib.sha256((repo_root / rel).read_bytes()).hexdigest()
        assert manifest["files"][rel] == expected, f"fabricated or stale hash for {rel}"


def test_suite_version_drift_is_reported_as_change(tmp_path, monkeypatch):
    _write_surface(tmp_path)
    baseline = build_manifest(root=tmp_path)
    assert baseline["state"] == "complete"
    assert baseline["suite_versions"]

    drifted = [{**entry, "version": f"{entry['version']}-drifted"} for entry in baseline["suite_versions"].values()]
    monkeypatch.setattr(BenchmarkRunner, "list_suites", lambda self: drifted)

    ok, changes = verify_manifest(baseline, root=tmp_path)
    assert ok is False
    assert any("suite drift" in line for line in changes), changes
    assert any("-drifted" in line for line in changes), changes


def test_verify_manifest_fails_closed_on_malformed_baseline():
    cases = (
        (None, "malformed"),
        ("not an object", "malformed"),
        ([], "malformed"),
        ({}, "malformed"),
        ({"version": 99, "files": {}, "suite_versions": {}, "state": "complete", "missing": []}, "version"),
    )
    for bad, needle in cases:
        ok, changes = verify_manifest(bad)
        assert ok is False, bad
        assert any(needle in line for line in changes), (bad, changes)


def test_missing_baseline_never_verifies():
    assert load_baseline() is None
    ok, changes = verify_baseline()
    assert ok is False
    assert any("missing" in line and "baseline.json" in line for line in changes), changes


def test_corrupt_baseline_reports_real_error_and_never_verifies():
    path = store_baseline({"version": MANIFEST_VERSION, "files": {}, "suite_versions": {}, "state": "complete", "missing": []})
    path.write_text("{ this is not valid json", encoding="utf-8")

    assert load_baseline() is None
    ok, changes = verify_baseline()
    assert ok is False
    assert any("unreadable" in line and "JSONDecodeError" in line for line in changes), changes


def test_store_cycle_manifest_round_trip_and_cycle_id_validation(tmp_path):
    # A frozen tmp surface keeps store→load→verify race-free (siblings keep
    # writing backend/tests/** in parallel with this suite).
    _write_surface(tmp_path)
    manifest = build_manifest(root=tmp_path)
    path = store_cycle_manifest("cycle-0001", manifest)
    assert path.name == "cycle-0001.json"
    assert json.loads(path.read_text(encoding="utf-8")) == manifest
    # storing a cycle manifest must not fabricate a baseline
    assert load_baseline() is None

    store_baseline(manifest)
    assert load_baseline() == manifest
    ok, changes = verify_baseline(root=tmp_path)
    assert ok is True and changes == [], changes

    for unsafe in ("../escape", "nested/id", "", "."):
        with pytest.raises(ValueError):
            store_cycle_manifest(unsafe, manifest)
