"""Per-provider readiness isolation for ``GET /api/channels/providers``.

``/providers`` fans one readiness reconciliation out per configured provider.
The two properties that fan-out has to hold are independent, and it is easy
to trade one for the other:

* **independence** -- one provider whose transport is wedged must degrade on
  its own row, leaving every sibling provider reconciled and reported;
* **concurrency** -- one slow channel restart must not serialize the response.

The implementation had a comment stating both intents and a bare
``asyncio.gather`` stating neither: the first escaping failure propagated out
of the fan-out, so the whole ``/providers`` response became a 500 and every
other provider's reconciliation result was abandoned. The reconciliation
result is then *reported* per provider, so a provider that could not be
reconciled is never advertised as connectable on the strength of a
``get_status`` snapshot taken around the failure.

Every test below fails against the bare-gather implementation. No
``skip``/``xfail``: each one asserts the exact row that must degrade and the
exact rows that must not.
"""

from __future__ import annotations

import asyncio
import time
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import UUID

import anyio
import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from alpha.config.app_config import AppConfig, reset_app_config, set_app_config
from alpha.config.channel_connections_config import ChannelConnectionsConfig
from app.channels.runtime_config_store import ChannelRuntimeConfigStore
from app.gateway.auth.models import User
from app.gateway.routers import channel_connections

pytestmark = pytest.mark.usefixtures("_stub_app_config")

#: Three independent providers, so "one broken" leaves two healthy siblings
#: whose reconciliation must still be observed and reported.
_CHANNELS_CONFIG: dict[str, dict] = {
    "slack": {"enabled": True, "bot_token": "xoxb-operator", "app_token": "xapp-operator"},
    "discord": {"enabled": True, "bot_token": "discord-bot"},
    "feishu": {"enabled": True, "app_id": "feishu-app", "app_secret": "feishu-secret"},
}

_CONNECTIONS_CONFIG: dict = {
    "enabled": True,
    "slack": {"enabled": True},
    "discord": {"enabled": True},
    "feishu": {"enabled": True},
}


@pytest.fixture(autouse=True)
def _stub_app_config(monkeypatch):
    """Keep the router independent of a developer-local config.yaml."""
    monkeypatch.setenv("AGENT_WORKSPACE_AUTH_DISABLED", "0")
    set_app_config(AppConfig.model_validate({"sandbox": {"use": "alpha.sandbox.local:LocalSandboxProvider"}}))
    yield
    reset_app_config()


def _user() -> User:
    return User(
        id=UUID("11111111-2222-3333-4444-555555555555"),
        email="alice@example.com",
        password_hash="x",
        system_role="admin",
    )


async def _make_repo(tmp_path):
    from alpha.persistence.channel_connections import ChannelConnectionRepository
    from alpha.persistence.engine import get_session_factory, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'readiness.db'}", sqlite_dir=str(tmp_path))
    return ChannelConnectionRepository(get_session_factory())


def _make_app(repo, channels_config: dict):
    app = make_authed_test_app(user_factory=_user)
    app.state.channel_connections_config = ChannelConnectionsConfig.model_validate(_CONNECTIONS_CONFIG)
    app.state.channel_connection_repo = repo
    app.state.channels_config = {name: dict(cfg) for name, cfg in channels_config.items()}
    tmpdir = TemporaryDirectory()
    app.state.channel_runtime_config_tmpdir = tmpdir
    app.state.channel_runtime_config_store = ChannelRuntimeConfigStore(f"{tmpdir.name}/runtime-config.json")
    app.include_router(channel_connections.router)
    return app


def _install_service(monkeypatch, service_factory) -> None:
    monkeypatch.setattr("app.channels.service.get_channel_service", service_factory)


def _all_running_status() -> dict:
    return {
        "service_running": True,
        "channels": {name: {"enabled": True, "running": True} for name in _CHANNELS_CONFIG},
    }


def _get_providers(app):
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/channels/providers")
    assert response.status_code == 200, response.text
    return {item["provider"]: item for item in response.json()["providers"]}


