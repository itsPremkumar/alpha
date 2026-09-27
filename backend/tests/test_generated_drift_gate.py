"""Hermetic contract tests for the two CI gate scripts under ``scripts/``.

Both gates live here because they share one failure mode worth pinning: a gate
that hangs is not a gate, and a gate that cannot fail is worse than no gate.

The drift-gate fixture checkouts contain a copy of the *real* official
generator, so every test exercises the same code path CI does, including the
shim's refusal to run when the generator's output cannot be redirected out of
the checkout.

Line endings are the interesting part of the drift gate.  The official generator
writes through ``Path.write_text(...)``, which emits the host newline, while
``.gitattributes`` pins ``*.json`` to LF in the index.  Both halves of the
contract are therefore tested explicitly:

* ``--line-endings normalized`` (the default) *allows* a line-ending-only
  difference, and the test asserts the gate says so out loud;
* ``--line-endings exact`` *detects* it, with the carriage return visible in the
  diff;
* in neither mode may the ``ignored_timestamp`` flag describe a difference that
  is not the ``generated_at`` value.

The lint-gate section drives a real child that would otherwise sleep for ten
minutes, and asserts it is killed within its budget - the regression the review
flagged, where a wedged ``git`` or ``ruff`` held the job open instead of failing
it.  The CI-wiring section parses the workflows themselves, because a correct
script that CI never runs, or a failure CI hides, is not a gate either.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = ROOT / "scripts" / "check_generated_drift.py"
LINT_GATE_PATH = ROOT / "scripts" / "check_changed_python_lint.py"
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


def _load_lint_gate():
    spec = importlib.util.spec_from_file_location("alpha_changed_python_lint_gate_test", LINT_GATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lint_gate = _load_lint_gate()


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
    """The printed contract must be the documented contract, verbatim."""
    docstring = gate.__doc__ or ""
    help_text = gate._parser().format_help()

    for text in (docstring, help_text):
        assert "generated_at" in text
        assert "normalized" in text
        assert "exact" in text
    # The exact sentence the gate prints on every run, whitespace-normalised.
    flat = " ".join(docstring.split())
    assert "only the generated_at value is ignored; every other byte is drift" in flat
    # A normalised comparison must never be described as unqualified byte fidelity.
    assert "compared on raw bytes" not in flat


def test_lint_gate_holds_every_scope_to_one_repository_policy(tmp_path: Path) -> None:
    """A verdict that depends on the caller's working directory is not a verdict.

    This used to be enforced by splitting the changed files in two: those under
    ``backend/`` had a ``ruff.toml`` above them, and every other file was handed
    ``--isolated``.  Two directories were therefore measured by two different
    rule sets -- measured on this host, an unconfigured file such as
    ``scripts/check_changed_python_lint.py`` was held to line-length 88 from the
    repository root and 240 from ``backend/`` -- and which set a file landed in
    depended on where the gate happened to be launched.

    There is now one policy at the repository root that ``backend/ruff.toml``
    extends, and the gate proves it instead of assuming it.  It picks one
    representative file per configuration scope, asks ruff which settings that
    file actually resolved to, and fails closed when a scope resolves to no
    configuration at all -- ruff then silently falls back to its own defaults,
    which is exactly how 108 findings outside ``backend/`` went unmeasured -- or
    resolves to anything other than the repository policy, or resolves with a
    blanket ``exclude``/``per_file_ignores`` that could hide findings behind it.
    """
    # One representative per configuration scope, shallowest first.  Two files in
    # one directory always resolve identically, so ``tools/deep/nested`` is
    # already covered by ``tools/deep``; two sibling directories under backend/
    # are two scopes and each has to be asked.  Paths are absolute because ruff
    # is launched from ruff_cwd, where a repo-relative path would resolve
    # against the wrong root.
    paths = [
        "C:/repo/scripts/check_changed_python_lint.py",
        "C:/repo/backend/agents/lead.py",
        "C:/repo/backend/tests/test_x.py",
        "C:/repo/tools/deep/x.py",
        "C:/repo/tools/deep/nested/y.py",
    ]
    assert lint_gate._scope_representatives(paths) == [
        "C:/repo/scripts/check_changed_python_lint.py",
        "C:/repo/backend/agents/lead.py",
        "C:/repo/backend/tests/test_x.py",
        "C:/repo/tools/deep/x.py",
    ]

    # The repository's own config is the rule set, so the answer does not move
    # with the caller's cwd.  Read it from the real checkout, where the policy
    # is actually authored.
    line_length, target_version, selected = lint_gate._expected_policy(ROOT)
    assert isinstance(line_length, int) and line_length > 0, "policy must declare a numeric line-length"
    assert target_version.startswith("py"), f"unexpected target-version {target_version!r}"
    assert selected and all(isinstance(rule, str) and rule for rule in selected), "policy must select rules"
    # and it must be the same file the whole tree is measured against
    assert (ROOT / "ruff.toml").is_file(), "the repository-wide policy is missing"

    # Failing closed is the point: with no policy above it the gate must refuse
    # to guess, because a gate that proceeds is a gate that reports a clean tree
    # it never measured.
    with pytest.raises(lint_gate.GateError) as excinfo:
        lint_gate._expected_policy(tmp_path)
    assert "missing" in str(excinfo.value)
    # ...and policy verification must refuse before it ever launches ruff
    with pytest.raises(lint_gate.GateError) as excinfo:
        lint_gate._verify_policy(tmp_path, tmp_path / "x.py",
                                 ruff_cwd=tmp_path, timeout=5)
    assert "missing" in str(excinfo.value)

    # No scope is measured with ruff's built-in defaults any more, and the gate
    # must never turn that back on.
    command = lint_gate._ruff_command(Path("C:/repo/backend"), "check",
                                      [Path("C:/repo/scripts/x.py")])
    assert "--isolated" not in command, "a default-configuration scope is a hole in the measurement"
    assert "isolated=True" not in LINT_GATE_PATH.read_text(encoding="utf-8"), (
        "the gate itself must not enable --isolated; every scope is measured "
        "against the repository policy")
    # the escape hatch still exists for callers that ask for it explicitly
    assert "--isolated" in lint_gate._ruff_command(Path("C:/repo/backend"), "format",
                                                   [Path("C:/repo/scripts/x.py")],
                                                   isolated=True)
    assert "--isolated" not in lint_gate._ruff_command(Path("C:/repo/backend"), "format",
                                                       [Path("C:/repo/backend/x.py")],
                                                       isolated=False)


# --------------------------------------------------------------------------
# The real checkout
# --------------------------------------------------------------------------


def test_this_checkout_is_a_real_measurement() -> None:
    """Runs the gate on the repository itself and asserts it is hermetic."""
    before = (ROOT / gate.MANIFEST_REL).read_bytes()

    exit_code = gate.run_gate(ROOT)

    assert (ROOT / gate.MANIFEST_REL).read_bytes() == before, "the gate rewrote the committed artifact"
    assert exit_code in (0, 1), "the gate must reach a verdict, never crash"


# --------------------------------------------------------------------------
# The incremental lint gate: a stall must fail, never hang
# --------------------------------------------------------------------------

_SLEEPER = "import time; time.sleep(600)\n"


def _git_repo(tmp_path: Path) -> Path:
    """A minimal two-commit repository with one clean Python file.

    It carries a repository-wide ``ruff.toml`` because the gate refuses to
    trust a ruff verdict it cannot attribute to the repository policy, and it
    checks that *before* launching ruff.  A fixture without one is not a
    repository this gate can run against: it dies at policy lookup, so any test
    built on this fixture that means to exercise a later step -- a wedged ruff,
    say -- silently stops testing it and passes for the wrong reason.
    """
    repo = tmp_path / "lint-repo"
    (repo / "backend").mkdir(parents=True)
    target = repo / "backend" / "clean_module.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    # The two keys _expected_policy insists on: a numeric line-length and a
    # non-empty lint.select. Deliberately minimal -- this fixture stands for
    # "any repository", not for this project's own rule set.
    (repo / "ruff.toml").write_text(
        'line-length = 100\n\n[lint]\nselect = ["E", "F"]\n',
        encoding="utf-8",
    )
    for args in (
        ["init", "-q"],
        ["config", "user.email", "gate@example.invalid"],
        ["config", "user.name", "gate"],
        ["config", "commit.gpgsign", "false"],
        ["add", "-A"],
        ["commit", "-q", "-m", "seed"],
    ):
        result = lint_gate.run_command(["git", *args], cwd=repo, timeout=60)
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return repo


def test_lint_gate_kills_a_stalled_child_within_its_budget() -> None:
    """A child that starts and then wedges must not outlive its timeout."""
    import time

    started = time.monotonic()
    with pytest.raises(lint_gate.CommandTimeout) as excinfo:
        lint_gate.run_command([sys.executable, "-c", _SLEEPER], timeout=2)
    elapsed = time.monotonic() - started

    assert elapsed < 60, f"the stall was not bounded: {elapsed:.1f}s"
    assert "exceeded its 2s budget and was killed" in str(excinfo.value)


def test_lint_gate_kills_the_whole_process_tree(tmp_path: Path) -> None:
    """`uv run ruff` is a grandchild; killing only the parent leaks it.

    The parent records that it spawned the grandchild, so a failure can be told
    apart from a race where the kill simply arrived before the spawn did.  The
    grandchild's marker lands well after the gate's budget expires, so a
    surviving grandchild is observable rather than inferred.
    """
    import time

    leaked = tmp_path / "grandchild-survived.txt"
    spawned = tmp_path / "grandchild-spawned.txt"
    grandchild = f"import pathlib, time; time.sleep(12); pathlib.Path({str(leaked)!r}).write_text('leaked')"
    parent = f"import pathlib, subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {grandchild!r}]); pathlib.Path({str(spawned)!r}).write_text('ok'); time.sleep(600)"

    with pytest.raises(lint_gate.CommandTimeout):
        lint_gate.run_command([sys.executable, "-c", parent], timeout=6)

    assert spawned.is_file(), "the grandchild was never spawned; the test raced its own fixture"
    time.sleep(15)
    assert not leaked.exists(), "a grandchild outlived the gate's timeout"


def test_lint_gate_fails_when_ruff_stalls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """End to end: a wedged ruff must produce exit 1, not a stuck job."""
    import time

    repo = _git_repo(tmp_path)
    base = lint_gate.run_command(["git", "rev-parse", "HEAD"], cwd=repo, timeout=60).stdout.decode().strip()
    (repo / "backend" / "second_module.py").write_text("VALUE = 2\n", encoding="utf-8")
    for args in (["add", "-A"], ["commit", "-q", "-m", "second"]):
        assert lint_gate.run_command(["git", *args], cwd=repo, timeout=60).returncode == 0
    head = lint_gate.run_command(["git", "rev-parse", "HEAD"], cwd=repo, timeout=60).stdout.decode().strip()
    monkeypatch.setattr(lint_gate, "_ruff_command", lambda *a, **k: [sys.executable, "-c", _SLEEPER])

    started = time.monotonic()
    exit_code = lint_gate.main(["--repo-root", str(repo), "--base-ref", base, "--head-ref", head, "--ruff-timeout", "2"])
    elapsed = time.monotonic() - started

    assert exit_code == 1
    assert elapsed < 60, f"the gate hung for {elapsed:.1f}s instead of failing"
    assert "exceeded its 2s budget and was killed" in capsys.readouterr().err


def test_lint_gate_fails_when_git_stalls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The git side of the gate fails closed on a timeout too."""
    repo = _git_repo(tmp_path)

    def _stalled(*args, **kwargs):
        raise lint_gate.CommandTimeout("command exceeded its 120s budget and was killed: git diff")

    monkeypatch.setattr(lint_gate, "run_command", _stalled)

    assert lint_gate.main(["--repo-root", str(repo), "--base-ref", "HEAD", "--head-ref", "HEAD"]) == 1
    assert "exceeded its 120s budget" in capsys.readouterr().err


