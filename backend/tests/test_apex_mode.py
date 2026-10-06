"""The APEX mode switch: spec §3 (``/apex on`` / ``/apex off``) and §33.

The switch is the one control that decides whether anything else in this package
is live, so every test here is about a claim that could be wrong rather than
about a shape being right.

Three groups:

* **Fail-closed.** An unreadable mode store must answer every scope as OFF. A
  store that cannot be read is the one condition where guessing "on" would turn
  a corrupt file into an autonomy grant, and the opposite reading ("there is
  nothing configured") is a clean, confident, wrong answer.
* **Two claims, not one.** ``enabled`` is the recorded intent; ``contract_enabled``
  is what the frozen contract actually grants. They disagree in exactly the cases
  that matter — a record persisted by a newer build under a profile this one does
  not know degrades to ``assist``, so ``enabled`` can be true while the authority
  is not what was asked for.
* **One implementation behind three surfaces.** The slash command, the HTTP route
  and the UI all call :mod:`alpha.apex.mode`. Two independently written toggles
  would eventually disagree about what "on" means, and the disagreement would be
  invisible until it mattered.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.apex.contract import AutonomyProfile
from alpha.apex.mode import ApexModeRecord, ApexModeStore, read_mode, set_mode

# --------------------------------------------------------------------------- #
# Fail-closed
# --------------------------------------------------------------------------- #


def test_an_unknown_scope_reads_off_and_grants_nothing(tmp_path: Path) -> None:
    """No record means nobody granted autonomy for that session."""
    store = ApexModeStore(tmp_path / "mode.json")
    record = store.for_scope("never-configured")

    assert record.enabled is False
    assert record.profile == AutonomyProfile.OFF.value
    assert store.is_enabled("never-configured") is False
    assert store.contract_for("never-configured").enabled is False
    assert store.contract_for("never-configured").may("terminal") is False


def test_a_corrupt_store_fails_closed_and_discloses(tmp_path: Path) -> None:
    """The refusal must be visible, not merely enacted.

    An unreadable store answers OFF for every scope. Showing that as a plain
    "OFF" would present a degraded read as a considered decision, so the store
    reports both.
    """
    bad = tmp_path / "bad.json"
    bad.write_text("{ nope", encoding="utf-8")
    store = ApexModeStore(bad)

    assert store.is_degraded is True
    assert store.load_error is not None
    assert store.is_enabled("anyone") is False
    assert store.contract_for("anyone").enabled is False


def test_an_absent_store_is_not_degraded(tmp_path: Path) -> None:
    """Absent is not corrupt — a first run would otherwise report a fault."""
    store = ApexModeStore(tmp_path / "never-written.json")
    assert store.is_degraded is False
    assert store.load_error is None


def test_a_contradictory_record_does_not_hand_out_authority(tmp_path: Path) -> None:
    """``enabled: true`` beside ``profile: "off"`` resolves to the OFF contract.

    The flag is only honoured for a profile this build actually knows. Reading it
    the other way would hand out authority for a profile that grants none.
    """
    store = ApexModeStore(tmp_path / "mode.json")
    store._rows["weird"] = ApexModeRecord(scope_key="weird", enabled=True, profile="off")

    record = store.for_scope("weird")
    assert record.enabled is True
    assert store.contract_for("weird").enabled is False


def test_an_unrecognised_stored_profile_degrades_visibly(tmp_path: Path) -> None:
    """A profile from a newer build resolves to ``assist``, and says so.

    Silently resolving to ``off`` would look like a settings problem; silently
    resolving to the requested authority would be a privilege grant. ``assist``
    plus a disclosure is the honest middle.
    """
    store = ApexModeStore(tmp_path / "mode.json")
    store._rows["legacy"] = ApexModeRecord(scope_key="legacy", enabled=True, profile="apex_pro_max")

    contract = store.contract_for("legacy")
    assert store.for_scope("legacy").profile == "apex_pro_max", "the stored name is reported verbatim"
    assert contract.profile is AutonomyProfile.ASSIST


# --------------------------------------------------------------------------- #
# Transitions
# --------------------------------------------------------------------------- #


def test_enable_and_disable_persist_across_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "mode.json"
    first = ApexModeStore(path)
    assert first.enable("thread-a", "autonomous", owner="u1")["changed"] is True

    reloaded = ApexModeStore(path)
    assert reloaded.is_enabled("thread-a") is True
    assert reloaded.contract_for("thread-a").enabled is True


def test_disabling_retains_the_profile(tmp_path: Path) -> None:
    """Re-enabling must restore the authority the operator had.

    Resetting to a default would silently downgrade someone who deliberately ran
    at a higher profile.
    """
    store = ApexModeStore(tmp_path / "mode.json")
    store.enable("thread-a", "apex_max")
    outcome = store.disable("thread-a")

    assert outcome["record"]["enabled"] is False
    assert outcome["record"]["profile"] == "apex_max"
    assert store.contract_for("thread-a").enabled is False


def test_enable_and_disable_are_idempotent(tmp_path: Path) -> None:
    """A second call reports that nothing changed instead of acting again."""
    store = ApexModeStore(tmp_path / "mode.json")

    first = store.enable("t", "assist")
    second = store.enable("t", "assist")
    assert first["changed"] is True
    assert second["changed"] is False
    assert "already" in second["reason"]

    off = store.disable("t")
    again = store.disable("t")
    assert off["changed"] is True
    assert again["changed"] is False


def test_switching_profile_changes_the_authority(tmp_path: Path) -> None:
    store = ApexModeStore(tmp_path / "mode.json")
    store.enable("t", "assist")
    assist_calls = store.contract_for("t").budget.max_tool_calls

    store.enable("t", "apex_max")
    assert store.for_scope("t").profile == "apex_max"
    assert store.contract_for("t").budget.max_tool_calls > assist_calls


def test_scopes_are_isolated(tmp_path: Path) -> None:
    store = ApexModeStore(tmp_path / "mode.json")
    store.enable("thread-a", "autonomous")

    assert store.is_enabled("thread-a") is True
    assert store.is_enabled("thread-b") is False


def test_enabling_with_the_off_profile_is_refused(tmp_path: Path) -> None:
    store = ApexModeStore(tmp_path / "mode.json")
    with pytest.raises(ValueError, match="disable"):
        store.enable("t", "off")


def test_an_unknown_profile_is_refused_and_the_valid_ones_are_named(tmp_path: Path) -> None:
    store = ApexModeStore(tmp_path / "mode.json")
    with pytest.raises(ValueError) as excinfo:
        store.enable("t", "god_mode")

    message = str(excinfo.value)
    assert "god_mode" in message
    assert "apex_max" in message, "the refusal must let the caller pick a real profile"


def test_every_transition_is_journalled_with_its_provenance(tmp_path: Path) -> None:
    """Autonomy that appears with no record of who enabled it is the thing this
    whole package exists to avoid."""
    store = ApexModeStore(tmp_path / "mode.json")
    store.enable("t", "assist", owner="u1")
    store.enable("t", "apex_max", owner="u1")
    store.disable("t", owner="u1")

    events = store.read_events("t")
    assert [e["event"] for e in events] == ["apex.enabled", "apex.enabled", "apex.disabled"]
    assert all(e["owner"] == "u1" for e in events)
    assert events[1]["profile"] == "apex_max"
    assert all(e["durable"] is True for e in events)


def test_a_journal_scoped_to_another_key_reads_empty(tmp_path: Path) -> None:
    store = ApexModeStore(tmp_path / "mode.json")
    store.enable("t", "assist", owner="u1")
    assert store.read_events("other") == []


# --------------------------------------------------------------------------- #
# The shared entry point the three surfaces call
# --------------------------------------------------------------------------- #


def test_set_mode_is_the_one_function_every_surface_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two independently written toggles would eventually disagree.

    The slash command and the HTTP route both route through :func:`set_mode`, so
    a change to what "on" means cannot land in one of them.
    """
    import alpha.apex.mode as mode_module

    instance = ApexModeStore(tmp_path / "mode.json")
    monkeypatch.setattr(mode_module, "_store", instance)
    monkeypatch.setattr(mode_module, "_default_storage_path", lambda: instance.storage_path)

    assert set_mode("t", True, profile="assist")["changed"] is True
    assert read_mode("t")["enabled"] is True
    assert set_mode("t", False)["record"]["enabled"] is False


