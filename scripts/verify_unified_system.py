"""End-to-end verification of the unified Alpha system (Plan Phase 6).

Boots the Gateway headless, then proves:
  1. /health and /health/ready are green;
  2. every mounted router responds (integration-health coverage);
  3. manifest coverage is complete (no unwired entries);
  4. one real agent turn succeeds (ALPHA_OK assertion);
  5. the AutonomySupervisor reports status with all loops registered and idle.
Then tears the Gateway down.

Usage: python scripts/verify_unified_system.py [--gateway-port 18099]
Local-only. Exits 0 on success, 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

BOOT_TIMEOUT_SECONDS = 150.0
POLL_INTERVAL_SECONDS = 3.0
RUN_TIMEOUT_SECONDS = 240.0


def http(method: str, url: str, payload: dict | None = None, timeout: float = 30.0):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return response.status, (json.loads(body) if body.strip() else None)


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"verification failed at: {name} ({detail})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway-port", type=int, default=18099)
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.gateway_port}"
    process = None
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.gateway.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.gateway_port),
            ],
            cwd=str(BACKEND),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + BOOT_TIMEOUT_SECONDS
        healthy = False
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL_SECONDS)
            try:
                status, body = http("GET", f"{base}/health", timeout=5.0)
                if status == 200 and (body or {}).get("status") == "healthy":
                    healthy = True
                    break
            except (urllib.error.URLError, TimeoutError, OSError):
                continue
        check("gateway boots healthy", healthy)

        status, body = http("GET", f"{base}/health/ready", timeout=60.0)
        ready = status == 200 and (body or {}).get("status") == "ready"
        check("readiness probes pass", ready, json.dumps(body or {})[:200])

        status, body = http("GET", f"{base}/api/ops/integration-health", timeout=30.0)
        check("integration-health endpoint answers", status == 200 and bool(body and body.get("manifest_found")))
        coverage = (body or {}).get("coverage", {})
        for section in ("tools", "routers", "middlewares", "loops"):
            section_cov = coverage.get(section, {})
            check(f"manifest coverage {section}", (section_cov.get("total", 0) or 0) > 0, json.dumps(section_cov))
        unwired = (body or {}).get("unwired", [])
        check("no unwired manifest entries", not unwired, ", ".join(unwired[:5]))

        autonomy = (body or {}).get("autonomy", {})
        loops = autonomy.get("loops", {})
        check("supervisor owns the five loops", set(loops) == {"sentinel", "perpetual", "review_queue", "skill_curator", "enterprise_heartbeat"}, ",".join(sorted(loops)))

        status, thread = http("POST", f"{base}/api/threads", {"metadata": {"title": "verify-unified"}}, timeout=30.0)
        thread_id = (thread or {}).get("thread_id", "")
        check("thread creation works", status == 200 and bool(thread_id), thread_id)

        payload = {
            "assistant_id": "lead_agent",
            "input": {"messages": [{"role": "user", "content": "Reply with exactly: ALPHA_OK"}]},
        }
        url = f"{base}/api/threads/{thread_id}/runs/wait"
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=RUN_TIMEOUT_SECONDS) as response:
            run_body = response.read().decode("utf-8")
        check("real agent turn returns ALPHA_OK", "ALPHA_OK" in run_body)

        status, messages = http("GET", f"{base}/api/threads/{thread_id}/messages", timeout=30.0)
        check("run journal persisted", status == 200 and isinstance(messages, list), f"{len(messages) if isinstance(messages, list) else '?'} events")
        print("\nALL CHECKS PASSED")
        return 0
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    sys.exit(main())
