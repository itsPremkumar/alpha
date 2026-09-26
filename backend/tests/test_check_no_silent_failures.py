"""Tests for the silent-failure CI gate itself.

A gate that cannot fail is worse than no gate, because it is trusted. These
tests prove three things:

1. **It detects the real thing** -- ``except: pass`` and a handler that swallows
   while doing unrelated work, in every breadth from ``bare`` to ``Exception``.
2. **It does not flag legitimate handling** -- re-raise, log, report through
   ``alpha.errors``, or propagate the caught error as data. False positives
   train people to ignore the gate, which is how it dies.
3. **It is a ratchet that only tightens** -- ``--update-baseline`` is structurally
   prune-only, ``--accept-new`` is the separate deliberate act that admits debt,
   and the recorded baseline covers the current tree so the build is green today.

The detection cases run against source strings in a temp tree, so they are fast
and independent of what the rest of the repository happens to contain.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_no_silent_failures.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("_check_no_silent_failures", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec: a ``dataclass(slots=True)`` resolves annotations
    # through sys.modules[cls.__module__], which does not exist yet otherwise.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GATE = _load_gate()


def _find(source: str, path: str = "mod.py"):
    findings, _suppressed, handlers, _count = GATE.find_in_file(path, source)
    return findings, handlers


class TestDetection:
    @pytest.mark.parametrize(
        ("label", "source", "expected"),
        [
            ("bare pass", "try:\n    f()\nexcept:\n    pass\n", 1),
            ("bare ellipsis", "try:\n    f()\nexcept:\n    ...\n", 1),
            ("narrow pass", "try:\n    f()\nexcept ValueError:\n    pass\n", 1),
            ("exception pass", "try:\n    f()\nexcept Exception:\n    pass\n", 1),
            ("control flow swallowed", "try:\n    f()\nexcept KeyboardInterrupt:\n    pass\n", 1),
            ("continue", "for x in y:\n    try:\n        f(x)\n    except ValueError:\n        continue\n", 1),
            ("break", "while a:\n    try:\n        f()\n    except ValueError:\n        break\n", 1),
            ("return none", "def f():\n    try:\n        g()\n    except ValueError:\n        return\n", 1),
            ("default value", "def f():\n    try:\n        return int(x)\n    except ValueError:\n        return 0\n", 1),
            ("swallow plus unrelated work", "try:\n    f()\nexcept Exception:\n    total += 1\n", 1),
            ("bound name never read", "try:\n    f()\nexcept ValueError as exc:\n    pass\n", 1),
        ],
    )
    def test_silent_failures_are_found(self, label, source, expected):
        findings, _handlers = _find(source)
        assert len(findings) == expected, f"{label}: expected {expected} finding(s), got {findings}"

    @pytest.mark.parametrize(
        ("label", "source"),
        [
            ("bare re-raise", "try:\n    f()\nexcept:\n    raise\n"),
            ("re-raise new", "try:\n    f()\nexcept ValueError as e:\n    raise RuntimeError('x') from e\n"),
            ("logger.warning", "try:\n    f()\nexcept ValueError:\n    logger.warning('failed')\n"),
            ("logger.exception", "try:\n    f()\nexcept ValueError:\n    logger.exception('failed')\n"),
            ("self.logger.error", "try:\n    f()\nexcept ValueError:\n    self.logger.error('failed')\n"),
            ("logging module", "try:\n    f()\nexcept ValueError:\n    logging.error('failed')\n"),
            ("report_error", "try:\n    f()\nexcept ValueError as e:\n    report_error('TIMEOUT', exc=e)\n"),
            ("report_exception", "try:\n    f()\nexcept Exception as e:\n    report_exception(e)\n"),
            ("propagate as data", "try:\n    f()\nexcept ValueError as e:\n    return _unavailable(str(e))\n"),
            ("append the error", "try:\n    f()\nexcept ValueError as e:\n    self.errors.append(e)\n"),
            ("warnings.warn", "try:\n    f()\nexcept ValueError:\n    warnings.warn('degraded')\n"),
        ],
    )
    def test_legitimate_handling_is_not_flagged(self, label, source):
        findings, _handlers = _find(source)
        assert findings == [], f"{label} was wrongly flagged as a silent failure: {findings}"

    def test_nested_handler_does_not_launder_the_outer_one(self):
        """A parent's silence cannot hide behind a nested handler's log line."""
        source = (
            "try:\n"
            "    f()\n"
            "except ValueError:\n"
            "    try:\n"
            "        g()\n"
            "    except TypeError:\n"
            "        logger.error('inner')\n"
        )
        findings, _handlers = _find(source)
        assert len(findings) == 1
        assert findings[0].lineno == 3, "the outer handler is the one that swallowed"

    def test_no_handler_means_no_finding(self):
        findings, handlers = _find("def f():\n    return 1\n")
        assert findings == [] and handlers == 0

    def test_breadth_is_classified(self):
        assert GATE.breadth_of(_handler("except:\n    pass\n")) == GATE.BREADTH_BARE
        assert GATE.breadth_of(_handler("except BaseException:\n    pass\n")) == GATE.BREADTH_BASE
        assert GATE.breadth_of(_handler("except SystemExit:\n    pass\n")) == GATE.BREADTH_CONTROL
        assert GATE.breadth_of(_handler("except Exception:\n    pass\n")) == GATE.BREADTH_EXCEPTION
        assert GATE.breadth_of(_handler("except ValueError:\n    pass\n")) == GATE.BREADTH_NARROW

    def test_shape_is_classified(self):
        assert GATE.shape_of(_handler("except ValueError:\n    pass\n")) == "inert"
        assert GATE.shape_of(_handler("except ValueError:\n    return 0\n")) == "return-fallback"
        assert GATE.shape_of(_handler("except ValueError:\n    x = 0\n")) == "default-value"
        assert GATE.shape_of(_handler("except ValueError:\n    raise\n")) == "re-raise"

    def test_fingerprint_survives_unrelated_edits_above_the_handler(self):
        top = "try:\n    f()\nexcept ValueError:\n    pass\n"
        padded = "# a new comment\ndef unrelated():\n    return 1\n\n\n" + top
        assert _fingerprint(top) == _fingerprint(padded)

    def test_fingerprint_ignores_reformatting_and_comments(self):
        plain = "try:\n    f()\nexcept ValueError:\n    pass\n"
        noisy = "try:\n    f()  # trailing\nexcept ValueError:\n    # why\n    pass\n"
        assert _fingerprint(plain) == _fingerprint(noisy)

    def test_fingerprint_changes_when_the_handler_changes(self):
        """A waiver must not survive the fix it was written for."""
        before = "try:\n    f()\nexcept ValueError:\n    pass\n"
        after = "try:\n    f()\nexcept ValueError:\n    logger.error('failed')\n"
        assert _fingerprint(before) != _fingerprint(after)

    def test_declared_suppress_is_reported_but_is_not_a_violation(self):
        source = "from contextlib import suppress\nwith suppress(ValueError):\n    f()\n"
        findings, suppressed, _handlers, count = GATE.find_in_file("mod.py", source)
        assert findings == [], "a named suppress() is a declared swallow, not a silent one"
        assert len(suppressed) == 1 and count == 1


class TestRatchet:
    def test_prune_only_never_adds(self):
        """The routine sync step must be unable to absorb a new violation."""
        baseline = {"a.py": {"narrow:aaa": 1}}
        actual = {"a.py": {"narrow:aaa": 1, "narrow:bbb": 1}}
        pruned, removed = GATE.prune_baseline(baseline, actual)
        assert pruned == {"a.py": {"narrow:aaa": 1}}
        assert "narrow:bbb" not in pruned["a.py"], "prune must not admit the new fingerprint"
        assert removed == 0

    def test_prune_removes_resolved_debt(self):
        baseline = {"a.py": {"narrow:aaa": 2, "narrow:bbb": 1}}
        actual = {"a.py": {"narrow:aaa": 1}}
        pruned, removed = GATE.prune_baseline(baseline, actual)
        assert pruned == {"a.py": {"narrow:aaa": 1}}
        assert removed == 2

    def test_prune_drops_fully_resolved_files(self):
        baseline = {"a.py": {"narrow:aaa": 1}, "b.py": {"narrow:bbb": 1}}
        pruned, removed = GATE.prune_baseline(baseline, {"a.py": {"narrow:aaa": 1}})
        assert set(pruned) == {"a.py"}
        assert removed == 1

    def test_duplicates_are_counted_not_deduplicated(self):
        """Two identical handlers are two places a failure can vanish."""
        source = "try:\n    f()\nexcept ValueError:\n    pass\n" * 2
        findings, _handlers = _find(source)
        assert len(findings) == 2
        assert findings[0].fingerprint == findings[1].fingerprint

    def test_new_findings_are_reported_against_the_baseline(self):
        result = GATE.ScanResult(findings=[GATE.Finding("a.py", 3, "narrow", "inert", "narrow:new")])
        new, resolved = GATE.compare(result, {})
        assert len(new) == 1 and resolved == []

    def test_waived_findings_are_not_reported_as_new(self):
        result = GATE.ScanResult(findings=[GATE.Finding("a.py", 3, "narrow", "inert", "narrow:aaa")])
        new, resolved = GATE.compare(result, {"a.py": {"narrow:aaa": 1}})
        assert new == [] and resolved == []

    def test_a_second_identical_handler_is_still_new(self):
        result = GATE.ScanResult(
            findings=[
                GATE.Finding("a.py", 3, "narrow", "inert", "narrow:aaa"),
                GATE.Finding("a.py", 9, "narrow", "inert", "narrow:aaa"),
            ]
        )
        new, _resolved = GATE.compare(result, {"a.py": {"narrow:aaa": 1}})
        assert len(new) == 1 and new[0].lineno == 9, "a waiver covers exactly one copy"

    def test_rebase_rewrites_between_markers_and_is_idempotent(self):
        text = SCRIPT.read_text(encoding="utf-8")
        mapping = {"z.py": {"narrow:zzz": 2}, "a.py": {"bare:aaa": 1}}
        once = GATE.rebase_baseline(text, mapping)
        twice = GATE.rebase_baseline(once, mapping)
        assert once == twice, "rebasing must be idempotent"
        assert [line for line in once.splitlines() if line.strip() == GATE._BASELINE_BEGIN] == [GATE._BASELINE_BEGIN]
        assert '    "a.py": {"bare:aaa": 1},' in once
        assert '    "z.py": {"narrow:zzz": 2},' in once
        # Nothing outside the block moved.
        begin = text.index(GATE._BASELINE_BEGIN)
        assert once.startswith(text[:begin])

    def test_rebase_wraps_a_file_with_many_entries(self):
        """A generated block must never trip the linter's line-length limit."""
        mapping = {"big.py": {f"narrow:{index:016x}": 1 for index in range(30)}}
        emitted = "\n".join(GATE._emit_baseline(mapping))
        longest = max(len(line) for line in emitted.splitlines())
        assert longest <= GATE._BASELINE_MAX_WIDTH, f"generated a {longest}-char line"
        assert '"narrow:0000000000000000": 1,' in emitted

    def test_rebase_accepts_the_whole_shipped_baseline_within_the_line_limit(self):
        emitted = "\n".join(GATE._emit_baseline(GATE.BASELINE))
        longest = max(len(line) for line in emitted.splitlines())
        assert longest <= GATE._BASELINE_MAX_WIDTH, f"the shipped baseline would emit a {longest}-char line"

    def test_rebase_refuses_a_file_without_markers(self):
        with pytest.raises(RuntimeError):
            GATE.rebase_baseline("x = 1\n", {"a.py": {"b": 1}})

    def test_accept_new_requires_update_baseline(self, capsys):
        assert GATE.main(["--accept-new", "--root", str(REPO_ROOT)]) == 2
        assert "--accept-new requires --update-baseline" in capsys.readouterr().err


