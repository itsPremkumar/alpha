"""Targeted tests for the Deep Agents storage workspace (guide sections 44/48/49).

Every test exercises real behaviour: the LocalWorkspace round-trip goes through
the actual tmp disk (no mocks of the thing under test), and routing/deny tests
assert the fail-closed contract (no route or denied route means no IO).
"""

from __future__ import annotations

import pytest

from alpha.deepagent import (
    CompositeWorkspace,
    GrepMatch,
    LocalWorkspace,
    Route,
    VirtualWorkspace,
    WorkspaceConflictError,
    WorkspaceDenied,
    WorkspaceEntry,
    WorkspaceError,
    WorkspaceNotFoundError,
    WorkspacePathError,
    WorkspaceUnsupported,
)


def test_local_workspace_satisfies_protocol_surface() -> None:
    # runtime_checkable protocols check method presence on the instance type.
    for name in ("ls", "read", "write", "edit", "delete", "glob", "grep", "execute"):
        assert callable(getattr(LocalWorkspace, name)), name
        assert hasattr(VirtualWorkspace, name), name


def test_seam_reexports_from_sandbox() -> None:
    import alpha.sandbox as sandbox_pkg

    for name in (
        "CompositeWorkspace",
        "GrepMatch",
        "LocalWorkspace",
        "Route",
        "VirtualWorkspace",
        "WorkspaceConflictError",
        "WorkspaceDenied",
        "WorkspaceEntry",
        "WorkspaceError",
        "WorkspaceNotFoundError",
        "WorkspacePathError",
        "WorkspaceUnsupported",
    ):
        assert getattr(sandbox_pkg, name) is not None, name
        assert name in sandbox_pkg.__all__, name


@pytest.mark.asyncio
async def test_roundtrip_through_real_disk(tmp_path) -> None:
    ws = LocalWorkspace(tmp_path)

    await ws.write("/notes/a.txt", "alpha\nbeta\ngamma\n")
    assert await ws.read("/notes/a.txt") == "alpha\nbeta\ngamma\n"

    # offset/limit are line-based windows.
    assert await ws.read("/notes/a.txt", offset=1, limit=1) == "beta\n"

    await ws.edit("/notes/a.txt", "beta", "BETA")
    assert "BETA" in await ws.read("/notes/a.txt")

    entries = await ws.ls("/notes")
    assert [e.path for e in entries] == ["/notes/a.txt"]
    assert isinstance(entries[0], WorkspaceEntry)
    assert entries[0].kind == "file"
    # size_bytes is measured, never guessed.
    assert entries[0].size_bytes == (tmp_path / "notes" / "a.txt").stat().st_size

    assert await ws.glob("notes/*.txt") == ["/notes/a.txt"]

    matches = await ws.grep("BETA", "/")
    assert matches == [GrepMatch(path="/notes/a.txt", line=2, text="BETA")]
    assert matches[0].line == 2

    await ws.delete("/notes/a.txt")
    with pytest.raises(WorkspaceNotFoundError):
        await ws.read("/notes/a.txt")


@pytest.mark.asyncio
async def test_traversal_and_drive_components_fail_closed(tmp_path) -> None:
    ws = LocalWorkspace(tmp_path)
    for bad in ("/../escape.txt", "/notes/../../escape.txt", "C:/windows/system32"):
        with pytest.raises(WorkspacePathError):
            await ws.read(bad)
        with pytest.raises(WorkspacePathError):
            await ws.write(bad, "nope")
    with pytest.raises(WorkspacePathError):
        await ws.glob("../outside/*")


@pytest.mark.asyncio
async def test_edit_conflicts_fail_closed(tmp_path) -> None:
    ws = LocalWorkspace(tmp_path)
    await ws.write("/f.txt", "one two one")

    with pytest.raises(WorkspaceConflictError):
        await ws.edit("/f.txt", "", "x")
    with pytest.raises(WorkspaceConflictError):
        await ws.edit("/f.txt", "missing", "x")
    # Ambiguous multi-occurrence replace without replace_all is refused.
    with pytest.raises(WorkspaceConflictError):
        await ws.edit("/f.txt", "one", "uno")

    await ws.edit("/f.txt", "one", "uno", replace_all=True)
    assert await ws.read("/f.txt") == "uno two uno"