# --------------------------------------------------------------------------- #
# The /apex command family
# --------------------------------------------------------------------------- #


@pytest.fixture()
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> object:
    """The real process-wide registry, with a temp mode store behind it."""
    import alpha.apex.mode as mode_module
    from alpha.commands.registry import command_registry

    instance = ApexModeStore(tmp_path / "mode.json")
    monkeypatch.setattr(mode_module, "_store", instance)
    monkeypatch.setattr(mode_module, "_default_storage_path", lambda: instance.storage_path)

    import alpha.apex.commands as apex_commands
    import alpha.commands  # noqa: F401  (binds the handlers)

    apex_commands.register_apex_commands()
    return command_registry


def test_apex_binds_exactly_the_five_published_verbs() -> None:
    """The binding list is the audit surface for the command family.

    ``bound_commands()`` exists so the parity test and this one can read what
    was registered rather than trusting that registration happened.
    """
    import alpha.apex.commands as apex_commands

    assert set(apex_commands.bound_commands()) == {"/apex", "/apex on", "/apex off", "/apex status", "/apex policy"}


def test_registering_the_family_twice_does_not_double_bind() -> None:
    """Re-binding replaces the handler rather than stacking a second one.

    ``alpha.commands.__init__`` imports the module for its binding side effect,
    so a module reload would otherwise leave two live handlers for one command.
    """
    import alpha.apex.commands as apex_commands
    from alpha.commands.registry import command_registry

    apex_commands.register_apex_commands()
    apex_commands.register_apex_commands()
    assert set(apex_commands.bound_commands()) == set(apex_commands.bound_commands())
    assert command_registry.get("/apex on") is not None