class TestScanAndCli:
    def test_scan_checks_every_file_even_without_the_keyword(self, tmp_path):
        (tmp_path / "clean.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (tmp_path / "bad.py").write_text("try:\n    f()\nexcept ValueError:\n    pass\n", encoding="utf-8")
        result = GATE.scan(tmp_path, scan_dirs=(".",))
        assert result.files_scanned == 2, "every file must be parsed, so 'unparsed' stays meaningful"
        assert len(result.findings) == 1

    def test_scan_skips_vendored_and_cache_directories(self, tmp_path):
        for name in (".venv", "__pycache__", "node_modules"):
            hidden = tmp_path / name
            hidden.mkdir()
            (hidden / "x.py").write_text("try:\n    f()\nexcept ValueError:\n    pass\n", encoding="utf-8")
        assert GATE.scan(tmp_path, scan_dirs=(".",)).findings == []

    def test_an_unparseable_file_is_reported_not_ignored(self, tmp_path):
        """No pre-filter may hide a file that cannot be parsed.

        This is the failure the substring fast-path would have caused: a broken
        file is exactly the one a caller must be told about, and a substring
        test cannot tell "has no handler" from "is not Python".
        """
        (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
        result = GATE.scan(tmp_path, scan_dirs=(".",))
        assert len(result.unparsed) == 1
        assert result.unparsed[0][0] == "broken.py"

    def test_an_unparseable_file_makes_the_cli_exit_two(self, tmp_path):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "broken.py").write_text("def f(:\n", encoding="utf-8")
        assert GATE.main(["--root", str(tmp_path), "--quiet"]) == 2

    def test_a_utf8_bom_file_is_valid_python_not_unparseable(self, tmp_path):
        """Regression: CPython strips a BOM, so the gate must not call it broken.

        Reading with plain ``utf-8`` leaves a leading U+FEFF that ``ast.parse``
        rejects, which reported a perfectly working module as unverified.
        """
        (tmp_path / "bom.py").write_bytes("﻿try:\n    f()\nexcept ValueError:\n    pass\n".encode("utf-8"))
        result = GATE.scan(tmp_path, scan_dirs=(".",))
        assert result.unparsed == [], "a BOM'd module is valid Python and must be scanned"
        assert len(result.findings) == 1, "and its handlers must still be judged"

    def test_json_mode_emits_machine_readable_counts(self, tmp_path, capsys):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "bad.py").write_text("try:\n    f()\nexcept ValueError:\n    pass\n", encoding="utf-8")
        code = GATE.main(["--root", str(tmp_path), "--json"])
        payload = capsys.readouterr().out
        assert code == 1
        assert '"new": 1' in payload
        assert "scripts/bad.py" in payload

    def test_clean_tree_exits_zero(self, tmp_path, capsys):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "ok.py").write_text(
            "try:\n    f()\nexcept ValueError as e:\n    logger.error('x', exc_info=e)\n", encoding="utf-8"
        )
        assert GATE.main(["--root", str(tmp_path), "--quiet"]) == 0

    def test_missing_root_is_distinguishable_from_clean(self, tmp_path):
        assert GATE.main(["--root", str(tmp_path / "nope")]) == 2

    def test_report_is_written_with_file_and_line(self, tmp_path):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "bad.py").write_text("try:\n    f()\nexcept ValueError:\n    pass\n", encoding="utf-8")
        report = tmp_path / "out.md"
        GATE.main(["--root", str(tmp_path), "--report", str(report)])
        text = report.read_text(encoding="utf-8")
        assert "scripts/bad.py:3" in text
        assert "**NEW**" in text


