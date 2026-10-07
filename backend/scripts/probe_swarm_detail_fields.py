"""What a swarm detail surface could actually show — measured, not assumed.

The Team Ops list renders one line per swarm: id, status, strategy, objective,
and aggregate counts ("0 of 3 tasks complete · running 0 · pending 3"). Nothing
per-worker, nothing per-task, no events. The backend, however, exposes
`/{id}/events`, `/{id}/events/stream`, `/{id}/metrics`, `/{id}/leader`,
`/{id}/incidents`, `/{id}/memory`, and a task subtree. So the question is
whether the detail payload actually carries workers and tasks, or whether those
routes are the empty half.

Read-only. No swarm is created, started, paused or cancelled.
"""

from __future__ import annotations

import os
import sys

import httpx

GATEWAY = os.environ.get("ALPHA_GATEWAY_URL", "http://127.0.0.1:8001")


def shape(value: object, depth: int = 0) -> str:
    """A compact one-line description, so a wide payload stays readable."""
    if isinstance(value, dict):
        if depth >= 2:
            return f"{{{len(value)} keys}}"
        inner = ", ".join(f"{k}={shape(v, depth + 1)}" for k, v in list(value.items())[:8])
        return f"{{{inner}{', ...' if len(value) > 8 else ''}}}"
    if isinstance(value, list):
        if not value:
            return "[]"
        return f"[{len(value)} x {shape(value[0], depth + 1)}]"
    if isinstance(value, str):
        return f"{value[:34]!r}"
    return repr(value)


def get(c: httpx.Client, path: str) -> tuple[int, object]:
    r = c.get(f"{GATEWAY}{path}")
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text[:200]


def main() -> int:
    c = httpx.Client(timeout=60.0)

    code, listing = get(c, "/api/swarms?limit=6")
    print(f"GET /api/swarms -> HTTP {code}")
    if code != 200:
        print(f"  {listing}")
        return 1
    rows = listing if isinstance(listing, list) else (listing.get("swarms") or listing.get("plans") or [])
    print(f"  swarms returned: {len(rows)}")
    if not rows:
        print("  no swarms to inspect; create one first")
        return 0

    first = rows[0]
    print(f"\n=== LIST ROW shape ===\n  {shape(first)}")
    print(f"  list row keys: {sorted(first) if isinstance(first, dict) else 'n/a'}")

    # Pick the most interesting swarm: prefer one that actually ran.
    candidates = [
        r
        for r in rows
        if isinstance(r, dict) and (r.get("task_counts") or {}).get("total", 0) not in (0, None)
    ]
    target = candidates[0] if candidates else first
    sid = target.get("id") or target.get("swarm_id")
    print(f"\n=== inspecting {sid} (status={target.get('status')!r}) ===")

    code, detail = get(c, f"/api/swarms/{sid}")
    print(f"\nGET /api/swarms/{{id}} -> HTTP {code}")
    if isinstance(detail, dict):
        print(f"  keys: {sorted(detail)}")
        for key in ("tasks", "workers", "agents", "task_counts", "metrics", "topology", "events", "leader"):
            if key in detail:
                v = detail[key]
                n = len(v) if isinstance(v, (list, dict)) else v
                print(f"    {key:14} {type(v).__name__:6} len/val={n}")
                if isinstance(v, list) and v:
                    print(f"                   first -> {shape(v[0])}")
                elif isinstance(v, dict):
                    print(f"                   {shape(v)}")
    else:
        print(f"  {detail}")

    for name, path in (
        ("events", f"/api/swarms/{sid}/events"),
        ("metrics", f"/api/swarms/{sid}/metrics"),
        ("leader", f"/api/swarms/{sid}/leader"),
        ("incidents", f"/api/swarms/{sid}/incidents"),
        ("memory", f"/api/swarms/{sid}/memory"),
        ("messages", f"/api/swarms/{sid}/messages"),
    ):
        code, body = get(c, path)
        if isinstance(body, list):
            head = f"[{len(body)}] first -> {shape(body[0])}" if body else "[] empty"
        elif isinstance(body, dict):
            head = f"keys={sorted(body)[:9]}"
        else:
            head = str(body)[:90]
        print(f"\nGET {path:44} HTTP {code}  {head}")

    return 0


if __name__ == "__main__":
    sys.exit(main())