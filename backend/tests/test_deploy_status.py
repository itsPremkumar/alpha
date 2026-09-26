"""The tri-state that keeps Alpha's own health reporting honest.

``scripts/deploy_status.py`` exists because two things were quietly lying:

1. ``logs/alpha_health.json`` outlived the launcher that wrote it, so it kept
   claiming ``status: "starting"`` (or ``"healthy"``) with a PID that no longer
   existed. Every later diagnostic trusted it, so the lie compounded.
2. A liveness-only ``GET /health`` - which cannot fail, because "the process is
   up" is all it asserts - was treated as "Alpha can serve". A Gateway whose
   database is unreachable answers 200 there and 503 on ``/health/ready``.

These tests pin both behaviours plus the exit-code contract. Every case asserts
a concrete state: there is no "unknown means fine" path, because that is the
defect.
"""

from __future__ import annotations

import json
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

import deploy_status as ds
import pytest

NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _write_health(path: Path, **fields) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": 4242,
        "status": "starting",
        "detail": "waiting for services",
        "timestamp_utc": NOW.isoformat().replace("+00:00", "Z"),
        "gateway_port": 8001,
        "frontend_port": 3000,
        "repo_root": "C:/checkout",
    }
    payload.update(fields)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _Response:
    """Minimal stand-in for the object ``urlopen`` returns."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:  # pragma: no cover - trivial
        pass


def _ready_body(**overrides) -> bytes:
    payload = {
        "status": "ready",
        "service": "agent-workspace-gateway",
        "database": "ok",
        "checkpointer": "ok",
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def _degraded_body(database: str = "unreachable", checkpointer: str = "ok") -> bytes:
    return json.dumps(
        {
            "status": "degraded",
            "service": "agent-workspace-gateway",
            "database": database,
            "checkpointer": checkpointer,
        }
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# verify_status_file: the stale-status-file lie
# ---------------------------------------------------------------------------


class TestVerifyStatusFile:
    def test_absent_file_is_unknown_not_ok(self, tmp_path: Path) -> None:
        """No file means Alpha is not running. That is absence of evidence."""
        verdict = ds.verify_status_file(tmp_path / "alpha_health.json", now=NOW, is_running=lambda _p: True)

        assert verdict.state == ds.UNKNOWN
        assert not verdict.ok
        assert "no launcher status file" in verdict.reason

    def test_live_launcher_with_fresh_heartbeat_is_ok(self, tmp_path: Path) -> None:
        health = _write_health(tmp_path / "alpha_health.json", status="healthy")

        verdict = ds.verify_status_file(health, now=NOW, is_running=lambda p: p == 4242)

        assert verdict.state == ds.OK
        assert verdict.ok

    def test_dead_launcher_is_reported_not_ok_not_unknown(self, tmp_path: Path) -> None:
        """THE bug: the file claims 'starting' but nothing is running."""
        health = _write_health(tmp_path / "alpha_health.json", status="starting", pid=999999)

        verdict = ds.verify_status_file(health, now=NOW, is_running=lambda _p: False)

        assert verdict.state == ds.NOT_OK
        assert not verdict.ok
        assert "STALE" in verdict.reason
        assert "999999" in verdict.reason
        assert "starting" in verdict.reason

    @pytest.mark.parametrize("status", ["failed", "stopped", "stale"])
    def test_terminal_status_is_surfaced_even_while_the_pid_lives(self, tmp_path: Path, status: str) -> None:
        """A recorded failure must never render as healthy, PID or not."""
        health = _write_health(tmp_path / "alpha_health.json", status=status, detail="node not found")

        verdict = ds.verify_status_file(health, now=NOW, is_running=lambda _p: True)

        assert verdict.state == ds.NOT_OK
        assert status in verdict.reason
        assert verdict.detail == "node not found"

    def test_frozen_heartbeat_is_stale_even_while_the_pid_lives(self, tmp_path: Path) -> None:
        """A live PID that stopped writing is a frozen launcher, not a live one."""
        old = (NOW - timedelta(seconds=ds.LAUNCHER_MAX_HEARTBEAT_AGE_SECONDS + 30)).isoformat()
        health = _write_health(tmp_path / "alpha_health.json", status="healthy", timestamp_utc=old)

        verdict = ds.verify_status_file(health, now=NOW, is_running=lambda _p: True)

        assert verdict.state == ds.NOT_OK
        assert "STALE" in verdict.reason
        assert "heartbeat" in verdict.reason

    def test_heartbeat_just_inside_the_window_is_ok(self, tmp_path: Path) -> None:
        recent = (NOW - timedelta(seconds=ds.LAUNCHER_MAX_HEARTBEAT_AGE_SECONDS - 5)).isoformat()
        health = _write_health(tmp_path / "alpha_health.json", status="healthy", timestamp_utc=recent)

        assert ds.verify_status_file(health, now=NOW, is_running=lambda _p: True).state == ds.OK

    def test_corrupt_file_is_not_ok_not_unknown(self, tmp_path: Path) -> None:
        """A corrupt monitor is a defect, not an absence of one."""
        health = tmp_path / "alpha_health.json"
        health.write_text("{not json", encoding="utf-8")

        verdict = ds.verify_status_file(health, now=NOW, is_running=lambda _p: True)

        assert verdict.state == ds.NOT_OK
        assert "corrupt" in verdict.reason

    def test_non_object_json_is_not_ok(self, tmp_path: Path) -> None:
        health = tmp_path / "alpha_health.json"
        health.write_text('["healthy"]', encoding="utf-8")

        assert ds.verify_status_file(health, now=NOW, is_running=lambda _p: True).state == ds.NOT_OK

    def test_unrecognised_status_is_not_ok(self, tmp_path: Path) -> None:
        """A status nobody can interpret must not be treated as good news."""
        health = _write_health(tmp_path / "alpha_health.json", status="running")

        verdict = ds.verify_status_file(health, now=NOW, is_running=lambda _p: True)

        assert verdict.state == ds.NOT_OK
        assert "unrecognised" in verdict.reason

    def test_missing_status_field_is_not_ok(self, tmp_path: Path) -> None:
        health = _write_health(tmp_path / "alpha_health.json")
        health.write_text(json.dumps({"pid": 1, "timestamp_utc": NOW.isoformat()}), encoding="utf-8")

        verdict = ds.verify_status_file(health, now=NOW, is_running=lambda _p: True)

        assert verdict.state == ds.NOT_OK
        assert "no 'status' field" in verdict.reason

    def test_missing_timestamp_is_stale_not_ok(self, tmp_path: Path) -> None:
        health = _write_health(tmp_path / "alpha_health.json")
        health.write_text(json.dumps({"pid": 4242, "status": "healthy"}), encoding="utf-8")

        verdict = ds.verify_status_file(health, now=NOW, is_running=lambda _p: True)

        assert verdict.state == ds.NOT_OK
        assert "timestamp_utc" in verdict.reason

    def test_unparseable_pid_is_not_ok(self, tmp_path: Path) -> None:
        health = _write_health(tmp_path / "alpha_health.json")
        health.write_text(
            json.dumps({"pid": "not-a-pid", "status": "healthy", "timestamp_utc": NOW.isoformat()}),
            encoding="utf-8",
        )

        assert ds.verify_status_file(health, now=NOW, is_running=lambda _p: True).state == ds.NOT_OK

    def test_clock_skew_is_unknown_not_ok(self, tmp_path: Path) -> None:
        """A future heartbeat is not evidence of liveness in either direction."""
        future = (NOW + timedelta(seconds=600)).isoformat()
        health = _write_health(tmp_path / "alpha_health.json", status="healthy", timestamp_utc=future)

        verdict = ds.verify_status_file(health, now=NOW, is_running=lambda _p: True)

        assert verdict.state == ds.UNKNOWN
        assert "clock skew" in verdict.reason

    @pytest.mark.parametrize(
        ("raw", "reference"),
        [
            # PowerShell [DateTime]::UtcNow.ToString("o"): 7 fractional digits, Z.
            ("2026-09-26T11:59:59.6722722Z", NOW),
            # Python datetime.isoformat() with an explicit UTC offset.
            ("2026-09-26T12:00:00.672272+00:00", NOW),
            # A non-UTC offset: the same instant, written in local time.
            ("2026-09-26T14:00:00.672272+02:00", NOW),
            # Second precision, no fraction.
            ("2026-09-26T12:00:00Z", NOW),
        ],
    )
    def test_timestamp_shapes_both_launchers_emit(self, tmp_path: Path, raw: str, reference: datetime) -> None:
        health = _write_health(tmp_path / "alpha_health.json", status="healthy", timestamp_utc=raw)

        assert ds.verify_status_file(health, now=reference, is_running=lambda _p: True).state == ds.OK

    def test_sub_second_future_stamp_is_tolerated_not_called_skew(self, tmp_path: Path) -> None:
        """A reader that runs microseconds after the writer must not see skew."""
        slightly_ahead = (NOW + timedelta(milliseconds=400)).isoformat()
        health = _write_health(tmp_path / "alpha_health.json", status="healthy", timestamp_utc=slightly_ahead)

        verdict = ds.verify_status_file(health, now=NOW, is_running=lambda _p: True)

        assert verdict.state == ds.OK
        assert "clock skew" not in verdict.reason


# ---------------------------------------------------------------------------
# pid file cross-check
# ---------------------------------------------------------------------------


class TestPidAgreement:
    def test_mismatch_between_the_two_writers_is_not_ok(self, tmp_path: Path) -> None:
        _write_health(tmp_path / "alpha_health.json", pid=111)
        (tmp_path / "alpha.pid").write_text("222", encoding="utf-8")

        verdict = ds.pid_file_agrees_with_status(tmp_path)

        assert verdict.state == ds.NOT_OK
        assert "111" in verdict.reason and "222" in verdict.reason
        assert "disagree" in verdict.reason

    def test_agreement_is_ok(self, tmp_path: Path) -> None:
        _write_health(tmp_path / "alpha_health.json", pid=111)
        (tmp_path / "alpha.pid").write_text("111\n", encoding="utf-8")

        assert ds.pid_file_agrees_with_status(tmp_path).state == ds.OK

    def test_pid_file_missing_but_status_present_is_not_ok(self, tmp_path: Path) -> None:
        _write_health(tmp_path / "alpha_health.json", pid=111)

        verdict = ds.pid_file_agrees_with_status(tmp_path)

        assert verdict.state == ds.NOT_OK
        assert "missing or unreadable" in verdict.reason


# ---------------------------------------------------------------------------
# probe_readiness: does the health endpoint actually probe anything?
# ---------------------------------------------------------------------------


class TestProbeReadiness:
    def test_uses_the_readiness_route_never_liveness(self) -> None:
        """A 200 from /health would make this probe decorative."""
        seen: list[str] = []

        def opener(url: str, _timeout: float):
            seen.append(url)
            return _Response(200, _ready_body())

        verdict = ds.probe_readiness("http://127.0.0.1:8001", opener=opener)

        assert verdict.state == ds.OK
        assert seen == ["http://127.0.0.1:8001/health/ready"]
        assert ds.READINESS_PATH == "/health/ready"
        assert ds.READINESS_PATH != ds.LIVENESS_PATH

    def test_ready_200_is_ok_and_names_the_backends(self) -> None:
        verdict = ds.probe_readiness(
            "http://127.0.0.1:8001",
            opener=lambda _u, _t: _Response(200, _ready_body(database="not_configured")),
        )

        assert verdict.state == ds.OK
        assert "database='not_configured'" in verdict.reason

    def test_503_is_not_ok_and_carries_the_real_cause(self) -> None:
        """A Gateway that is up but cannot serve must never be green."""
        verdict = ds.probe_readiness(
            "http://127.0.0.1:8001",
            opener=lambda _u, _t: _Response(503, _degraded_body(database="unreachable")),
        )

        assert verdict.state == ds.NOT_OK
        assert "NOT ready" in verdict.reason
        assert "database='unreachable'" in verdict.reason
        assert "503" in verdict.reason

    def test_200_that_says_degraded_is_still_not_ok(self) -> None:
        """Status code alone is not the verdict; the body is."""
        verdict = ds.probe_readiness(
            "http://127.0.0.1:8001",
            opener=lambda _u, _t: _Response(200, _degraded_body(checkpointer="unreachable")),
        )

        assert verdict.state == ds.NOT_OK
        assert "checkpointer='unreachable'" in verdict.reason

    def test_503_raised_as_httperror_is_not_ok_with_body(self) -> None:
        def opener(_url: str, _timeout: float):
            raise urllib.error.HTTPError(
                "http://127.0.0.1:8001/health/ready", 503, "Service Unavailable", {}, None
            )

        verdict = ds.probe_readiness("http://127.0.0.1:8001", opener=opener)

        assert verdict.state == ds.NOT_OK
        assert "503" in verdict.reason

    def test_connection_refused_is_unknown_never_ok(self) -> None:
        def opener(_url: str, _timeout: float):
            raise urllib.error.URLError(ConnectionRefusedError(10061, "connection refused"))

        verdict = ds.probe_readiness("http://127.0.0.1:8001", opener=opener)

        assert verdict.state == ds.UNKNOWN
        assert not verdict.ok
        assert "could not reach" in verdict.reason

    def test_non_json_body_is_not_ok(self) -> None:
        verdict = ds.probe_readiness(
            "http://127.0.0.1:8001",
            opener=lambda _u, _t: _Response(200, b"<html>proxy error</html>"),
        )

        assert verdict.state == ds.NOT_OK
        assert "non-JSON" in verdict.reason

    def test_trailing_slash_in_base_url_is_normalised(self) -> None:
        seen: list[str] = []
        ds.probe_readiness(
            "http://127.0.0.1:8001/",
            opener=lambda url, _t: (seen.append(url), _Response(200, _ready_body()))[1],
        )

        assert seen == ["http://127.0.0.1:8001/health/ready"]


# ---------------------------------------------------------------------------
# probe_http_ok (the web UI has no readiness route)
# ---------------------------------------------------------------------------


class TestProbeHttpOk:
    @pytest.mark.parametrize("code", [200, 204, 301, 302])
    def test_2xx_and_3xx_are_ok(self, code: int) -> None:
        verdict = ds.probe_http_ok("http://127.0.0.1:3000", opener=lambda _u, _t: _Response(code, b""))
        assert verdict.state == ds.OK

    def test_500_is_not_ok(self) -> None:
        verdict = ds.probe_http_ok(
            "http://127.0.0.1:3000", opener=lambda _u, _t: _Response(500, b"boom")
        )
        assert verdict.state == ds.NOT_OK
        assert "500" in verdict.reason

    def test_refused_is_unknown(self) -> None:
        def opener(_url: str, _timeout: float):
            raise urllib.error.URLError(ConnectionRefusedError("refused"))

        assert ds.probe_http_ok("http://127.0.0.1:3000", opener=opener).state == ds.UNKNOWN


# ---------------------------------------------------------------------------
# pid_is_running
# ---------------------------------------------------------------------------


class TestPidIsRunning:
    def test_own_pid_is_running(self) -> None:
        import os

        assert ds.pid_is_running(os.getpid()) is True

    def test_impossible_pid_is_not_running(self) -> None:
        assert ds.pid_is_running(0) is False
        assert ds.pid_is_running(-1) is False


# ---------------------------------------------------------------------------
# CLI contract: 0 ok / 1 not ok / 2 unknown
# ---------------------------------------------------------------------------


class TestCliExitCodes:
    def test_absent_everything_exits_two_not_zero(self, tmp_path: Path, capsys) -> None:
        """Nothing was checked, so the answer is 'unknown' - and 2, not 0."""
        code = ds.main(
            [
                "--project-root",
                str(tmp_path),
                "--gateway-port",
                "1",  # nothing listens on port 1
                "--frontend-port",
                "1",
                "--timeout",
                "0.5",
            ]
        )

        out = capsys.readouterr().out
        assert code == 2
        assert "UNKNOWN" in out
        assert "[OK     ]" not in out

    def test_corrupt_status_file_exits_one(self, tmp_path: Path, capsys) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "alpha_health.json").write_text("{broken", encoding="utf-8")

        code = ds.main(["--project-root", str(tmp_path), "--gateway-port", "1", "--frontend-port", "1", "--timeout", "0.5"])

        out = capsys.readouterr().out
        assert code == 1
        assert "NOT OK" in out
        assert "corrupt" in out

    def test_healthy_live_stack_exits_zero(self, tmp_path: Path, capsys) -> None:
        """The only path to 0: a live launcher and a serving Gateway."""
        import os

        logs = tmp_path / "logs"
        logs.mkdir()
        # main() audits against the real clock, so this record has to be stamped
        # with the real current time - a fixture timestamp from 2026 would be
        # read as an impossible future heartbeat.
        _write_health(
            logs / "alpha_health.json",
            pid=os.getpid(),
            status="healthy",
            timestamp_utc=datetime.now(UTC).isoformat(),
        )
        (logs / "alpha.pid").write_text(str(os.getpid()), encoding="utf-8")

        # Port 1 is closed, so the HTTP checks are unknown: that must NOT exit 0.
        assert ds.main(["--project-root", str(tmp_path), "--gateway-port", "1", "--frontend-port", "1", "--timeout", "0.5"]) == 2
        capsys.readouterr()  # discard the first report

        # Patch the probes to a serving Gateway and the web UI -> now 0 is earned.
        original = (ds.probe_readiness, ds.probe_http_ok)
        try:
            ds.probe_readiness = lambda *a, **k: ds.Verdict(ds.OK, "stubbed ready")
            ds.probe_http_ok = lambda *a, **k: ds.Verdict(ds.OK, "stubbed frontend")
            code = ds.main(["--project-root", str(tmp_path), "--gateway-port", "8001", "--frontend-port", "3000"])
        finally:
            ds.probe_readiness, ds.probe_http_ok = original

        out = capsys.readouterr().out
        assert code == 0
        # All four audited checks, and nothing degraded or undetermined.
        assert out.count("[OK     ]") == 4
        assert "NOT OK" not in out
        assert "UNKNOWN" not in out