@pytest.fixture(scope="module")
def repo_scan():
    """One full-tree scan, shared.

    Scanning the repository parses ~3,000 Python files, which is far too slow to
    repeat per assertion. The scan is pure, so one pass answers every
    repository-level question below.
    """
    return GATE.scan(REPO_ROOT)


class TestRepositoryIsGreen:
    """The gate must pass on the tree it shipped with, and must not be toothless.

    This class is what makes the gate binding without a CI workflow edit: the
    backend suite runs on ``make test``, so a new silent failure anywhere in the
    repository fails the build here.
    """

    def test_baseline_covers_the_current_tree(self, repo_scan):
        assert repo_scan.unparsed == [], f"the gate could not verify: {repo_scan.unparsed}"
        new, _resolved = GATE.compare(repo_scan, GATE.BASELINE)
        assert new == [], "every open silent failure must be in the recorded baseline: " + "; ".join(f.as_row() for f in new[:20])

    def test_baseline_is_not_vacuous(self):
        assert len(GATE.BASELINE) > 100, "a tiny baseline would mean the gate covers almost nothing"
        assert sum(sum(v.values()) for v in GATE.BASELINE.values()) > 500

    def test_the_new_files_this_change_added_are_clean(self, repo_scan):
        """The deliverable must not be the first thing the gate catches."""
        owned = {
            "backend/packages/harness/alpha/errors/__init__.py",
            "backend/packages/harness/alpha/errors/registry.py",
            "backend/packages/harness/alpha/errors/report.py",
            "scripts/check_no_silent_failures.py",
            "tests/test_error_codes.py",
            "tests/test_run_error_coded.py",
            "tests/test_check_no_silent_failures.py",
        }
        offenders = sorted({f"{f.path}:{f.lineno}" for f in repo_scan.findings if f.path.lstrip("./") in owned})
        assert offenders == [], f"this change introduced silent failures: {offenders}"

    def test_a_new_silent_failure_would_fail_the_gate(self, tmp_path):
        """Proof the ratchet still bites: plant one and it must go red."""
        script = tmp_path / "check.py"
        script.write_text(
            "import sys, pathlib\n"
            f"sys.path.insert(0, {str(SCRIPT.parent)!r})\n"
            "import check_no_silent_failures as gate\n"
            "root = pathlib.Path(sys.argv[1])\n"
            "result = gate.scan(root, scan_dirs=('.',))\n"
            "new, _ = gate.compare(result, gate.BASELINE)\n"
            "sys.exit(1 if new else 0)\n",
            encoding="utf-8",
        )
        (tmp_path / "planted.py").write_text(
            "def f():\n    try:\n        g()\n    except ValueError:\n        pass\n", encoding="utf-8"
        )
        result = subprocess.run([sys.executable, str(script), str(tmp_path)], capture_output=True, text=True)
        assert result.returncode == 1, "a planted silent failure must fail the gate"

    def test_the_shipped_script_runs_as_a_program(self, tmp_path):
        """The CLI is what CI invokes, so its exit code is part of the contract."""
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "clean.py").write_text(
            "try:\n    f()\nexcept ValueError as e:\n    logger.error('x', exc_info=e)\n", encoding="utf-8"
        )
        clean = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path), "--json"], capture_output=True, text=True)
        assert clean.returncode == 0, clean.stderr[-400:]
        assert '"new": 0' in clean.stdout

        (scripts / "dirty.py").write_text("try:\n    f()\nexcept ValueError:\n    pass\n", encoding="utf-8")
        dirty = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path), "--json"], capture_output=True, text=True)
        assert dirty.returncode == 1, "a new silent failure must fail the CLI"


def _handler(body: str):
    """The first ``except`` handler in ``body``, wrapping it in a try if needed.

    Parses ``body`` as-is first and only synthesises a wrapper when that yields
    no handler, so a snippet that merely *starts* with a comment before its
    ``try`` is still read correctly. Any parse failure is collected and named in
    the final AssertionError, so a malformed fixture reports *why* rather than
    just "not found".
    """
    import ast

    failures: list[str] = []
    for candidate in (body, f"try:\n    f()\n{body}"):
        try:
            module = ast.parse(candidate)
        except SyntaxError as exc:
            failures.append(f"{exc.msg} (line {exc.lineno})")
            continue
        for node in module.body:
            if isinstance(node, ast.Try) and node.handlers:
                return node.handlers[0]
    raise AssertionError(f"no handler found in {body!r}; parse failures: {failures or ['no Try block']}")


def _fingerprint(body: str) -> str:
    handler = _handler(body)
    return GATE.fingerprint_of(handler, GATE.breadth_of(handler))