@pytest.mark.asyncio
async def test_execute_is_honestly_unsupported(tmp_path) -> None:
    ws = LocalWorkspace(tmp_path)
    with pytest.raises(WorkspaceUnsupported) as excinfo:
        await ws.execute("ls -la")
    # Points callers at the execution surface that does exist.
    assert "alpha.sandbox" in str(excinfo.value)


@pytest.mark.asyncio
async def test_composite_routes_prefixes_to_two_backends(tmp_path) -> None:
    left_root = tmp_path / "left"
    right_root = tmp_path / "right"
    left = LocalWorkspace(left_root, create=True)
    right = LocalWorkspace(right_root, create=True)
    router = CompositeWorkspace(
        [
            Route("/workspace", left),
            Route("/artifacts", right),
        ]
    )

    await router.write("/workspace/a.txt", "in-left")
    await router.write("/artifacts/b.txt", "in-right")

    # Prefixes are stripped before delegation: each backend sees its own mount.
    assert (left_root / "a.txt").read_text(encoding="utf-8") == "in-left"
    assert (right_root / "b.txt").read_text(encoding="utf-8") == "in-right"
    assert not (left_root / "b.txt").exists()
    assert not (right_root / "a.txt").exists()

    assert await router.read("/workspace/a.txt") == "in-left"
    assert [e.path for e in await router.ls("/artifacts")] == ["/artifacts/b.txt"]
    assert await router.glob("/artifacts/*.txt") == ["/artifacts/b.txt"]
    matches = await router.grep("in-left", "/workspace")
    assert matches == [GrepMatch(path="/workspace/a.txt", line=1, text="in-left")]


@pytest.mark.asyncio
async def test_deny_route_shadows_catch_all_in_declaration_order(tmp_path) -> None:
    allowed = LocalWorkspace(tmp_path, create=True)
    router = CompositeWorkspace(
        [
            Route.deny("/secrets", name="no-model-access"),
            Route("/", allowed),
        ]
    )

    with pytest.raises(WorkspaceDenied) as excinfo:
        await router.read("/secrets/token.txt")
    assert "denied" in str(excinfo.value) or "no-model-access" in str(excinfo.value)

    # Everything outside the deny prefix still routes to the catch-all.
    await router.write("/workspace/ok.txt", "fine")
    assert await router.read("/workspace/ok.txt") == "fine"
    # ...and the deny never touched disk.
    assert not (tmp_path / "secrets").exists()


@pytest.mark.asyncio
async def test_unrouted_path_fails_closed(tmp_path) -> None:
    allowed = LocalWorkspace(tmp_path, create=True)
    router = CompositeWorkspace([Route("/workspace", allowed)])
    with pytest.raises(WorkspaceDenied):
        await router.read("/elsewhere/file.txt")
    with pytest.raises(WorkspaceDenied):
        await router.write("/elsewhere/file.txt", "nope")
    assert not (tmp_path / "elsewhere").exists()


def test_duplicate_route_prefix_rejected(tmp_path) -> None:
    ws = LocalWorkspace(tmp_path)
    with pytest.raises(WorkspaceError, match="duplicate route prefix"):
        CompositeWorkspace([Route("/data", ws), Route("/data/", ws)])
    # Glob-ish prefixes are rejected: routes are literal prefixes.
    with pytest.raises(WorkspaceError, match="literal path prefixes"):
        Route("/data/*", ws)


@pytest.mark.asyncio
async def test_composite_execute_is_honestly_unsupported(tmp_path) -> None:
    ws = LocalWorkspace(tmp_path, create=True)
    router = CompositeWorkspace([Route("/", ws)])
    with pytest.raises(WorkspaceUnsupported) as excinfo:
        await router.execute("echo hi")
    assert "alpha.sandbox" in str(excinfo.value)


def test_route_normalization_and_suffix(tmp_path) -> None:
    backend = LocalWorkspace(tmp_path, create=True)
    route = Route("/workspace/", backend)
    assert route.prefix == "/workspace"
    assert not route.denied
    assert route.matches("/workspace") and route.matches("/workspace/x")
    assert not route.matches("/workspacex")
    assert route.suffix_for("/workspace/x") == "/x"
    assert route.suffix_for("/workspace") == "/"
    deny = Route.deny("/secrets")
    assert deny.denied
    assert deny.matches("/secrets/token")
    assert not deny.matches("/secretsfoo")
    assert Route("/").matches("/anything")
