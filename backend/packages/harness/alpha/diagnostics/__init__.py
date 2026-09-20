"""Fail-loud runtime self-checks (invariant registry)."""

from alpha.diagnostics.invariants import (
    InvariantCheck,
    InvariantError,
    InvariantRegistry,
    check_clarification_is_last,
    check_unique_tool_names,
    default_registry,
    register_invariant,
    verify_agent_assembly,
    verify_invariants,
)

__all__ = [
    "InvariantCheck",
    "InvariantError",
    "InvariantRegistry",
    "check_clarification_is_last",
    "check_unique_tool_names",
    "default_registry",
    "register_invariant",
    "verify_agent_assembly",
    "verify_invariants",
]
