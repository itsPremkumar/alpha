"""Tests for the fail-loud invariant registry and assembly gate."""

from __future__ import annotations

import pytest

from alpha.diagnostics.invariants import (
    InvariantCheck,
    InvariantError,
    InvariantRegistry,
    check_clarification_is_last,
    check_unique_tool_names,
    verify_agent_assembly,
)


def _named(name: str):
    from types import SimpleNamespace

    return SimpleNamespace(name=name)


class ClarificationMiddleware:
    """Test double standing in for the real clarification gate (matched by type name)."""


class _OtherMiddleware:
    pass


class TestInvariantRegistry:
    def test_verify_passes_and_returns_names_in_order(self):
        registry = InvariantRegistry()
        registry.register(InvariantCheck(name="b-check", description="b", check=lambda: None))
        registry.register(InvariantCheck(name="a-check", description="a", check=lambda: None))
        assert registry.verify() == ["b-check", "a-check"]
        assert registry.check_names == ("b-check", "a-check")

    def test_duplicate_registration_is_itself_a_breach(self):
        registry = InvariantRegistry()
        registry.register(InvariantCheck(name="x", description="x", check=lambda: None))
        with pytest.raises(InvariantError) as excinfo:
            registry.register(InvariantCheck(name="x", description="x again", check=lambda: None))
        assert excinfo.value.check_name == "x"

    def test_breach_raises_invariant_error_naming_the_check(self):
        registry = InvariantRegistry()

        def _broken():
            raise AssertionError("boom")

        registry.register(InvariantCheck(name="broken", description="b", check=_broken))
        with pytest.raises(InvariantError) as excinfo:
            registry.verify()
        assert excinfo.value.check_name == "broken"
        assert "boom" in str(excinfo.value)

    def test_invariant_error_passthrough_keeps_original(self):
        registry = InvariantRegistry()
        original = InvariantError("inner", "nope")
        registry.register(InvariantCheck(name="inner", description="i", check=lambda: (_ for _ in ()).throw(original)))
        with pytest.raises(InvariantError) as excinfo:
            registry.verify()
        assert excinfo.value is original

    def test_allowlist_and_blocklist_scope_verification(self):
        registry = InvariantRegistry()
        registry.register(InvariantCheck(name="fast", description="f", check=lambda: None))
        registry.register(InvariantCheck(name="slow", description="s", check=lambda: None))
        assert registry.verify(allowlist=["fast"]) == ["fast"]
        assert registry.verify(blocklist=["slow"]) == ["fast"]
        assert registry.verify(allowlist=["nothing"]) == []


class TestAssemblyChecks:
    def test_unique_names_pass(self):
        check_unique_tool_names([_named("bash"), _named("read_file")])

    def test_duplicate_names_breach_lists_dupes(self):
        with pytest.raises(InvariantError) as excinfo:
            check_unique_tool_names([_named("bash"), _named("read_file"), _named("bash")])
        assert excinfo.value.check_name == "unique-tool-names"
        assert "bash" in str(excinfo.value)

    def test_non_string_names_are_skipped_not_probed(self):
        class _Weird:
            name = object()

        check_unique_tool_names([_Weird(), _named("bash"), None])

    def test_clarification_absent_passes_vacuously(self):
        check_clarification_is_last([_OtherMiddleware(), _OtherMiddleware()])
        check_clarification_is_last([])

    def test_clarification_last_passes(self):
        check_clarification_is_last([_OtherMiddleware(), ClarificationMiddleware()])

    def test_clarification_not_last_breaches(self):
        with pytest.raises(InvariantError) as excinfo:
            check_clarification_is_last([ClarificationMiddleware(), _OtherMiddleware()])
        assert excinfo.value.check_name == "clarification-is-last"

    def test_wrapped_middleware_is_unwrapped(self):
        class _Isolated:
            def __init__(self, inner):
                self.inner = inner

        check_clarification_is_last([_OtherMiddleware(), _Isolated(ClarificationMiddleware())])
        with pytest.raises(InvariantError):
            check_clarification_is_last([_Isolated(ClarificationMiddleware()), _OtherMiddleware()])


class TestVerifyAgentAssembly:
    def test_clean_assembly_passes(self):
        passed = verify_agent_assembly(tools=[_named("bash")], middlewares=[_OtherMiddleware(), ClarificationMiddleware()])
        assert passed == ["unique-tool-names", "clarification-is-last"]

    def test_duplicate_tools_breach(self):
        with pytest.raises(InvariantError) as excinfo:
            verify_agent_assembly(tools=[_named("bash"), _named("bash")], middlewares=[])
        assert excinfo.value.check_name == "unique-tool-names"

    def test_extra_checks_run_through_throwaway_registry(self):
        calls: list[str] = []
        passed = verify_agent_assembly(
            tools=[],
            middlewares=[],
            extra_checks={"custom": lambda: calls.append("ran")},
        )
        assert calls == ["ran"]
        assert passed == ["unique-tool-names", "clarification-is-last", "custom"]
