"""Hermetic tests for the official documentation index and drift gate."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = ROOT / "scripts" / "generate_docs_index.py"
DRIFT_GATE_PATH = ROOT / "scripts" / "check_docs_index_drift.py"

pytestmark = pytest.mark.no_auto_user


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


generator = _load_module("alpha_docs_index_generator_test", GENERATOR_PATH)
drift_gate = _load_module("alpha_docs_index_drift_gate_test", DRIFT_GATE_PATH)


def _write(path: Path, text: str = "# Fixture\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / "fixture"
    _write(root / "docs" / "README.md", "# Fixture library\n")
    _write(root / "docs" / "architecture" / "overview.md", "# Architecture\n")
    _write(root / "docs" / "memory" / "overview.md", "# Memory\n")
    _write(root / "docs" / "api" / "reference.md", "# API\n")
    _write(root / "docs" / "plans" / "roadmap.md", "# Plan\n")
    _write(root / "docs" / "decisions" / "0001-start.md", "# Decision\n")
    _write(root / "docs" / "benchmarks" / "latency.md", "# Benchmark\n")
    return root


def _invoke(
    module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
) -> tuple[int, str, str]:
    code = module.main(arguments)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_generator_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fixture_root(tmp_path)
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"

    first_code, first_out, first_err = _invoke(
        generator,
        capsys,
        ["--root", str(root), "--commit", "fixture-commit", "--output", str(first)],
    )
    second_code, second_out, second_err = _invoke(
        generator,
        capsys,
        ["--root", str(root), "--commit", "fixture-commit", "--output", str(second)],
    )
    assert first_code == 0, first_err or first_out
    assert second_code == 0, second_err or second_out
    assert first.read_bytes() == second.read_bytes()
    data = first.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in data
    assert data.endswith(b"\n")
    assert not re.search(rb"\b(?:19|20)\d{2}-\d{2}-\d{2}\b", data)
    assert b"generated_at" not in data
    assert b"now" not in data.lower()
    assert str(tmp_path).encode() not in data

    monkeypatch.setitem(
        generator.FILE_OVERRIDES,
        "architecture/overview.md",
        generator.DocumentSpec("memory", "Exact override description."),
    )
    scan = generator.scan_documents(root, tmp_path / "override-index.md")
    document = next(item for item in scan.documents if item.path == "architecture/overview.md")
    assert document.section == "memory"
    assert document.description == "Exact override description."

    unclassified = root / "docs" / "not-classified.md"
    _write(unclassified)
    failed = _invoke(
        generator,
        capsys,
        [
            "--root",
            str(root),
            "--commit",
            "fixture-commit",
            "--output",
            str(tmp_path / "unclassified.md"),
        ],
    )
    assert failed[0] == 2
    assert "unclassified document" in failed[2]
    assert "not-classified.md" in failed[2]
    assert not (tmp_path / "unclassified.md").exists()
    unclassified.unlink()

    _write(root / "docs" / "INDEX.md", "# generated index\n")
    _write(root / "docs" / "scratch.tmp.md")
    _write(root / "docs" / "node_modules" / "vendor.md")
    skipped = _invoke(
        generator,
        capsys,
        ["--root", str(root), "--commit", "fixture-commit", "--output", str(first)],
    )
    assert skipped[0] == 0, skipped[2] or skipped[1]
    assert "skipped=3" in skipped[1]
    assert "skipped[generated-index]: INDEX.md" in skipped[1]
    assert "skipped[filename-pattern]: scratch.tmp.md" in skipped[1]
    assert "skipped[directory:node_modules]: node_modules/" in skipped[1]

    clean = _invoke(
        generator,
        capsys,
        ["--root", str(root), "--commit", "fixture-commit", "--output", str(first), "--check"],
    )
    assert clean[0] == 0, clean[2] or clean[1]
    assert "check: clean" in clean[1]

    first.write_text("tampered\n", encoding="utf-8", newline="\n")
    drift = _invoke(
        generator,
        capsys,
        ["--root", str(root), "--commit", "fixture-commit", "--output", str(first), "--check"],
    )
    assert drift[0] == 1
    assert "--- docs/INDEX.md" in drift[1]
    assert "+++ generated documentation index" in drift[1]
    assert "check: drift" in drift[1]
    assert first.read_text(encoding="utf-8") == "tampered\n"


def test_drift_gate_json_shape_and_read_only_behavior(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _fixture_root(tmp_path)
    output = root / "docs" / "INDEX.md"
    generated_code, generated_out, generated_err = _invoke(
        generator,
        capsys,
        ["--root", str(root), "--commit", "fixture-commit", "--output", str(output)],
    )
    assert generated_code == 0, generated_err or generated_out

    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    clean_code, clean_out, clean_err = _invoke(
        drift_gate,
        capsys,
        ["--root", str(root), "--commit", "fixture-commit", "--json"],
    )
    after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}

    assert clean_code == 0, clean_err or clean_out
    assert before == after
    clean_payload = json.loads(clean_out)
    assert clean_payload["status"] == "clean"
    assert clean_payload["drift"] is False
    assert clean_payload["exit_code"] == 0
    assert clean_payload["artifact"] == "docs/INDEX.md"
    assert clean_payload["diff"] == ""
    assert clean_payload["summary"]["documents"] > 0

    output.write_text("tampered\n", encoding="utf-8", newline="\n")
    drift_code, drift_out, drift_err = _invoke(
        drift_gate,
        capsys,
        ["--root", str(root), "--commit", "fixture-commit", "--json"],
    )
    drift_payload = json.loads(drift_out)
    assert drift_code == 1, drift_err
    assert drift_payload["status"] == "drift"
    assert drift_payload["drift"] is True
    assert drift_payload["exit_code"] == 1
    assert drift_payload["diff"]
    assert "--- docs/INDEX.md" in drift_payload["diff"]


def test_committed_real_index_is_exactly_generator_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The committed index must equal the generator's output for the COMMITTED tree.

    The comparison is made against a materialisation of git-TRACKED docs, not the
    working directory. In CI a checkout is clean and the two are identical; in a
    developer's tree they are not, and an unrelated uncommitted document (this
    repository routinely has several in flight at once) would otherwise make this
    test red for a reason that has nothing to do with the index. The gate's real
    question is "does the committed navigation still describe the committed
    docs?", so that is exactly what is asserted here.
    """
    tracked_root = tmp_path / "tracked"
    (tracked_root / "docs").mkdir(parents=True)
    listed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "ls-files", "--", "docs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked_docs = [line for line in listed.stdout.splitlines() if line.strip()]
    assert tracked_docs, "git reported no tracked docs; the fixture is wrong"
    for relative in tracked_docs:
        source = ROOT / relative
        target = tracked_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())

    generated = tmp_path / "real-index.md"
    result = _invoke(
        generator,
        capsys,
        [
            "--root",
            str(tracked_root),
            "--commit",
            "fixture-commit",
            "--output",
            str(generated),
        ],
    )

    assert result[0] == 0, result[2] or result[1]
    assert generated.read_bytes() == (ROOT / "docs" / "INDEX.md").read_bytes()
