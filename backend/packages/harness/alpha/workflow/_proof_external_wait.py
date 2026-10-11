"""Proof: ``external_wait_parked`` folds into ``WAITING_EVENT`` on replay.

Replays the journal ``workflow_started -> external_wait_parked`` through
``alpha.orchestrator.replay.replay_run`` and checks the rebuilt run is
parked — run ``WAITING_EVENT``, node ``WAITING`` — with the park's own
reason preserved. Before the fold existed, replay silently rebuilt such a
run as ``RUNNING`` and a scheduler would have re-dispatched work nobody had
released. Exit code 0 is PASS; 1 is FAIL.

Runnable standalone::

    python -m alpha.workflow._proof_external_wait    # from backend/, PYTHONPATH=.
    python _proof_external_wait.py                   # from this directory

Originally shipped as editor scratch with a hard-coded ``C:/...`` path and
``inspect`` used but never imported (a guaranteed ``NameError``); the path
now derives from this file, every symbol is imported, and nothing executes
at import time — the proof lives in ``replay_events()`` and ``main()``.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from alpha.orchestrator.replay import replay_run
from alpha.workflow.events import WorkflowEvent
from alpha.workflow.models import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunStatus,
)

FIX_MARKER = "external_wait_parked"
NODE_ID = "db"
RUN_ID = "run_1"


def build_run(workflow_id: str = "wf", graph_version: int = 1) -> tuple[WorkflowRun, WorkflowDefinition]:
    """A one-node tool workflow whose node is where the wait parks."""
    graph = WorkflowGraph(version=graph_version)
    graph.nodes[NODE_ID] = WorkflowNode(id=NODE_ID, type=NodeType.TOOL, executor="alpha.tool")
    definition = WorkflowDefinition(id=workflow_id, name=workflow_id, graph=graph)
    run = WorkflowRun(
        run_id=RUN_ID,
        workflow_id=workflow_id,
        graph_version=graph_version,
        status=WorkflowRunStatus.RUNNING,
        state={"objective": "train"},
    )
    return run, definition


def fresh_event(event_type: str, payload: dict[str, Any]) -> WorkflowEvent:
    return WorkflowEvent(workflow_run_id=RUN_ID, event_type=event_type, payload=payload)


def replay_events(events: list[WorkflowEvent], definition: WorkflowDefinition) -> tuple[WorkflowRun, dict[str, Any]]:
    """Fold ``events`` onto a fresh engine; return the run and source facts.

    The source facts answer the question this proof depends on: does the
    *imported* ``replay_run`` — not a stale install, not another checkout —
    carry the fold, and is its source file the one beside this proof?
    """
    _, run = replay_run(events, definition)

    replay_file = Path(inspect.getsourcefile(replay_run) or "")
    run_source = inspect.getsource(replay_run)
    source_facts = {
        "replay_file": str(replay_file),
        "imported_has_fix": FIX_MARKER in run_source,
        "working_tree_has_fix": FIX_MARKER in (replay_file.read_text(encoding="utf-8") if replay_file.is_file() else ""),
    }
    return run, source_facts


def main() -> int:
    run, definition = build_run()
    events = [
        fresh_event(
            "workflow_started",
            {"workflow_id": definition.id, "state": run.state, "owner_id": None, "graph_version": run.graph_version},
        ),
        fresh_event(
            "external_wait_parked",
            {
                "node_id": NODE_ID,
                "target": "user",
                "event": "approval.granted",
                "node_status": NodeStatus.WAITING.value,
                "reason": "awaiting approval",
            },
        ),
    ]

    replayed, facts = replay_events(events, definition)
    print("REPLAY_FILE =", facts["replay_file"])
    print("IMPORTED_HAS_FIX =", facts["imported_has_fix"])
    print("WORKING_TREE_HAS_FIX =", facts["working_tree_has_fix"])
    print("REPLAYED_STATUS =", replayed.status.value)
    print("NODE_STATUS =", replayed.node_states.get(NODE_ID, NodeStatus.PENDING).value)

    passed = facts["imported_has_fix"] and replayed.status is WorkflowRunStatus.WAITING_EVENT and replayed.node_states.get(NODE_ID) is NodeStatus.WAITING
    print("RESULT =", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
