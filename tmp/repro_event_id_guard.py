"""Proof the guard bites (run against the installed alpha package).

Forges a genuine duplicate event id (frozen counter) and asserts the dispatcher
refuses the second emit instead of recording it. This is the exact failure mode
the guard targets: back-to-back nodes settling on one address would corrupt a
fork point that resolves by id.
"""

import sys

sys.path.insert(0, "C:/Users/PREM KUMAR/Videos/alpha/backend/packages/harness/alpha")

from alpha.workflow.events import WorkflowEventDispatcher, _new_event_id  # noqa: E402

ORIG = _new_event_id


def frozen():
    return "20261014123345000001-000000000001"


try:
    _new_event_id = frozen
    d = WorkflowEventDispatcher(durable_sink=None)
    first = second = None
    try:
        d.emit("node_attempt_started", "run-1", node_id="n")
        first = "ok"
    except RuntimeError:
        first = "refused"
    try:
        d.emit("node_completed", "run-1", node_id="n", output="ok")
        second = "ok"
    except RuntimeError:
        second = "refused"
    finally:
        _new_event_id = ORIG

    print("first =", first, "| second =", second, "| log_len =", len(d._event_log))
    assert first == "ok", first
    assert second == "refused", second
    assert len(d._event_log) == 0, "no event may be recorded under a duplicate id"
    print("GUARD_BITES")
except Exception as exc:
    print("FAILED:", type(exc).__name__, exc)
    raise SystemExit(1)
