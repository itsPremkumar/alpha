"""StructuredStateCondenser: Formats execution history into an incremental structured state snapshot."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkingState:
    goal: str = ""
    hypothesis: str = ""
    modified_files: list[str] = field(default_factory=list)
    encountered_errors: list[str] = field(default_factory=list)
    completed_milestones: list[str] = field(default_factory=list)
    next_action_focus: str = ""

    def to_markdown(self) -> str:
        lines = ["### Context State Summary"]
        if self.goal:
            lines.append(f"**Goal**: {self.goal}")
        if self.hypothesis:
            lines.append(f"**Working Hypothesis**: {self.hypothesis}")
        if self.modified_files:
            lines.append(f"**Modified Files**: {', '.join(self.modified_files)}")
        if self.encountered_errors:
            lines.append("**Encountered Errors**:")
            for err in self.encountered_errors[-3:]:
                lines.append(f"  - {err}")
        if self.completed_milestones:
            lines.append("**Completed Milestones**:")
            for m in self.completed_milestones[-4:]:
                lines.append(f"  - [x] {m}")
        if self.next_action_focus:
            lines.append(f"**Next Action Focus**: {self.next_action_focus}")
        return "\n".join(lines)


class StructuredStateCondenser:
    """Stage 3: Extracts and summarizes key structured state elements from messages/events."""

    FILE_PATH_PATTERN = re.compile(r"([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9]+)")

    def condense(
        self,
        messages: list[dict[str, Any]],
        existing_state: WorkingState | None = None,
    ) -> WorkingState:
        state = existing_state or WorkingState()

        for msg in messages:
            content = str(msg.get("content", ""))
            role = msg.get("role", "")

            # If user message and goal not set, capture goal
            if role == "user" and not state.goal:
                state.goal = content.strip().splitlines()[0][:120]

            # Detect file modifications
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                func_name = tc.get("function", {}).get("name", "") if isinstance(tc.get("function"), dict) else tc.get("name", "")
                args = tc.get("function", {}).get("arguments", {}) if isinstance(tc.get("function"), dict) else tc.get("args", {})
                if isinstance(args, dict):
                    fp = args.get("TargetFile") or args.get("path") or args.get("file_path") or args.get("target_file")
                    if fp and str(fp) not in state.modified_files:
                        state.modified_files.append(str(fp))

                if func_name in {"write_to_file", "replace_file_content", "edit_file", "hashline_edit"}:
                    state.completed_milestones.append(f"Edited: {args.get('TargetFile') or func_name}")

            # Detect errors in tool output
            if role in {"tool", "observation"} or msg.get("status") in {"error", "failed"}:
                if "error" in content.lower() or "exception" in content.lower() or "traceback" in content.lower():
                    first_err_line = next(
                        (l.strip() for l in content.splitlines() if "error" in l.lower() or "exception" in l.lower()),
                        content[:100],
                    )
                    if first_err_line and first_err_line not in state.encountered_errors:
                        state.encountered_errors.append(first_err_line[:120])

        return state
