"""The ``/mission`` command family: reachable, honest, owner-safe.

Covers the registration parity (catalog row + bound handler + exact bound set)
and the runtime behaviour that matters: a mission set in one scope is read back
from the durable store under the **server-resolved** owner (the client-supplied
context never names the owner), and an unknown subcommand or a missing mission is
reported honestly rather than faked.
"""

from __future__ import annotations

from alpha.commands.registry import command_registry
from alpha.runtime.missions import MissionManager
from alpha.runtime.user_context import resolve_runtime_user_id


def _catalog_names() -> set[str]:
    from alpha.commands.catalog import get_default_catalog_entries

    return {row[0] for row in get_default_catalog_entries()}


def test_mission_rows_are_published_core_commands() -> None:
    import alpha.mission.commands  # noqa: F401 - binding side effect
    from alpha.commands.catalog import get_default_catalog_entries

    rows = {row[0]: row for row in get_default_catalog_entries()}
    assert {"/mission", "/mission set"} <= _catalog_names()
    for name in ("/mission", "/mission set"):
        row = rows[name]
        assert row[4] is True, f"{name} must be a core command"
        assert str(row[3]).strip(), f"{name} must publish a usage string"
    # The free-text row must declare its argument so a mistyped subcommand isn't
    # silently swallowed as the objective.
    assert "<objective>" in rows["/mission set"][3]


def test_binds_exactly_the_published_verbs() -> None:
    import alpha.mission.commands as m

    assert set(m.bound_commands()) == {"/mission", "/mission set"}
    assert command_registry.has_handler("/mission")
    assert command_registry.has_handler("/mission set")


def test_no_mission_is_honest_and_suggests_set(tmp_path, monkeypatch) -> None:
    import alpha.config.paths as paths_mod

    monkeypatch.setattr(paths_mod, "get_paths", lambda: _paths(tmp_path))
    result = command_registry.execute("/mission", context={"thread_id": "fresh-thread"})
    assert result.status == "success"
    assert "No mission" in result.output and result.data.get("active") is False


def test_set_then_read_back_roundtrips_through_durable_store(tmp_path, monkeypatch) -> None:
    import alpha.config.paths as paths_mod

    monkeypatch.setattr(paths_mod, "get_paths", lambda: _paths(tmp_path))

    set_result = command_registry.execute("/mission set reduce p95 checkout latency", context={"thread_id": "t1"})
    assert set_result.status == "success" and set_result.data["changed"] is True

    # Stored under the SERVER-resolved owner, never a client-supplied one.
    owner = resolve_runtime_user_id(None)
    stored = MissionManager(_paths(tmp_path)).load(owner, "t1")
    assert stored.spec_objective == "reduce p95 checkout latency"

    read = command_registry.execute("/mission", context={"thread_id": "t1"})
    assert read.status == "success" and read.data["active"] is True
    assert "reduce p95 checkout latency" in read.output
    assert read.data["progress"]["milestones_total"] == 0  # objective only, not a fabricated plan


def test_set_is_idempotent(tmp_path, monkeypatch) -> None:
    import alpha.config.paths as paths_mod

    monkeypatch.setattr(paths_mod, "get_paths", lambda: _paths(tmp_path))
    command_registry.execute("/mission set same objective", context={"thread_id": "t2"})
    again = command_registry.execute("/mission set same objective", context={"thread_id": "t2"})
    assert again.data["changed"] is False


def test_set_requires_an_objective_and_typo_is_not_found(tmp_path, monkeypatch) -> None:
    import alpha.config.paths as paths_mod

    monkeypatch.setattr(paths_mod, "get_paths", lambda: _paths(tmp_path))
    assert command_registry.execute("/mission set", context={"thread_id": "t3"}).status == "error"
    typo = command_registry.execute("/mission bogus-subcommand", context={"thread_id": "t3"})
    assert typo.status == "not_found"


def test_two_scopes_never_share_a_mission(tmp_path, monkeypatch) -> None:
    import alpha.config.paths as paths_mod

    monkeypatch.setattr(paths_mod, "get_paths", lambda: _paths(tmp_path))
    command_registry.execute("/mission set alpha objective", context={"thread_id": "thread-a"})

    other = command_registry.execute("/mission", context={"thread_id": "thread-b"})
    assert other.data["active"] is False  # thread-b has its own (empty) mission


def _paths(root) -> object:
    from alpha.config.paths import Paths

    return Paths(str(root))
