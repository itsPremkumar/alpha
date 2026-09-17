"""Automated Markdown Architectural Decision Record (ADR) Generator.

Bridges in-chat and council decisions to durable Markdown ADRs stored directly in
the project workspace (`projects/<project_id>/decisions/ADR-XXX-<slug>.md`).
Provides human-readable, version-controllable architectural documentation.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from agent_workspace.config.runtime_paths import runtime_home
from agent_workspace.projects.decisions import Decision, get_decision_log

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert human title to a clean filename-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-")[:60] or "decision"


def get_adrs_directory(project_id: str, base_dir: Path | None = None) -> Path:
    """Return the directory where ADR markdown files are stored for a project."""
    root = base_dir or runtime_home()
    return root / "projects" / project_id / "decisions" / "markdown"


def generate_markdown_adr(
    decision: Decision | dict[str, Any],
    project_id: str,
    output_dir: Path | None = None,
) -> Path:
    """Generate a standard Markdown ADR file from a Decision record.

    Writes to `<output_dir>/<decision_id>-<slug>.md`.
    """
    if isinstance(decision, dict):
        d = Decision.from_dict(decision)
    else:
        d = decision

    target_dir = output_dir or get_adrs_directory(project_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    slug = _slugify(d.title)
    filename = f"{d.decision_id}-{slug}.md"
    target_file = target_dir / filename

    status = "Accepted" if d.approved_by else "Proposed"
    approved_line = f"- **Approved By**: @{d.approved_by}" if d.approved_by else "- **Approved By**: Pending"
    arch_line = f"- **Architecture Version**: {d.arch_version}" if d.arch_version else "- **Architecture Version**: Initial"
    author = f"@{d.made_by}" if d.made_by else "System"

    content = f"""# {d.decision_id}: {d.title}

- **Status**: {status}
- **Date**: {d.created_at}
- **Author**: {author}
{approved_line}
{arch_line}

## Context and Problem Statement
{d.body.strip()}

## Decision Outcome
{d.reason.strip() if d.reason else "Decision ratified by project team."}

## Consequences
### Positive
- Enforces clear architectural boundaries across the AI workforce.
- Documented in project memory to prevent repetitive rediscovery.

### Compliance
- All agents working on project `{project_id}` are bound by this decision.
"""

    target_file.write_text(content, encoding="utf-8")
    logger.info("Generated Markdown ADR for %s at %s", d.decision_id, target_file)
    return target_file


def sync_all_adrs(project_id: str, output_dir: Path | None = None) -> list[Path]:
    """Synchronize all decisions in the project's decision log to Markdown files."""
    log = get_decision_log(project_id)
    decisions = log.list()
    generated: list[Path] = []
    for d in decisions:
        p = generate_markdown_adr(d, project_id, output_dir=output_dir)
        generated.append(p)
    return generated


def read_adr_markdown(project_id: str, decision_id: str, base_dir: Path | None = None) -> str | None:
    """Find and read the markdown content of an ADR by its ID."""
    target_dir = get_adrs_directory(project_id, base_dir)
    if not target_dir.exists():
        return None
    for p in target_dir.glob(f"{decision_id}*.md"):
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            pass
    return None
