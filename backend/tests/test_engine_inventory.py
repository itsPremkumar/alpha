"""Tests for the engine inventory generator (scripts/generate_engine_inventory.py)."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import generate_engine_inventory as gen  # noqa: E402


def test_scan_detects_packages_and_tiers(tmp_path):
    root = tmp_path / "alpha"
    (root / "foo").mkdir(parents=True)
    (root / "foo" / "a.py").write_text("x = 1\n" * 10, encoding="utf-8")
    (root / "bar").mkdir()
    (root / "bar" / "b.py").write_text("# simulated path\n", encoding="utf-8")

    pkgs = gen.scan_packages(root)
    by_name = {p.name: p for p in pkgs}
    assert set(by_name) == {"foo", "bar"}
    assert by_name["foo"].tier == "core"
    assert by_name["bar"].tier == "preview/partial"


def test_render_shape_and_counts(tmp_path):
    root = tmp_path / "alpha"
    (root / "one").mkdir(parents=True)
    (root / "one" / "a.py").write_text("a\n", encoding="utf-8")
    md = gen.render(gen.scan_packages(root))
    assert "# Alpha Engine Inventory" in md
    assert "**Packages:** 1" in md
    assert "`alpha/one`" in md


def test_scan_skips_empty_and_private_dirs(tmp_path):
    root = tmp_path / "alpha"
    (root / "_private").mkdir(parents=True)
    (root / "_private" / "x.py").write_text("x\n", encoding="utf-8")
    (root / "empty").mkdir()
    assert gen.scan_packages(root) == []
