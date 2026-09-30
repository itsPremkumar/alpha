"""The recorded execution: one real command run, and the proof it really ran.

A self-verifying loop is only worth having if its evidence is real, so this
module draws the line once and refuses to move it: **the only way to obtain a
:class:`RecordedExecution` is to have actually executed the command.**

How that is enforced, from the outside in:

* :class:`RecordedExecution` carries a private seal token that is module-private.
  Hand-constructing one — ``RecordedExecution(command="pytest", output="12 passed")``
  — raises ``TypeError``. The dataclass is not a bag of fields; it is a
  constructor nobody but :func:`record_execution` may complete.
* :func:`record_execution` refuses an empty command or an empty ``tool_call_id``,
  so "an execution" always names the call it came from.
* :class:`BashToolExecutor` produces its record the way the graph does: it
  invokes the **real** ``bash`` tool, wraps the real result into the real
  ``AIMessage``/``ToolMessage`` pair, and then hands that state to the **real**
  ``alpha.subagents.executor._harvest_bash_executions``. The evidence dict the
  acceptance checker anchors to is therefore the production harvester's own
  output, not a second implementation of it that can drift.

What this deliberately does not do: it does not parse, summarise, or
reconstruct a test summary. A tail with no summary shape in it stays
unshaped, and :mod:`alpha.subagents.acceptance_checks` renders that
UNVERIFIED — which is the correct answer for a command that produced no
recognisable result.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = [
    "BashToolExecutor",
    "CommandExecutor",
    "RecordedExecution",
    "record_execution",
]

#: Module-private seal. A ``RecordedExecution`` cannot be completed without it,
#: so a caller cannot mint evidence by constructing the dataclass directly. The
#: object identity is the whole check: the value never leaves this module.
_SEAL = object()


def record_execution(
    *,
    command: str,
    tool_call_id: str,
    tool_name: str,
    output: str,
    status: str,
    shell_persistent: bool | None,
    command_truncated: bool = False,
    status_marker: str | None = None,
) -> RecordedExecution:
    """Seal one executed command as a :class:`RecordedExecution`.

    Every field is validated here rather than at read time, so an invalid record
    is a construction error instead of a silent UNVERIFIED three layers later.

    ``shell_persistent`` is tri-state on purpose and mirrors the harvester: True
    (one shared shell), False (one-shot sessions), and ``None`` (unknown — the
    producing sandbox could not be identified or never declared its session
    semantics). ``None`` is the fail-closed case: the acceptance checker renders
    it UNVERIFIED, because an earlier call in a shared shell could have mutated
    the state this run executed in.
    """
    if not isinstance(command, str) or not command.strip():
        raise ValueError("a recorded execution needs a non-empty command")
    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
        raise ValueError("a recorded execution needs the tool_call_id of the call that produced it; a transcript without one is not a recording")
    if not isinstance(output, str):
        raise ValueError(f"recorded execution output must be text, got {type(output).__name__}")
    if shell_persistent is not None and not isinstance(shell_persistent, bool):
        raise ValueError("shell_persistent is tri-state: True, False, or None for unknown")
    return RecordedExecution(
        command=command,
        tool_call_id=tool_call_id,
        tool_name=tool_name or "bash",
        output=output,
        status=status or "unknown",
        status_marker=status_marker,
        command_truncated=bool(command_truncated),
        shell_persistent=shell_persistent,
        _seal=_SEAL,
    )


@dataclass(frozen=True)
class RecordedExecution:
    """One real command execution, in the shape the acceptance checker consumes.

    Construct this through :func:`record_execution`. The ``_seal`` field has no
    default on purpose: a direct ``RecordedExecution(...)`` raises, which is what
    makes "fed a synthesised transcript" a construction error instead of a
    silently forged pass.
    """

    command: str
    tool_call_id: str
    tool_name: str
    output: str
    status: str
    status_marker: str | None
    command_truncated: bool
    shell_persistent: bool | None
    _seal: object = field(repr=False, compare=False)

    def to_acceptance_execution(self) -> dict[str, Any]:
        """Render as the dict ``check_acceptance_criteria(bash_executions=...)`` reads.

        Key-for-key the harvester's own shape. Written out here rather than
        reused from the harvester so the field list is visible at the point it
        matters; :class:`BashToolExecutor` does not go through this method at all
        (it hands the real harvester the real state), so production never depends
        on this rendering being complete.
        """
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "command": self.command,
            "command_truncated": self.command_truncated,
            "output_tail": self.output,
            "status": self.status,
            "status_marker": self.status_marker,
            "shell_persistent": self.shell_persistent,
        }


@runtime_checkable
class CommandExecutor(Protocol):
    """Runs a verification command and returns what actually happened.

    The controller depends on this and nothing else about execution, which is
    what lets a test substitute a stand-in for the *sandbox* while leaving the
    *recording* real. A substitute that returns a dict, a string, or anything
    that is not a sealed :class:`RecordedExecution` is not an executor result and
    the controller treats it as no evidence at all — never as a pass.
    """

    async def execute(self, command: str) -> RecordedExecution: ...


class BashToolExecutor:
    """Run a command through the real ``bash`` tool and harvest it for real.

    Three production calls, in order, and no shortcuts past any of them:

    1. ``alpha.sandbox.tools.bash_tool`` — the same tool object the agent's
       toolset binds, so path validation, the cwd prefix, secret masking, output
       truncation, and the trailing ``Exit Code: N`` marker are all the real
       ones.
    2. the result is wrapped into the ``AIMessage``/``ToolMessage`` pair the
       graph's tool node would have produced, so the record is a real message
       rather than a string somebody typed.
    3. ``alpha.subagents.executor._harvest_bash_executions`` derives the evidence
       dict — including the exit-marker status and the sandbox's
       ``persistent_shell_sessions`` provenance stamp.

    If any step cannot complete, this raises. It never returns a partial record:
    an executor that cannot produce a real one must let the controller report
    UNVERIFIED, not hand it something that looks like evidence.
    """

    def __init__(self, runtime: Any, *, tool_name: str = "bash") -> None:
        self._runtime = runtime
        self._tool_name = tool_name

    async def execute(self, command: str) -> RecordedExecution:
        from langchain_core.messages import AIMessage, ToolMessage

        from alpha.sandbox.tools import bash_tool

        tool_call_id = f"alpha-verification-{id(command):x}"
        output = bash_tool.invoke({"command": command, "description": "verification loop"}, config=_tool_config(self._runtime))  # type: ignore[arg-type]
        # The graph wraps a tool result this way; reproducing the wrap (rather
        # than inventing an evidence dict) is what lets the production harvester
        # do the rest, including the exit-marker status derivation.
        state = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"name": self._tool_name, "args": {"command": command}, "id": tool_call_id, "type": "tool_call"}],
                ),
                ToolMessage(content=output, tool_call_id=tool_call_id, name=self._tool_name),
            ],
            # The harvester resolves the producing sandbox's shell-persistence
            # stamp from the state that carried the evidence, so the sandbox that
            # actually ran the command must be identifiable here.
            "sandbox": _sandbox_state(self._runtime),
        }

        from alpha.subagents.executor import _harvest_bash_executions

        harvested = _harvest_bash_executions(state)  # type: ignore[arg-type]
        if not harvested:
            raise RuntimeError("the production bash harvester produced no evidence for an executed command")
        entry = harvested[-1]
        return record_execution(
            command=command,
            tool_call_id=str(entry.get("tool_call_id") or tool_call_id),
            tool_name=str(entry.get("tool_name") or self._tool_name),
            output=str(entry.get("output_tail") or ""),
            status=str(entry.get("status") or "unknown"),
            status_marker=entry.get("status_marker") if isinstance(entry.get("status_marker"), str) else None,
            command_truncated=bool(entry.get("command_truncated")),
            shell_persistent=entry.get("shell_persistent") if isinstance(entry.get("shell_persistent"), bool) else None,
        )


def _tool_config(runtime: Any) -> dict[str, Any]:
    """Minimal runnable config so an injected ``Runtime`` reaches the tool.

    The bash tool takes ``runtime: Runtime`` as a bare required first parameter,
    which LangChain injects from the runnable config. Calling the tool outside a
    graph therefore needs the context to be supplied explicitly, or the tool
    would run with ``runtime=None`` and fail sandbox acquisition.
    """
    context = getattr(runtime, "context", None)
    configurable = dict(context) if isinstance(context, Mapping) else {}
    return {"configurable": configurable}


def _sandbox_state(runtime: Any) -> dict[str, Any] | None:
    """The ``{"sandbox_id": ...}`` record for the sandbox that ran the command.

    Read from the runtime's own state, after the tool has run, so it names the
    sandbox that actually executed rather than one the caller hoped for.
    """
    state = getattr(runtime, "state", None)
    if not isinstance(state, Mapping):
        return None
    sandbox = state.get("sandbox")
    if isinstance(sandbox, Mapping):
        sandbox_id = sandbox.get("sandbox_id")
        if isinstance(sandbox_id, str) and sandbox_id:
            return {"sandbox_id": sandbox_id}
    return None
