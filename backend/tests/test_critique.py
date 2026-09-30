"""Tests for the code critic (alpha.critique)."""

from __future__ import annotations

import sys
from pathlib import Path

_HARNESS_ROOT = Path(__file__).resolve().parents[1] / "packages" / "harness"
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))

from alpha.critique import critique_path  # noqa: E402


def _write(tmp_path: Path, code: str) -> Path:
    p = tmp_path / "m.py"
    p.write_text(code, encoding="utf-8")
    return p


def test_flags_bare_except(tmp_path):
    p = _write(tmp_path, '"""d."""\ntry:\n    x = 1\nexcept Exception:\n    pass\n')
    assert "bare-except" in {f.rule for f in critique_path(p).findings}


def test_flags_mutable_default(tmp_path):
    p = _write(tmp_path, '"""d."""\n\n\ndef f(x=[]):\n    """d."""\n    return x\n')
    assert "mutable-default" in {f.rule for f in critique_path(p).findings}


def test_flags_open_without_encoding(tmp_path):
    p = _write(tmp_path, '"""d."""\nopen("x")\n')
    assert "open-encoding" in {f.rule for f in critique_path(p).findings}


def test_flags_from_dict_without_unknown_check(tmp_path):
    code = '"""d."""\n\n\nclass A:\n    """d."""\n\n    @classmethod\n    def from_dict(cls, data):\n        """d."""\n        return cls(**data)\n'
    assert "from-dict-no-unknown-check" in {f.rule for f in critique_path(_write(tmp_path, code)).findings}


def test_clean_module_has_no_findings(tmp_path):
    code = '"""A clean module."""\n\n\ndef add(a: int, b: int) -> int:\n    """Add two ints."""\n    return a + b\n'
    assert critique_path(_write(tmp_path, code)).findings == []


def test_report_shape_and_render(tmp_path):
    rep = critique_path(_write(tmp_path, '"""d."""\nprint("hi")\n'))
    assert rep.files_scanned == 1
    assert {"root", "files_scanned", "by_severity", "by_rule", "findings"} <= set(rep.to_dict())
    assert "# Code Critique Report" in rep.render_markdown()


def test_own_packages_are_clean():
    """Regression guard: the hardened packages must stay finding-free."""
    root = _HARNESS_ROOT / "alpha"
    if not root.exists():
        return
    pkgs = ("routines", "connectors", "egress", "scorecard", "modes", "experts",
            "automations", "skills_market", "workspace", "critique")
    findings = []
    for pkg in pkgs:
        findings.extend(critique_path(root / pkg).findings)
    assert findings == [], [f"{f.rule}:{f.path}:{f.line}" for f in findings]
