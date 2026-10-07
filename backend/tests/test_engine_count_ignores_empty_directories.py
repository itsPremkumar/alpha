"""`collect_engines()` must not count an empty directory chain as an engine.

Found on 2026-10-06 while correcting the capability counts in Phase 0 of
`docs/FEATURE_COMPLETION_PLAN.md`. The committed
`contracts/feature_manifest.json` said 118 engines and a fresh regeneration said
119, so the number depended on when it was read.

Cause: `collect_engines()` treated "contains a subdirectory" as sufficient to be
an importable package:

    submodules = sorted(p.name for p in child.iterdir() if p.is_dir() and ...)

`backend/packages/harness/alpha/backend/packages/harness/alpha` existed as three
nested **empty** directories with zero tracked files, so `alpha.backend` was
counted as an engine. That contradicts the function's own docstring, which says
"Directories holding only data (or nothing) are excluded".

The consequence is worse than an off-by-one: a generated count that changes
depending on when it is generated is not a measurement, and every document that
quotes it inherits the ambiguity.

The empty directories are deliberately NOT deleted - the count must not depend on
whether unrelated junk is present on disk. These tests pin the counting rule
against a synthetic tree so the behaviour is reproducible without touching the
real one.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_feature_manifest.py"


def _load_generator(monkeypatch: pytest.MonkeyPatch, alpha_root: Path):
    """Import the generator with ``ALPHA`` pointed at a synthetic tree."""
    spec = importlib.util.spec_from_file_location("_manifest_gen_under_test", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ALPHA", alpha_root)
    return module


def _engine_ids(alpha_root: Path, monkeypatch: pytest.MonkeyPatch) -> set[str]:
    module = _load_generator(monkeypatch, alpha_root)
    return {str(e["id"]) for e in module.collect_engines()}


def _make(root: Path, rel: str, *, init: bool = False, modules: tuple[str, ...] = ()) -> Path:
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    if init:
        (d / "__init__.py").write_text("", encoding="utf-8")
    for m in modules:
        (d / m).write_text("", encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------


def test_a_chain_of_empty_directories_is_not_an_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact shape found on disk: nested empty dirs, no Python anywhere."""
    alpha = tmp_path / "alpha"
    alpha.mkdir()
    (alpha / "backend" / "packages" / "harness" / "alpha").mkdir(parents=True)

    ids = _engine_ids(alpha, monkeypatch)

    assert "alpha.backend" not in ids
    assert ids == set(), f"nothing here is importable, got {sorted(ids)}"


def test_a_real_submodule_still_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fix must not swallow a genuine engine that merely nests a package."""
    alpha = tmp_path / "alpha"
    _make(alpha, "agents", init=True, modules=("__init__.py",))
    _make(alpha, "agents/lead_agent", modules=("agent.py",))

    ids = _engine_ids(alpha, monkeypatch)

    assert "alpha.agents" in ids


def test_a_package_nested_two_levels_deep_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Depth alone must not decide it; content does."""
    alpha = tmp_path / "alpha"
    _make(alpha, "outer", init=True)
    _make(alpha, "outer/middle", modules=("__init__.py",))
    _make(alpha, "outer/middle/deep", modules=("thing.py",))

    ids = _engine_ids(alpha, monkeypatch)

    assert "alpha.outer" in ids


# ---------------------------------------------------------------------------
# The boundaries the docstring already promised
# ---------------------------------------------------------------------------


def test_a_data_only_directory_is_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    alpha = tmp_path / "alpha"
    _make(alpha, "assets", modules=())
    (alpha / "assets" / "logo.png").write_bytes(b"\x89PNG")

    assert _engine_ids(alpha, monkeypatch) == set()


def test_a_pycache_only_directory_is_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    alpha = tmp_path / "alpha"
    _make(alpha, "cache_only", modules=())
    (alpha / "cache_only" / "__pycache__").mkdir()
    (alpha / "cache_only" / "__pycache__" / "x.pyc").write_bytes(b"")

    assert _engine_ids(alpha, monkeypatch) == set()


def test_a_bare_init_file_is_an_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    alpha = tmp_path / "alpha"
    _make(alpha, "utils", init=True)

    assert _engine_ids(alpha, monkeypatch) == {"alpha.utils"}


def test_the_count_is_stable_regardless_of_unrelated_junk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The property that actually broke: the number depended on disk state.

    Same real engines, one tree with junk and one without, must count identically.
    That is the difference between a measurement and a snapshot of whatever
    happened to be on the filesystem.
    """
    clean = tmp_path / "clean"
    _make(clean, "agents", init=True)
    _make(clean, "bots", init=True)
    _make(clean, "memory", init=True)

    dirty = tmp_path / "dirty"
    _make(dirty, "agents", init=True)
    _make(dirty, "bots", init=True)
    _make(dirty, "memory", init=True)
    (dirty / "backend" / "packages" / "harness" / "alpha").mkdir(parents=True)
    (dirty / "zzz_stray").mkdir()
    (dirty / "zzz_stray" / "nested").mkdir()

    assert _engine_ids(clean, monkeypatch) == _engine_ids(dirty, monkeypatch)


def test_the_committed_manifest_matches_a_fresh_generation() -> None:
    """The end-to-end claim: the committed number equals the generated number."""
    import json

    manifest = json.loads((ROOT.parent / "contracts" / "feature_manifest.json").read_text(encoding="utf-8"))
    module = _load_generator(__import__("pytest").MonkeyPatch(), ROOT / "packages" / "harness" / "alpha")

    assert len(module.collect_engines()) == len(manifest["engines"]), "contracts/feature_manifest.json is stale; run backend/scripts/generate_feature_manifest.py"
