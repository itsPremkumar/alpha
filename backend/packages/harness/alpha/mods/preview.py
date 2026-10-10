"""Deterministic impact preview — the dry run behind a hold.

Claude Code's blast-radius mod does not merely classify a risky command; it
*shows what it would change* before asking. ``git clean -n``, ``git status
--porcelain``, ``git log HEAD..origin/main``, ``showmigrations``: the report comes
from the tools' own dry-run flags, and pressing Cancel is a decision the operator
makes having seen it.

This module is that dry run, with one deliberate difference: **it never runs
anything.** Alpha already has a guarded shell policy, and a mod that shells out
to compute a preview would be a mod that executes the thing it is previewing.
Instead the impact is computed from the command text and a bounded, read-only
filesystem walk.

The honest half is the ``ImpactPreview.measurable`` flag. A preview that cannot
be computed says so, because a confident-sounding empty preview on a destructive
command is worse than no preview: the operator presses Proceed believing nothing
is at stake.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

#: Upper bound on how many paths a preview will enumerate. A ``rm -rf`` on a
#: home directory can name millions of files; the preview must stay instant.
MAX_PREVIEW_PATHS = 50

#: Upper bound on the total path bytes a preview will emit.
MAX_PREVIEW_BYTES = 4000


class ImpactKind(StrEnum):
    """What category of effect the preview measured."""

    FILE_DELETE = "file_delete"
    FILE_WRITE = "file_write"
    FILE_MOVE = "file_move"
    GIT_DISCARD = "git_discard"
    SQL_SCHEMA = "sql_schema"
    NETWORK = "network"
    PACKAGE_INSTALL = "package_install"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ImpactPreview:
    """A bounded, read-only estimate of what an action would touch.

    ``measurable`` is the load-bearing field. ``False`` means the preview could
    not be computed — it does *not* mean "nothing is at stake".
    """

    kind: str
    measurable: bool
    summary: str
    affected_paths: tuple[str, ...] = ()
    truncated: bool = False
    estimated_bytes: int | None = None
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "measurable": self.measurable,
            "summary": self.summary,
            "affected_paths": list(self.affected_paths),
            "truncated": self.truncated,
            "estimated_bytes": self.estimated_bytes,
            "reason": self.reason,
            "evidence": dict(self.evidence),
        }


def _clip(paths: list[str], budget: int = MAX_PREVIEW_BYTES) -> tuple[tuple[str, ...], bool]:
    """Clip a path list to the byte budget, reporting whether it was clipped."""
    kept: list[str] = []
    used = 0
    for path in paths[:MAX_PREVIEW_PATHS]:
        cost = len(path) + 1
        if used + cost > budget:
            return tuple(kept), True
        kept.append(path)
        used += cost
    return tuple(kept), len(paths) > len(kept)


def _walk_paths(patterns: list[str]) -> tuple[tuple[str, ...], bool]:
    """Enumerate existing paths matching ``patterns``, bounded.

    The walk is deliberately shallow and capped: it answers "roughly how much is
    here", not "exactly what is here". A directory contributes itself and its
    immediate children only, which keeps the preview O(depth) rather than
    O(filesystem).
    """
    from pathlib import Path

    found: list[str] = []
    truncated = False
    for pattern in patterns[:8]:
        if len(pattern) > 512:
            truncated = True
            continue
        try:
            candidate = Path(pattern)
            if candidate.is_file() or candidate.is_symlink():
                found.append(str(candidate))
                continue
            if candidate.is_dir():
                found.append(str(candidate))
                try:
                    for child in sorted(candidate.iterdir())[:MAX_PREVIEW_PATHS]:
                        found.append(str(child))
                        if len(found) >= MAX_PREVIEW_PATHS:
                            truncated = True
                            break
                except OSError:
                    continue
            else:
                # A glob pattern that matches nothing yet: report it as named,
                # never as "nothing here".
                found.append(f"{pattern} (not found)")
        except OSError:
            truncated = True
    clipped, clip_truncated = _clip(found)
    return clipped, truncated or clip_truncated


def _split_targets(raw: str) -> list[str]:
    """Split a shell argument string, preserving backslashes.

    ``shlex.split(posix=True)`` is *wrong here*: it treats ``\\`` as an escape
    character, so a Windows path ``C:\\Users\\x`` comes back as ``C:Usersx`` and
    the preview then enumerates a path that does not exist. Quotes are honored
    for paths that contain spaces; backslashes are left alone, because a path
    separator is far more likely than an escape.
    """
    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in str(raw or ""):
        if quote:
            if char == quote:
                quote = None
            else:
                current.append(char)
            continue
        if char in ("'", '"'):
            quote = char
            continue
        if char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


def _extract_token_values(command: str, flag: str) -> list[str]:
    """Pull values that follow ``flag`` out of a shell command string."""
    pattern = re.compile(rf"(?:^|\s){re.escape(flag)}(?:=|\s+)([^\s|;&]+)")
    return pattern.findall(command or "")


_RM_PATTERN = re.compile(r"\brm\b(?P<rest>.*)$")
_GIT_CLEAN = re.compile(r"\bgit\s+clean\s+(?P<args>.*)$", re.IGNORECASE)
_GIT_RESET = re.compile(r"\bgit\s+reset\s+(?P<args>.*)$", re.IGNORECASE)
_SQL_DROP = re.compile(r"\b(drop|truncate)\s+(table|database|schema)\s+(?P<target>[a-zA-Z0-9_.\"]+)", re.IGNORECASE)
_INSTALL_PATTERN = re.compile(r"\b(pip|npm|pnpm|yarn|apt|brew|choco|winget|cargo|go)\s+(install|add|i)\s+(?P<pkgs>.+)$", re.IGNORECASE)


def _preview_delete(command: str) -> ImpactPreview:
    match = _RM_PATTERN.search(command or "")
    if not match:
        return ImpactPreview(kind=ImpactKind.FILE_DELETE, measurable=False, summary="Delete command could not be parsed", reason="UNPARSED_COMMAND")
    # Everything after ``rm`` is flags and targets; flags are dropped so the
    # preview names paths, not options. A bare ``rm`` with nothing after it lands
    # on NO_TARGETS, which is a different finding from an unparseable command.
    tokens = _split_targets(match.group("rest") or "")
    paths = [t for t in tokens if not t.startswith("-") and t not in {"2>", "&"}]
    if not paths:
        return ImpactPreview(kind=ImpactKind.FILE_DELETE, measurable=False, summary="Delete command names no paths", reason="NO_TARGETS")
    enumerated, truncated = _walk_paths(paths)
    return ImpactPreview(
        kind=ImpactKind.FILE_DELETE,
        measurable=True,
        summary=f"Would remove {len(enumerated)} path(s) under: {', '.join(paths[:4])}",
        affected_paths=enumerated,
        truncated=truncated,
        reason="",
        evidence={"targets": paths[:8]},
    )


def _preview_git_clean(command: str) -> ImpactPreview:
    match = _GIT_CLEAN.search(command or "")
    args = match.group("args") if match else ""
    forced = bool(re.search(r"-[a-zA-Z]*f", args or "")) or "--force" in (args or "")
    return ImpactPreview(
        kind=ImpactKind.GIT_DISCARD,
        measurable=True,
        summary=("Would delete untracked files and directories in the working tree" if forced else "Would list untracked files (dry run; nothing removed)"),
        affected_paths=(),
        truncated=False,
        reason="" if forced else "DRY_RUN_NO_CHANGE",
        evidence={"forced": forced, "tool": "git"},
    )


def _preview_git_reset(command: str) -> ImpactPreview:
    match = _GIT_RESET.search(command or "")
    args = match.group("args") if match else ""
    hard = "--hard" in (args or "")
    return ImpactPreview(
        kind=ImpactKind.GIT_DISCARD,
        measurable=True,
        summary=("Would discard all uncommitted changes in the working tree and index" if hard else "Would move HEAD without touching the working tree"),
        affected_paths=(),
        truncated=False,
        reason="" if hard else "NO_WORKING_TREE_CHANGE",
        evidence={"hard": hard, "tool": "git"},
    )


def _preview_sql(command: str) -> ImpactPreview:
    match = _SQL_DROP.search(command or "")
    target = match.group("target").strip('"') if match else ""
    return ImpactPreview(
        kind=ImpactKind.SQL_SCHEMA,
        measurable=True,
        summary=f"Would permanently remove schema object '{target or '(unnamed)'}' and every row it holds",
        affected_paths=(),
        truncated=False,
        reason="",
        evidence={"target": target},
    )


def _preview_install(command: str) -> ImpactPreview:
    match = _INSTALL_PATTERN.search(command or "")
    packages = _split_targets(match.group("pkgs") or "") if match else []
    return ImpactPreview(
        kind=ImpactKind.PACKAGE_INSTALL,
        measurable=True,
        summary=f"Would install {len(packages)} package(s): {', '.join(packages[:6])}",
        affected_paths=(),
        truncated=len(packages) > 6,
        reason="THIRD_PARTY_CODE",
        evidence={"packages": packages[:16]},
    )


def _preview_file_write(tool_args: dict[str, Any]) -> ImpactPreview:
    target = str(tool_args.get("TargetFile") or tool_args.get("path") or tool_args.get("file_path") or tool_args.get("absolute_path") or "").strip()
    content = tool_args.get("ReplacementContent") or tool_args.get("CodeContent") or tool_args.get("content") or ""
    size = len(str(content).encode("utf-8")) if content else 0
    return ImpactPreview(
        kind=ImpactKind.FILE_WRITE,
        measurable=bool(target),
        summary=(f"Would overwrite {target} ({size} bytes of new content)" if target else "Write tool names no target file"),
        affected_paths=(target,) if target else (),
        truncated=False,
        estimated_bytes=size or None,
        reason="" if target else "NO_TARGET",
    )


def preview_impact(tool_name: str, tool_args: dict[str, Any]) -> ImpactPreview:
    """Estimate what a tool invocation would change, without running it.

    Order matters: the first matching branch names the *most* consequential
    effect, because a command that both writes a file and installs a package is
    an install.
    """
    lowered = str(tool_name or "").lower()
    args = tool_args if isinstance(tool_args, dict) else {}

    # File-writing tools are classified before any shell parsing, so a write of a
    # script that contains "rm -rf" is reported as the write it is.
    if lowered in {"write_to_file", "replace_file_content", "edit_file", "apply_patch", "str_replace_editor"} or "write" in lowered or "edit" in lowered or "patch" in lowered:
        preview = _preview_file_write(args)
        if preview.measurable:
            return preview

    command = str(args.get("CommandLine") or args.get("command") or args.get("cmd") or args.get("script") or "").strip()
    if lowered in {"run_command", "bash", "shell", "terminal", "powershell", "cmd", "execute_command"} and command:
        if _SQL_DROP.search(command):
            return _preview_sql(command)
        if re.search(r"\brm\b", command):
            # Any ``rm`` goes to the delete preview, including a bare one: it
            # must report *why* it has no targets rather than being lumped into
            # "unclassified command", because the two call for different fixes.
            return _preview_delete(command)
        if _GIT_CLEAN.search(command):
            return _preview_git_clean(command)
        if _GIT_RESET.search(command):
            return _preview_git_reset(command)
        if re.search(r"\bgit\s+push\b.*(--force|-f\b)", command, re.IGNORECASE):
            return ImpactPreview(
                kind=ImpactKind.GIT_DISCARD,
                measurable=True,
                summary="Would force-push, overwriting remote history others may have based work on",
                affected_paths=(),
                truncated=False,
                reason="REMOTE_HISTORY_REWRITE",
            )
        if _INSTALL_PATTERN.search(command):
            return _preview_install(command)
        if re.search(r"\b(curl|wget|nc|ncat|ssh|scp)\b", command, re.IGNORECASE):
            return ImpactPreview(
                kind=ImpactKind.NETWORK,
                measurable=True,
                summary="Would make an outbound network request",
                affected_paths=(),
                truncated=False,
                reason="NETWORK_EGRESS",
            )
        return ImpactPreview(
            kind=ImpactKind.UNKNOWN,
            measurable=False,
            summary="Shell command effect was not classified",
            reason="UNCLASSIFIED_COMMAND",
            evidence={"command": command[:200]},
        )

    return ImpactPreview(
        kind=ImpactKind.UNKNOWN,
        measurable=False,
        summary=f"No impact model for tool '{tool_name or 'unknown'}'",
        reason="NO_IMPACT_MODEL",
    )
