"""Live sweep: drive every parameterless GET route on the real app.

The suite proves routes are *mounted*. It never proved a route's *body* runs.
A handler whose statement is mis-parsed, whose `await` has the wrong precedence,
or which dereferences a missing attribute stays green until a human calls it.

This boots the real Gateway through the real lifespan and issues one real GET to
every route that needs no path/query parameters. 5xx and unhandled exceptions are
failures; 2xx/3xx/4xx are answers.

Usage (from ``backend/``)::

    python scripts/route_sweep.py
    python scripts/route_sweep.py --json out.json
    python scripts/route_sweep.py --prefix /api/intelligence
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from typing import Any

import httpx

HOST = "127.0.0.1"
PORT = int(__import__("os").environ.get("ALPHA_SWEEP_PORT", "8099"))
BASE = f"http://{HOST}:{PORT}"

#: A route path with no ``{}`` segments can be requested verbatim. Anything with a
#: placeholder is skipped rather than guessed: a fabricated id would exercise a
#: 404 branch, not the handler body, and a green 404 is exactly the blindness
#: this sweep exists to remove.
_PLACEHOLDER = re.compile(r"\{[^}]+\}")

#: Paths that would create state, block on a network dependency, or take an
#: unbounded amount of work. Every entry is justified.
_SKIP_EXACT: set[str] = {
    # Readiness waits on a live database/checkpointer probe under its own deadline.
    "/health/ready",
    # Long-lived event/poll surfaces. A stream that stays open forever is not a
    # failure to report -- it is not a route that returns. `/api/peer-network/events`
    # was reached by the first sweep and held the connection open until the whole
    # run was killed, which is why the SSE family is excluded by name rather than
    # discovered.
    "/api/peer-network/events",
    "/api/ops/event-loop",
    "/api/ops/network",
    "/api/ops/runtime",
    "/api/system-monitor/snapshot",
}

_SKIP_PREFIXES: tuple[str, ...] = (
    "/api/threads/",  # needs a real thread id; the thread family is covered elsewhere
    "/api/runs/",
    "/api/langgraph/",
    "/extension-ws",
    # `/stream`, `/events`, `/wait` are unbounded-by-design on their thread family.
    "/api/runs/stream",
)


def candidate_paths(app: Any) -> list[str]:
    """Every parameterless GET path the app exposes, de-duplicated, sorted."""
    seen: set[str] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        if "GET" not in methods:
            continue
        path = getattr(route, "path", None)
        if not path or _PLACEHOLDER.search(path):
            continue
        if path in _SKIP_EXACT or path.startswith(_SKIP_PREFIXES):
            continue
        # Mounts report their own sub-path only; the leaf routes are enumerated.
        if path.endswith("/") and path != "/":
            continue
        seen.add(path)
    return sorted(seen)


async def run(prefix: str | None, save: str | None) -> int:
    import uvicorn

    from app.gateway.app import app

    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="error", lifespan="on", access_log=False)
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    deadline = time.time() + 180
    while not server.started and time.time() < deadline:
        await asyncio.sleep(0.5)
    if not server.started:
        server.should_exit = True
        await task
        print("BOOT_FAILED", flush=True)
        return 2
    print("BOOT_OK", flush=True)

    paths = candidate_paths(app)
    if prefix:
        paths = [p for p in paths if p.startswith(prefix)]
    print(f"SWEeping {len(paths)} GET routes", flush=True)

    results: list[dict[str, Any]] = []
    # A per-request ceiling well under the harness timeout: one slow route must not
    # consume the whole run, and a route that needs more than 30s to answer a GET
    # is itself the finding.
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        for path in paths:
            t0 = time.monotonic()
            try:
                r = await client.get(BASE + path)
                status = r.status_code
                body = r.text
                exc = ""
            except Exception as e:  # noqa: BLE001
                status = -1
                body = ""
                exc = f"{type(e).__name__}: {e}"
            dt = (time.monotonic() - t0) * 1000
            results.append({"path": path, "status": status, "ms": round(dt, 1), "exception": exc, "body": body[:400]})
            flag = "FAIL" if (status >= 500 or status == -1) else "ok  "
            print(f"  {flag} {status!s:>5} {dt:8.1f}ms {path} {exc}{body[:160] if status >= 500 else ''}", flush=True)

    server.should_exit = True
    await task

    failures = [r for r in results if r["status"] >= 500 or r["status"] == -1]
    print(f"\nSUMMARY: {len(results) - len(failures)} answered, {len(failures)} failed (5xx/exception)", flush=True)
    for f in failures:
        print(f"  FAILED {f['status']} {f['path']}", flush=True)
        print(f"         {f['exception'] or f['body'][:300]}", flush=True)

    if save:
        with open(save, "w", encoding="utf-8") as fh:
            json.dump({"total": len(results), "failures": len(failures), "results": results}, fh, indent=2)
        print(f"wrote {save}", flush=True)

    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    return asyncio.run(run(args.prefix, args.json))


if __name__ == "__main__":
    raise SystemExit(main())
