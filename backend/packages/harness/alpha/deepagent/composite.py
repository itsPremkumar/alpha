"""Composite storage routing for virtual workspaces (guide sections 5.3, 44, 48, 49.1).

The guide's strongest "copy this" idea is path-based routing so different
classes of data get different durability and security:

``/workspace`` -> local/sandbox storage, ``/artifacts`` -> durable object
storage, ``/secrets`` -> blocked from model tools.

Alpha has no equivalent today (audit finding: MISSING — the only virtual path
contract is the single ``/mnt/user-data`` prefix in
``alpha.config.paths.VIRTUAL_PATH_PREFIX``), so this module implements it
behind the Alpha-owned :class:`~alpha.deepagent.workspace.VirtualWorkspace`
protocol:

* routes are evaluated in **declaration order, first match wins** — the same
  rule the guide gives filesystem permission rules (section 13), so a
  ``/secrets`` deny declared first shadows a later catch-all route;
* unrouted paths and ``deny`` routes (``backend=None``) fail closed with
  :class:`~alpha.deepagent.workspace.WorkspaceDenied` — no route, no IO;
* the matched route's prefix is stripped before delegating, so every backend
  is rooted at its own mount;
* shell ``execute`` is not routed and fails closed with
  :class:`~alpha.deepagent.workspace.WorkspaceUnsupported` (execution belongs
  to ``alpha.sandbox``).

Routes are pure constructor arguments — no config file, no config_version
bump, defaults-off by construction (guide section 41 is report-only for this
integration).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from alpha.deepagent.workspace import (
    GrepMatch,
    VirtualWorkspace,
    WorkspaceDenied,
    WorkspaceEntry,
    WorkspaceError,
    WorkspaceUnsupported,
)


def _normalize_prefix(prefix: str) -> str:
    raw = str(prefix).replace("\\", "/")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts:
        return "/"
    for part in parts:
        if part == "..":
            raise WorkspaceError(f"'..' is not allowed in route prefixes: {prefix!r}")
        if ":" in part:
            raise WorkspaceError(f"drive/stream components are not allowed in route prefixes: {prefix!r}")
        if "*" in part or "?" in part:
            raise WorkspaceError(
                f"route prefixes are literal path prefixes, not glob patterns: {prefix!r}"
                " (route the prefix and let the backend glob)"
            )
    return "/" + "/".join(parts)


@dataclass(frozen=True, slots=True)
class Route:
    """One ordered routing rule.

    ``backend=None`` marks a deny route: everything under ``prefix`` fails
    closed with :class:`WorkspaceDenied` (the guide's ``/secrets`` case).
    """

    prefix: str
    backend: VirtualWorkspace | None = None
    name: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "prefix", _normalize_prefix(self.prefix))

    @classmethod
    def deny(cls, prefix: str, *, name: str = "") -> Route:
        """A fail-closed route: nothing under ``prefix`` is reachable."""
        return cls(prefix=prefix, backend=None, name=name or f"deny:{_normalize_prefix(prefix)}")

    @property
    def denied(self) -> bool:
        return self.backend is None

    @staticmethod
    def _normalize_path(path: str) -> str:
        raw = str(path).replace("\\", "/")
        return raw if raw.startswith("/") else "/" + raw

    def matches(self, path: str) -> bool:
        normalized = self._normalize_path(path)
        if self.prefix == "/":
            return True
        return normalized == self.prefix or normalized.startswith(self.prefix + "/")

    def suffix_for(self, path: str) -> str:
        normalized = self._normalize_path(path)
        if self.prefix == "/":
            return normalized
        rest = normalized[len(self.prefix) :]
        return rest or "/"


class CompositeWorkspace:
    """Path-prefix router over :class:`VirtualWorkspace` backends.

    Declaration-order first-match routing with fail-closed defaults; see the
    module docstring for the contract.
    """

    def __init__(self, routes: Sequence[Route]) -> None:
        if isinstance(routes, (str, bytes)):
            raise WorkspaceError("routes must be a sequence of Route objects, not a string")
        normalized: list[Route] = []
        seen: set[str] = set()
        for route in routes:
            if not isinstance(route, Route):
                raise WorkspaceError(f"routes must contain Route objects, got {route!r}")
            if route.prefix in seen:
                raise WorkspaceError(f"duplicate route prefix {route.prefix!r} (routing must be unambiguous)")
            seen.add(route.prefix)
            normalized.append(route)
        self._routes = tuple(normalized)

    @property
    def routes(self) -> tuple[Route, ...]:
        """The validated routes in declaration order."""
        return self._routes

    def route_for(self, path: str) -> Route:
        """First matching route in declaration order; fail closed otherwise."""
        for route in self._routes:
            if route.matches(path):
                return route
        raise WorkspaceDenied(f"no workspace route matches {path!r} (fail-closed)")

    def _delegate(self, path: str) -> tuple[Route, str]:
        route = self.route_for(path)
        backend = route.backend
        if backend is None:
            raise WorkspaceDenied(
                f"{path!r} is on the denied route {route.prefix!r}"
                + (f" ({route.name})" if route.name else "")
            )
        return route, route.suffix_for(path)

    @staticmethod
    def _refix(route: Route, path: str) -> str:
        """Map a backend-absolute path back into the composite namespace.

        Backends report paths relative to their own mount; callers of the
        router only ever see composite addresses (``/artifacts/b.txt``).
        """
        if route.prefix == "/":
            return path
        normalized = path if path.startswith("/") else "/" + path
        return route.prefix + normalized

    async def ls(self, path: str = "/") -> list[WorkspaceEntry]:
        route, suffix = self._delegate(path)
        entries = await route.backend.ls(suffix)
        if route.prefix == "/":
            return list(entries)
        return [
            WorkspaceEntry(path=self._refix(route, entry.path), kind=entry.kind, size_bytes=entry.size_bytes)
            for entry in entries
        ]

    async def read(self, path: str, offset: int = 0, limit: int = 1000) -> str:
        route, suffix = self._delegate(path)
        return await route.backend.read(suffix, offset, limit)

    async def write(self, path: str, content: str) -> None:
        route, suffix = self._delegate(path)
        await route.backend.write(suffix, content)

    async def edit(self, path: str, old: str, new: str, replace_all: bool = False) -> None:
        route, suffix = self._delegate(path)
        await route.backend.edit(suffix, old, new, replace_all)

    async def delete(self, path: str) -> None:
        route, suffix = self._delegate(path)
        await route.backend.delete(suffix)

    async def glob(self, pattern: str) -> list[str]:
        route, suffix = self._delegate(pattern)
        matches = await route.backend.glob(suffix)
        if route.prefix == "/":
            return list(matches)
        return [self._refix(route, match) for match in matches]

    async def grep(self, pattern: str, path: str = "/", max_results: int = 200) -> list[GrepMatch]:
        route, suffix = self._delegate(path)
        hits = await route.backend.grep(pattern, suffix, max_results)
        if route.prefix == "/":
            return list(hits)
        return [GrepMatch(path=self._refix(route, hit.path), line=hit.line, text=hit.text) for hit in hits]

    async def execute(self, command: str, cwd: str | None = None) -> str:
        raise WorkspaceUnsupported(
            "CompositeWorkspace does not route shell execution (not implemented by design); "
            "shell execution lives in alpha.sandbox (the 'bash' tool / Sandbox.execute_command)"
        )
