"""Hermetic contract tests for the generated-artifact drift gate.

The fixture checkouts contain a copy of the *real* official generator, so every
test exercises the same code path CI does, including the shim's refusal to run
when the generator's output cannot be redirected out of the checkout.

Line endings are the interesting part.  The official generator writes through
``Path.write_text(...)``, which emits the host newline, while ``.gitattributes``
pins ``*.json`` to LF in the index.  Both halves of the contract are therefore
tested explicitly:

* ``--line-endings normalized`` (the default) *allows* a line-ending-only
  difference, and the test asserts the gate says so out loud;
* ``--line-endings exact`` *detects* it, with the carriage return visible in the
  diff;
* in neither mode may the ``ignored_timestamp`` flag describe a difference that
  is not the ``generated_at`` value.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = ROOT / "scripts" / "check_generated_drift.py"
GENERATOR_PATH = ROOT / "backend" / "scripts" / "generate_feature_manifest.py"
MANIFEST_REL = Path("contracts/feature_manifest.json")


def _load_gate():
    spec = importlib.util.spec_from_file_location("alpha_generated_drift_gate_test", GATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace = tmp_path / "agent-workspace"
    workspace.mkdir()
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(workspace))
    return tmp_path


def _make_generator_fixture(tmp_path: Path) -> Path:
    """Create a minimal checkout that uses the real official generator."""
    repo = tmp_path / "fixture-repo"
    backend = repo / "backend"
    (backend / "scripts").mkdir(parents=True)
    (backend / "packages" / "harness" / "alpha" / "tools" / "builtins").mkdir(parents=True)
    (backend / "packages" / "harness" / "alpha" / "agents" / "middlewares").mkdir(parents=True)
    (backend / "app" / "gateway" / "routers").mkdir(parents=True)
    (backend / "app" / "gateway" / "autonomy").mkdir(parents=True)
    (repo / "contracts").mkdir()
    shutil.copy2(GENERATOR_PATH, backend / "scripts" / "generate_feature_manifest.py")

    (backend / "packages" / "harness" / "alpha" / "tools" / "tools.py").write_text("BUILTIN_TOOLS = []\n", encoding="utf-8")
    (backend / "packages" / "harness" / "alpha" / "tools" / "builtins" / "__init__.py").write_text("", encoding="utf-8")
    (backend / "app" / "gateway" / "app.py").write_text("from app.gateway.routers import sample\napp.include_router(sample.router)\n", encoding="utf-8")
    (backend / "app" / "gateway" / "routers" / "sample.py").write_text("router = object()\n", encoding="utf-8")
    (backend / "app" / "gateway" / "autonomy" / "supervisor.py").write_text('def register_default_loops():\n    return [("heartbeat", "Heartbeat", lambda: None, 60)]\n', encoding="utf-8")

    seed_output = tmp_path / "seed-output"
    result = subprocess.run(
        [sys.executable, "-c", _SEED_GENERATOR, str(backend / "scripts" / "generate_feature_manifest.py"), str(seed_output / "feature_manifest.json")],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    shutil.copy2(seed_output / "feature_manifest.json", repo / "contracts" / "feature_manifest.json")
    return repo


_SEED_GENERATOR = """
import importlib.util
import sys
from pathlib import Path

