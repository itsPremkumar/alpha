"""Online Refinement Engine for Continual Harness (inspired by Prime Agent /refine).

Analyzes run events, tool errors, and agent trajectories to synthesize small,
evidence-backed updates to the supplemental harness state.
"""

from __future__ import annotations

import logging
from typing import Any

from agent_workspace.harness.continual.snapshots import HarnessSnapshotManager
from agent_workspace.harness.continual.state import HarnessEntry, HarnessState, RefinementEvent

logger = logging.getLogger(__name__)


class ContinualRefinementEngine:
    """Extracts learnings and updates HarnessState based on execution feedback."""

    def __init__(self, state: HarnessState):
        self.state = state
        self.snapshots = HarnessSnapshotManager(state)

    def refine_from_events(
        self,
        events: list[dict[str, Any]],
        *,
        trigger: str = "auto_turn_review",
        auto_snapshot: bool = True,
    ) -> RefinementEvent | None:
        """Analyze run events and apply evidence-backed refinements."""
        changes: list[str] = []
        evidence_points: list[str] = []

        # 1. Scan for repeated tool execution errors
        tool_errors: list[dict[str, Any]] = []
        for ev in events:
            ev_type = ev.get("type") or ev.get("event_type")
            payload = ev.get("payload") or ev.get("data") or {}

            # Check for tool errors in event payloads
            if ev_type in ("tool_error", "tool_result", "run_event"):
                output = str(payload.get("output") or payload.get("result") or payload.get("error") or "")
                if "Error:" in output or "Exception:" in output or "Permission denied" in output:
                    tool_name = payload.get("tool_name") or payload.get("tool") or "unknown_tool"
                    tool_errors.append({"tool": tool_name, "error": output[:300]})

        # Distill failure rules from tool errors
        for err in tool_errors:
            tool = err["tool"]
            msg = err["error"]

            if "No such file or directory" in msg or "FileNotFoundError" in msg:
                title = f"Path Verification Rule ({tool})"
                content = f"Always verify directory structure or file existence with `ls` or `glob` before calling `{tool}`."
                entry = self._upsert_memory(title, content, source="refinement_error_analysis")
                changes.append(f"Added memory: {title}")
                evidence_points.append(f"Tool {tool} failed with file not found: {msg[:100]}")

            elif "Permission denied" in msg:
                title = f"Permission Awareness ({tool})"
                content = f"Avoid writing to protected or read-only locations when invoking `{tool}`."
                entry = self._upsert_memory(title, content, source="refinement_error_analysis")
                changes.append(f"Added memory: {title}")
                evidence_points.append(f"Permission denied observed for {tool}")

            elif "exceeds" in msg and "limit" in msg:
                title = f"Size Limit Policy ({tool})"
                content = f"Chunk large outputs or use incremental edits rather than single large writes with `{tool}`."
                entry = self._upsert_prompt_directive(title, content, source="refinement_error_analysis")
                changes.append(f"Added prompt directive: {title}")
                evidence_points.append(f"Size limit exceeded on {tool}")

        if not changes:
            return None

        if auto_snapshot:
            self.snapshots.create_snapshot(description=f"Snapshot before refinement {trigger}")

        refinement_event = self.state.record_refinement(
            trigger=trigger,
            changes=changes,
            evidence="; ".join(evidence_points),
            outcome=f"Successfully applied {len(changes)} continual harness updates.",
        )
        return refinement_event

    def _upsert_memory(self, title: str, content: str, source: str) -> HarnessEntry:
        existing = [m for m in self.state.list_entries("memory", enabled_only=False) if m.title == title]
        if existing:
            updated = self.state.update_entry(existing[0].id, content=content)
            return updated or existing[0]
        return self.state.add_entry("memory", title, content, source=source)

    def _upsert_prompt_directive(self, title: str, content: str, source: str) -> HarnessEntry:
        existing = [p for p in self.state.list_entries("prompt", enabled_only=False) if p.title == title]
        if existing:
            updated = self.state.update_entry(existing[0].id, content=content)
            return updated or existing[0]
        return self.state.add_entry("prompt", title, content, source=source)
