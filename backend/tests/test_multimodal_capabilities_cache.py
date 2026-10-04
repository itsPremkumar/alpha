"""Regression: the capability matrix must not cost a minute to read, and must say so.

The defect
----------
``GET /api/multimodal/capabilities`` called ``chain.capabilities_report()``
directly on every request. The report is a pure observation of imports and
config, but its observers *import* the engine modules (kokoro, piper,
faster-whisper, rapidocr, openwakeword, webrtcvad). Measured on this host:

* first call in a fresh process: **63.0 seconds**
* second call: **0.33 seconds**

The route description claims "Import/config observation only -- reachability is
never probed", which is true, and therefore made the cost look like a rounding
error. It is not: 63s exceeds a default 60s HTTP client timeout, so an operator
who opened the matrix on a freshly restarted Gateway saw **nothing at all** --
a timeout, not a slow answer. The live route sweep could not get an answer
inside its own 30s budget for the same reason.

The fix caches the report behind a disclosed TTL. The disclosure is load-bearing:
this repo's standing rule is that "not measured" and "measured earlier" must
never render identically, so a reused answer says so.
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI

from alpha.multimodal import chain


@pytest.fixture(autouse=True)
def _clear_cache():
    chain.invalidate_capabilities_cache()
    yield
    chain.invalidate_capabilities_cache()


def test_second_read_is_served_from_cache_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def counting() -> dict:
        calls["n"] += 1
        return {"rows": [], "voice": {}}

    monkeypatch.setattr(chain, "capabilities_report", counting)

    first = chain.capabilities_report_cached()
    second = chain.capabilities_report_cached()

    assert calls["n"] == 1, "the report must be computed once for two reads"
    assert first["cache"]["cached"] is False
    assert second["cache"]["cached"] is True
    assert second["cache"]["age_seconds"] >= 0.0
    assert second["cache"]["computed_at"], "a cached answer must still name when it was computed"


def test_an_expired_ttl_recomputes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def counting() -> dict:
        calls["n"] += 1
        return {"rows": [], "voice": {}}

    monkeypatch.setattr(chain, "capabilities_report", counting)

    chain.capabilities_report_cached(ttl_seconds=60.0)
    time.sleep(0.05)
    chain.capabilities_report_cached(ttl_seconds=0.0)
    assert calls["n"] == 2


def test_zero_ttl_disables_reuse_without_disabling_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def counting() -> dict:
        calls["n"] += 1
        return {"rows": [{"a": 1}], "voice": {}}

    monkeypatch.setattr(chain, "capabilities_report", counting)
    a = chain.capabilities_report_cached(ttl_seconds=0.0)
    b = chain.capabilities_report_cached(ttl_seconds=0.0)
    assert calls["n"] == 2
    # The cache block is additive; it must not shadow the real payload.
    assert a["rows"] == [{"a": 1}] and b["rows"] == [{"a": 1}]


def test_force_refresh_recomputes_and_repopulates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def counting() -> dict:
        calls["n"] += 1
        return {"rows": [], "voice": {}}

    monkeypatch.setattr(chain, "capabilities_report", counting)

    chain.capabilities_report_cached()
    forced = chain.capabilities_report_cached(force_refresh=True)
    assert calls["n"] == 2
    assert forced["cache"]["cached"] is False
    # ...and the forced result is what the next reader now reuses.
    again = chain.capabilities_report_cached()
    assert calls["n"] == 2
    assert again["cache"]["cached"] is True


def test_invalidate_drops_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def counting() -> dict:
        calls["n"] += 1
        return {"rows": [], "voice": {}}

    monkeypatch.setattr(chain, "capabilities_report", counting)

    chain.capabilities_report_cached()
    chain.invalidate_capabilities_cache()
    chain.capabilities_report_cached()
    assert calls["n"] == 2


def test_a_concurrent_cold_miss_never_queues_behind_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second caller must not block on the minute-long first build.

    This is the bug the lock placement was wrong about. CPython cannot interrupt
    a thread already inside the build, so the Gateway's `asyncio.wait_for`
    cancels the *await* while the worker keeps going. A second request that then
    waited on the same lock burned its own 30s deadline and answered 503 even
    though the answer was seconds away. Measured live: cold 503 at 30.6s, then an
    immediate second call also 503 at 30.7s.

    So while a build is in flight the *other* callers must return immediately --
    with the previous answer when there is one, and an honest "still loading"
    disclosure when there is not.
    """
    import threading

    calls = {"n": 0}
    gate = threading.Event()
    started = threading.Event()

    def slow() -> dict:
        calls["n"] += 1
        started.set()
        gate.wait(timeout=10.0)
        return {"rows": [{"warm": True}], "voice": {}}

    monkeypatch.setattr(chain, "capabilities_report", slow)

    results: list[tuple[threading.Thread, dict]] = []
    threads: list[threading.Thread] = []

    def call() -> None:
        results.append((threading.current_thread(), chain.capabilities_report_cached()))

    first = threading.Thread(target=call)
    first.start()
    assert started.wait(timeout=5.0), "the build never started"

    # While the build runs, a second and third caller must come back promptly.
    late: list[dict] = []
    for _ in range(2):
        t = threading.Thread(target=lambda: late.append(chain.capabilities_report_cached()))
        threads.append(t)
        t.start()

    deadline = time.monotonic() + 5.0
    while any(t.is_alive() for t in threads) and time.monotonic() < deadline:
        time.sleep(0.02)
    for t in threads:
        t.join(timeout=1.0)

    assert len(late) == 2, "a concurrent caller waited on the build instead of returning"
    for payload in late:
        assert payload["cache"]["building"] is True
        assert payload["rows"] == [], "no answer exists yet; one must not be invented"
        assert "still loading" in payload["note"]
    assert calls["n"] == 1, "only one thread may build"

    gate.set()
    first.join(timeout=10)
    assert len(results) == 1 and results[0][1]["rows"] == [{"warm": True}]


