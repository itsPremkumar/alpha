"""``GET /api/features`` must never block the Gateway's event loop.

## Why this suite exists

The wiring audit behind ``advertised_capabilities`` is an AST walk over the whole
harness and app tree. It measured **358 s** on this checkout, and it used to run
*inline* the first time the route was called - from inside an ``async def``
handler, which means on the asyncio event loop thread. The Gateway therefore
stopped answering everything else for six minutes: ``/health/ready`` timed out,
the launcher gave up at its 240 s budget and restarted, the watchdog escalated to
a full stack restart, and the audit never lived long enough to fill its cache, so
the next boot repeated it. The app appeared to "crash continuously" while never
actually crashing.

These tests pin the properties that prevent it:

1. :func:`wiring_report` returns without ever computing on the caller's thread;
2. the audit is single-flight (two callers never start two walks);
3. a *failed* audit is not retried on every request (the old failure path
   returned ``[]`` without caching, so one exception meant the full walk ran
   again per request, forever);
4. a whole claim batch shares one tree walk.
"""

from __future__ import annotations

import threading
import time

from alpha.capabilities import honesty
from alpha.capabilities.honesty import Claim, WiringReport, WiringState
from app.gateway.routers import capability_honesty as mod


def _quiesce(timeout: float = 15.0) -> None:
    """Wait until no background audit is in flight.

    The worker resolves ``audit_registered_claims`` through the module global at
    call time, so a test must let it finish before ``monkeypatch`` unwinds -
    otherwise the thread would run the *real* multi-second audit after the test
    that installed the stub has ended.
    """
    deadline = time.monotonic() + timeout
    while mod.audit_state() == "running" and time.monotonic() < deadline:
        time.sleep(0.02)


def test_wiring_report_returns_without_computing_on_the_calling_thread(monkeypatch):
    """The load-bearing invariant: a request thread must never do the walk.

    If this regresses, ``elapsed`` jumps to the full audit duration and the
    Gateway stops answering every other route while it runs.
    """

    def slow_audit() -> list[WiringReport]:
        time.sleep(3.0)
        return []

    mod.reset_for_tests()
    monkeypatch.setattr(mod, "audit_registered_claims", slow_audit)

    started = time.monotonic()
    result = mod.wiring_report()
    elapsed = time.monotonic() - started

    assert result == [], "nothing has been verified yet, so nothing may be claimed"
    assert elapsed < 1.0, f"wiring_report blocked its caller for {elapsed:.1f}s; this freezes the event loop"
    _quiesce()
    mod.reset_for_tests()


def test_the_background_worker_is_not_the_calling_thread(monkeypatch):
    """Proves the walk really is handed off, not merely skipped."""
    seen: list[str] = []

    def audit() -> list[WiringReport]:
        seen.append(threading.current_thread().name)
        return []

    mod.reset_for_tests()
    monkeypatch.setattr(mod, "audit_registered_claims", audit)

    mod.wiring_report()

    deadline = time.monotonic() + 10.0
    while not seen and time.monotonic() < deadline:
        time.sleep(0.02)

    assert seen, "the audit never ran"
    assert seen[0] != threading.current_thread().name, "the audit ran inline on the caller"
    _quiesce()
    mod.reset_for_tests()


def test_the_audit_is_single_flight(monkeypatch):
    """Two concurrent starters must not run two full-tree walks."""
    entered = threading.Event()
    release = threading.Event()

    def audit() -> list[WiringReport]:
        entered.set()
        release.wait(10.0)
        return []

    mod.reset_for_tests()
    monkeypatch.setattr(mod, "audit_registered_claims", audit)

    assert mod.start_wiring_audit() is True, "the first call should start the worker"
    assert entered.wait(10.0), "the worker never started"
    assert mod.start_wiring_audit() is False, "a second worker was started while one was running"
    assert mod.audit_state() == "running"

    release.set()
    _quiesce()
    assert mod.audit_state() == "ready"
    assert mod.start_wiring_audit() is False, "a finished audit must not be recomputed"
    mod.reset_for_tests()


