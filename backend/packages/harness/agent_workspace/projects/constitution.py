"""Project constitution: the non-negotiable rules every assigned agent must obey.

Stored as markdown plus a content hash. Agents joining a project receive the
hash; a changed constitution invalidates pinned runs so stale rules can never
silently govern new work.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REQUIRED_SECTIONS = (
    "objective",
    "non-negotiable requirements",
    "technology constraints",
    "security rules",
    "coding rules",
    "architecture rules",
    "quality standards",
    "definition of done",
    "allowed tools",
    "forbidden actions",
    "approval requirements",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _projects_root() -> Path:
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        return runtime_home() / "projects"
    except Exception:
        return Path.cwd() / ".agent_workspace" / "projects"


def constitution_path(project_id: str) -> Path:
    return _projects_root() / project_id / "project-config" / "PROJECT_CONSTITUTION.md"


def hash_constitution(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:16]


def validate_constitution(markdown: str) -> list[str]:
    """Return the list of missing required sections (empty = valid)."""
    lowered = markdown.lower()
    return [s for s in REQUIRED_SECTIONS if s not in lowered]


DEFAULT_CONSTITUTION_TEMPLATE = """# Project Constitution

## Objective
{TODO: one paragraph stating what this project must achieve.}

## Non-negotiable Requirements
- {TODO}

## Technology Constraints
- {TODO}

## Security Rules
- Never exfiltrate secrets; request scoped credentials via the broker.
- External writes need approval per the approval requirements below.

## Coding Rules
- {TODO}

## Architecture Rules
- {TODO}

## Quality Standards
- All changes verified by tests before merge.

## Definition of Done
- Implementation + tests + lint + review evidence attached to the task.

## Allowed Tools
- {TODO}

## Forbidden Actions
- Irreversible production changes without human approval.

## Approval Requirements
- External communication and deployments require approval.
"""


@dataclass
class Constitution:
    project_id: str
    markdown: str
    sha16: str = ""
    updated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.sha16:
            self.sha16 = hash_constitution(self.markdown)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_lock = threading.Lock()


def get_constitution(project_id: str) -> Constitution | None:
    path = constitution_path(project_id)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        return Constitution(project_id=project_id, markdown=text)
    except Exception:
        logger.warning("Constitution read failed for %s", project_id, exc_info=True)
        return None


def put_constitution(project_id: str, markdown: str) -> Constitution:
    missing = validate_constitution(markdown)
    if missing:
        raise ValueError(f"Constitution missing sections: {', '.join(missing)}")
    path = constitution_path(project_id)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(markdown, encoding="utf-8")
        tmp.replace(path)
    return Constitution(project_id=project_id, markdown=markdown)
