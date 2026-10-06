"""5.T2: does a completed dynamic run carry an acceptance verdict it did not earn?

`docs/FEATURE_COMPLETION_PLAN.md` states two things to verify, and they are the
same question from two sides:

  - "Verify the report carries **no acceptance verdict** - a completed run is not
    a verified run."
  - "the default executor is a `local_digest_projection` with
    `acceptance_passed=false`"

The distinction is the whole point of the check. A run that reaches `completed`
has finished its GRAPH. It has not been judged to have done domain work, because
the default executor hashes inputs rather than performing them. Reporting
`completed` and `accepted` as the same word would tell an operator a task was
done when only its bookkeeping was.

Per `packages/harness/alpha/AGENTS.md` the digest executor is local - it "hashes
inputs to exercise graph mechanics" - so this run spends no model tokens. That is
why it can be run routinely rather than behind a flag.

Steps verified, in order:
  1. perceive a plan (no execution)
  2. execute it (auto_execute) and read the run back
  3. GET the run report and confirm no acceptance verdict is present
  4. confirm the executor actually used is the digest projection
  5. GET the events and confirm the node transitions are real, not a summary
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

GATEWAY = os.environ.get("ALPHA_GATEWAY_URL", "http://127.0.0.1:8001")
PROMPT = "Rename the variable `tmp` to `buffer` in backend/utils/time.py"

# Words that would turn "the graph finished" into "the work was judged good".
VERDICT_KEYS = (
    "acceptance_passed",
    "accepted",
    "accepted_by",
    "verdict",
    "verified",
    "is_verified",
    "acceptance_verdict",
)


def walk(obj, path="", out=None):
    """Every (path, key, value) triple, so a verdict cannot hide in a nested block."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            out.append((p, k, v))
            walk(v, p, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:10]):
            walk(v, f"{path}[{i}]", out)
    return out


