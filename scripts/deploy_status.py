#!/usr/bin/env python3
"""Read the launcher's status file and the gateway's readiness verdict honestly.

Why this module exists
----------------------
``logs/alpha_health.json`` is written by ``start.ps1`` and consumed by every
subsequent diagnostic. Two failure modes make it a *lie* rather than a signal:

1. **The file outlives its process.** A launcher that is ``taskkill /F``'d, that
   dies of an unhandled error, or that aborts on a missing dependency never runs
   its cleanup, so the file keeps claiming ``status: "starting"`` (or
   ``"healthy"``) while nothing is running. Every later reader that trusts the
   status is now wrong, and the wrongness compounds: each diagnostic that
   believes it inherits the lie.
2. **Nobody checks whether the process can actually serve.** A green
   ``/health`` (pure liveness: "the process is up") is routinely read as
   "Alpha works", so a checkout that cannot complete a run reports Ready.

This module is the single place that turns those two raw artifacts into a
**tri-state** verdict - :data:`OK` / :data:`NOT_OK` / :data:`UNKNOWN` - so no
caller has to re-implement (and get wrong) the staleness rules:

* :func:`verify_status_file` - is the status file currently telling the truth?
  A dead launcher or a frozen heartbeat is :data:`NOT_OK` (never :data:`OK`).
* :func:`probe_readiness` - can the gateway actually serve? Uses
  ``/health/ready`` (which probes the persistence backends), never ``/health``.
* :func:`probe_http_ok` - generic liveness probe for the web UI.

The verdict vocabulary is deliberately three-valued. Absence of evidence is
:data:`UNKNOWN`, which is *not* success: collapsing it into ``ok`` is exactly
how "Status: Ready" came to mean "nothing was checked".

Dependencies: standard library only, so this runs from a bare checkout before
any virtualenv exists (that is when a broken status file is most likely).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# --- verdict vocabulary (the tri-state) ---------------------------------------
#: The thing was checked and is fine.
OK = "ok"
#: The thing was checked and is broken. Never render this as healthy.
NOT_OK = "not_ok"
#: The thing could not be determined. Absence of evidence, never success.
UNKNOWN = "unknown"

#: File names the launchers write, relative to ``<repo>/logs``.
HEALTH_FILE_NAME = "alpha_health.json"
PID_FILE_NAME = "alpha.pid"

#: The Gateway's readiness route. ``/health`` is liveness only (it cannot fail);
#: anything that claims "Alpha can serve" must use this one.
READINESS_PATH = "/health/ready"
LIVENESS_PATH = "/health"
DEFAULT_GATEWAY_PORT = 8001
DEFAULT_FRONTEND_PORT = 3000
DEFAULT_HOST = "127.0.0.1"

#: Statuses ``start.ps1``/``start.sh`` may write. Anything else means the file
#: was produced by something we do not understand, which is itself a defect.
LAUNCHER_STATUSES = frozenset(
    {"starting", "healthy", "degraded", "recovering", "failed", "stopped", "stale"}
)
#: Statuses that mean "this launcher is not (or no longer) serving".
TERMINAL_LAUNCHER_STATUSES = frozenset({"failed", "stopped", "stale"})

#: A launcher heartbeat older than this is a frozen launcher, not a live one.
#: Matches the 240 s window ``start.ps1`` already uses for its own
#: already-running check, so the writer and this reader agree on one number.
LAUNCHER_MAX_HEARTBEAT_AGE_SECONDS = 240.0

#: Tolerance for a heartbeat stamped slightly in the future. A reader that runs
#: a few hundred microseconds after the writer, on a host with coarse timer
#: granularity, otherwise sees a negative age and would call a perfectly healthy
#: launcher "clock skew". Only a skew beyond this is treated as suspect.
CLOCK_SKEW_TOLERANCE_SECONDS = 5.0

#: Client timeout for the probes. Above the Gateway's own 3 s
#: ``_READINESS_DEADLINE_SECONDS`` so a slow-but-answered probe is measured as
#: answered rather than as unreachable.
DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class Verdict:
    """A tri-state answer plus the reason it is that answer.

    ``reason`` always names the *actual* observed cause (the port, the PID, the
    field the Gateway reported). An operator must never have to read source to
    find out which dependency failed.
    """

    state: str
    reason: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == OK

    @property
    def not_ok(self) -> bool:
        return self.state == NOT_OK

    @property
    def unknown(self) -> bool:
        return self.state == UNKNOWN


def _bad(reason: str, detail: str = "") -> Verdict:
    return Verdict(NOT_OK, reason, detail)


def _unknown(reason: str, detail: str = "") -> Verdict:
    return Verdict(UNKNOWN, reason, detail)


def _good(reason: str, detail: str = "") -> Verdict:
    return Verdict(OK, reason, detail)


def default_logs_dir(project_root: Path) -> Path:
    """Return the launcher log/status directory for *project_root*."""
    return Path(project_root) / "logs"


# ---------------------------------------------------------------------------
# Status file
# ---------------------------------------------------------------------------


def read_json(path: Path) -> tuple[dict[str, Any] | None, str]:
    """Read *path* as a JSON object.

    Returns ``(record, reason)``. ``record`` is None when the file is absent
    (``"absent"``) or unparseable/not-an-object (``"unreadable"``) - the two are
    reported apart because a corrupt monitor is a defect while an absent one
    usually just means Alpha is not running.
    """
    path = Path(path)
    if not path.is_file():
        return None, "absent"
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, f"unreadable ({type(exc).__name__}: {exc})"
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return None, f"unreadable (invalid JSON: {exc})"
    if not isinstance(data, dict):
        return None, f"unreadable (top-level {type(data).__name__}, expected object)"
    return data, "ok"


def parse_timestamp_utc(value: Any) -> datetime | None:
    """Parse a launcher timestamp into an aware UTC datetime, or None.

    Accepts the round-trip ISO-8601 ``datetime.utcnow().isoformat()``/
    ``ToString("o")`` shapes both launchers emit, including the 7-digit
    fractional seconds PowerShell produces and the ``Z`` suffix Python emits.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def pid_is_running(pid: int) -> bool:
    """Return True when a process with *pid* currently exists.

    Deliberately conservative: any error that is not "no such process" reports
    the PID as running, so a permissions or sandbox error degrades to
    "cannot prove the launcher is gone" rather than to a false accusation.
    """
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - exercised on Windows only
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return int(code.value) == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def read_pid_file(path: Path) -> int:
    """Return the PID recorded in *path*, or 0 when absent/unparseable."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig").strip()
    except OSError:
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def verify_status_file(
    health_path: Path,
    *,
    now: datetime | None = None,
    is_running: Callable[[int], bool] = pid_is_running,
    max_age_seconds: float = LAUNCHER_MAX_HEARTBEAT_AGE_SECONDS,
) -> Verdict:
    """Decide whether the launcher status file is currently telling the truth.

    ``NOT_OK`` cases (a monitoring lie - never rendered as healthy):

    * the file exists but is corrupt / not a JSON object;
    * it claims a status no launcher can write;
    * it says ``failed``/``stopped``/``stale`` (a recorded failure, surfaced
      verbatim so the operator reads the reason);
    * its ``pid`` is not running - **the file outlived its process**;
    * its ``timestamp_utc`` is missing or older than *max_age_seconds* - a
      frozen launcher, not a live one.

    ``UNKNOWN`` case: the file is absent. Alpha is simply not running, which
    says nothing about whether this checkout can serve.
    """
    record, why = read_json(Path(health_path))
    if record is None:
        if why == "absent":
            return _unknown("no launcher status file (Alpha is not running)")
        return _bad(f"launcher status file is corrupt: {why}")

    status = record.get("status")
    if not isinstance(status, str) or not status.strip():
        return _bad("launcher status file has no 'status' field", f"keys={sorted(record)}")
    status = status.strip()
    if status not in LAUNCHER_STATUSES:
        return _bad(f"launcher status file carries an unrecognised status {status!r}")
    detail = str(record.get("detail") or "")

    if status in TERMINAL_LAUNCHER_STATUSES:
        return _bad(f"launcher recorded status={status!r}", detail)

    raw_pid = record.get("pid")
    try:
        pid = int(raw_pid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _bad("launcher status file has no usable 'pid' field", f"pid={raw_pid!r}")
    if pid <= 0:
        return _bad(f"launcher status file has a non-positive pid ({pid})")
    if not is_running(pid):
        return _bad(
            f"STALE status file: it claims status={status!r} but launcher PID {pid} is not running",
            detail,
        )

    stamp = parse_timestamp_utc(record.get("timestamp_utc"))
    if stamp is None:
        return _bad(
            f"STALE status file: launcher PID {pid} is running but 'timestamp_utc' is missing or unparseable",
            f"timestamp_utc={record.get('timestamp_utc')!r}",
        )
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    age = (reference - stamp).total_seconds()
    if age < -CLOCK_SKEW_TOLERANCE_SECONDS:
        # A clock that is genuinely wrong is not evidence of liveness either
        # way; say so instead of silently treating a future stamp as fresh.
        return _unknown(
            f"launcher PID {pid} heartbeat is {-age:.0f}s in the future (clock skew?)",
            detail,
        )
    age = max(age, 0.0)
    if age > max_age_seconds:
        return _bad(
            f"STALE status file: launcher PID {pid} heartbeat is {age:.0f}s old (limit {max_age_seconds:.0f}s), "
            f"last status={status!r}",
            detail,
        )
    return _good(f"launcher PID {pid} is live and reported status={status!r} {age:.0f}s ago", detail)


def audit_status_file(
    logs_dir: Path,
    **kwargs: Any,
) -> Verdict:
    """``verify_status_file`` against the default path inside *logs_dir*."""
    return verify_status_file(Path(logs_dir) / HEALTH_FILE_NAME, **kwargs)


def pid_file_agrees_with_status(logs_dir: Path) -> Verdict:
    """Cross-check ``alpha.pid`` against ``alpha.pid`` inside the status file.

    A mismatch means two writers disagree about who owns the stack, which is the
    precondition for two launchers fighting over the same ports.
    """
    logs = Path(logs_dir)
    pid_path = logs / PID_FILE_NAME
    status_pid = read_pid_file(pid_path)
    record, why = read_json(logs / HEALTH_FILE_NAME)
    if record is None:
        if why == "absent":
            return _unknown("no launcher status file to cross-check against")
        return _bad(f"launcher status file is corrupt: {why}")
    try:
        recorded = int(record.get("pid"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _unknown("launcher status file records no usable pid to cross-check")
    if status_pid == 0:
        return _bad(
            f"pid file {pid_path.name} is missing or unreadable while the status file claims PID {recorded}",
            str(record.get("detail") or ""),
        )
    if status_pid != recorded:
        return _bad(
            f"pid file says {status_pid} but the status file says {recorded} - two writers disagree on the launcher",
            str(record.get("detail") or ""),
        )
    return _good(f"pid file and status file agree on launcher PID {recorded}")


# ---------------------------------------------------------------------------
# HTTP probes
# ---------------------------------------------------------------------------


def _open(url: str, timeout: float):  # pragma: no cover - thin urllib wrapper
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "alpha-deploy-status/1"})
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - fixed loopback/operator URL


def _decode_body(raw: bytes) -> tuple[dict[str, Any] | None, str]:
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError as exc:
        return None, f"non-JSON body ({exc})"
    if not isinstance(data, dict):
        return None, f"unexpected body type {type(data).__name__}"
    return data, "ok"


def _describe_payload(payload: dict[str, Any]) -> str:
    interesting = ("status", "database", "checkpointer", "failing", "service")
    parts = [f"{key}={payload[key]!r}" for key in interesting if key in payload]
    return " ".join(parts) if parts else f"keys={sorted(payload)}"


def probe_readiness(
    base_url: str,
    *,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    opener: Callable[..., Any] | None = None,
) -> Verdict:
    """Ask the Gateway whether it can actually serve, via ``/health/ready``.

    ``/health/ready`` is the route that probes the persistence backends, so a
    200 from it is evidence the product can serve. ``/health`` is liveness only
    and is never used here: a 200 from a liveness route would make this probe
    decorative.

    * ``OK``       - 200 and the body says ``ready``.
    * ``NOT_OK``   - the Gateway answered but is not ready, or answered
      something that is not a readiness verdict. The body's own
      ``database``/``checkpointer`` fields are carried into the reason so the
      operator sees the real cause instead of "not ready".
    * ``UNKNOWN``  - nothing answered (connection refused, DNS, timeout). We
      genuinely do not know, and unknown is not success.
    """
    url = f"{base_url.rstrip('/')}{READINESS_PATH}"
    open_url = opener or _open
    try:
        response = open_url(url, timeout)
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001 - the status line is what matters
            body = b""
        payload, why = _decode_body(body)
        described = _describe_payload(payload) if payload else why
        return _bad(
            f"Gateway readiness probe returned HTTP {exc.code} from {url}: {described}",
            f"url={url}",
        )
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return _unknown(f"Gateway readiness probe could not reach {url}: {reason}")

    try:
        status_code = int(getattr(response, "status", None) or getattr(response, "code", 0))
        body = response.read()
    except Exception as exc:  # noqa: BLE001 - a broken socket is a broken probe
        return _unknown(f"Gateway readiness probe failed while reading {url}: {type(exc).__name__}: {exc}")
    finally:
        closer = getattr(response, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass

    payload, why = _decode_body(body)
    if payload is None:
        return _bad(f"Gateway readiness probe at {url} returned {status_code} with {why}")

    described = _describe_payload(payload)
    reported = str(payload.get("status") or "")
    if status_code == 200 and reported == "ready":
        return _good(f"Gateway reports ready at {url}: {described}", described)
    failing = payload.get("failing")
    suffix = f" failing={failing!r}" if failing else ""
    return _bad(
        f"Gateway is NOT ready: HTTP {status_code} from {url}: {described}{suffix}",
        described,
    )


def probe_http_ok(
    base_url: str,
    path: str = "/",
    *,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    opener: Callable[..., Any] | None = None,
) -> Verdict:
    """Generic liveness probe (used for the web UI, which has no readiness route)."""
    url = f"{base_url.rstrip('/')}{path}"
    open_url = opener or _open
    try:
        response = open_url(url, timeout)
    except urllib.error.HTTPError as exc:
        return _bad(f"{url} returned HTTP {exc.code}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return _unknown(f"{url} is not answering: {getattr(exc, 'reason', exc)}")
    try:
        status_code = int(getattr(response, "status", None) or getattr(response, "code", 0))
        response.read()
    except Exception as exc:  # noqa: BLE001
        return _unknown(f"{url} failed while reading the response: {type(exc).__name__}: {exc}")
    finally:
        closer = getattr(response, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass
    if 200 <= status_code < 400:
        return _good(f"{url} returned HTTP {status_code}")
    return _bad(f"{url} returned HTTP {status_code}")


def gateway_base_url(port: int = DEFAULT_GATEWAY_PORT, host: str = DEFAULT_HOST) -> str:
    """Loopback base URL for the Gateway (the only default we ever probe)."""
    return f"http://{host}:{port}"


def frontend_base_url(port: int = DEFAULT_FRONTEND_PORT, host: str = DEFAULT_HOST) -> str:
    """Loopback base URL for the web UI."""
    return f"http://{host}:{port}"


def main(argv: list[str] | None = None) -> int:
    """CLI: print the tri-state audit of the local deployment. 0/1/2 = ok/not ok/unknown."""
    import argparse

    parser = argparse.ArgumentParser(description="Audit the Alpha launcher status file and gateway readiness.")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--gateway-port", type=int, default=DEFAULT_GATEWAY_PORT)
    parser.add_argument("--frontend-port", type=int, default=DEFAULT_FRONTEND_PORT)
    parser.add_argument("--timeout", type=float, default=DEFAULT_PROBE_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    logs_dir = default_logs_dir(args.project_root)
    checks: list[tuple[str, Verdict]] = [
        ("launcher status file", audit_status_file(logs_dir)),
        ("pid file agreement", pid_file_agrees_with_status(logs_dir)),
        ("gateway readiness", probe_readiness(gateway_base_url(args.gateway_port), timeout=args.timeout)),
        ("frontend http", probe_http_ok(frontend_base_url(args.frontend_port), timeout=args.timeout)),
    ]

    worst = OK
    for label, verdict in checks:
        marker = {"ok": "OK     ", "not_ok": "NOT OK ", "unknown": "UNKNOWN"}[verdict.state]
        print(f"[{marker}] {label}: {verdict.reason}")
        if verdict.state == NOT_OK:
            worst = NOT_OK
        elif verdict.state == UNKNOWN and worst == OK:
            worst = UNKNOWN
    return {"ok": 0, "not_ok": 1, "unknown": 2}[worst]


if __name__ == "__main__":
    sys.exit(main())
