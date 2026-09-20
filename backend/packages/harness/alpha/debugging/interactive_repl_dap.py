"""Interactive Runtime REPL and Debug Adapter Protocol (DAP) Engine.

Provides stateful execution stepping, breakpoint management, dynamic expression
evaluation, and live variable scope inspection without restarting processes.
"""

from __future__ import annotations

import ast
import io
import linecache
import logging
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class Breakpoint:
    file_path: str
    line_number: int
    condition: Optional[str] = None
    hit_count: int = 0
    enabled: bool = True


@dataclass
class StackFrame:
    frame_id: int
    function_name: str
    file_path: str
    line_number: int
    locals_dict: dict[str, str]
    globals_keys: list[str]


class DebugSession:
    """Stateful debug execution session with tracing hooks."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.breakpoints: dict[tuple[str, int], Breakpoint] = {}
        self.current_frame: Optional[StackFrame] = None
        self.is_paused = False
        self.pause_reason: Optional[str] = None
        self.step_mode: Optional[str] = None  # "over", "into", "out"
        self._execution_lock = threading.Event()
        self.stdout_capture = io.StringIO()
        self.stderr_capture = io.StringIO()
        self.call_stack: list[StackFrame] = []
        self._target_depth = 0

    def add_breakpoint(self, file_path: str, line_number: int, condition: Optional[str] = None) -> Breakpoint:
        norm_path = str(Path(file_path).resolve())
        bp = Breakpoint(file_path=norm_path, line_number=line_number, condition=condition)
        self.breakpoints[(norm_path, line_number)] = bp
        return bp

    def remove_breakpoint(self, file_path: str, line_number: int) -> bool:
        norm_path = str(Path(file_path).resolve())
        return self.breakpoints.pop((norm_path, line_number), None) is not None

    def trace_callback(self, frame, event: str, arg: Any):
        """Python sys.settrace callback."""
        if event == "line":
            file_path = str(Path(frame.f_code.co_filename).resolve())
            line_no = frame.f_lineno
            bp = self.breakpoints.get((file_path, line_no))

            should_break = False
            if bp and bp.enabled:
                if bp.condition:
                    try:
                        should_break = bool(eval(bp.condition, frame.f_globals, frame.f_locals))
                    except Exception:
                        should_break = False
                else:
                    should_break = True

                if should_break:
                    bp.hit_count += 1
                    self.pause_reason = f"Breakpoint hit at {file_path}:{line_no}"

            if self.step_mode == "into" or (self.step_mode == "over" and frame.f_code.co_name == frame.f_code.co_name):
                should_break = True
                self.pause_reason = f"Step pause at {file_path}:{line_no}"
                self.step_mode = None

            if should_break:
                # Capture frame snapshot
                locals_summary = {k: str(v)[:100] for k, v in frame.f_locals.items() if not k.startswith("__")}
                self.current_frame = StackFrame(
                    frame_id=id(frame),
                    function_name=frame.f_code.co_name,
                    file_path=file_path,
                    line_number=line_no,
                    locals_dict=locals_summary,
                    globals_keys=list(frame.f_globals.keys())[:30],
                )
                self.is_paused = True

        return self.trace_callback

    def evaluate_in_current_frame(self, expr: str, frame) -> Any:
        """Evaluate expression in active paused frame."""
        return eval(expr, frame.f_globals, frame.f_locals)


class InteractiveDebugEngine:
    """Orchestrates interactive debugging sessions."""

    def __init__(self):
        self._sessions: dict[str, DebugSession] = {}

    def create_session(self, session_id: str) -> DebugSession:
        session = DebugSession(session_id)
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[DebugSession]:
        return self._sessions.get(session_id)

    def execute_with_breakpoints(
        self,
        session_id: str,
        code_str: str,
        filename: str = "<debug_test>",
    ) -> dict[str, Any]:
        session = self.get_session(session_id) or self.create_session(session_id)
        session.is_paused = False
        session.pause_reason = None

        compiled = compile(code_str, filename, "exec")
        old_trace = sys.gettrace()
        global_scope = {"__name__": "__main__"}
        local_scope = {}

        try:
            sys.settrace(session.trace_callback)
            exec(compiled, global_scope, local_scope)
        finally:
            sys.settrace(old_trace)

        return {
            "session_id": session_id,
            "paused": session.is_paused,
            "pause_reason": session.pause_reason,
            "current_frame": asdict(session.current_frame) if session.current_frame else None,
            "breakpoints": [asdict(bp) for bp in session.breakpoints.values()],
        }
