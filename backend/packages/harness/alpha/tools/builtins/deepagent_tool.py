"""The deep-agent working plane as one model-facing tool.

``deepagents`` gives its agent six filesystem tools over a pluggable backend
(``ls``, ``read_file``, ``write_file``, ``edit_file``, ``glob``, ``grep``).
Alpha already owns six *sandbox* filesystem tools that need a sandbox acquire
and operate on the host workspace — those are not a scratch space, and a second
copy of them would be a second surface nobody authorized.

So this is one tool with an ``action``, bound to the thread-scoped state
backend in :mod:`alpha.deepagent.state`. One tool, not six: the house rule is
that a new capability is an action on an existing surface rather than a new
registry entry, so ``BUILTIN_TOOLS`` and the generated
``contracts/feature_manifest.json`` stay comparable across releases.

Why the plane is worth having at all: on a long run the agent's findings live
in the message tail, and the tail is what compaction eats. A note written here
is a state channel — it survives compaction, checkpoint/resume and a Gateway
restart, and it is projected back as a bounded index on every request.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command

from alpha.config.deepagent_config import get_deepagent_config
from alpha.deepagent.state import (
    StateWorkspace,
    WorkingFile,
    WorkingFileOp,
    merge_working_files,
)
from alpha.deepagent.workspace import (
    WorkspaceError,
)
from alpha.tools.types import Runtime

#: Actions this tool answers. Anything else is refused by name rather than
#: silently falling back to ``index``, because a caller that asked to ``write``
#: and got a listing has been told a falsehood.
ACTIONS = ("index", "ls", "read", "write", "edit", "delete", "glob", "grep")

_MAX_PATTERN_CHARS = 500
_MAX_PATH_ARG_CHARS = 200
_READ_LIMIT_CEILING = 1000
_GREP_LIMIT_CEILING = 200


class _ActionError(ValueError):
    """A refusal the model can act on: the reason, and the bound it hit."""


def _workspace(runtime: Runtime, files: list[WorkingFile]) -> StateWorkspace:
    """A workspace narrowed to the operator's configured bounds.

    The configuration may only **narrow** the module-level channel ceiling:
    ``StateWorkspace`` refuses a wider limit, because a config that could widen
    the channel would let a long run grow the checkpoint without bound.
    """
    limits = _limits()
    return StateWorkspace(
        files,
        max_files=limits["max_files"],
        max_file_bytes=limits["max_file_bytes"],
        max_total_bytes=limits["max_total_bytes"],
    )


def _current_files(runtime: Runtime) -> list[WorkingFile]:
    files = runtime.state.get("working_files") if runtime.state else None
    if not isinstance(files, list):
        return []
    return [f for f in files if isinstance(f, dict) and isinstance(f.get("path"), str) and isinstance(f.get("content"), str)]


def _limits() -> dict[str, int]:
    config = get_deepagent_config()
    return {
        "max_files": config.max_files,
        "max_file_bytes": config.max_file_bytes,
        "max_total_bytes": config.max_total_bytes,
        "max_summary_chars": config.max_summary_chars,
    }


def _require_path(path: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise _ActionError("'path' is required for this action — a workspace-absolute path like /notes/findings.md")
    if len(path) > _MAX_PATH_ARG_CHARS:
        raise _ActionError(f"'path' exceeds {_MAX_PATH_ARG_CHARS} characters")
    return path


def _require_content(content: str) -> str:
    if not isinstance(content, str) or not content:
        raise _ActionError("'content' is required and must be non-empty for 'write'")
    return content


def _clamp(value: object, *, default: int, ceiling: int, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(minimum, min(ceiling, value))


def _payload(action: str, **fields: Any) -> str:
    """Serialize a reply.

    ``action`` is the first positional parameter and is *also* the name of a
    key some callers pass in ``fields`` — which is a collision by construction,
    not an accident. The fields key always wins because it is the more specific
    value (the action the caller actually requested, which for an
    ``unknown_action`` refusal is the one they asked for, not the one we run).
    """
    body: dict[str, Any] = {"action": action}
    body.update(fields)
    return json.dumps(body, ensure_ascii=False)


def _report(workspace: StateWorkspace, action: str, *, changed: bool, **fields: Any) -> str:
    """Every reply carries the plane's measured size and its live bounds.

    The bounds are in every reply, including successes, because a refusal that
    names only "too big" sends the model guessing at the number.
    """
    return _payload(
        action,
        changed=changed,
        files=len(workspace.files),
        total_bytes=workspace.total_bytes,
        limits=_limits(),
        **fields,
    )


def _refusal(exc: WorkspaceError) -> str:
    """A typed refusal in the model's own vocabulary, with the real reason.

    Never a bare ``invalid arguments``: the typed errors from
    :mod:`alpha.deepagent.workspace` already distinguish "no such file" from
    "path escapes" from "over the byte ceiling", and collapsing them throws
    away the one thing that lets the model correct itself.
    """
    return _payload("error", changed=False, error=type(exc).__name__, reason=str(exc), limits=_limits())


def _deepagent_workspace(
    runtime: Runtime,
    action: str = "index",
    path: str = "",
    content: str = "",
    summary: str = "",
    pattern: str = "",
    old: str = "",
    new: str = "",
    replace_all: bool = False,
    offset: int = 0,
    limit: int = 400,
    max_results: int = 50,
) -> str | Command:
    """Read and write your own scratch files for this thread.

    A working plane is your own bounded filesystem living in the thread's state.
    Write findings, drafts, constraints, decisions and open questions here
    instead of holding them in the answer, because a long task's message tail
    is exactly what context compaction discards — a file here survives it,
    survives a checkpoint resume, and is listed back to you on every step.

    It is *your* record of your own work, not a verified fact store and not user
    memory: nothing here has been checked. Say so when you rely on it.

    Actions:
        index: Manifest of every file — path, size, your one-line purpose. No bodies.
        ls: Rows under a path ('/' lists everything).
        read: One file in pages of `limit` lines starting at `offset`.
        write: Create or replace a file, replacing its content wholesale.
        edit: Replace `old` with `new` inside one existing file.
        delete: Remove one file.
        glob: Paths matching a '/' glob such as /notes/*.md.
        grep: Lines matching a regular expression across the plane.

    Rules that keep this honest:
        Every bound is a refusal, never a clamp. Write a smaller file, or delete
        one, rather than expecting the content to be trimmed for you.
        `edit` refuses an empty `old`, a missing occurrence and an ambiguous
        multi-occurrence replace unless `replace_all=True` — a half-applied edit
        is worse than a refused one.
        A deleted path stays deleted: deletion is an operation, not a tombstone,
        so no older write can resurrect it.
        Pass `summary` on write so the index tells you *which* note to open.

    Args:
        action: One of index, ls, read, write, edit, delete, glob, grep.
        path: Workspace-absolute path such as /notes/findings.md.
        content: Full content for `write`; unused for every read action.
        summary: Optional one-line purpose for this file, shown in the index.
        pattern: Glob pattern for `glob`, regular expression for `grep`.
        old: Text to find for `edit`.
        new: Replacement text for `edit`.
        replace_all: Replace every occurrence for `edit` instead of refusing an ambiguous match.
        offset: Zero-based first line for `read`.
        limit: Lines per `read` page.
        max_results: Most matches for `grep`.
    """
    normalized = (action or "index").strip().lower()
    if normalized not in ACTIONS:
        return _payload("refused", error="unknown_action", requested=str(action), allowed=list(ACTIONS), changed=False)

    current = _current_files(runtime)
    workspace = _workspace(runtime, current)
    try:
        if normalized == "index":
            return _report(workspace, normalized, changed=False, index=workspace.index())

        if normalized == "ls":
            rows = [{"path": row_path, "kind": kind, "bytes": size} for row_path, kind, size in workspace.ls(path or "/")]
            return _report(workspace, normalized, changed=False, rows=rows)

        if normalized == "read":
            target = _require_path(path)
            page = _clamp(limit, default=400, ceiling=_READ_LIMIT_CEILING)
            start = max(0, offset if isinstance(offset, int) and not isinstance(offset, bool) else 0)
            text = workspace.read(target, offset=start, limit=page)
            next_offset = start + page if text else None
            return _report(workspace, normalized, changed=False, path=target, text=text, next_offset=next_offset)

        if normalized == "write":
            target = _require_path(path)
            body = _require_content(content)
            declared = summary if isinstance(summary, str) else ""
            workspace.write(target, body, declared)
            return _commit(
                runtime,
                current,
                [{"path": target, "content": body, **({"summary": " ".join(declared.split())[: _limits()["max_summary_chars"]]} if declared.strip() else {})}],
                normalized,
                path=target,
                bytes=len(body.encode("utf-8")),
            )

        if normalized == "edit":
            target = _require_path(path)
            before = workspace.read(target, limit=10_000)
            workspace.edit(target, old, new, bool(replace_all))
            # The channel delta carries the file's *whole* new content: an edit
            # is not a replayable instruction (the file may itself have been
            # written by another step), and a stale instruction applied twice
            # is corruption. `before` is only used to detect a real change.
            after = workspace.read(target, limit=10_000)
            if after == before:
                return _report(workspace, normalized, changed=False, path=target, note="replacement produced identical content")
            return _commit(
                runtime,
                current,
                [{"path": target, "content": after, **({"summary": _existing_summary(current, target)} if _existing_summary(current, target) else {})}],
                normalized,
                path=target,
                bytes=len(after.encode("utf-8")),
                replaced=None if replace_all else 1,
            )

        if normalized == "delete":
            target = _require_path(path)
            removed = workspace.delete(target)
            if not removed:
                return _report(workspace, normalized, changed=False, path=target, deleted=False)
            return _commit(runtime, current, [{"path": target, "content": None}], normalized, path=target, deleted=True)

        if normalized == "glob":
            if not isinstance(pattern, str) or not pattern.strip():
                raise _ActionError("'pattern' is required for 'glob' — a '/' glob such as /notes/*.md")
            if len(pattern) > _MAX_PATTERN_CHARS:
                raise _ActionError(f"'pattern' exceeds {_MAX_PATTERN_CHARS} characters")
            return _report(workspace, normalized, changed=False, paths=workspace.glob(pattern))

        # grep
        if not isinstance(pattern, str) or not pattern.strip():
            raise _ActionError("'pattern' is required for 'grep' — a regular expression")
        if len(pattern) > _MAX_PATTERN_CHARS:
            raise _ActionError(f"'pattern' exceeds {_MAX_PATTERN_CHARS} characters")
        cap = _clamp(max_results, default=50, ceiling=_GREP_LIMIT_CEILING)
        matches = [{"path": match.path, "line": match.line, "text": match.text} for match in workspace.grep(pattern, path or "/", max_results=cap)]
        return _report(workspace, normalized, changed=False, matches=matches, truncated=len(matches) >= cap)
    except _ActionError as exc:
        return _payload("error", changed=False, error="invalid_request", reason=str(exc), limits=_limits())
    except WorkspaceError as exc:
        return _refusal(exc)


def _existing_summary(files: list[WorkingFile], path: str) -> str:
    for file in files:
        if file.get("path") == path and isinstance(file.get("summary"), str):
            return file["summary"]
    return ""


def _commit(
    runtime: Runtime,
    current: list[WorkingFile],
    ops: list[WorkingFileOp],
    action: str,
    **fields: Any,
) -> Command:
    """Emit the minimal channel delta and report the plane it produces.

    The reply is built from ``merge_working_files(current, ops)`` — the same
    function the channel reducer runs — so the numbers the model reads are the
    numbers that will be stored, including any eviction the bounds forced.
    Reporting the pre-merge state instead would let a write claim a file that
    the reducer then dropped for capacity.
    """
    merged = merge_working_files(current, ops)
    predicted = _workspace(runtime, merged)
    kept = {file["path"] for file in merged}
    evicted = [file["path"] for file in current if file["path"] not in kept]
    reply = _report(predicted, action, changed=True, evicted=evicted, **fields)
    return Command(
        update={
            "working_files": ops,
            "messages": [ToolMessage(content=reply, tool_call_id=runtime.tool_call_id)],
        }
    )


async def _adeepagent_workspace(
    runtime: Runtime,
    action: str = "index",
    path: str = "",
    content: str = "",
    summary: str = "",
    pattern: str = "",
    old: str = "",
    new: str = "",
    replace_all: bool = False,
    offset: int = 0,
    limit: int = 400,
    max_results: int = 50,
) -> str | Command:
    # No `run_file_io` hop: every operation is an in-memory read of a state
    # channel, so there is no blocking syscall to move off the event loop.
    return _deepagent_workspace(
        runtime,
        action,
        path,
        content,
        summary,
        pattern,
        old,
        new,
        replace_all,
        offset,
        limit,
        max_results,
    )


# Both execution modes are required: Gateway runs asynchronously, while
# AlphaClient.stream drives a synchronous graph.
deepagent_workspace_tool = StructuredTool.from_function(
    _deepagent_workspace,
    coroutine=_adeepagent_workspace,
    name="deepagent_workspace",
)


def append_deepagent_tools(tools: list, app_config, *, existing_names: set[str] | None = None) -> None:
    """Bind the working-plane tool when the feature is enabled.

    Gated on ``deepagent.enabled`` exactly like ``task_continuity.enabled``
    gates ``task_note``, so a deployment that wants the previous behaviour sets
    one key and gets it — no code path, no partial feature.
    """
    config = getattr(app_config, "deepagent", None)
    if config is None or getattr(config, "enabled", False) is not True:
        return
    names = {getattr(t, "name", "") for t in tools} | (existing_names or set())
    if deepagent_workspace_tool.name in names:
        return
    tools.append(deepagent_workspace_tool)
    names.add(deepagent_workspace_tool.name)
