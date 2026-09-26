"""Path layout for the L1 store (records, provenance log, quota state, persona).

All L1 state lives below a single root (``memory.l1.storage_path`` or the
agent runtime home), mirroring the existing per-user DeerMem layout:

```text
{root}/users/{user_id}/l1/records/{agent_safe}.json
{root}/users/{user_id}/l1/generation-log/YYYY-MM-DD.jsonl
{root}/users/{user_id}/l1/quota.json
{root}/users/{user_id}/l1/persona/{agent_safe}.md
```

``agent_safe`` is ``__default__`` when ``agent_name`` is None (the same
reserved bucket name DeerMem uses for the default agent).
"""

from __future__ import annotations

import re
from pathlib import Path

#: Reserved bucket name for the default/global agent (DeerMem convention).
DEFAULT_AGENT_BUCKET = "__default__"

_UNSAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_segment(value: str | None) -> str:
    """Sanitize one path segment (user id or agent name) without collisions
    into parent directories: everything non ``[A-Za-z0-9._-]`` folds to ``_``."""
    if not value:
        return DEFAULT_AGENT_BUCKET
    cleaned = _UNSAFE_SEGMENT_RE.sub("_", value).strip("._") or "x"
    return cleaned[:100]


def agent_bucket(agent_name: str | None) -> str:
    """Map ``agent_name`` to its on-disk bucket (``None`` -> ``__default__``)."""
    if not agent_name:
        return DEFAULT_AGENT_BUCKET
    return safe_segment(agent_name)


def l1_root(storage_path: str | None) -> Path:
    """Resolve the L1 state root.

    ``None`` falls back to the agent runtime home (the same zero-config base
    DeerMem uses), so the default layout follows the process-wide state dir.
    """
    if storage_path:
        return Path(storage_path).expanduser().resolve()
    from alpha.config.runtime_paths import runtime_home

    return Path(runtime_home())


def user_dir(root: Path, user_id: str | None) -> Path:
    """Per-user L1 directory under ``root``."""
    return root / "users" / safe_segment(user_id or "default") / "l1"


def records_path(root: Path, user_id: str | None, agent_name: str | None) -> Path:
    """Path of one scope's record document."""
    return user_dir(root, user_id) / "records" / f"{agent_bucket(agent_name)}.json"


def generation_log_path(root: Path, user_id: str | None, day: str) -> Path:
    """Path of one day's append-only generation log (``day`` is ``YYYY-MM-DD``)."""
    return user_dir(root, user_id) / "generation-log" / f"{day}.jsonl"


def quota_path(root: Path, user_id: str | None) -> Path:
    """Path of the per-user quota state document."""
    return user_dir(root, user_id) / "quota.json"


def persona_path(root: Path, user_id: str | None, agent_name: str | None) -> Path:
    """Path of the synthesized persona profile for one scope."""
    return user_dir(root, user_id) / "persona" / f"{agent_bucket(agent_name)}.md"


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp file + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    import os

    os.replace(tmp, path)
