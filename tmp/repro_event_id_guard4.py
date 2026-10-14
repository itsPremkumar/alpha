"""Proof the guard bites at emit time.

Forges a genuine duplicate event id by rebinding the class's default factory,
then calls two back-to-back emits. The second must be refused (RuntimeError)
with "collision" in the message and the log must contain exactly one event.
This is the failure mode the guard prevents: an id that can repeat is not an
address, and back-to-back nodes settling on one address silently corrupt a
fork point that resolves by id.
"""

import sys

sys.path.insert(0, "C:/Users/PREM KUMAR/Videos/alpha/backend/packages/harness/alpha")


import alpha.workflow.events as ev  # noqa: E402
from alpha.workflow.events import WorkflowEventDispatcher  # noqa: E402

COLLIDING = "20261014123345000001-000000000001"
factory = ev.WorkflowEvent.model_fields["event_id"].default_factory


def forge_duplicate_factory():
    def _dup():
        return COLLIDING

    return _dup


try:
    ev.WorkflowEvent.model_fields["event_id"].default_factory = forge_duplicate_factory()
    d = WorkflowEventDispatcher(durable_sink=None)
    first = second = None
    try:
        d.emit("node_attempt_started", "run-1", node_id="n")
        first = "ok"
    except RuntimeError as exc:
        first = "refused:" + str(exc)

    try:
        d.emit("node_completed", "run-1", node_id="n", output="ok")
        second = "ok"
    except RuntimeError as exc:
        second = "refused:" + str(exc)

    print("first =", first, "| second =", second, "| log_len =", len(d._event_log))
    assert first == "ok", first
    assert "collision" in second, second
    assert len(d._event_log) == 1, "only the first event may be recorded"
    print("GUARD_BITES")
except Exception as exc:
    print("FAILED:", type(exc).__name__, exc)
    raise SystemExit(1)
finally:
    ev.WorkflowEvent.model_fields["event_id"].default_factory = factory