def test_apex_with_no_argument_is_status_and_never_enables(registry) -> None:
    """A bare or truncated line must not grant autonomy from a typo."""
    result = registry.execute(command_line="/apex", context={"thread_id": "t1"})

    assert result.status == "success"
    assert result.data["mode"]["enabled"] is False
    assert "OFF" in result.output


def test_apex_on_defaults_to_assist_not_the_most_permissive_profile(registry) -> None:
    """Turning APEX on is not a request for maximum authority."""
    result = registry.execute(command_line="/apex on", context={"thread_id": "t1"})

    assert result.status == "success"
    assert result.data["enabled"] is True
    assert result.data["mode"]["profile"] == "assist"


def test_apex_on_names_the_profile_it_granted(registry) -> None:
    result = registry.execute(command_line="/apex on apex_max", context={"thread_id": "t1"})
    assert result.data["mode"]["profile"] == "apex_max"


def test_apex_on_with_a_bad_profile_is_refused_and_changes_nothing(registry) -> None:
    result = registry.execute(command_line="/apex on god_mode", context={"thread_id": "t1"})

    assert result.status == "error"
    assert "apex_max" in result.output, "the refusal names the valid profiles"
    assert result.data["enabled"] is False


def test_apex_commands_are_scoped_to_their_conversation(registry) -> None:
    registry.execute(command_line="/apex on", context={"thread_id": "t1"})

    other = registry.execute(command_line="/apex status", context={"thread_id": "t2"})
    assert other.data["mode"]["enabled"] is False


def test_a_mistyped_subcommand_is_refused_rather_than_run_as_enable(registry) -> None:
    """/apex enabel must not be interpreted as /apex on."""
    result = registry.execute(command_line="/apex enabel", context={"thread_id": "t1"})

    assert result.status == "not_found"
    assert "enabel" in result.output


def test_apex_off_preserves_the_profile_and_is_idempotent(registry) -> None:
    registry.execute(command_line="/apex on apex_max", context={"thread_id": "t1"})

    off = registry.execute(command_line="/apex off", context={"thread_id": "t1"})
    assert off.data["enabled"] is False
    assert off.data["mode"]["profile"] == "apex_max"

    again = registry.execute(command_line="/apex off", context={"thread_id": "t1"})
    assert again.data["changed"] is False


def test_apex_policy_reports_in_force_only_for_the_active_profile(registry) -> None:
    """/apex policy assist during an apex_max session must not claim to be active.

    ``contract.enabled`` answers "does this profile grant authority", which is
    true for every non-OFF profile — so reading that as "is this the profile in
    force" would make every profile look active.
    """
    registry.execute(command_line="/apex on apex_max", context={"thread_id": "t1"})

    active = registry.execute(command_line="/apex policy", context={"thread_id": "t1"})
    assert "in force" in active.output.splitlines()[0]

    inactive = registry.execute(command_line="/apex policy assist", context={"thread_id": "t1"})
    assert "not in force" in inactive.output.splitlines()[0]


def test_apex_policy_after_off_says_nothing_is_granted(registry) -> None:
    registry.execute(command_line="/apex on", context={"thread_id": "t1"})
    registry.execute(command_line="/apex off", context={"thread_id": "t1"})

    result = registry.execute(command_line="/apex policy", context={"thread_id": "t1"})
    assert "not in force" in result.output
    assert "grants nothing right now" in result.output


def test_apex_status_is_read_only(registry) -> None:
    registry.execute(command_line="/apex on", context={"thread_id": "t1"})
    result = registry.execute(command_line="/apex status", context={"thread_id": "t1"})

    assert result.data["read_only"] is True
    assert "ON" in result.output