def test_lint_gate_reports_a_clean_tree_and_rejects_unpaired_refs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _git_repo(tmp_path)
    head = lint_gate.run_command(["git", "rev-parse", "HEAD"], cwd=repo, timeout=60).stdout.decode().strip()

    assert lint_gate.main(["--repo-root", str(repo), "--base-ref", head, "--head-ref", head]) == 0
    # The gate prints this only after walking every changed file and explaining
    # each one it skipped, so the number it reports is "lintable", not "changed".
    # Pinning the real wording keeps the promise honest when both are zero and
    # they read the same only by coincidence.
    assert "incremental ruff gate: 0 (no lintable changed Python files)" in capsys.readouterr().out

    assert lint_gate.main(["--base-ref", "HEAD"]) == 2
    assert lint_gate.main(["--ruff-timeout", "0"]) == 2
    capsys.readouterr()


def test_lint_gate_help_documents_its_timeouts() -> None:
    help_text = lint_gate._parser().format_help()

    assert "--git-timeout" in help_text
    assert "--ruff-timeout" in help_text
    assert "--generator-timeout" not in help_text
    assert "killed" in (lint_gate.__doc__ or "")


# --------------------------------------------------------------------------
# CI wiring: a script nobody runs, or a failure nobody sees, is not a gate
# --------------------------------------------------------------------------

WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> dict:
    import yaml

    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _steps(name: str, job: str) -> list[dict]:
    return _workflow(name)["jobs"][job]["steps"]


def _runs(name: str, job: str) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(name, job))


def test_frontend_workflow_runs_the_branding_suite() -> None:
    """`pnpm test` only globs src/lib, so the branding pin needs its own step."""
    runs = _runs("frontend-unit-tests.yml", "frontend-unit-tests")

    assert "pnpm test:branding" in runs
    assert "pnpm test" in runs
    branding = next(step for step in _steps("frontend-unit-tests.yml", "frontend-unit-tests") if "test:branding" in str(step.get("run", "")))
    assert "continue-on-error" not in branding, "the branding pin must be able to fail the job"


def test_generated_drift_workflow_runs_both_line_ending_modes() -> None:
    runs = _runs("generated-drift-gate.yml", "generated-drift")

    assert "--line-endings exact" in runs, "CI must pin the strict byte-fidelity check"
    assert "--line-endings normalized" in runs, "CI must also run the disclosed default"
    assert "--generator-timeout" in runs
    job = _workflow("generated-drift-gate.yml")["jobs"]["generated-drift"]
    assert job.get("timeout-minutes")


def test_lint_workflow_gates_changed_files_with_explicit_timeouts() -> None:
    workflow = _workflow("lint-check.yml")
    runs = _runs("lint-check.yml", "lint-backend")

    assert "scripts/check_changed_python_lint.py" in runs
    assert runs.count("--git-timeout") == 2, "both the pull-request and push invocations need a bound"
    assert runs.count("--ruff-timeout") == 2
    assert workflow["jobs"]["lint-backend"].get("timeout-minutes")