def test_a_failed_audit_is_not_retried_on_every_request(monkeypatch):
    """The old failure path returned ``[]`` *without* caching.

    One exception therefore re-ran the whole multi-second walk on every single
    ``/api/features`` call - a permanent background CPU burn that only ever
    happened while the Gateway was already unhealthy.
    """
    calls: list[int] = []

    def boom() -> list[WiringReport]:
        calls.append(1)
        raise RuntimeError("audit exploded")

    mod.reset_for_tests()
    monkeypatch.setattr(mod, "audit_registered_claims", boom)

    assert mod.wiring_report() == []
    _quiesce()
    assert mod.audit_state() == "failed"
    assert len(calls) == 1

    for _ in range(5):
        assert mod.wiring_report() == []
    assert len(calls) == 1, f"the failed audit was recomputed {len(calls)} times"
    mod.reset_for_tests()


def test_refresh_recomputes_in_the_background_without_blocking(monkeypatch):
    mod.reset_for_tests()
    monkeypatch.setattr(mod, "audit_registered_claims", lambda: [])

    mod.wiring_report()
    _quiesce()
    assert mod.audit_state() == "ready"

    mod.wiring_report(refresh=True)
    assert mod.audit_state() in {"running", "ready"}
    _quiesce()
    mod.reset_for_tests()


def test_the_payload_states_the_verdict_without_merging_it_into_a_flag(monkeypatch):
    """\"configured and started\" and \"wired\" must stay two separate facts."""
    report = WiringReport(
        state=WiringState.UNWIRED,
        capability_id="stub_capability",
        symbol="mod.py::thing",
        reason="because the ratchet needs it",
        detail="no module outside it references it",
    )
    mod.reset_for_tests()
    monkeypatch.setattr(mod, "audit_registered_claims", lambda: [report])

    mod.start_wiring_audit()
    _quiesce()

    payload = mod.advertised_capabilities()
    assert payload == [
        {
            "capability_id": "stub_capability",
            "wiring": "unwired",
            "consumers": [],
            "detail": "no module outside it references it",
            "reason": "because the ratchet needs it",
        }
    ]
    mod.reset_for_tests()


def test_advertised_capabilities_is_empty_before_the_first_audit_finishs(monkeypatch):
    """An unverified capability must not be advertised, and must not stall."""
    release = threading.Event()

    def audit() -> list[WiringReport]:
        release.wait(10.0)
        return [WiringReport(WiringState.WIRED, "late", "m::x")]

    mod.reset_for_tests()
    monkeypatch.setattr(mod, "audit_registered_claims", audit)
    mod.start_wiring_audit()

    started = time.monotonic()
    assert mod.advertised_capabilities() == []
    assert time.monotonic() - started < 1.0

    release.set()
    _quiesce()
    assert [item["capability_id"] for item in mod.advertised_capabilities()] == ["late"]
    mod.reset_for_tests()


def test_check_claims_walks_the_tree_once_for_the_whole_batch(tmp_path, monkeypatch):
    """Four claims must not mean four tree walks.

    Per-claim walking and re-parsing was what made the audit cost 358 s, and
    that cost was paid on the event loop.
    """
    harness = tmp_path / "backend" / "packages" / "harness" / "alpha"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "capability.py").write_text("def do_the_thing():\n    return 1\n", encoding="utf-8")
    (harness / "consumer.py").write_text("from alpha.capability import do_the_thing\n", encoding="utf-8")

    walks: list[int] = []
    real_scan = honesty._scan_roots

    def spy(roots):
        walks.append(1)
        return real_scan(roots)

    monkeypatch.setattr(honesty, "_scan_roots", spy)

    claims = [
        Claim("a", "capability.py::do_the_thing"),
        Claim("b", "capability.py::do_the_thing"),
        Claim("c", "capability.py::do_the_thing"),
        Claim("d", "capability.py::do_the_thing"),
    ]
    reports = honesty.check_claims(claims, roots=[harness])

    assert len(reports) == len(claims)
    assert len(walks) == 1, f"{len(walks)} tree walks for {len(claims)} claims; share one index"


def test_a_binary_file_in_a_source_root_cannot_crash_the_audit(tmp_path):
    """``ast.parse`` raises ``ValueError`` on embedded NUL bytes.

    The old handler caught only ``(OSError, SyntaxError)``, so one stray binary
    file made the whole audit raise - which, because the failure path did not
    cache, re-ran it on every request.
    """
    harness = tmp_path / "backend" / "packages" / "harness" / "alpha"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "capability.py").write_text("def do_the_thing():\n    return 1\n", encoding="utf-8")
    (harness / "binary.py").write_bytes(b"\x00\x01\x02 def ((): \x00")

    report = honesty.find_consumers(Claim("stub", "capability.py::do_the_thing"), roots=[harness])
    assert report.state is WiringState.UNWIRED
