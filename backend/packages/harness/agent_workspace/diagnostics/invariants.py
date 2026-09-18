"""Fail-loud runtime self-checks (DeepSeek-Harness-style invariant registry).

``ctx.invariants`` in DeepSeek Harness is a live registry of self-checks that
raise on breach instead of limping on. This module is the Agent Workspace equivalent:
small, dependency-free, and safe to import from anywhere in the harness
(it imports stdlib only, so factory modules can use it without cycles).

Rules borrowed from ``ctx.invariants``:
- checks are named, independently registered, and fail LOUD
  (:class:`InvariantError` naming the breaching check);
- allow/blocklist filters scope verification without editing code;
- only check owned relations (assembly inputs this process built), never
  remote state.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field


class InvariantError(RuntimeError):
    """A registered self-check was breached. ``check_name`` owns the failure."""

    def __init__(self, check_name: str, message: str) -> None:
        super().__init__(f"invariant violated by {check_name}: {message}")
        self.check_name = check_name


@dataclass(frozen=True)
class InvariantCheck:
    """One named self-check. ``check`` raises on breach, returns None when held."""

    name: str
    description: str
    check: Callable[[], None] = field(compare=False)


def _match_any(name: str, patterns: Sequence[str] | None) -> bool:
    return bool(patterns) and any(pattern in name for pattern in patterns)


class InvariantRegistry:
    """Named self-checks with fail-loud verification and allow/block filters."""

    def __init__(self) -> None:
        self._checks: dict[str, InvariantCheck] = {}

    @property
    def check_names(self) -> tuple[str, ...]:
        """Registered check names in registration order."""
        return tuple(self._checks)

    def register(self, check: InvariantCheck) -> InvariantCheck:
        """Register *check*. Duplicate names raise — a shadowed self-check is
        itself an invariant breach."""
        if check.name in self._checks:
            raise InvariantError(check.name, f"duplicate invariant registration for {check.name!r}")
        self._checks[check.name] = check
        return check

    def verify(
        self,
        *,
        allowlist: Sequence[str] | None = None,
        blocklist: Sequence[str] | None = None,
    ) -> list[str]:
        """Run selected checks, fail LOUD on the first breach.

        Returns the names that passed. A breaching check raises
        :class:`InvariantError` (wrapping foreign exceptions); ``allowlist``
        restricts to substring matches, ``blocklist`` excludes them.
        """
        passed: list[str] = []
        for name, check in self._checks.items():
            if allowlist is not None and not _match_any(name, allowlist):
                continue
            if _match_any(name, blocklist):
                continue
            try:
                check.check()
            except InvariantError:
                raise
            except Exception as exc:
                raise InvariantError(name, f"{type(exc).__name__}: {exc}") from exc
            passed.append(name)
        return passed


_DEFAULT_REGISTRY = InvariantRegistry()


def default_registry() -> InvariantRegistry:
    """Process-global registry for harness-owned self-checks."""
    return _DEFAULT_REGISTRY


def register_invariant(check: InvariantCheck) -> InvariantCheck:
    """Register *check* on the process-global registry."""
    return _DEFAULT_REGISTRY.register(check)


def verify_invariants(
    *,
    allowlist: Sequence[str] | None = None,
    blocklist: Sequence[str] | None = None,
) -> list[str]:
    """Fail-loud verification of the process-global registry."""
    return _DEFAULT_REGISTRY.verify(allowlist=allowlist, blocklist=blocklist)


def _unwrap_middleware(middleware: object) -> object:
    """Unwrap ``IsolatedMiddleware`` so extension contributions cannot evade
    assembly checks (mirrors ``extensions.ordering._indices_of``)."""
    inner = getattr(middleware, "inner", None)
    return inner if inner is not None else middleware


def _middleware_type_names(middlewares: Iterable[object]) -> list[str]:
    return [type(_unwrap_middleware(middleware)).__name__ for middleware in (middlewares or [])]


def check_unique_tool_names(tools: Iterable[object] | None) -> None:
    """Fail when two assembled tools share a model-visible name.

    Only string names participate — mock/partial tools without a real name
    are skipped rather than probed. Duplicate names make model tool_calls
    ambiguous, so this is fail-closed.
    """
    seen: dict[str, int] = {}
    for tool in tools or []:
        name = getattr(tool, "name", None)
        if not isinstance(name, str) or not name:
            continue
        seen[name] = seen.get(name, 0) + 1
    duplicates = sorted(name for name, count in seen.items() if count > 1)
    if duplicates:
        raise InvariantError("unique-tool-names", f"duplicate assembled tool names: {', '.join(duplicates)}")


def check_clarification_is_last(middlewares: Iterable[object] | None) -> None:
    """Fail when ClarificationMiddleware is present but not the terminal entry.

    A non-terminal clarification gate lets later middleware run sibling tool
    calls before the user answers. Absent middleware passes vacuously.
    """
    names = _middleware_type_names(middlewares)
    positions = [index for index, name in enumerate(names) if name == "ClarificationMiddleware"]
    if not positions:
        return
    if max(positions) != len(names) - 1:
        raise InvariantError(
            "clarification-is-last",
            f"ClarificationMiddleware at position(s) {positions} but middleware chain has length {len(names)}",
        )


def verify_agent_assembly(
    *,
    tools: Iterable[object] | None,
    middlewares: Iterable[object] | None,
    extra_checks: Mapping[str, Callable[[], None]] | None = None,
) -> list[str]:
    """Fail-loud assembly gate for the lead-agent factory.

    Runs the built-in assembly checks plus any caller-supplied *extra_checks*
    through a throwaway registry (no global state touched). Returns passed
    check names.
    """
    registry = InvariantRegistry()
    registry.register(
        InvariantCheck(
            name="unique-tool-names",
            description="Assembled tools must have unique model-visible names.",
            check=lambda: check_unique_tool_names(tools),
        )
    )
    registry.register(
        InvariantCheck(
            name="clarification-is-last",
            description="ClarificationMiddleware must terminate the middleware chain.",
            check=lambda: check_clarification_is_last(middlewares),
        )
    )
    for name, check in (extra_checks or {}).items():
        registry.register(InvariantCheck(name=name, description="Caller-supplied assembly check.", check=check))
    return registry.verify()
