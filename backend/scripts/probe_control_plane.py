"""Prove which execution path can ever populate GET /api/subagents/control.

Measured on 2026-10-06 while trying to make a delegated subagent visible in the
UI. Three subagent execution paths exist in this repo, and the control plane is
the surface `SubagentsSection`'s "Running now" block reads. The question is
which of them writes there.

Read from the source, the only producer is `manager.spawn_subagent(...)` and its
single caller is the `/subagent:spawn` slash command
(`commands/backend_handlers.py:567`). The ordinary `task` tool - the path an
agent actually delegates over, and the one verified writing three on-disk
artifacts - never registers.

This drives the plane directly rather than trusting the reading: it spawns
through the documented route, reads the list back, and then reports the state.
A plane that stays empty here is not broken by a failed spawn; it is a plane
whose writer is a different path from the one the UI cares about.

Usage:  backend/.venv/Scripts/python.exe backend/scripts/probe_control_plane.py
"""

from __future__ import annotations

import os
import sys
import time

import httpx

GATEWAY = os.environ.get("ALPHA_GATEWAY_URL", "http://127.0.0.1:8001")
OBJECTIVE = "control-plane probe: confirm which execution path registers here"


def _headers() -> dict[str, str]:
    token = os.environ.get("ALPHA_API_KEY") or os.environ.get("ALPHA_AUTH_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def main() -> int:
    c = httpx.Client(timeout=60.0, headers=_headers())
    failures: list[str] = []

    before = c.get(f"{GATEWAY}/api/subagents/control")
    print(f"before        GET /api/subagents/control -> HTTP {before.status_code}  {before.text[:160]}")

    spawn = c.post(
        f"{GATEWAY}/api/subagents/control/spawn",
        json={"objective": OBJECTIVE, "role": "specialist", "parent_agent_id": "probe"},
    )
    print(f"spawn         POST /api/subagents/control/spawn -> HTTP {spawn.status_code}  {spawn.text[:200]}")

    if spawn.status_code != 200:
        print("\nFAIL  the documented spawn route did not accept the request, so this probe")
        print("      cannot distinguish 'the plane is unwired' from 'the probe cannot reach it'.")
        return 1

    subagent_id = ""
    try:
        subagent_id = str((spawn.json() or {}).get("subagent_id", ""))
    except Exception:  # noqa: BLE001 - a non-JSON body is itself the finding
        pass

    after = c.get(f"{GATEWAY}/api/subagents/control")
    rows = after.json() if after.headers.get("content-type", "").startswith("application/json") else None
    print(f"after         GET /api/subagents/control -> HTTP {after.status_code}  rows={len(rows) if isinstance(rows, list) else rows}")

    mine = None
    if subagent_id:
        found = c.get(f"{GATEWAY}/api/subagents/control/{subagent_id}")
        print(f"readback      GET /api/subagents/control/{subagent_id[:12]} -> HTTP {found.status_code}  {found.text[:160]}")
        if found.status_code != 200:
            failures.append(f"the spawned subagent {subagent_id[:12]} is not readable back by id")
        elif isinstance(rows, list):
            # Measure THIS record, not the row count. The plane retains history -
            # a cancelled record from an earlier probe is still listed - so
            # "rows > 0" would pass on a previous run's litter and prove nothing
            # about the spawn just performed.
            mine = next((r for r in rows if r.get("subagent_id") == subagent_id), None)
            if mine is None:
                failures.append(f"spawn returned {subagent_id} but the list does not contain it")

    catalog = c.get(f"{GATEWAY}/api/subagents")
    catalog_rows = (catalog.json() or {}).get("subagents", []) if catalog.status_code == 200 else []
    print(f"catalog       GET /api/subagents -> HTTP {catalog.status_code}  definitions={len(catalog_rows)}")
    print()
    print("Two different planes, and they are not connected:")
    print(f"  /api/subagents        definitions  {len(catalog_rows)}  (what a subagent IS)")
    print(f"  /api/subagents/control live records {len(rows) if isinstance(rows, list) else '?'}  (what a subagent IS DOING)")
    print()
    if mine:
        print("VERIFIED  a spawn through the documented route DOES reach the control plane,")
        print("          so the plane is wired and an empty list during ordinary `task`")
        print("          delegation means that path never writes to it.")
        print()
        print("What the record says about EXECUTION:")
        for field in ("status", "started_at", "completed_at", "last_heartbeat", "result"):
            print(f"  {field:<15}{mine.get(field)!r}")
        lease = mine.get("lease") or {}
        print(f"  {'renew_count':<15}{lease.get('renew_count')!r}")
        print(f"  {'lease_expires':<15}{lease.get('expires_at')!r}  now={time.time():.0f}")
        if mine.get("started_at") is None:
            print()
            print("  `started_at` is None and `last_heartbeat` is None: nothing ever RAN")
            print("  this. `manager.start_subagent` has no production caller - grep finds it")
            print("  only in tests - so a spawned subagent is a registered intention, and")
            print("  this plane cannot report real work even when it is non-empty.")
    else:
        print("The plane stayed EMPTY for the subagent this probe spawned.")
        if spawn.status_code == 200:
            failures.append("spawn returned 200 but the control plane still lists no records")

    if subagent_id:
        cancelled = c.post(
            f"{GATEWAY}/api/subagents/control/{subagent_id}/cancel",
            json={"reason": "probe cleanup"},
        )
        print(f"\ncleanup       cancel {subagent_id[:12]} -> HTTP {cancelled.status_code}")
        print("  (the plane retains history, so a cancelled row stays listed - which is")
        print("   why this probe matches on its own id rather than counting rows)")

    for f in failures:
        print(f"FAIL  {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
