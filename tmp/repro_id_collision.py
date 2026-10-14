import threading

from alpha.workflow.events import WorkflowEventDispatcher

# Prove the guard: two events emitted with the SAME id (clock congested or a
# shared counter) are refused instead of entering the log as duplicate
# addresses that would corrupt a fork point.
disp = WorkflowEventDispatcher(durable_sink=None)
survived = []
failures = []


def boom():
    try:
        disp.emit("node_attempt_started", "run-1", node_id="n")
        disp.emit("node_completed", "run-1", node_id="n", output="ok")
        survived.append("both_emitted")
    except RuntimeError as exc:
        failures.append(str(exc))


t = threading.Thread(target=boom)
t.start()
t.join(timeout=10)
print("survived=", survived)
print("failures=", failures)
assert not survived, "duplicate event id must be refused, not silently recorded"
assert len(failures) == 1, "exactly one collision must be raised"
assert "collision" in failures[0] and "node_completed" in failures[0]
print("GUARD_BITES")
