#!/usr/bin/env python3
"""Live route smoke test for the Alpha Gateway.

Unit tests can pass while the running app is broken. This script exercises the
LIVE gateway: it pulls the OpenAPI schema and requests every parameterless GET
route, then reports status classes.

It exists because a real regression was only caught this way — bots were being
provisioned with placeholder roles while every unit test passed.

Usage:
    python scripts/smoke_routes.py                    # http://127.0.0.1:8001
    python scripts/smoke_routes.py --base-url URL
    python scripts/smoke_routes.py --fail-on 5xx      # default
    python scripts/smoke_routes.py --allow-4xx        # treat 4xx as acceptable

Exit codes:
    0  no server errors
    1  at least one 5xx (or 4xx when --strict-4xx)
    2  gateway unreachable / no schema

Note: pass --no-proxy if the environment sets an HTTP proxy for localhost.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any


def build_opener(no_proxy: bool) -> urllib.request.OpenerDirector:
    if no_proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def fetch_schema(base_url: str, opener: urllib.request.OpenerDirector) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/openapi.json"
    with opener.open(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parameterless_get_routes(schema: dict[str, Any]) -> list[str]:
    """GET routes with no path params and no required parameters."""
    out: list[str] = []
    for path, methods in (schema.get("paths") or {}).items():
        if "{" in path:
            continue
        get = methods.get("get") if isinstance(methods, dict) else None
        if not get:
            continue
        params = get.get("parameters") or []
        if any(p.get("required") for p in params):
            continue
        out.append(path)
    return sorted(out)


def hit(base_url: str, path: str, opener: urllib.request.OpenerDirector, timeout: float) -> tuple[str, int]:
    try:
        req = urllib.request.Request(base_url.rstrip("/") + path, headers={"Accept": "application/json"})
        with opener.open(req, timeout=timeout) as resp:
            return path, resp.status
    except urllib.error.HTTPError as exc:
        return path, exc.code
    except Exception:
        return path, 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Live Gateway route smoke test.")
    ap.add_argument("--base-url", default="http://127.0.0.1:8001")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-proxy", action="store_true", help="Bypass any environment HTTP proxy (needed in some sandboxes).")
    ap.add_argument("--strict-4xx", action="store_true", help="Treat 4xx as a failure too (default: only 5xx fails).")
    args = ap.parse_args(argv)

    opener = build_opener(args.no_proxy)

    try:
        schema = fetch_schema(args.base_url, opener)
    except Exception as exc:
        print(f"FATAL: cannot reach {args.base_url} ({type(exc).__name__}: {exc})")
        return 2

    routes = parameterless_get_routes(schema)
    if not routes:
        print("FATAL: no parameterless GET routes found in schema")
        return 2

    print(f"Gateway: {args.base_url}")
    print(f"Testing {len(routes)} parameterless GET routes\n")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda p: hit(args.base_url, p, opener, args.timeout), routes))

    ok = [r for r in results if 200 <= r[1] < 300]
    redirect = [r for r in results if 300 <= r[1] < 400]
    client = [r for r in results if 400 <= r[1] < 500]
    server = [r for r in results if r[1] >= 500]
    dead = [r for r in results if r[1] == 0]

    print(f"  2xx OK      : {len(ok)}")
    print(f"  3xx redirect: {len(redirect)}")
    print(f"  4xx client  : {len(client)}")
    print(f"  5xx SERVER  : {len(server)}   <-- fail")
    print(f"  no response : {len(dead)}")

    if server:
        print("\n--- 5xx SERVER ERRORS ---")
        for path, code in sorted(server):
            print(f"  {code}  {path}")

    if dead:
        print("\n--- NO RESPONSE (timeout/connection) ---")
        for path, _ in sorted(dead)[:20]:
            print(f"  ???  {path}")

    if client and args.strict_4xx:
        print("\n--- 4xx (strict mode) ---")
        for path, code in sorted(client)[:20]:
            print(f"  {code}  {path}")

    failed = bool(server) or bool(dead)
    if args.strict_4xx:
        failed = failed or bool(client)

    print("\nRESULT:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
