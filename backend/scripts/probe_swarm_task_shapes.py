"""The exact task and event shapes a swarm detail view must render.

`swarmDetails()` and `swarmMetrics()` already exist in `frontend/src/lib/teamops.ts`
but nothing imports them, so the whole structure of a swarm — the DAG, the
workers, the elected leader, the events — is unread in the UI. Building that view
means guessing at field names, and a guess here is exactly the kind of thing that
renders as an empty panel instead of an error.

So this prints the real keys of one task, every task's status/worker/dependencies,
and the event vocabulary across several swarms. Read-only.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter

import httpx

GATEWAY = os.environ.get("ALPHA_GATEWAY_URL", "http://127.0.0.1:8001")


def main() -> int:
    c = httpx.Client(timeout=60.0)
    rows = c.get(f"{GATEWAY}/api/swarms?limit=20").json()
    rows = rows if isinstance(rows, list) else (rows.get("swarms") or [])
    print(f"swarms: {len(rows)}")
    if not rows:
        print("no swarms")
        return 1

    best = None
    for r in rows:
        d = c.get(f"{GATEWAY}/api/swarms/{r['swarm_id']}").json()
        if best is None or len(d.get("tasks") or {}) > len(best.get("tasks") or {}):
            best = d

    sid = best["swarm_id"]
    tasks = best.get("tasks") or {}
    print(f"\n=== {sid}  status={best.get('status')!r}  mode={best.get('mode')!r}  tasks={len(tasks)} ===")

    first = list(tasks)[0]
    print(f"\n=== every key on task {first!r} ({len(tasks[first])} keys) ===")
    for key, val in tasks[first].items():
        shown = json.dumps(val, default=str)
        print(f"  {key:26} {shown[:70]}")

    print("\n=== every task ===")
    for tid, t in tasks.items():
        print(
            f"  {tid:18} status={str(t.get('status')):11} worker={str(t.get('assigned_worker')):12} "
            f"type={str(t.get('worker_type')):15} deps={t.get('dependencies')}"
        )

    # Event vocabulary across every swarm, so the view can render a type it has
    # never seen rather than dropping it.
    types: Counter[str] = Counter()
    sample: dict[str, dict] = {}
    for r in rows:
        ev = c.get(f"{GATEWAY}/api/swarms/{r['swarm_id']}/events").json()
        for e in ev if isinstance(ev, list) else []:
            t = str(e.get("event_type"))
            types[t] += 1
            sample.setdefault(t, e)
    print(f"\n=== event vocabulary across {len(rows)} swarms ===")
    for t, n in types.most_common():
        s = sample[t]
        print(f"  {t:26} x{n:3}  keys={sorted(s)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())