"""Repository context files: project files that shape every conversation.

Walks up from a working directory looking for conventional context files
(``AGENTS.md``, ``CONTEXT.md``, ``.agent_workspace/CONTEXT.md``) and returns a
bounded excerpt. Pure filesystem reads with hard caps — safe to call from
per-turn paths; callers decide where the excerpt rides.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CONTEXT_FILENAMES = ("AGENTS.md", "CONTEXT.md")
CONTEXT_SUBPATHS = (".agent_workspace/CONTEXT.md", ".agent_workspace/AGENTS.md")
MAX_FILE_CHARS = 6000
MAX_TOTAL_CHARS = 12000
MAX_SEARCH_DEPTH = 6


def find_context_files(start_dir: str | Path) -> list[Path]:
    """Walk up at most MAX_SEARCH_DEPTH levels collecting context files."""
    try:
        current = Path(start_dir).resolve()
    except OSError:
        return []
    found: list[Path] = []
    for _ in range(MAX_SEARCH_DEPTH):
        for name in CONTEXT_FILENAMES:
            candidate = current / name
            if candidate.is_file():
                found.append(candidate)
        for sub in CONTEXT_SUBPATHS:
            candidate = current / sub
            if candidate.is_file():
                found.append(candidate)
        if (current / ".git").exists():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    seen: set[str] = set()
    unique: list[Path] = []
    for path in found:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def load_context_files(start_dir: str | Path, *, max_total_chars: int = MAX_TOTAL_CHARS) -> str:
    """Read and cap context files into one excerpt block (empty when none)."""
    parts: list[str] = []
    budget = max_total_chars
    for path in find_context_files(start_dir):
        if budget <= 0:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        header = f"--- {path.name} ({path.parent}) ---\n"
        allowance = max(0, budget - len(header))
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + "\n…[truncated]"
        if len(text) > allowance:
            text = text[:allowance] + "\n…[truncated]"
        parts.append(header + text)
        budget -= len(header) + len(text)
    return "\n\n".join(parts)


def resolve_runtime_repo_root(runtime) -> str | None:
    """Best-effort repo root from a LangGraph runtime; None when absent.

    Reads only the explicit ``repo_root`` context/config key set by the
    caller — never probes the filesystem here. Never raises.
    """
    try:
        if runtime is None:
            return None
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            value = context.get("repo_root")
            if isinstance(value, str) and value.strip():
                return value.strip()
        config = getattr(runtime, "config", None)
        if isinstance(config, dict):
            configurable = config.get("configurable")
            if isinstance(configurable, dict):
                value = configurable.get("repo_root")
                if isinstance(value, str) and value.strip():
                    return value.strip()
    except Exception:
        return None
    return None
