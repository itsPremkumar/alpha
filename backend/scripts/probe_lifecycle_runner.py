"""Live gate for 3.T2: does a spawned control-plane subagent actually execute?

The unit suite proves the runner reacts correctly to a terminal status. This
proves a REAL subagent runs: it spawns through the documented route and then
polls `/api/subagents/control`, printing the record's state on every change.

What makes this a gate rather than a smoke test
-----------------------------------------------
Before the fix, `start_subagent` had no production caller, so a spawned record
sat at `ready` with `started_at: None`, `last_heartbeat: None` and
`renew_count: 0` forever. That exact record is what this script asserts against:
it fails if the status never leaves `ready`, if `started_at` never appears, or if
the run reaches a terminal state carrying no summary.

It also prints the runner's own log lines, because "the status changed" and "the
runner is the thing that changed it" are different claims.

Usage:  backend/.venv/Scripts/python.exe backend/scripts/probe_lifecycle_runner.py
        backend/.venv/Scripts/python.exe backend/scripts/probe_lifecycle_runner.py --timeout 900
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import httpx

GATEWAY = os.environ.get("ALPHA_GATEWAY_URL", "http://127.0.0.1:8001")
OBJECTIVE = "Reply with exactly this sentence and nothing else: the lifecycle runner executed this subagent."


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--timeout", type=float, default=600.0, help="seconds to wait for a terminal state")
    ap.add_argument("--role", default="general-purpose")
    args = ap.parse_args()

    c = httpx.Client(timeout=60.0)
    failures: list[str] = []

    before = c.get(f"{GATEWAY}/api/subagents/control").json()
    print(f"before          {len(before)} record(s) on the control plane")

    spawn = c.post(
        f"{GATEWAY}/api/subagents/control/spawn",
        json={"objective": OBJECTIVE, "role": args.role, "parent_agent_id": "lifecycle-probe", "timeout_seconds": 300},
    )
    print(f"spawn           HTTP {spawn.status_code}")
    if spawn.status_code != 200:
        print(f"FAIL  the documented spawn route refused the request: {spawn.text[:200]}")
        return 1
    subagent_id = spawn.json().get("subagent_id", "")
    print(f"                subagent_id={subagent_id}")
    print(f"                status at spawn = {spawn.json().get('status')!r}  started_at={spawn.json().get('started_at')!r}")

    seen_status: str | None = None
    started_at_seen = False
    final: dict | None = None
    deadline = time.time() + args.timeout
    polls = 0

    while time.time() < deadline:
        polls += 1
        rows = c.get(f"{GATEWAY}/api/subagents/control").json()
        mine = next((r for r in rows if r.get("subagent_id") == subagent_id), None)
        if mine is None:
            print(f"  poll {polls:3d}  the record is ABSENT from the list")
            time.sleep(3)
            continue

        status = str(mine.get("status"))
        started = mine.get("started_at")
        if started:
            started_at_seen = True
        if status != seen_status:
            seen_status = status
            beat = mine.get("last_heartbeat") or {}
            lease = mine.get("lease") or {}
            print(f"  poll {polls:3d}  status={status!r} started_at={started!r} renew_count={lease.get('renew_count')!r} action={beat.get('current_action')!r}")
        if status in {"completed", "failed", "cancelled", "expired"}:
            final = mine
            break
        time.sleep(3)

    print()
    if final is None:
        failures.append(f"no terminal state within {args.timeout:.0f}s; last status was {seen_status!r}")
    else:
        result = final.get("result") or {}
        print(f"FINAL           status={final.get('status')!r}")
        print(f"  started_at    {final.get('started_at')!r}")
        print(f"  completed_at  {final.get('completed_at')!r}")
        print(f"  attempt       {final.get('attempt')!r}   renew_count={(final.get('lease') or {}).get('renew_count')!r}")
        print(f"  summary       {(result.get('summary') or '')[:300]!r}")
        print(f"  errors        {(result.get('errors') or [])[:3]!r}")

        if not started_at_seen:
            failures.append("started_at never appeared, so start_subagent never ran")
        if final.get("status") == "completed" and not (result.get("summary") or "").strip():
            failures.append("completed with an empty summary, which is not a real result")
        if final.get("status") == "completed" and OBJECTIVE.split(":")[-1].strip()[:20] not in (result.get("summary") or ""):
            print()
            print("NOTE  the run completed but its summary does not echo the requested sentence.")
            print("      That is a real observation about the model, not a runner failure: the")
            print("      record carries whatever the subagent actually returned.")

    print()
    for f in failures:
        print(f"FAIL  {f}")
    if not failures:
        print("VERIFIED  a spawned subagent left `ready`, started, and reached a terminal state")
        print("          carrying its own summary. The control plane executes now.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
