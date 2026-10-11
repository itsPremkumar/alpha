"""Probe: does ``replay_run`` fold every non-terminal, non-dispatchable park?

Answers one concrete question with a real replay: after the engine parks a
``connectivity_wait`` node and journals ``connectivity_wait_parked``, does
folding the event log back through ``alpha.orchestrator.replay.replay_run``
rebuild the run in its parked state — run ``WAITING_CONNECTIVITY``, node
``WAITING`` — or does it silently rebuild a runnable ``RUNNING`` run that a
scheduler would re-dispatch into an outage the original run had already
measured?

This was the diagnostic that found the gap: replay handled
``external_wait_parked`` but no connectivity park, so a connectivity-parked
run replayed as ``RUNNING``. The fold now exists
(``orchestrator/replay.py``), and this probe is its regression check.

Runnable standalone::

    python -m alpha.workflow._probe_replay_fold    # from backend/, PYTHONPATH=.
    python _probe_replay_fold.py                   # from this directory

Nothing executes at import time; the probe logic lives in ``run_probe()`` and
``main()``.
"""

from __future__ import annotations

from typing import Any

from alpha.orchestrator.replay import replay_run
from alpha.workflow.events import WorkflowEvent
from alpha.workflow.models import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRunStatus,
)

STARTED_AT = "2026-10-10T00:00:00+00:00"
PARKED_AT = "2026-10-10T00:00:01+00:00"
NODE_ID = "n0"
RUN_ID = "r1"


def build_definition() -> WorkflowDefinition:
    """A single-node graph whose node kind is the connectivity wait."""
    graph = WorkflowGraph(
        nodes={NODE_ID: WorkflowNode(id=NODE_ID, type=NodeType.CONNECTIVITY_WAIT, config={})},
        edges=[],
    )
    return WorkflowDefinition(id="wf1", name="wf1", graph=graph)


def park_events() -> list[WorkflowEvent]:
    """The minimal honest journal: a start, then the engine's own park event.

    The park payload mirrors ``DynamicWorkflowEngine``'s emit exactly
    (``node_id``/``target``/``event``/``deadline_seconds``/``node_status``/
    ``reason``) so this probe exercises the production shape, not a fixture.
    """
    started = WorkflowEvent(
        workflow_run_id=RUN_ID,
        event_type="workflow_started",
        timestamp=STARTED_AT,
        payload={"workflow_id": "wf1", "state": {}, "owner_id": None},
    )
    park = WorkflowEvent(
        workflow_run_id=RUN_ID,
        event_type="connectivity_wait_parked",
        timestamp=PARKED_AT,
        payload={
            "node_id": NODE_ID,
            "target": "the network",
            "event": "network_restored",
            "deadline_seconds": None,
            "node_status": NodeStatus.WAITING.value,
            "reason": "network offline: probe unreachable",
        },
    )
    return [started, park]


def run_probe() -> dict[str, Any]:
    """Replay the park and report what the fold actually rebuilt."""
    _, run = replay_run(park_events(), build_definition())
    folded = run.status is WorkflowRunStatus.WAITING_CONNECTIVITY and run.node_states.get(NODE_ID) is NodeStatus.WAITING
    return {
        "status": run.status.value,
        "node_states": {nid: state.value for nid, state in run.node_states.items()},
        "waiting_reason": run.waiting_reason,
        "folded": folded,
    }


def main() -> int:
    result = run_probe()
    print("PROBE = connectivity_wait_parked fold on replay_run")
    print("REPLAY_STATUS =", result["status"])
    print("NODE_STATES =", result["node_states"])
    print("WAITING_REASON =", result["waiting_reason"])
    print("RESULT =", "PASS" if result["folded"] else "FAIL")
    return 0 if result["folded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
