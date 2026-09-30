"""Tests for the checkout-integrity verifier (scripts/verify_checkout.py)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import verify_checkout  # noqa: E402


def test_complete_checkout_is_ok():
    report = verify_checkout.evaluate("r", tracked=1000, missing=[], required_missing=[])
    assert report.ok is True
    assert "complete" in report.render()


def test_incomplete_checkout_fails():
    report = verify_checkout.evaluate("r", tracked=1000, missing=["backend/tests/test_x.py"], required_missing=[])
    assert report.ok is False
    assert "INCOMPLETE" in report.render()


def test_missing_required_path_fails_even_with_no_missing_tracked():
    report = verify_checkout.evaluate("r", tracked=1000, missing=[], required_missing=["backend/pyproject.toml"])
    assert report.ok is False
    assert "backend/pyproject.toml" in report.render()


def test_max_missing_tolerance():
    assert verify_checkout.evaluate("r", tracked=10, missing=["a"], required_missing=[], max_missing=1).ok is True
    assert verify_checkout.evaluate("r", tracked=10, missing=["a", "b"], required_missing=[], max_missing=1).ok is False


def test_to_dict_shape():
    d = verify_checkout.evaluate("r", tracked=5, missing=["a"], required_missing=[], max_missing=0).to_dict()
    assert set(d) == {"repo", "tracked", "missing", "required_missing", "max_missing", "ok"}
    assert d["missing"] == 1
    assert d["ok"] is False


def test_required_paths_include_build_config():
    # Guards against the exact "no pyproject" confusion this verifier exists to catch.
    assert "backend/pyproject.toml" in verify_checkout.REQUIRED_PATHS
    assert "backend/uv.lock" in verify_checkout.REQUIRED_PATHS