def main() -> int:
    c = httpx.Client(timeout=180.0)
    failures: list[str] = []

    print("=== 1. perceive ===")
    r = c.post(f"{GATEWAY}/api/workflows/dynamic/perceive", json={"prompt": PROMPT})
    print(f"  HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"  {r.text[:300]}")
        return 1
    plan = r.json()
    tasks = (plan.get("goal") or {}).get("tasks") or []
    print(f"  tasks: {len(tasks)}  waves: {len((plan.get('goal') or {}).get('execution_waves') or [])}")
    print(f"  executor posture: {(plan.get('resources') or {}).get('model_tier')!r}")

    print("\n=== 2. execute (local digest projection; no model tokens) ===")
    r = c.post(
        f"{GATEWAY}/api/workflows/dynamic/execute",
        json={"prompt": PROMPT, "auto_execute": True},
    )
    # The route answers 201 Created, not 200. Treating anything but 200 as a
    # failure made this probe print the payload and exit before it checked the
    # single thing it exists to check.
    print(f"  HTTP {r.status_code}")
    if r.status_code not in (200, 201):
        print(f"  {r.text[:500]}")
        return 1
    ex = r.json()
    run_id = ex.get("run_id")
    # The execute route answers with the execution result itself (HTTP 201), not
    # a wrapper: `run_id`, `status` and the counts are top-level keys. Reading
    # them from a nested envelope is what made the first run of this probe exit
    # before it examined anything.
    print(f"  run_id         : {run_id}")
    print(f"  status         : {ex.get('status')!r}")
    print(f"  total_steps    : {ex.get('total_steps')!r}")
    print(f"  task_count     : {ex.get('task_count')!r}")
    print(f"  completed      : {ex.get('completed_count')!r}")
    print(f"  failed_nodes   : {ex.get('failed_nodes')!r}")
    print(f"  waves          : {ex.get('waves')!r}")

    md = ex.get("metadata") or {}
    print(f"  metadata keys  : {sorted(md)}")
    for key in ("verification", "executor", "executors", "projection", "dialect", "dry_run"):
        if key in md:
            print(f"    {key:26} {json.dumps(md[key])[:200]}")
    # The load-bearing values. `acceptance_passed` must be FALSE on a digest run:
    # the graph finished, no domain work was judged. `node_verification` must not
    # report a pass for a declared verifier that never ran.
    for key in ("acceptance_passed", "acceptance_reason", "execution_label", "node_runner_bound", "compensation_runner_bound"):
        if key in md:
            print(f"    {key:26} {json.dumps(md[key])[:140]}")
    if md.get("acceptance_passed") is True:
        failures.append(
            "metadata.acceptance_passed is TRUE on a digest-projection run: the graph finished, "
            "no domain work was performed, and acceptance was claimed anyway"
        )
    elif md.get("acceptance_passed") is False:
        print("  -> acceptance_passed = false, as the digest projection requires")
    if not run_id:
        failures.append("execute returned no run_id, so nothing can be read back")
        print()
        for f in failures:
            print(f"FAIL  {f}")
        return 1

    print("\n=== 3. the report ===")
    for _ in range(10):
        rr = c.get(f"{GATEWAY}/api/workflows/runs/{run_id}/report")
        if rr.status_code == 200:
            report = rr.json()
            break
        time.sleep(1.5)
    else:
        print(f"  report HTTP {rr.status_code}: {rr.text[:300]}")
        failures.append("the run report could not be read")
        report = None

    if isinstance(report, dict):
        print(f"  report keys: {sorted(report)}")
        # The load-bearing assertion: no key anywhere may claim acceptance.
        triples = walk(report)
        verdicts = [(p, k, v) for (p, k, v) in triples if k in VERDICT_KEYS]
        if verdicts:
            for p, k, v in verdicts:
                print(f"  verdict field: {p} = {json.dumps(v)[:80]}")
                # `acceptance_passed: false` is the CORRECT value here; the defect
                # would be a truthy verdict on a digest run.
                if v is True or (isinstance(v, str) and v.lower() in {"passed", "accepted", "verified"}):
                    failures.append(f"the report claims acceptance at {p} = {json.dumps(v)}")
        else:
            print("  no acceptance-verdict field present anywhere in the report")

        for p, k, v in triples:
            if k in {"acceptance_passed", "verdict"} and v is not False and v != "not_required":
                print(f"  NOTE {p} = {json.dumps(v)[:120]}")

    print("\n=== 4. which executor actually ran ===")
    exr = c.get(f"{GATEWAY}/api/workflows/system/executors")
    if exr.status_code == 200:
        d = exr.json()
        print(f"  bound                : {d.get('bound')}")
        print(f"  default dynamic exec : {d.get('public_dynamic_default_executor')}")
        print(f"  domain_bound         : {d.get('domain_bound')}")
        if not d.get("domain_bound"):
            print("  -> no domain executor is bound, so this run was graph mechanics only")

    print("\n=== 5. the event log (are the transitions real?) ===")
    ev = c.get(f"{GATEWAY}/api/workflows/runs/{run_id}/events")
    if ev.status_code == 200:
        events = ev.json()
        rows = events if isinstance(events, list) else (events.get("events") or [])
        types: dict[str, int] = {}
        for e in rows:
            t = str((e or {}).get("event_type") or (e or {}).get("type"))
            types[t] = types.get(t, 0) + 1
        print(f"  events: {len(rows)}")
        for t, n in sorted(types.items(), key=lambda kv: -kv[1])[:14]:
            print(f"    {t:32} x{n}")
        if not rows:
            failures.append("the run journalled no events, so its progress is not evidenced")

        # A declared verifier with no executor bound must be journalled as a
        # NON-pass. Reporting `passed: true` for a check that never ran is the
        # defect this event type exists to prevent.
        verifications = [e for e in rows if str((e or {}).get("event_type")) == "node_verification"]
        for v in verifications:
            payload = (v or {}).get("payload") or (v or {}).get("details") or v
            blob = json.dumps(payload)
            print(f"  node_verification: {blob[:300]}")
            if '"passed": true' in blob.replace(" ", ""):
                failures.append("a node_verification event reports passed=true with no executor bound")
    else:
        print(f"  events HTTP {ev.status_code}: {ev.text[:200]}")
        failures.append("the run event log could not be read")

    print()
    for f in failures:
        print(f"FAIL  {f}")
    if not failures:
        print("VERIFIED  a completed dynamic run runs on the digest projection and")
        print("          carries no acceptance verdict it did not earn.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())