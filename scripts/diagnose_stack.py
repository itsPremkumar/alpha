"""Alpha stack diagnostician — one command that says exactly where startup is stuck.

The tray shows "starting ... waiting for services" without ever saying *which*
service, *how long* it has waited, or *why*. This probes every layer of the
local stack in dependency order, times each probe, and prints the real reason a
layer is not ready — so a slow boot can be told apart from a broken one instead
of guessed at.

Read-only: it issues GETs and inspects local process/port state. It never
mutates, starts, or kills anything.

Usage:
    python scripts/diagnose_stack.py            # table + verdict
    python scripts/diagnose_stack.py --json     # machine readable
    python scripts/diagnose_stack.py --wait 300 # block until ready or 300s
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGS = REPO_ROOT / "logs"

# The Windows console defaults to cp1252 and cannot encode characters that
# appear routinely in these logs (Next.js prints U+2713 CHECK MARK, box drawing,
# emoji). Without this the diagnostician dies with UnicodeEncodeError at the
# exact moment it has something useful to show, so force UTF-8 and never raise
# on unencodable output.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 - older/odd streams stay as they are
        pass


def console_safe(text: str) -> str:
    """Make arbitrary log text printable on a cp1252 console."""
    return text.encode("utf-8", "replace").decode("utf-8", "replace").replace("\u0000", "")

GATEWAY = os.environ.get("ALPHA_GATEWAY_BASE", "http://127.0.0.1:8001")
FRONTEND = os.environ.get("ALPHA_FRONTEND_BASE", "http://127.0.0.1:3000")
PROBE_TIMEOUT = float(os.environ.get("ALPHA_DIAG_TIMEOUT", "20"))


# --------------------------------------------------------------------------- #
# probes
# --------------------------------------------------------------------------- #
def probe(url: str, timeout: float = PROBE_TIMEOUT) -> dict:
    """GET a URL and classify the outcome. Never raises."""
    started = time.monotonic()
    req = urllib.request.Request(url, headers={"Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(600).decode("utf-8", "replace")
            return {
                "url": url,
                "status": r.status,
                "ms": round((time.monotonic() - started) * 1000),
                "ok": r.status == 200,
                "detail": body.strip()[:300],
            }
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(300).decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001
            pass
        return {
            "url": url,
            "status": e.code,
            "ms": round((time.monotonic() - started) * 1000),
            "ok": False,
            # 503 from /health/ready means "still booting", which is the single
            # most useful distinction when diagnosing a slow start.
            "detail": f"HTTP {e.code} {body}"[:300]
            + ("  (still initialising)" if e.code == 503 else ""),
        }
    except socket.timeout:
        return {"url": url, "status": 0, "ms": round((time.monotonic() - started) * 1000),
                "ok": False, "detail": f"TIMEOUT after {timeout:.0f}s — port open but not answering"}
    except Exception as e:  # noqa: BLE001
        return {"url": url, "status": 0, "ms": round((time.monotonic() - started) * 1000),
                "ok": False, "detail": f"{type(e).__name__}: {e}"}


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(3)
        return s.connect_ex((host, port)) == 0


def port_owner(port: int) -> str:
    """Best-effort owning PID without psutil."""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=20, check=False
        ).stdout
    except Exception:  # noqa: BLE001
        return "?"
    for line in out.splitlines():
        if f":{port}" in line and "LISTENING" in line.upper():
            parts = line.split()
            if parts:
                return f"PID {parts[-1]}"
    return "?"


def read_json(name: str) -> dict | None:
    path = LOGS / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return None


def tail(name: str, lines: int = 12) -> list[str]:
    path = LOGS / name
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------- #
# checks, in dependency order
# --------------------------------------------------------------------------- #
def collect() -> dict:
    checks: list[dict] = []

    gw_port = 8001
    fe_port = 3000

    checks.append({
        "layer": "gateway port",
        "target": f"tcp/{gw_port}",
        **_port_row(gw_port),
    })
    checks.append({
        "layer": "gateway liveness",
        "target": f"{GATEWAY}/health",
        **_fold(probe(f"{GATEWAY}/health", timeout=10)),
    })
    checks.append({
        "layer": "gateway readiness",
        "target": f"{GATEWAY}/health/ready",
        **_fold(probe(f"{GATEWAY}/health/ready")),
    })
    checks.append({
        "layer": "gateway data",
        "target": f"{GATEWAY}/api/bots",
        **_fold(probe(f"{GATEWAY}/api/bots")),
    })
    checks.append({
        "layer": "frontend port",
        "target": f"tcp/{fe_port}",
        **_port_row(fe_port),
    })
    checks.append({
        "layer": "frontend page",
        "target": f"{FRONTEND}/",
        **_fold(probe(f"{FRONTEND}/", timeout=60)),
    })
    checks.append({
        "layer": "frontend -> gateway proxy",
        "target": f"{FRONTEND}/api/bots",
        **_fold(probe(f"{FRONTEND}/api/bots", timeout=60)),
    })

    health = read_json("alpha_health.json")
    heartbeat = read_json("watchdog_heartbeat.json")
    return {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "checks": checks,
        "launcher_health": health,
        "watchdog_heartbeat": heartbeat,
        "gateway_log_tail": tail("gateway.err.log", 15),
        "frontend_log_tail": tail("frontend.log", 15),
        "frontend_err_tail": tail("frontend.err.log", 15),
    }


def _port_row(port: int) -> dict:
    open_ = port_open(port)
    return {
        "status": "LISTEN" if open_ else "CLOSED",
        "ms": 0,
        "ok": open_,
        "detail": f"{port_owner(port)}" if open_ else "nothing is listening on this port",
    }


def _fold(p: dict) -> dict:
    return {"status": p["status"] or "-", "ms": p["ms"], "ok": p["ok"], "detail": p["detail"]}


def verdict(report: dict) -> str:
    by = {c["layer"]: c for c in report["checks"]}
    if not by["gateway port"]["ok"]:
        return "GATEWAY NOT LISTENING — start.ps1 has not bound :8001 yet (or it is being restarted)."
    if not by["gateway readiness"]["ok"]:
        d = by["gateway readiness"]["detail"]
        if "503" in d:
            return ("GATEWAY STILL BOOTING — the port is bound but the app has not finished "
                    "startup. This is the normal slow-boot state under load; it resolves on its "
                    "own PROVIDED nothing restarts the process (see the F6 watchdog-threshold bug).")
        if "TIMEOUT" in d:
            return ("GATEWAY BOUND BUT NOT ANSWERING — startup is wedged past the probe timeout. "
                    "Check gateway.err.log for the last phase reached.")
        return f"GATEWAY NOT READY — {d}"
    if not by["frontend port"]["ok"]:
        return "GATEWAY READY, FRONTEND NOT LISTENING — the UI process is still starting or restarting."
    if not by["frontend page"]["ok"]:
        d = by["frontend page"]["detail"]
        if "TIMEOUT" in d:
            return ("FRONTEND STILL COMPILING — Next.js dev cold-compile has been measured at "
                    "220 s+ on this machine. Not a crash; it needs minutes, not seconds.")
        return f"FRONTEND NOT SERVING — {d}"
    if not by["frontend -> gateway proxy"]["ok"]:
        return ("FRONTEND SERVES THE PAGE BUT THE /api PROXY FAILS — the page loads with no data. "
                "This is a distinct failure from a dead backend.")
    return "STACK HEALTHY — gateway ready, frontend serving, and the frontend proxy reaches the gateway."


def render(report: dict) -> str:
    out: list[str] = []
    out.append("=" * 100)
    out.append(f"ALPHA STACK DIAGNOSTIC  {report['checked_at']}")
    out.append("=" * 100)
    out.append(f"{'LAYER':28} {'STATUS':8} {'ms':>7}  DETAIL")
    out.append("-" * 100)
    for c in report["checks"]:
        detail = console_safe(c["detail"]).replace("\n", " ")[:52]
        out.append(f"{c['layer']:28} {str(c['status']):8} {c['ms']:>7}  {detail}")
    out.append("-" * 100)
    lh = report.get("launcher_health") or {}
    if lh:
        out.append(f"launcher : status={lh.get('status')} phase={lh.get('phase')} "
                   f"attempt={lh.get('attempt')}/{lh.get('max_attempts')} "
                   f"gateway_ready={lh.get('gateway_ready')} frontend_ready={lh.get('frontend_ready')}")
        if lh.get("detail"):
            out.append(f"          {console_safe(str(lh['detail']))}")
        if lh.get("gateway_last_error"):
            out.append(f"          gateway_last_error: {console_safe(str(lh['gateway_last_error']))}")
    hb = report.get("watchdog_heartbeat") or {}
    if hb:
        out.append(f"watchdog : {console_safe(str(hb.get('stack')))} status={hb.get('status')}")
    for label, key in (("gateway.err.log", "gateway_log_tail"),
                       ("frontend.log", "frontend_log_tail"),
                       ("frontend.err.log", "frontend_err_tail")):
        rows = report.get(key) or []
        if rows:
            out.append(f"--- last lines of {label} " + "-" * max(0, 60 - len(label)))
            for line in rows[-6:]:
                out.append(f"    {console_safe(line)[:150]}")
    out.append("=" * 100)
    out.append(f"VERDICT: {console_safe(verdict(report))}")
    out.append("=" * 100)
    return "\n".join(out)


def ready(report: dict) -> bool:
    by = {c["layer"]: c for c in report["checks"]}
    return all(by[k]["ok"] for k in ("gateway readiness", "frontend page", "frontend -> gateway proxy"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnose the local Alpha stack.")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--wait", type=float, default=0.0, metavar="SECONDS",
                    help="re-probe until healthy or the deadline passes")
    args = ap.parse_args()

    deadline = time.monotonic() + args.wait
    report = collect()
    while args.wait and not ready(report) and time.monotonic() < deadline:
        remaining = int(deadline - time.monotonic())
        print(f"[{remaining:4d}s left] not ready: {verdict(report)[:90]}", file=sys.stderr, flush=True)
        time.sleep(10)
        report = collect()

    print(json.dumps(report, indent=1) if args.json else render(report))
    return 0 if ready(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
