"""Built-in tool for managing Continual Harness state and triggering online refinement."""

from __future__ import annotations

from typing import Literal

from langchain.tools import tool
from langgraph.runtime import Runtime

from alpha.harness.continual.refine import ContinualRefinementEngine
from alpha.harness.continual.snapshots import HarnessSnapshotManager
from alpha.harness.continual.state import HarnessKind, HarnessScope, HarnessState


@tool("harness_refine", parse_docstring=True)
def harness_refine_tool(
    action: Literal["refine", "list", "add", "rollback"],
    kind: HarnessKind | None = None,
    title: str = "",
    content: str = "",
    snapshot_id: str = "",
    scope: HarnessScope = "local",
    runtime: Runtime | None = None,
) -> str:
    """Manage self-improving Continual Harness state and trigger online refinement.

    Inspired by Prime Agent's /refine and durable harness state. Entries record
    supplemental prompt directives, failure rules, project memories, or specialized
    subagent profiles without altering the immutable base system prompt.

    Args:
        action: The operation to perform: 'refine', 'list', 'add', or 'rollback'.
        kind: Entry category ('prompt', 'memory', 'skill', 'subagent'). Required for 'add'.
        title: Short title or rule summary. Required for 'add'.
        content: The detailed directive or memory. Required for 'add'.
        snapshot_id: The snapshot identifier to rollback to. Required for 'rollback'.
        scope: Storage scope ('local' for workspace/thread, 'global' for cross-session). Defaults to 'local'.
    """
    state = HarnessState(scope=scope)

    if action == "list":
        entries = state.list_entries(kind=kind, enabled_only=True)
        if not entries:
            return f"No active {kind or ''} entries in {scope} harness state."
        out = [f"=== Continual Harness Entries ({scope}) ==="]
        for e in entries:
            out.append(f"[{e.id}] ({e.kind}) {e.title}: {e.content}")
        return "\n".join(out)

    elif action == "add":
        if not kind or not title or not content:
            return "Error: 'kind', 'title', and 'content' are required for action 'add'."
        entry = state.add_entry(kind=kind, title=title, content=content, source="agent_tool")
        return f"Successfully recorded harness entry [{entry.id}] in {scope} state."

    elif action == "refine":
        engine = ContinualRefinementEngine(state)
        # In a real run, events could be loaded from thread event store if runtime provided.
        # Fallback to current state audit
        ev = engine.refine_from_events([], trigger="agent_manual_refine")
        if ev:
            return f"Refinement completed: {ev.outcome}"
        return f"Refinement audit complete. No new failure rules or heuristics detected for {scope} state."

    elif action == "rollback":
        if not snapshot_id:
            mgr = HarnessSnapshotManager(state)
            snapshots = mgr.list_snapshots()
            if not snapshots:
                return "No snapshots available for rollback."
            return f"Error: snapshot_id is required. Available snapshots: {[s['snapshot_id'] for s in snapshots]}"
        mgr = HarnessSnapshotManager(state)
        success = mgr.restore_snapshot(snapshot_id)
        if success:
            return f"Successfully rolled back {scope} harness state to snapshot {snapshot_id}."
        return f"Error: Failed to rollback to snapshot {snapshot_id} (not found or invalid)."

    return f"Error: Unknown action '{action}'."