generator = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve()
spec = importlib.util.spec_from_file_location("seed_generator", generator)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.OUT = output
raise SystemExit(module.main())
"""


def _snapshot(repo: Path) -> dict[str, bytes]:
    return {path.relative_to(repo).as_posix(): path.read_bytes() for path in repo.rglob("*") if path.is_file()}


def _manifest(repo: Path) -> Path:
    return repo / MANIFEST_REL


def _read_manifest(repo: Path) -> dict:
    return json.loads(_manifest(repo).read_text(encoding="utf-8"))


def _write_manifest(repo: Path, payload: dict, *, newline: str = "\n") -> None:
    text = json.dumps(payload, indent=1) + "\n"
    _manifest(repo).write_bytes(text.replace("\n", newline).encode("utf-8"))


def _flip_line_endings(repo: Path) -> str:
    """Rewrite the committed artifact with the other line-ending style.

    The official generator emits the *host* newline, so the committed artifact
    is flipped relative to whatever this host produces.  That keeps the
    line-ending tests meaningful on a Windows checkout and a Linux runner alike.
    """
    payload = _read_manifest(repo)
    committed = _manifest(repo).read_bytes()
    if b"\r\n" in committed:
        opposite = "\n"
    else:
        opposite = "\r\n"
    payload["generated_at"] = "1999-01-01T00:00:00Z"
    _write_manifest(repo, payload, newline=opposite)
    return opposite


# --------------------------------------------------------------------------
# The clean baseline and hermeticity
# --------------------------------------------------------------------------


def test_clean_generated_tree_passes_without_repository_writes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_generator_fixture(tmp_path)
    before = _snapshot(repo)

    assert gate.run_gate(repo) == 0

    assert _snapshot(repo) == before, "the gate modified the checkout it inspects"
    output = capsys.readouterr().out
    assert "generated artifact drift: 0 file(s)" in output
    assert "only the generated_at value is ignored" in output


def test_gate_reports_the_line_ending_mode_it_used(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_generator_fixture(tmp_path)

    assert gate.run_gate(repo, line_endings=gate.LINE_ENDINGS_EXACT) == 0

    output = capsys.readouterr().out
    assert "compared on exact bytes" in output


def test_documented_generator_command_is_reported_and_the_shim_is_printable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_generator_fixture(tmp_path)

    assert gate.run_gate(repo) == 0

    output = capsys.readouterr().out
    assert gate.GENERATOR_COMMAND_DOC in output
    assert "shim command:" in output
    capsys.readouterr()
    assert gate.main(["--print-shim"]) == 0
    assert "module.OUT = output" in capsys.readouterr().out


def test_generator_command_redirects_output_out_of_the_checkout(tmp_path: Path) -> None:
    repo = _make_generator_fixture(tmp_path)
    output_dir = tmp_path / "scratch"
    output_dir.mkdir()

    command = gate.generator_command(repo, output_dir)

    assert Path(command[1]) == output_dir / gate.GATE_DIRNAME / gate.SHIM_NAME
    assert Path(command[2]).resolve() == (repo / gate.GENERATOR_REL).resolve()
    assert Path(command[3]).resolve() == (output_dir / "feature_manifest.json").resolve()
    assert Path(command[4]).resolve() == (repo / gate.MANIFEST_REL).resolve()


def test_gate_refuses_to_run_a_generator_that_cannot_be_redirected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The hermeticity interlock is load-bearing, so it is tested."""
    repo = _make_generator_fixture(tmp_path)
    generator = repo / gate.GENERATOR_REL
    generator.write_text(generator.read_text(encoding="utf-8") + "\nOUT = None\n", encoding="utf-8")

    assert gate.run_gate(repo) == 1

    output = capsys.readouterr().out
    assert "refusing to run" in output
    assert "ERROR" in output


# --------------------------------------------------------------------------
# Acceptance (b): a non-timestamp difference is drift, always
# --------------------------------------------------------------------------