# ---------------------------------------------------------------------------
# independence
# ---------------------------------------------------------------------------


def test_one_provider_raising_does_not_blank_the_response_for_the_others(tmp_path, monkeypatch):
    """A reconciliation that raises must cost exactly one row, not the endpoint.

    ``ensure_channel_ready`` is the documented shape of a real provider fault
    (a transport that refuses to start). Under a bare ``asyncio.gather`` the
    first such failure propagated out of the fan-out, so the whole
    ``/providers`` response was a 500 and the two healthy providers' rows were
    never built at all.
    """
    repo = anyio.run(_make_repo, tmp_path)
    reconciled: list[str] = []

    async def ensure_channel_ready(provider, runtime_config):
        reconciled.append(provider)
        if provider == "discord":
            raise RuntimeError("discord transport is wedged")
        return True

    _install_service(
        monkeypatch,
        lambda: SimpleNamespace(get_status=_all_running_status, ensure_channel_ready=ensure_channel_ready),
    )
    app = _make_app(repo, _CHANNELS_CONFIG)

    providers = _get_providers(app)

    # The endpoint survived, and every enabled provider is still reported.
    assert set(providers) == set(_CHANNELS_CONFIG)
    # The healthy siblings are reported healthy...
    assert providers["slack"]["connectable"] is True
    assert providers["slack"]["unavailable_reason"] is None
    assert providers["feishu"]["connectable"] is True
    assert providers["feishu"]["unavailable_reason"] is None
    # ...and the broken one is degraded on its own row, with a reason, and is
    # not advertised as connectable off a stale "running" snapshot.
    assert providers["discord"]["connectable"] is False
    assert "Discord" in providers["discord"]["unavailable_reason"]
    assert providers["discord"]["connection_status"] == "not_connected"
    # Every provider really was reconciled; the fault did not skip its siblings.
    assert sorted(reconciled) == ["discord", "feishu", "slack"]

    anyio.run(repo.close)


def test_channel_service_resolution_failure_degrades_per_provider(tmp_path, monkeypatch):
    """A broken channel-service lookup must not 500 the whole response.

    ``get_channel_service()`` is a process-wide singleton accessor resolved
    inside the reconciliation coroutine. When it raises, the pre-existing
    guard only covered the ``ensure_channel_ready`` call, so the fault escaped
    into the shared fan-out and every provider disappeared behind a 500.
    """
    repo = anyio.run(_make_repo, tmp_path)
    calls = {"n": 0}

    def exploding_service():
        calls["n"] += 1
        raise OSError("channel service registry unavailable")

    _install_service(monkeypatch, exploding_service)
    app = _make_app(repo, _CHANNELS_CONFIG)

    providers = _get_providers(app)

    assert set(providers) == set(_CHANNELS_CONFIG)
    for name in _CHANNELS_CONFIG:
        assert providers[name]["connectable"] is False, name
        assert "could not be reconciled" in providers[name]["unavailable_reason"], name
    # Every provider was attempted independently, on both of the paths that
    # resolve the singleton for one provider: the readiness reconciliation and
    # the status read that builds that provider's row. A bare fan-out that
    # abandoned its siblings attempts fewer than this (or 500s instead).
    assert calls["n"] == 2 * len(_CHANNELS_CONFIG), calls["n"]

    anyio.run(repo.close)


