"""Code-Mode programmatic tool orchestration package inspired by OpenClaw."""

from agent_workspace.tools.code_mode.bridge import CodeExecutionResult, ToolBridge, execute_code_mode
from agent_workspace.tools.code_mode.tool import code_mode_eval

__all__ = ["CodeExecutionResult", "ToolBridge", "execute_code_mode", "code_mode_eval"]