def test_a_building_flag_is_cleared_after_a_failed_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raised build must not leave the endpoint reporting 'still loading'."""
    calls = {"n": 0}

    def exploding() -> dict:
        calls["n"] += 1
        raise RuntimeError("engine import blew up")

    monkeypatch.setattr(chain, "capabilities_report", exploding)

    with pytest.raises(RuntimeError):
        chain.capabilities_report_cached()

    # Second attempt must actually retry rather than short-circuit on the flag.
    with pytest.raises(RuntimeError):
        chain.capabilities_report_cached()
    assert calls["n"] == 2


def test_a_cached_answer_is_served_immediately_while_a_rebuild_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stale-but-disclosed beats queueing: the caller gets data plus a flag."""
    import threading

    state = {"mode": "first"}
    gate = threading.Event()
    started = threading.Event()

    def report() -> dict:
        if state["mode"] == "first":
            return {"rows": [{"v": 1}], "voice": {}}
        started.set()
        gate.wait(timeout=10.0)
        return {"rows": [{"v": 2}], "voice": {}}

    monkeypatch.setattr(chain, "capabilities_report", report)
    assert chain.capabilities_report_cached()["rows"] == [{"v": 1}]

    state["mode"] = "rebuild"
    # `force_refresh` bypasses the TTL deterministically; sleeping out a 60s TTL
    # would make this test slow and still timing-dependent.
    rebuild = threading.Thread(target=lambda: chain.capabilities_report_cached(force_refresh=True))
    rebuild.start()
    assert started.wait(timeout=5.0), "the rebuild never started"

    during = chain.capabilities_report_cached()
    assert during["rows"] == [{"v": 1}], "the previous answer is served, not a queue"
    assert during["cache"]["building"] is True
    assert "previous answer" in during["cache"]["note"]

    gate.set()
    rebuild.join(timeout=10)


def test_the_uncached_report_is_still_reachable_and_unchanged() -> None:
    """`capabilities_report` keeps its contract; the cache is additive."""
    report = chain.capabilities_report()
    assert isinstance(report.get("rows"), list)
    assert "cache" not in report, "the cache block belongs to the cached wrapper only"


def _app_with_auth_disabled() -> FastAPI:
    """The router behind ``AuthMiddleware`` with auth disabled.

    ``@require_permission("runs", "read")`` calls ``require_user()``, which reads
    ``request.state.user`` -- and only ``AuthMiddleware`` sets it. Without the
    middleware every request 401s before reaching the handler, which would make
    these tests assert nothing. Auth itself is out of scope; the authorization
    boundary has its own suite (``tests/test_authorization_route_permissions.py``).
    """
    from fastapi import FastAPI

    from app.gateway.auth_middleware import AuthMiddleware
    from app.gateway.routers.multimodal import router as multimodal_router

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(multimodal_router)
    return app


@pytest.fixture
def _auth_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPHA_AUTH_DISABLED", "1")
    monkeypatch.delenv("ALPHA_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)


def test_route_is_bounded_and_discloses_failure(monkeypatch: pytest.MonkeyPatch, _auth_disabled: None) -> None:
    """The HTTP leg bounds the probe and degrades honestly, like the WS leg.

    The WebSocket capabilities handler already wrapped the same call in a
    disclosure; the HTTP route raised a bare 500 (or hung). Two call sites, one
    report, two behaviours.
    """
    from fastapi.testclient import TestClient

    def exploding() -> dict:
        raise RuntimeError("engine import blew up")

    monkeypatch.setattr(chain, "capabilities_report_cached", exploding)
    with TestClient(_app_with_auth_disabled(), raise_server_exceptions=False) as client:
        response = client.get("/api/multimodal/capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["rows"] == []
    assert "capabilities probe failed" in payload["note"]
    assert "RuntimeError" in payload["note"], "the disclosure must name the real error class"


def test_route_returns_the_disclosed_cache_block(monkeypatch: pytest.MonkeyPatch, _auth_disabled: None) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        chain,
        "capabilities_report_cached",
        lambda: {
            "rows": [{"c": "tts"}],
            "voice": {},
            "cache": {"cached": True, "age_seconds": 3.0, "ttl_seconds": 60.0, "computed_at": "2026-10-04T00:00:00+00:00"},
        },
    )
    with TestClient(_app_with_auth_disabled(), raise_server_exceptions=False) as client:
        response = client.get("/api/multimodal/capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["cache"]["cached"] is True
    assert payload["rows"] == [{"c": "tts"}]


def test_route_reports_503_when_the_probe_overruns(monkeypatch: pytest.MonkeyPatch, _auth_disabled: None) -> None:
    """A cold build that overruns its bound is an operator fact, not a hang."""
    import time

    from fastapi.testclient import TestClient

    from app.gateway.routers import multimodal as multimodal_module

    monkeypatch.setattr(multimodal_module, "CAPABILITIES_PROBE_TIMEOUT_SECONDS", 0.15)

    # `asyncio.to_thread` needs a *sync* callable: handing it a coroutine
    # function returns the coroutine object as the thread's result, which would
    # make the route return a coroutine and fail somewhere unrelated.
    def slow() -> dict:
        time.sleep(5.0)
        return {"rows": [], "voice": {}}

    monkeypatch.setattr(chain, "capabilities_report_cached", slow)
    with TestClient(_app_with_auth_disabled(), raise_server_exceptions=False) as client:
        response = client.get("/api/multimodal/capabilities")
    assert response.status_code == 503
    assert "capability probe exceeded" in response.json()["detail"]