@pytest.mark.parametrize(
    ("fault", "expected", "label"),
    [
        pytest.param("service_lookup", False, "get_channel_service() raises", id="service_lookup_raises"),
        # ``getattr(service, "ensure_channel_ready", None)`` swallows the
        # AttributeError, so a service without that capability answers "not
        # available" (None) rather than "reconciled and not ready" (False).
        pytest.param("method_lookup", None, "the service has no ensure_channel_ready", id="no_readiness_capability"),
        pytest.param("call", False, "ensure_channel_ready raises", id="ensure_channel_ready_raises"),
    ],
)
@pytest.mark.asyncio
async def test_the_per_provider_reconciliation_never_raises(monkeypatch, fault: str, expected, label: str) -> None:
    """The per-provider helper owns its own faults, whatever shape they take.

    The route's fan-out wraps this helper too, so a route-level test alone
    cannot tell *which* layer absorbed a fault -- and a helper that leaks is a
    latent 500 for every other caller (``connect_channel_provider`` awaits the
    same function). Pinned directly, per fault shape:

    * the singleton accessor raising (it resolves *outside* the old guard);
    * a service object that raises from attribute lookup;
    * ``ensure_channel_ready`` itself raising, which the old guard did cover.
    """
    if fault == "service_lookup":

        def _explode() -> object:
            raise OSError("channel service registry unavailable")

        monkeypatch.setattr("app.channels.service.get_channel_service", _explode)
    elif fault == "method_lookup":

        class _Hostile:
            def __getattr__(self, name: str):
                raise AttributeError(name)

        monkeypatch.setattr("app.channels.service.get_channel_service", lambda: _Hostile())
    else:

        async def _ensure_channel_ready(provider, runtime_config):
            raise RuntimeError("transport is wedged")

        monkeypatch.setattr(
            "app.channels.service.get_channel_service",
            lambda: SimpleNamespace(ensure_channel_ready=_ensure_channel_ready),
        )

    # "not configured at all" is the None answer, whichever fault follows.
    assert await channel_connections._ensure_runtime_channel_ready_if_available("slack", {}) is None

    result = await channel_connections._ensure_runtime_channel_ready_if_available(
        "slack", {"slack": {"enabled": True, "bot_token": "xoxb-operator", "app_token": "xapp-operator"}}
    )
    assert result is expected, f"{label} must degrade this provider to {expected!r}, got {result!r}"


def test_a_provider_cancelling_its_reconciliation_does_not_blank_the_response(tmp_path, monkeypatch):
    """A ``CancelledError`` from one provider is that provider's failure.

    ``CancelledError`` is a ``BaseException``, so the ``except Exception``
    inside the reconciliation never saw it. Readiness reconciliation races
    Gateway shutdown (a channel ``stop()`` re-raises ``CancelledError``), so
    this is a real shape: one provider's cancellation must degrade its own
    row, not turn the endpoint into a 500 for everybody.
    """
    repo = anyio.run(_make_repo, tmp_path)

    async def ensure_channel_ready(provider, runtime_config):
        if provider == "feishu":
            raise asyncio.CancelledError()
        return True

    _install_service(
        monkeypatch,
        lambda: SimpleNamespace(get_status=_all_running_status, ensure_channel_ready=ensure_channel_ready),
    )
    app = _make_app(repo, _CHANNELS_CONFIG)

    providers = _get_providers(app)

    assert set(providers) == set(_CHANNELS_CONFIG)
    assert providers["slack"]["connectable"] is True
    assert providers["discord"]["connectable"] is True
    assert providers["feishu"]["connectable"] is False
    assert "Feishu" in providers["feishu"]["unavailable_reason"]

    anyio.run(repo.close)


def test_a_provider_returning_not_ready_reports_its_own_state(tmp_path, monkeypatch):
    """``ensure_channel_ready`` returning False must be visible, not swallowed.

    The reconciliation already reported "not ready", but a ``get_status``
    snapshot taken around the same instant still said ``running: true``, so the
    row was published as fully connectable with no reason at all. The run's own
    answer is the authoritative one for the provider it just tried to start.
    """
    repo = anyio.run(_make_repo, tmp_path)

    async def ensure_channel_ready(provider, runtime_config):
        return provider != "slack"

    _install_service(
        monkeypatch,
        lambda: SimpleNamespace(get_status=_all_running_status, ensure_channel_ready=ensure_channel_ready),
    )
    app = _make_app(repo, _CHANNELS_CONFIG)

    providers = _get_providers(app)

    assert providers["slack"]["connectable"] is False
    assert "Slack" in providers["slack"]["unavailable_reason"]
    assert providers["discord"]["connectable"] is True
    assert providers["feishu"]["connectable"] is True

    anyio.run(repo.close)