def test_docs_index_gate_is_untouched_and_still_gating() -> None:
    """The new gate must not have disarmed the gate main already had."""
    workflow = _workflow("lint-check.yml")
    job = workflow["jobs"]["docs-index"]

    assert "scripts/check_docs_index_drift.py --json" in "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "continue-on-error" not in job
    assert "if: always()" not in job, "the documentation index gate is gating, not a report"


def test_no_gate_failure_is_swallowed_without_disclosure() -> None:
    """`continue-on-error`, `|| true` and bare `set +e` must not appear.

    The two non-gating debt reports are allowed to measure a failing checker,
    but only because they capture its exit code, print it, label themselves
    non-gating, and still fail when the report itself cannot be produced.  That
    disclosure is asserted here rather than trusted to review.

    Only executable lines are scanned.  A YAML comment cannot hide a failure,
    and two workflows *document* the ban -- cold-start-budget.yml says "no
    `continue-on-error`, no `|| true`" and windows-installer.yml says
    "`continue-on-error`: exceeding the budget must fail the build" -- so a
    literal substring scan reported the sentence forbidding the pattern as an
    instance of it.  A false positive like that is not harmless: the first
    person to hit it deletes the comment that was doing the work, and the next
    real directive walks in behind it.

    The positive control below runs the same scan over a tiny fixture, so a
    green run here can never mean the scan quietly stopped looking.
    """

    def _hidden(workflow: str) -> str | None:
        """The construct that would hide a failure, ignoring YAML comments."""
        executable = "\n".join(
            line for line in workflow.splitlines()
            if not line.lstrip().startswith("#")
        )
        if re.search(r"^\s*continue-on-error\s*:", executable, re.MULTILINE):
            return "continue-on-error"
        if "|| true" in executable:
            return "|| true"
        return None

    # Positive control: the comment must be tolerated and the directive caught.
    # Without this, the narrowed scan could pass forever by matching nothing.
    assert _hidden("# no continue-on-error and no || true here\n") is None, "the scan now flags the documentation of the ban"
    assert _hidden("jobs:\n  build:\n    continue-on-error: true\n") == "continue-on-error", "the scan no longer sees a real continue-on-error directive"
    assert _hidden("      run: make check || true\n") == "|| true", "the scan no longer sees || true"
    assert _hidden("      run: |\n        # harmless || true in a shell comment\n") is None, "a shell comment must not be flagged"

    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        assert _hidden(text) is None, f"{path.name} would hide a failure"
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if line.lstrip().startswith("#"):
                # Prose about errexit cannot disable errexit.
                continue
            if "set +e" in line:
                # Locality matters: the disclosure has to sit next to the line
                # that disables errexit, not twenty lines below it.
                window = "\n".join(lines[index : index + 8])
                assert re.search(r"status=\$\?", window), f"{path.name}:{index + 1} disables errexit without capturing an exit code"
                assert "non-gating" in window, f"{path.name}:{index + 1} disables errexit without labelling itself non-gating"


def test_non_gating_reports_still_fail_when_they_cannot_produce_output() -> None:
    runs = _runs("lint-check.yml", "agent-guidance-debt-report")
    assert "non-gating" in runs
    assert "exit 1" in runs, "an empty report must fail the step, not pass quietly"
    debt = _runs("lint-check.yml", "ruff-debt-report")
    assert "non-gating" in debt
    # Both debt reports are shell steps, so `exit 1` is the idiom that makes an
    # empty report fatal -- the same guarantee line 712 pins for the sibling job.
    assert "exit 1" in debt
    # and the capture has to be real: set +e without reading $? would swallow
    # the checker's own failure before the empty-report check could run
    assert "status=$?" in debt