def test_modified_generated_manifest_fails_with_a_useful_diff(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_generator_fixture(tmp_path)
    payload = _read_manifest(repo)
    payload["version"] = "tampered"
    _write_manifest(repo, payload)

    assert gate.run_gate(repo) == 1

    output = capsys.readouterr().out
    assert "DRIFT contracts/feature_manifest.json" in output
    assert "changed line(s)" in output
    assert "generated artifact drift: 1 file(s)" in output
    assert "tampered" in output


def test_non_timestamp_difference_is_never_labelled_a_timestamp_difference(tmp_path: Path) -> None:
    """The review's exact probe: a CRLF manifest must not be excused."""
    repo = _make_generator_fixture(tmp_path)
    payload = _read_manifest(repo)
    payload["version"] = "tampered"
    _write_manifest(repo, payload, newline="\r\n")
    committed = _manifest(repo).read_bytes()

    comparison = gate.compare_artifact(
        MANIFEST_REL,
        committed,
        committed.replace(b'"version": "tampered"', b'"version": "tampered2"'),
        ignore_manifest_timestamp=True,
    )

    assert comparison.drifted
    assert not comparison.ignored_timestamp, "a content difference claimed to be only a timestamp"


def test_timestamp_only_difference_is_allowed_and_disclosed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_generator_fixture(tmp_path)
    payload = _read_manifest(repo)
    payload["generated_at"] = "1999-01-01T00:00:00Z"
    _write_manifest(repo, payload)

    assert gate.run_gate(repo) == 0

    output = capsys.readouterr().out
    assert "disclosed: generated_at is the only content difference" in output


def test_unmaskable_manifest_is_compared_in_full(tmp_path: Path) -> None:
    """A manifest without a maskable generated_at field gets no free pass."""
    payload = json.dumps({"version": "1.0"}, indent=1) + "\n"
    other = json.dumps({"version": "1.0", "generated_at": "2020-01-01T00:00:00Z"}, indent=1) + "\n"

    comparison = gate.compare_artifact(
        MANIFEST_REL,
        payload.encode("utf-8"),
        other.encode("utf-8"),
        ignore_manifest_timestamp=True,
    )

    assert comparison.drifted
    assert not comparison.ignored_timestamp


# --------------------------------------------------------------------------
# Acceptance (a): a line-ending-only change is either detected or allowed
# --------------------------------------------------------------------------


def test_line_ending_only_change_is_allowed_by_normalized_mode_and_disclosed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_generator_fixture(tmp_path)
    _flip_line_endings(repo)

    assert gate.run_gate(repo, line_endings=gate.LINE_ENDINGS_NORMALIZED) == 0

    output = capsys.readouterr().out
    assert "line endings differ" in output
    assert "CRLF" in output
    assert "allowed by --line-endings normalized" in output


def test_line_ending_only_change_is_drift_in_exact_mode(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_generator_fixture(tmp_path)
    _flip_line_endings(repo)

    assert gate.run_gate(repo, line_endings=gate.LINE_ENDINGS_EXACT) == 1

    output = capsys.readouterr().out
    assert "DRIFT contracts/feature_manifest.json" in output
    assert "<CR>" in output, "the diff must show the carriage return it is complaining about"
    assert "generated_at is the only content difference that was masked" not in output, "exact mode must not excuse the difference as a timestamp"


def test_timestamp_flag_cannot_cover_a_line_ending_change_in_normalized_mode() -> None:
    """CRLF plus a new timestamp: allowed, but both differences are disclosed."""
    committed = b'{\n "generated_at": "1999-01-01T00:00:00Z",\n "version": "1.0"\n}\n'
    generated = b'{\r\n "generated_at": "2026-01-01T00:00:00Z",\r\n "version": "1.0"\r\n}\r\n'

    normalized = gate.compare_artifact(MANIFEST_REL, committed, generated, ignore_manifest_timestamp=True)
    exact = gate.compare_artifact(MANIFEST_REL, committed, generated, line_endings=gate.LINE_ENDINGS_EXACT, ignore_manifest_timestamp=True)

    assert not normalized.drifted
    assert normalized.ignored_timestamp, "the timestamp really is the only content difference here"
    assert normalized.newline_difference, "the line-ending difference must be disclosed, not silent"
    assert exact.drifted
    assert not exact.ignored_timestamp, "exact mode cannot call a raw-byte difference a timestamp"


def test_crlf_and_a_content_change_never_claims_to_be_timestamp_only() -> None:
    """The review's exact probe, on raw bytes: CRLF plus a content change."""
    committed = b'{\r\n "generated_at": "1999-01-01T00:00:00Z",\r\n "version": "1.0"\r\n}\r\n'
    generated = b'{\r\n "generated_at": "2026-01-01T00:00:00Z",\r\n "version": "1.1"\r\n}\r\n'

    for mode in (gate.LINE_ENDINGS_NORMALIZED, gate.LINE_ENDINGS_EXACT):
        comparison = gate.compare_artifact(MANIFEST_REL, committed, generated, line_endings=mode, ignore_manifest_timestamp=True)
        assert comparison.drifted, f"{mode} mode let a content change through"
        assert not comparison.ignored_timestamp, f"{mode} mode blamed the timestamp for a content change"


# --------------------------------------------------------------------------
# Missing, malformed and hostile inputs
# --------------------------------------------------------------------------


def test_missing_committed_manifest_is_drift(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_generator_fixture(tmp_path)
    _manifest(repo).unlink()

    assert gate.run_gate(repo) == 1

    output = capsys.readouterr().out
    assert "committed file is missing" in output


def test_missing_generator_is_an_error_not_a_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_generator_fixture(tmp_path)
    (repo / gate.GENERATOR_REL).unlink()

    assert gate.run_gate(repo) == 1

    assert "official generator is missing" in capsys.readouterr().out


def test_malformed_committed_manifest_is_labelled(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_generator_fixture(tmp_path)
    _manifest(repo).write_bytes(b"{not json\n")

    assert gate.run_gate(repo) == 1

    assert "not valid UTF-8 JSON" in capsys.readouterr().out


def test_failing_generator_fails_the_gate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_generator_fixture(tmp_path)
    generator = repo / gate.GENERATOR_REL
    source = generator.read_text(encoding="utf-8")
    generator.write_text(source.replace("def main() -> int:", "def main() -> int:\n    raise SystemExit(3)\n", 1), encoding="utf-8")

    assert gate.run_gate(repo) == 1

    assert "generator exited with code 3" in capsys.readouterr().out


def test_generator_timeout_fails_closed_instead_of_hanging(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_generator_fixture(tmp_path)
    generator = repo / gate.GENERATOR_REL
    source = generator.read_text(encoding="utf-8")
    generator.write_text(source.replace("def main() -> int:", "def main() -> int:\n    import time; time.sleep(600)\n", 1), encoding="utf-8")

    assert gate.run_gate(repo, generator_timeout=2) == 1

    output = capsys.readouterr().out
    assert "exceeded its 2s budget and was killed" in output


def test_generator_output_the_gate_does_not_track_is_drift(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_generator_fixture(tmp_path)
    generator = repo / gate.GENERATOR_REL
    source = generator.read_text(encoding="utf-8")
    extra = source.replace("    OUT.parent.mkdir(parents=True, exist_ok=True)", "    (OUT.parent / 'extra_report.json').write_text('{}')\n    OUT.parent.mkdir(parents=True, exist_ok=True)", 1)
    generator.write_text(extra, encoding="utf-8")

    assert gate.run_gate(repo) == 1

    assert "extra_report.json" in capsys.readouterr().out


def test_diff_report_is_written_only_outside_the_repository(tmp_path: Path) -> None:
    repo = _make_generator_fixture(tmp_path)
    report = tmp_path / "artifacts" / "generated-drift.diff"
    payload = _read_manifest(repo)
    payload["version"] = "tampered"
    _write_manifest(repo, payload)

    assert gate.run_gate(repo, diff_output=report) == 1
    assert "DRIFT contracts/feature_manifest.json" in report.read_text(encoding="utf-8")


def test_diff_report_inside_the_repository_is_refused(tmp_path: Path) -> None:
    repo = _make_generator_fixture(tmp_path)

    with pytest.raises(gate.GateError, match="outside --repo-root"):
        gate.run_gate(repo, diff_output=repo / "drift.diff")


# --------------------------------------------------------------------------
# CLI surface: the help text and the docstring must agree with the behaviour
# --------------------------------------------------------------------------


def test_cli_help_documents_both_line_ending_modes() -> None:
    help_text = gate._parser().format_help()

    assert "normalized" in help_text
    assert "exact" in help_text
    assert "no\nnormalisation" in help_text or "no normalisation" in help_text


def test_cli_rejects_bad_usage_with_exit_code_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert gate.main(["--max-diff-lines", "0"]) == 2
    assert gate.main(["--generator-timeout", "0"]) == 2
    with pytest.raises(SystemExit) as excinfo:
        gate.main(["--line-endings", "loose"])
    assert excinfo.value.code == 2
    capsys.readouterr()


def test_docstring_and_contract_agree_on_what_is_ignored() -> None:
    docstring = gate.__doc__ or ""
    help_text = gate._parser().format_help()

    for text in (docstring, help_text):
        assert "generated_at" in text
        assert "normalized" in text
        assert "exact" in text
    # The old wording claimed byte fidelity while normalising newlines.
    assert "every other byte is drift" in docstring
    assert "byte-identical" not in docstring


# --------------------------------------------------------------------------
# The real checkout
# --------------------------------------------------------------------------


def test_this_checkout_is_a_real_measurement() -> None:
    """Runs the gate on the repository itself and asserts it is hermetic."""
    before = (ROOT / gate.MANIFEST_REL).read_bytes()

    exit_code = gate.run_gate(ROOT)

    assert (ROOT / gate.MANIFEST_REL).read_bytes() == before, "the gate rewrote the committed artifact"
    assert exit_code in (0, 1), "the gate must reach a verdict, never crash"
