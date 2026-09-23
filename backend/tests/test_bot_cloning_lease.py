"""Honesty tests for the ephemeral lease created by bot cloning (DY-R2 FIX 1).

Regression targets:
- ``clone_bot`` used to call ``ephemeral_mgr.provision`` with kwargs
  (``name_override``/``profile_override``) that ``register_lease``'s ``**kwargs``
  silently swallowed while omitting the required positional ``bot_name``.
  The guaranteed TypeError was swallowed into a warning, so NO clone ever got
  a lease and callers never learned about it.
- These tests assert against the REAL ephemeral store on disk (not just the
  clone payload), the honest ``lease_status`` payload on success/failure/zero
  TTL, and that lease failures are logged at ERROR with the real exception.

Registry state is isolated per test via ``AGENT_WORKSPACE_HOME`` -> tmp_path.
"""

from __future__ import annotations

import json
import logging

import pytest

from alpha.bots.cloning import BotCloneEngine
from alpha.bots.ephemeral import EphemeralBotManager
from alpha.bots.profile import BotProfile


@pytest.fixture(autouse=True)
def isolated_bot_state(tmp_path, monkeypatch):
    """Point all bot/ephemeral persistence at a per-test temp home.

    ``AGENT_WORKSPACE_HOME`` redirects ``runtime_home()`` for both the bot
    roster and ``bots/ephemeral.json``. The singletons are reset because
    ``get_bot_registry()``/``get_ephemeral_manager()`` may already hold
    instances bound to a previous home; unlike the registry, the ephemeral
    manager never re-resolves the environment variable.
    """
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))

    import alpha.bots.ephemeral as ephemeral_mod
    import alpha.bots.registry as registry_mod

    monkeypatch.setattr(registry_mod, "_global_registry", None)
    monkeypatch.setattr(registry_mod, "_global_registry_path", None)
    monkeypatch.setattr(ephemeral_mod, "_ephemeral_manager", None)
    yield tmp_path


def _engine_with_source() -> BotCloneEngine:
    engine = BotCloneEngine()
    engine.registry.register(
        BotProfile(
            name="lease_src",
            display_name="Lease Source",
            role="Source Bot",
            soul="Provide a source profile for clone-lease tests.",
            department="engineering",
        )
    )
    return engine


def test_clone_with_ttl_creates_real_lease_in_store(isolated_bot_state):
    tmp_path = isolated_bot_state
    engine = _engine_with_source()

    # Mixed-case target on purpose: register_lease keys leases by the
    # lower-cased bot_name, which pins the name-override mapping onto the
    # real signature parameter.
    target = "LeaseCloneOne"
    clone = engine.clone_bot(
        source_name="lease_src",
        target_name=target,
        specialist_directive="Handle lease honesty verification.",
        ttl_seconds=1800,
    )

    # 1. Honest payload: active status carries the real lease id + TTL, and
    #    the pre-existing metadata keys are preserved (additive-only payload).
    status = clone.metadata["lease_status"]
    assert status.startswith("active:")
    assert f"lease={target.lower()}" in status
    assert "ttl_seconds=1800" in status
    assert clone.metadata["cloned_from"] == "lease_src"
    assert clone.metadata["clone_mode"] == "specialist_fork"

    # 2. The lease ACTUALLY EXISTS in the real store - read from disk, not
    #    from the clone payload.
    lease_file = tmp_path / "bots" / "ephemeral.json"
    assert lease_file.is_file(), "register_lease never persisted a lease"
    payload = json.loads(lease_file.read_text(encoding="utf-8"))
    stored_names = [entry["bot_name"] for entry in payload["leases"]]
    assert target.lower() in stored_names

    # 3. A FRESH manager reading the same real store confirms the lease.
    fresh_mgr = EphemeralBotManager(storage_path=lease_file)
    lease = fresh_mgr.get_lease(target.lower())
    assert lease is not None
    assert lease.status == "active"
    assert lease.ttl_seconds == 1800
    assert lease.remaining_seconds > 0
    assert lease.domain == "engineering"
    assert lease.prompt_objective == "Handle lease honesty verification."

    # 4. The live manager the engine used agrees.
    live = engine.ephemeral_mgr.get_lease(target.lower())
    assert live is not None and live.status == "active"

    # 5. The clone itself is registered as before.
    assert engine.registry.get_bot(target) is not None


def test_lease_failure_is_surfaced_not_swallowed(isolated_bot_state, monkeypatch, caplog):
    tmp_path = isolated_bot_state
    engine = _engine_with_source()

    def boom(_mgr, *args, **kwargs):
        raise RuntimeError("lease backend unavailable: disk locked")

    # Inject the failure at the seam the call site actually resolves:
    # EphemeralBotManager.provision (== register_lease).
    monkeypatch.setattr(EphemeralBotManager, "provision", boom)
    caplog.set_level(logging.ERROR, logger="alpha.bots.cloning")

    clone = engine.clone_bot(
        source_name="lease_src",
        target_name="fail_clone",
        ttl_seconds=600,
    )

    # Clone still returns (profiles are usable) but the status is honest:
    # "failed: <real exception text>", never a silent/warning-only success.
    status = clone.metadata["lease_status"]
    assert status.startswith("failed:")
    assert "RuntimeError" in status
    assert "lease backend unavailable: disk locked" in status

    # No lease ever existed for the clone.
    lease_file = tmp_path / "bots" / "ephemeral.json"
    if lease_file.is_file():
        payload = json.loads(lease_file.read_text(encoding="utf-8"))
        assert "fail_clone" not in [entry["bot_name"] for entry in payload["leases"]]
    assert engine.ephemeral_mgr.get_lease("fail_clone") is None

    # The failure is visible in logs at ERROR, with the real exception text
    # and a traceback attached (no warning-only downgrade).
    error_records = [
        r
        for r in caplog.records
        if r.name == "alpha.bots.cloning" and r.levelno >= logging.ERROR
    ]
    assert error_records, "lease failure was not logged at ERROR level"
    assert any("lease backend unavailable: disk locked" in r.getMessage() for r in error_records)
    assert any(r.exc_info is not None for r in error_records)

    # The clone itself still succeeded and is registered with honest metadata.
    assert engine.registry.get_bot("fail_clone") is not None


def test_zero_ttl_creates_no_lease_and_reports_it(isolated_bot_state, monkeypatch):
    tmp_path = isolated_bot_state
    engine = _engine_with_source()

    calls: list[tuple] = []

    def spy(_mgr, *args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("provision must not be called when ttl_seconds=0")

    monkeypatch.setattr(EphemeralBotManager, "provision", spy)

    clone = engine.clone_bot(source_name="lease_src", target_name="permanent_clone", ttl_seconds=0)

    assert calls == [], "ttl_seconds=0 must not attempt a lease"
    status = clone.metadata["lease_status"]
    assert status.startswith("none:")
    assert "ttl_seconds=0" in status
    assert not (tmp_path / "bots" / "ephemeral.json").exists()
    assert engine.registry.get_bot("permanent_clone") is not None
