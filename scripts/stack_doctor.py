"""Stack diagnostician that distinguishes *why* something is unavailable.

Written after a real incident in which a health check reported "DOWN" for a
stack that was, in fact, healthy: the gateway answered 200, all three ports were
bound, and the frontend was mid cold-compile. A ten-second probe timed out and
the result was wrong in the direction that matters, because "down" sends you
looking for a crash that never happened.

A boolean probe cannot tell these apart:

    DEAD                 the process is gone
    STARTING             alive, and compiling its first response
    LISTENING_NO_HTTP    the port is bound but nothing answers
    SERVING_ERROR        answers, with a 4xx/5xx and a body
    UNREACHABLE          nothing is bound at all
    HEALTHY              answers as expected

Only the first and last are "down". The middle three have different fixes, and
conflating them is what makes a stack hard to debug.

Every failure prints the FULL error: status line, all response headers, and the
entire body, untruncated. A diagnostic that summarises the thing you are
debugging is worse than one that prints nothing.

Usage:
    python scripts/stack_doctor.py
    python scripts/stack_doctor.py --json
    python scripts/stack_doctor.py --wait 900      # tolerate a cold compile
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

# A cold `next dev` first response is measured at ~880s in this repository's own
# guide. A short timeout turns that into a false "DOWN".
COLD_COMPILE_FLOOR = 900.0
QUICK_TIMEOUT = 8.0

SERVICES: list[dict[str, Any]] = [
    {
        "name": "gateway",
        "port": 8001,
        "url": "http://127.0.0.1:8001/health/ready",
        "expect": 200,
        "process": "python",
        "kind": "api",
    },
    {
        "name": "frontend",
        "port": 3000,
        "url": "http://127.0.0.1:3000",
        "expect": 200,
        "process": "node",
        "kind": "web",
        "slow_ok": True,  # may be compiling
    },
    {
        "name": "nginx",
        "port": 2026,
        "url": "http://127.0.0.1:2026",
        "expect": 200,
        "process": "nginx",
        "kind": "proxy",
    },
]

# States that are NOT "down".
STARTING = "STARTING"
LISTENING_NO_HTTP = "LISTENING_NO_HTTP"
SERVING_ERROR = "SERVING_ERROR"
UNREACHABLE = "UNREACHABLE"
DEAD = "DEAD"
HEALTHY = "HEALTHY"
UNKNOWN = "UNKNOWN"


@dataclass
class Finding:
    name: str
    port: int
    state: str
    http_status: int | None = None
    elapsed_s: float | None = None
    process_alive: bool | None = None
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    reason: str = ""
    fix: str = ""

    @property
    def is_down(self) -> bool:
        return self.state in (DEAD, UNREACHABLE)


def port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        return s.connect_ex(("127.0.0.1", port)) == 0


def process_alive(pattern: str) -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {pattern}"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        return pattern.lower() in out.stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False


def http_probe(url: str, timeout: float) -> tuple[int | None, str, dict[str, str], float, str]:
    """Return (status, body, headers, elapsed, transport_error)."""
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - loopback diagnostics
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body, dict(resp.headers), time.monotonic() - started, ""
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body, dict(exc.headers or {}), time.monotonic() - started, ""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return None, "", {}, time.monotonic() - started, f"{type(exc).__name__}: {reason}"


def diagnose(svc: dict[str, Any], timeout: float) -> Finding:
    name, port, url, expect = svc["name"], svc["port"], svc["url"], svc["expect"]
    listening = port_listening(port)
    alive = process_alive(svc["process"])
    status, body, headers, elapsed, transport_error = http_probe(url, timeout)

    if status == expect:
        state, reason = HEALTHY, f"{status} in {elapsed:.2f}s"
        fix = ""
    elif status is not None:
        state = SERVING_ERROR
        reason = f"HTTP {status} in {elapsed:.2f}s"
        fix = (
            "The process is up and answering with an error. Read the body below - "
            "this is an application fault, not a startup fault."
        )
    elif transport_error and "timed out" in transport_error.lower():
        if not listening:
            state = UNREACHABLE
            reason = f"nothing is listening on {port}; {transport_error}"
            fix = f"Start it: make dev-daemon (expect ~5 min to be ready)."
        else:
            state = STARTING
            reason = (
                f"port {port} is bound but no response in {timeout:.0f}s "
                f"({transport_error}); process alive={alive}"
            )
            fix = (
                "Listening but not answering is a cold compile, not a crash. "
                "A `next dev` first response is measured at ~880s in this repo. "
                "Wait, or run `next build` once so the launcher serves the build instead."
            )
    elif not listening:
        state = UNREACHABLE
        reason = f"nothing is listening on {port}"
        fix = f"Start it: make dev-daemon"
    else:
        state = LISTENING_NO_HTTP
        reason = f"port {port} bound, transport failed: {transport_error}"
        fix = "Bound but not answering. Check the service log for a bind or crash loop."

    return Finding(
        name=name,
        port=port,
        state=state,
        http_status=status,
        elapsed_s=round(elapsed, 2),
        process_alive=alive,
        body=body,
        headers=headers,
        reason=reason,
        fix=fix,
    )


def probe_build_presence() -> str:
    """A missing BUILD_ID is why every boot silently pays a cold dev compile."""
    marker = "frontend/.next/BUILD_ID"
    if not (marker and __import__("pathlib").Path(marker).exists()):
        return (
            f"MISSING  {marker}\n"
            "         Every boot therefore falls back to `next dev` and pays a cold compile\n"
            "         measured at ~880s, during which the frontend is bound but silent.\n"
            "         Fix: cd frontend && node node_modules/next/dist/bin/next build"
        )
    return f"present  {marker}  (the launcher will serve the build, not cold-compile)"


def render(findings: list[Finding], as_json: bool) -> int:
    if as_json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
        return 1 if any(f.is_down for f in findings) else 0

    width = 74
    print("=" * width)
    print("ALPHA STACK DIAGNOSIS".center(width))
    print("=" * width)

    for f in findings:
        mark = "ok " if f.state == HEALTHY else "!! "
        print(f"\n{mark}{f.name.upper()}  (port {f.port})  ->  {f.state}")
        print(f"   {f.reason}")
        if f.fix:
            for line in f.fix.splitlines():
                print(f"   -> {line.strip()}" if line.strip() else "")
        if f.state not in (HEALTHY,):
            if f.http_status is not None:
                print(f"   HTTP status : {f.http_status}")
            if f.headers:
                print("   HEADERS (full):")
                for k, v in f.headers.items():
                    print(f"     {k}: {v}")
            if f.body:
                print("   BODY (full, untruncated):")
                for line in f.body.splitlines():
                    print(f"     {line}")
            else:
                print("   BODY: (empty)")

    print(f"\n{'-' * width}\nBUILD STATE\n{'-' * width}")
    for line in probe_build_presence().splitlines():
        print(f"  {line}")

    down = [f.name for f in findings if f.is_down]
    degraded = [f.name for f in findings if f.state not in (HEALTHY,) and not f.is_down]

    print(f"\n{'-' * width}")
    if not down and not degraded:
        print("VERDICT: healthy. Every service answered as expected.")
        return 0
    if down:
        print(f"VERDICT: DOWN -> {', '.join(down)}")
        print("         These are genuinely unavailable. Start them: make dev-daemon")
    if degraded:
        print(f"VERDICT: DEGRADED (not down) -> {', '.join(degraded)}")
        print("         These are running but not answering normally. Read each reason above.")
        print("         Do NOT restart the stack on this evidence - a cold compile looks")
        print("         exactly like a crash if you only check whether it answers fast.")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnose the Alpha stack without lying about why.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--wait", type=float, default=0.0, help="seconds to tolerate a cold compile")
    ap.add_argument("--timeout", type=float, default=QUICK_TIMEOUT, help="per-probe timeout")
    args = ap.parse_args()

    deadline = time.monotonic() + max(0.0, args.wait)
    while True:
        findings = [diagnose(s, args.timeout) for s in SERVICES]
        if not any(f.state == STARTING for f in findings) or time.monotonic() >= deadline:
            break
        print(f"[stack_doctor] a service is still starting; waiting (deadline {args.wait:.0f}s)...", file=sys.stderr)
        time.sleep(15.0)

    return render(findings, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
