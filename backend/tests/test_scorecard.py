"""Tests for the capability scorecard (alpha.scorecard)."""

from __future__ import annotations

import sys
from pathlib import Path

_HARNESS_ROOT = Path(__file__).resolve().parents[1] / "packages" / "harness"
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))

from alpha.scorecard import TAXONOMY, scan  # noqa: E402


def _fake_alpha(tmp_path: Path) -> Path:
    (tmp_path / "runtime").mkdir()
    (tmp_path / "swarm").mkdir()
    (tmp_path / "observability").mkdir()
    (tmp_path / "observability" / "span.py").write_text("", encoding="utf-8")
    return tmp_path


def test_scan_detects_present_and_absent(tmp_path):
    report = scan(_fake_alpha(tmp_path))
    by_key = {s.capability.key: s.present for s in report.statuses}
    assert by_key["core.runtime"] is True
    assert by_key["mas.swarm"] is True
    assert by_key["obs.otel"] is True
    assert by_key["mem.rag"] is False
    assert by_key["safety.guardrails"] is False


def test_coverage_math(tmp_path):
    report = scan(_fake_alpha(tmp_path))
    assert report.total == len(TAXONOMY)
    assert 0 < report.present_count < report.total
    assert report.coverage == report.present_count / report.total


def test_missing_and_render(tmp_path):
    report = scan(_fake_alpha(tmp_path))
    assert "mem.rag" in {c.key for c in report.missing()}
    md = report.render_markdown()
    assert "# Alpha Capability Scorecard" in md
    assert "Coverage:" in md
    assert "| Category |" in md


def test_to_dict_shape(tmp_path):
    d = scan(_fake_alpha(tmp_path)).to_dict()
    assert set(d) >= {"alpha_root", "total", "present", "coverage", "by_category", "missing"}
    assert isinstance(d["by_category"], dict)


def test_real_alpha_is_highly_covered():
    root = _HARNESS_ROOT / "alpha"
    if not root.exists():
        return  # not running inside the repo
    report = scan(root)
    assert report.coverage >= 0.9, f"coverage {report.coverage:.0%}; missing={[c.key for c in report.missing()]}"
    present = {s.capability.key for s in report.statuses if s.present}
    for expected in ("mas.deliberation", "mas.swarm", "mem.layered", "obs.otel", "safety.guardrails"):
        assert expected in present
