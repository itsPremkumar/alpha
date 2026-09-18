"""Programmatic Tool Calling (PTC) Engine.

Collapses multi-step tool interactions into a single Python script turn via
loopback RPC, with automatic stdout truncation and disk spilling for outputs > 50KB.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

MAX_STDOUT_BYTES = 50_000  # 50 KB ceiling before spilling


@dataclass
class ProgrammaticExecutionResult:
    stdout: str
    stderr: str
    return_code: int
    tool_calls_count: int
    duration_seconds: float
    stdout_truncated: bool = False
    spill_path: Optional[str] = None
    warning: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolExecutionBridge:
    """Dispatches programmatic tool invocations to registered backend tools."""

    def __init__(self, workspace_root: Path | str = "."):
        self.workspace_root = Path(workspace_root).resolve()
        self._handlers: dict[str, Callable[..., Any]] = {}
        self.call_count = 0
        self._register_default_handlers()

    def register_handler(self, name: str, handler: Callable[..., Any]) -> None:
        self._handlers[name] = handler

    def _register_default_handlers(self) -> None:
        # Default filesystem & search bridge tools
        self.register_handler("read_file", self._default_read_file)
        self.register_handler("write_file", self._default_write_file)
        self.register_handler("search_files", self._default_search_files)

    def _default_read_file(self, path: str, offset: int = 0, limit: int = 200) -> str:
        target = (self.workspace_root / path).resolve()
        if not target.exists():
            return f"Error: file '{path}' does not exist"
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            selected = lines[offset : offset + limit]
            return "\n".join(selected)
        except Exception as e:
            return f"Error reading file '{path}': {e}"

    def _default_write_file(self, path: str, content: str) -> str:
        target = (self.workspace_root / path).resolve()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} bytes to {path}"
        except Exception as e:
            return f"Error writing file '{path}': {e}"

    def _default_search_files(self, pattern: str, directory: str = ".") -> list[str]:
        target_dir = (self.workspace_root / directory).resolve()
        matches = []
        if target_dir.exists():
            for p in target_dir.rglob(f"*{pattern}*"):
                if p.is_file():
                    matches.append(str(p.relative_to(self.workspace_root)))
                    if len(matches) >= 50:
                        break
        return matches

    def call_tool(self, tool_name: str, *args, **kwargs) -> Any:
        self.call_count += 1
        handler = self._handlers.get(tool_name)
        if not handler:
            return f"Error: tool '{tool_name}' not supported in programmatic sandbox"
        try:
            return handler(*args, **kwargs)
        except Exception as e:
            return f"Error executing tool '{tool_name}': {e}"


class ProgrammaticCallingEngine:
    """Executes Python scripts that invoke tools programmatically with loopback RPC."""

    def __init__(self, workspace_root: Path | str = ".", spill_dir: Path | str | None = None):
        self.workspace_root = Path(workspace_root).resolve()
        self.spill_dir = Path(spill_dir or (self.workspace_root / "cache" / "exec")).resolve()
        self.spill_dir.mkdir(parents=True, exist_ok=True)
        self.bridge = ToolExecutionBridge(workspace_root=self.workspace_root)

    def execute_script(self, script_code: str, timeout_seconds: int = 30) -> ProgrammaticExecutionResult:
        """Execute a Python script with programmatic tools injected into scope."""
        start_time = time.time()
        self.bridge.call_count = 0

        # Build tools namespace proxy
        class ToolsProxy:
            pass

        tools = ToolsProxy()
        def _make_caller(tn: str):
            return lambda *args, **kw: self.bridge.call_tool(tn, *args, **kw)

        for tool_name in ["read_file", "write_file", "search_files"]:
            setattr(tools, tool_name, _make_caller(tool_name))

        # Capture standard output
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        original_stdout, original_stderr = sys.stdout, sys.stderr

        local_vars = {"tools": tools, "workspace_root": str(self.workspace_root)}
        return_code = 0

        try:
            sys.stdout = stdout_capture
            sys.stderr = stderr_capture
            exec(script_code, local_vars, local_vars)
        except Exception as e:
            return_code = 1
            stderr_capture.write(f"ScriptExecutionError: {e}\n")
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

        duration = time.time() - start_time
        raw_stdout = stdout_capture.getvalue()
        raw_stderr = stderr_capture.getvalue()

        # Handle stdout size ceiling and spilling
        stdout_bytes = raw_stdout.encode("utf-8", errors="replace")
        total_bytes = len(stdout_bytes)
        truncated = False
        spill_path = None
        warning = None
        final_stdout = raw_stdout

        if total_bytes > MAX_STDOUT_BYTES:
            truncated = True
            head_len = int(MAX_STDOUT_BYTES * 0.4)
            tail_len = int(MAX_STDOUT_BYTES * 0.6)
            head = stdout_bytes[:head_len].decode("utf-8", errors="replace")
            tail = stdout_bytes[-tail_len:].decode("utf-8", errors="replace")

            # Spill full content to disk
            digest = hashlib.sha256(stdout_bytes).hexdigest()[:12]
            spill_file = self.spill_dir / f"stdout-{digest}.txt"
            spill_file.write_text(raw_stdout, encoding="utf-8")
            spill_path = str(spill_file)

            final_stdout = (
                f"{head}\n\n[... {total_bytes - MAX_STDOUT_BYTES:,} bytes truncated. "
                f"Full output saved to {spill_path} ...]\n\n{tail}"
            )
            warning = f"Output truncated; page full results using read_file('{spill_path}')"

        return ProgrammaticExecutionResult(
            stdout=final_stdout,
            stderr=raw_stderr,
            return_code=return_code,
            tool_calls_count=self.bridge.call_count,
            duration_seconds=round(duration, 4),
            stdout_truncated=truncated,
            spill_path=spill_path,
            warning=warning,
        )