def test_apex_status_reports_an_unreadable_fleet_as_refusing_work(registry, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fleet state nobody could read must not render as a healthy system."""

    class _Boom:
        @staticmethod
        def read_state():
            raise RuntimeError("fleet state unavailable")

    monkeypatch.setitem(__import__("sys").modules, "alpha.runtime.control", _Boom)
    result = registry.execute(command_line="/apex status", context={"thread_id": "t1"})

    assert result.data["fleet"]["available"] is False
    assert "treated as refusing work" in result.output


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, uid: str, is_admin: bool) -> TestClient:
    import alpha.apex.mode as mode_module
    from app.gateway.routers import apex as apex_router

    instance = ApexModeStore(tmp_path / "mode.json")
    monkeypatch.setattr(mode_module, "_store", instance)
    monkeypatch.setattr(mode_module, "_default_storage_path", lambda: instance.storage_path)
    monkeypatch.setattr(apex_router, "get_apex_mode_store", lambda: instance)

    app = FastAPI()
    app.include_router(apex_router.router)

    @app.middleware("http")
    async def _inject(request, call_next):
        request.state.user = SimpleNamespace(id=uid, is_admin=is_admin)
        return await call_next(request)

    return TestClient(app)


def test_the_mode_route_answers_off_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch, uid="admin-1", is_admin=True)
    body = client.get("/api/apex/mode").json()

    assert body["enabled"] is False
    assert body["contract_enabled"] is False


def test_enabling_requires_an_administrator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Turning autonomy on is an operator act, not a self-service action."""
    client = _client(tmp_path, monkeypatch, uid="user-2", is_admin=False)

    assert client.post("/api/apex/enable", json={"profile": "autonomous"}).status_code == 403
    assert client.get("/api/apex/mode").json()["enabled"] is False


def test_a_user_cannot_read_another_session_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The cross-scope refusal is reachable on the read path.

    The write routes are admin-gated, so this read is where an unprivileged
    cross-session probe would actually land.
    """
    client = _client(tmp_path, monkeypatch, uid="alice", is_admin=False)

    assert client.get("/api/apex/mode", params={"scope_key": "bob"}).status_code == 403
    assert client.get("/api/apex/mode").status_code == 200


def test_an_admin_enables_and_the_write_is_durable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch, uid="admin-1", is_admin=True)
    body = client.post("/api/apex/enable", json={"profile": "assist"}).json()

    assert body["enabled"] is True
    assert body["contract_enabled"] is True
    assert body["changed"] is True
    assert body["durable"] is True
    assert body["contract_digest"].startswith("apxc-")


def test_a_second_enable_reports_no_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch, uid="admin-1", is_admin=True)
    client.post("/api/apex/enable", json={"profile": "assist"})
    body = client.post("/api/apex/enable", json={"profile": "assist"}).json()

    assert body["changed"] is False
    assert "already" in body["reason"]


def test_an_unknown_profile_is_422_naming_the_valid_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo must not silently grant a different authority."""
    client = _client(tmp_path, monkeypatch, uid="admin-1", is_admin=True)
    response = client.post("/api/apex/enable", json={"profile": "god_mode"})

    assert response.status_code == 422
    assert "apex_max" in response.json()["detail"]
    assert client.get("/api/apex/mode").json()["enabled"] is False


def test_disabling_keeps_the_profile_and_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch, uid="admin-1", is_admin=True)
    client.post("/api/apex/enable", json={"profile": "apex_max"})

    off = client.post("/api/apex/disable", json={}).json()
    assert off["enabled"] is False
    assert off["profile"] == "apex_max"
    assert off["contract_enabled"] is False

    assert client.post("/api/apex/disable", json={}).json()["changed"] is False


def test_a_degraded_store_is_disclosed_over_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed is not the same as silently off.

    An operator looking at a clean "OFF" has no way to tell a deliberate
    decision from a read that failed.
    """
    from app.gateway.routers import apex as apex_router

    bad = tmp_path / "bad.json"
    bad.write_text("{ nope", encoding="utf-8")
    degraded = ApexModeStore(bad)
    monkeypatch.setattr(apex_router, "get_apex_mode_store", lambda: degraded)

    app = FastAPI()
    app.include_router(apex_router.router)

    @app.middleware("http")
    async def _inject(request, call_next):
        request.state.user = SimpleNamespace(id="admin-1", is_admin=True)
        return await call_next(request)

    body = TestClient(app).get("/api/apex/mode").json()
    assert body["enabled"] is False
    assert body["load_error"] is not None


def test_the_mode_routes_are_not_shadowed_by_the_session_catch_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Route order is load-bearing, the same trap the groups router documents.

    A ``/sessions/{session_id}`` catch-all declared first would answer
    ``405 Session 'enable' not found`` for a feature that exists.
    """
    client = _client(tmp_path, monkeypatch, uid="admin-1", is_admin=True)

    assert client.get("/api/apex/mode").status_code == 200
    assert client.get("/api/apex/enable").status_code == 405
    assert client.get("/api/apex/sessions/nope").status_code == 404
    for path in ("/api/apex/status", "/api/apex/policy", "/api/apex/invariants"):
        assert client.get(path).status_code == 200
