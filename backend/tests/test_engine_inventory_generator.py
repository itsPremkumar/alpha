from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_engine_inventory.py"
SPEC = importlib.util.spec_from_file_location("generate_engine_inventory", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _tree(root: Path) -> None:
    package = root / "sample"
    package.mkdir(parents=True)
    (package / "engine.py").write_text("def run():\n    return True\n", encoding="utf-8")


def test_inventory_check_accepts_fresh_output_without_rewriting(tmp_path: Path) -> None:
    alpha_root = tmp_path / "alpha"
    _tree(alpha_root)
    output = tmp_path / "inventory.md"
    expected = MODULE.render(MODULE.scan_packages(alpha_root))
    output.write_text(expected, encoding="utf-8")
    before = output.stat().st_mtime_ns

    assert MODULE.main(["--alpha-root", str(alpha_root), "--out", str(output), "--check"]) == 0
    assert output.read_text(encoding="utf-8") == expected
    assert output.stat().st_mtime_ns == before


def test_inventory_check_fails_closed_on_stale_or_missing_output(tmp_path: Path) -> None:
    alpha_root = tmp_path / "alpha"
    _tree(alpha_root)
    output = tmp_path / "inventory.md"
    output.write_text("stale", encoding="utf-8")

    assert MODULE.main(["--alpha-root", str(alpha_root), "--out", str(output), "--check"]) == 1
    assert output.read_text(encoding="utf-8") == "stale"
    assert MODULE.main(["--alpha-root", str(alpha_root), "--out", str(tmp_path / "missing.md"), "--check"]) == 2
