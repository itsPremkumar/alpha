"""Gate execution tests: the typed registry, containment, and refusals.

Every case here is a way the only module that executes anything could start
lying, and each is named in the plan's §18 unit list:

* check-registry argument validation — a ``check_ref`` outside the allowlist
  is refused before dispatch, never reached at run time;
* timeout, exit-code and output-limit enforcement — output over the ceiling is
  ``failed``, never a *truncated match*;
* gate-definition digest determinism — the value an approval binds to;
* path canonicalisation and workspace escape — refusal, not repair;
* redaction and control-character stripping — untrusted output never reaches a
  UI or a log raw;
* command injection — argv arrays with ``shell=False`` cannot be broken by a
  parameter containing a shell metacharacter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alpha.completion.errors import PathEscape
from alpha.completion.evidence import definition_digest
from alpha.completion.gates import (
    CheckRegistry,
    CheckResult,
    check_context_for,
    default_registry,
    evaluate_expectation,
    run_argv,
    strip_control,
    tail,
)
from alpha.completion.models import CheckOutcome, Expectation, Gate, GateKind
from alpha.completion.policy import CompletionSettings, settings_from_config
from alpha.config.completion_config import CompletionConfig


def make_settings(tmp_path: Path, **overrides) -> CompletionSettings:
    payload = {
        "enabled": True,
        "mode": "enforce",
        "workspace_root": str(tmp_path),
        "store_path": str(tmp_path / "state.json"),
        "gate_timeout_seconds": 10.0,
        "max_output_bytes": 4096,
    }
    payload.update(overrides)
    return settings_from_config(CompletionConfig(**payload))


def make_gate(**overrides) -> Gate:
    payload = {
        "gate_id": "g1",
        "task_id": "t1",
        "title": "report exists",
        "kind": GateKind.FILE,
        "check_ref": "file.exists",
        "parameters": {"path": "report.md"},
        "expectation": Expectation(required_markers=["# Report"]),
    }
    payload.update(overrides)
    return Gate(**payload)


class TestRegistryAllowlist:
    def test_the_registry_holds_exactly_the_six_reviewed_checks(self):
        assert default_registry().names() == frozenset({"file.exists", "artifact.parse", "command.run", "pytest.run", "manual.review", "api.request"})

    def test_an_unregistered_ref_raises_check_unavailable(self):
        registry = CheckRegistry()
        registry.register("file.exists", lambda ctx: CheckResult(CheckOutcome.PASSED))
        with pytest.raises(Exception) as exc:
            registry.get("os.system")
        assert "not registered" in str(exc.value)

    def test_registering_the_same_name_twice_is_refused(self):
        registry = default_registry()
        with pytest.raises(ValueError):
            registry.register("file.exists", lambda ctx: CheckResult(CheckOutcome.PASSED))

    def test_a_broken_check_is_a_red_result_never_a_green_one(self):
        def explode(ctx):
            raise RuntimeError("boom")

        registry = CheckRegistry()
        registry.register("file.exists", explode)
        settings = make_settings(Path(".").resolve())
        ctx = check_context_for(make_gate(), settings)
        result = registry.run(ctx)
        assert result.outcome is CheckOutcome.INVALID_CONFIGURATION
        assert "boom" in result.reason


class TestContainment:
    def test_a_path_escaping_the_workspace_is_refused_not_repaired(self, tmp_path):
        settings = make_settings(tmp_path)
        with pytest.raises(PathEscape):
            settings.resolve("../outside.txt")
        with pytest.raises(PathEscape):
            settings.resolve(str(tmp_path.parent / "elsewhere.txt"))

    def test_a_path_inside_the_workspace_resolves(self, tmp_path):
        settings = make_settings(tmp_path)
        assert settings.resolve("sub/report.md") == (tmp_path / "sub" / "report.md").resolve()

    def test_a_dot_dot_normalises_then_still_refuses_an_escape(self, tmp_path):
        # The boundary is "normalise first, then refuse anything outside" —
        # the same shape `avo/paths.py` documents. A traversal that lands back
        # inside names the same file either way; one that leaves the workspace
        # is refused rather than repaired into a path that happens to fit.
        settings = make_settings(tmp_path)
        assert settings.resolve("sub/../report.md") == (tmp_path / "report.md").resolve()
        with pytest.raises(PathEscape):
            settings.resolve("../report.md")
        with pytest.raises(PathEscape):
            settings.resolve("nested/../../report.md")

    def test_a_shell_metacharacter_in_a_parameter_cannot_break_out(self, tmp_path):
        # The parameter travels as one argv element, not as shell text: the
        # file simply does not exist, rather than a second command running.
        settings = make_settings(tmp_path)
        gate = make_gate(parameters={"path": "report.md; rm -rf /"})
        ctx = check_context_for(gate, settings)
        result = default_registry().run(ctx)
        assert result.outcome is CheckOutcome.FAILED
        assert "does not exist" in result.reason

    def test_the_check_environment_is_allowlisted_not_inherited(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value-should-not-leak")
        settings = make_settings(tmp_path)
        gate = make_gate(kind=GateKind.COMMAND, check_ref="command.run", parameters={"argv": [__import__("sys").executable, "-c", "import os;print('KEY' in os.environ)"]}, expectation=Expectation(exit_code=0, required_markers=["False"]))
        ctx = check_context_for(gate, settings)
        result = default_registry().run(ctx)
        # Whether policy allows it or not, the secret must not be visible to
        # the child; the assertion below is about the *environment*.
        if result.outcome in {CheckOutcome.PASSED, CheckOutcome.FAILED}:
            assert "True" not in result.output


class TestOutputAndTimeout:
    def test_output_over_the_ceiling_is_failed_not_truncated_match(self, tmp_path):
        # The config floor is 1024 bytes, so the ceiling is set there and the
        # command prints far past it.
        result = run_argv([__import__("sys").executable, "-c", "print('x' * 100000)"], cwd=tmp_path, timeout_seconds=20, max_output_bytes=1024)
        assert result.outcome is CheckOutcome.FAILED
        assert "capture limit" in result.reason
        # A marker present only in the *observed tail* must never be matched.
        assert "cannot be matched" in result.reason
        # The tail it kept is stamped, so the truncation is visible to the
        # evidence layer rather than inferred from a short string.
        assert "earlier bytes omitted" in result.output

    def test_a_command_past_its_deadline_is_timed_out(self, tmp_path):
        result = run_argv([__import__("sys").executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path, timeout_seconds=0.5, max_output_bytes=4096)
        assert result.outcome is CheckOutcome.TIMED_OUT
        assert "timed out after" in result.reason

    def test_a_missing_executable_is_invalid_configuration(self, tmp_path):
        result = run_argv(["definitely-not-a-real-binary-xyz"], cwd=tmp_path, timeout_seconds=5, max_output_bytes=4096)
        assert result.outcome is CheckOutcome.INVALID_CONFIGURATION
        assert "not found" in result.reason

    def test_an_empty_argv_is_invalid_configuration(self, tmp_path):
        result = run_argv([], cwd=tmp_path, timeout_seconds=5, max_output_bytes=4096)
        assert result.outcome is CheckOutcome.INVALID_CONFIGURATION

    def test_a_nonexistent_working_directory_is_invalid_configuration(self, tmp_path):
        result = run_argv([__import__("sys").executable, "-c", "pass"], cwd=tmp_path / "nope", timeout_seconds=5, max_output_bytes=4096)
        assert result.outcome is CheckOutcome.INVALID_CONFIGURATION

    def test_a_nonzero_exit_is_failed_with_the_exit_code(self, tmp_path):
        result = run_argv([__import__("sys").executable, "-c", "raise SystemExit(3)"], cwd=tmp_path, timeout_seconds=20, max_output_bytes=4096)
        assert result.outcome is CheckOutcome.FAILED
        assert result.exit_code == 3


class TestExpectations:
    def test_a_missing_required_marker_fails_with_its_name(self):
        ok, reason = evaluate_expectation(Expectation(required_markers=["ALL_GOOD"]), exit_code=0, output="nothing here")
        assert ok is False
        assert "ALL_GOOD" in reason

    def test_a_forbidden_marker_present_fails(self):
        ok, reason = evaluate_expectation(Expectation(forbidden_markers=["Traceback"]), exit_code=0, output="Traceback (most recent call last)")
        assert ok is False
        assert "Traceback" in reason

    def test_size_ceilings_are_enforced(self):
        assert evaluate_expectation(Expectation(min_bytes=10), exit_code=0, output="x", size_bytes=2)[0] is False
        assert evaluate_expectation(Expectation(max_bytes=2), exit_code=0, output="x", size_bytes=10)[0] is False

    def test_an_empty_expectation_measures_nothing(self):
        # `Expectation()` alone is *not* empty — it defaults to exit_code 0,
        # which is a real assertion. Explicitly clearing every field is what
        # "measures nothing" means, and that is what contract validation
        # refuses.
        assert Expectation(exit_code=None).is_empty() is True
        assert Expectation().is_empty() is False
        assert Expectation(exit_code=0).is_empty() is False


class TestDigest:
    def test_the_definition_digest_is_deterministic(self, tmp_path):
        settings = make_settings(tmp_path)
        a = definition_digest(make_gate(), settings)
        b = definition_digest(make_gate(), settings)
        assert a == b

    def test_the_digest_moves_when_a_parameter_changes(self, tmp_path):
        settings = make_settings(tmp_path)
        assert definition_digest(make_gate(parameters={"path": "a.md"}), settings) != definition_digest(make_gate(parameters={"path": "b.md"}), settings)

    def test_the_digest_moves_when_the_policy_moves(self, tmp_path):
        gate = make_gate()
        assert definition_digest(gate, make_settings(tmp_path, gate_timeout_seconds=10.0)) != definition_digest(gate, make_settings(tmp_path, gate_timeout_seconds=11.0))

    def test_the_digest_ignores_purely_cosmetic_fields(self, tmp_path):
        settings = make_settings(tmp_path)
        assert definition_digest(make_gate(title="one"), settings) == definition_digest(make_gate(title="two"), settings)


class TestHygiene:
    def test_control_characters_are_stripped(self):
        assert "\x00" not in strip_control("ok\x00done")
        assert "\r" not in strip_control("a\rb")

    def test_ansi_escapes_are_stripped(self):
        cleaned = strip_control("\x1b[31mred\x1b[0m")
        assert "\x1b" not in cleaned
        assert "red" in cleaned

    def test_tail_keeps_the_last_bytes_with_an_omission_marker(self):
        rendered = tail("abcdefghij", limit=4)
        assert "6 earlier bytes omitted" in rendered
        assert rendered.endswith("ghij")

    def test_tail_is_a_noop_under_the_limit(self):
        assert tail("short", limit=100) == "short"
