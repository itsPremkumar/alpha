"""Protocol and data structures for RLM Persistent Python REPL Kernel."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ExecutionStatus = Literal["ok", "error"]


@dataclass
class CellResult:
    """The outcome of executing a code cell in the persistent REPL."""

    status: ExecutionStatus
    stdout: str = ""
    stderr: str = ""
    result: Any = None
    result_repr: str = ""
    error_name: str | None = None
    error_value: str | None = None
    traceback: str | None = None
    display_data: dict[str, Any] = field(default_factory=dict)

    def format_output(self, max_length: int = 10000) -> str:
        """Format the cell result into a clear, agent-readable response."""
        parts: list[str] = []

        if self.stdout.strip():
            parts.append(f"[stdout]\n{self.stdout.rstrip()}")

        if self.stderr.strip():
            parts.append(f"[stderr]\n{self.stderr.rstrip()}")

        if self.status == "error":
            err_msg = f"Error ({self.error_name}): {self.error_value}"
            if self.traceback:
                err_msg += f"\n{self.traceback}"
            parts.append(err_msg)
        elif self.result_repr:
            parts.append(f"[result]\n{self.result_repr}")

        combined = "\n\n".join(parts) if parts else "[Code executed successfully with no output]"
        if len(combined) > max_length:
            combined = combined[:max_length] + f"\n... [Output truncated at {max_length} characters]"
        return combined