def test_a_drifted_provider_table_degrades_instead_of_raising(tmp_path, monkeypatch):
    """A provider with no runtime-requirements entry skips itself, not the request.

    ``_PROVIDER_META``, ``_RUNTIME_REQUIREMENTS`` and ``_CREDENTIAL_FIELDS`` are
    three independent tables. Adding a provider to one and forgetting the next
    used to make the per-provider eligibility check raise ``KeyError`` inside
    the fan-out -- consumed eagerly in the request frame, before any
    reconciliation ran -- and 500 the response for every provider.
    """
    repo = anyio.run(_make_repo, tmp_path)
    reconciled: list[str] = []

    async def ensure_channel_ready(provider, runtime_config):
        reconciled.append(provider)
        return True

    _install_service(
        monkeypatch,
        lambda: SimpleNamespace(get_status=_all_running_status, ensure_channel_ready=ensure_channel_ready),
    )
    monkeypatch.setitem(
        channel_connections._RUNTIME_REQUIREMENTS,
        "slack",
        channel_connections._RUNTIME_REQUIREMENTS["slack"],
    )
    monkeypatch.delitem(channel_connections._RUNTIME_REQUIREMENTS, "discord")

    app = _make_app(repo, _CHANNELS_CONFIG)
    providers = _get_providers(app)

    assert set(providers) == set(_CHANNELS_CONFIG)
    # Discord is not reconciled (its eligibility cannot be evaluated) but it
    # is still reported; the other two are unaffected.
    assert sorted(reconciled) == ["feishu", "slack"]
    assert providers["slack"]["connectable"] is True
    assert providers["feishu"]["connectable"] is True

    anyio.run(repo.close)


# ---------------------------------------------------------------------------
# concurrency (must survive the fix)
# ---------------------------------------------------------------------------

#: Each provider's reconciliation parks for this long. Small enough to keep the
#: test quick, large enough that a serialised implementation is unmistakable.
_SLOW_PROVIDER_DELAY = 0.3


def test_a_slow_provider_does_not_serialize_the_response(tmp_path, monkeypatch):
    """Reconciliation stays concurrent: wall time is the slowest, not the sum.

    A fixed sleep is the only honest way to measure this -- the property under
    test is that the awaits overlap. Three providers each parked for
    ``_SLOW_PROVIDER_DELAY`` must cost about one delay, not three, and all
    three must have completed. This is the property the two lines above the
    original ``asyncio.gather`` claimed, so the fix must not have bought
    per-provider isolation by serialising the fan-out.
    """
    repo = anyio.run(_make_repo, tmp_path)
    started: list[str] = []
    finished: list[str] = []

    async def ensure_channel_ready(provider, runtime_config):
        started.append(provider)
        await asyncio.sleep(_SLOW_PROVIDER_DELAY)
        finished.append(provider)
        return True

    _install_service(
        monkeypatch,
        lambda: SimpleNamespace(get_status=_all_running_status, ensure_channel_ready=ensure_channel_ready),
    )
    app = _make_app(repo, _CHANNELS_CONFIG)

    begin = time.perf_counter()
    providers = _get_providers(app)
    elapsed = time.perf_counter() - begin

    assert sorted(finished) == ["discord", "feishu", "slack"], finished
    assert sorted(started) == ["discord", "feishu", "slack"], started
    assert all(item["connectable"] for item in providers.values())
    # Generous upper bound for CI jitter, but far below the serialised cost
    # (3 * delay). A serialised implementation fails this.
    assert elapsed < _SLOW_PROVIDER_DELAY * 2, f"reconciliation serialised: {elapsed:.3f}s for 3 x {_SLOW_PROVIDER_DELAY}s"
    # ...and it must not have skipped the slow provider to get there.
    assert elapsed >= _SLOW_PROVIDER_DELAY, "the slowest provider must still be waited for"

    anyio.run(repo.close)

