from __future__ import annotations

import re
from dataclasses import dataclass

from alpha.constants import DEFAULT_SKILLS_CONTAINER_PATH
from alpha.skills.types import Skill

#: Composer control commands that own the leading slash and must never be
#: treated as ``/skill`` activations. These values plus :data:`_SLASH_SKILL_RE`
#: are mirrored by the frontend display parser in
#: ``frontend/src/core/skills/slash.ts``; both sides are pinned to the shared
#: fixture at ``contracts/slash_skill_contract.json`` by contract tests
#: (``tests/test_slash_skill_contract.py`` here, ``slash-contract.test.ts`` on
#: the frontend), so a reserved command or grammar change in only one language
#: fails CI.
#: Every channel command the gateway actually registers is reserved, so a skill
#: named e.g. ``plan`` or ``team`` can never shadow ``/plan`` / ``/team``. The
#: list mirrors ``app.channels.commands.KNOWN_CHANNEL_COMMANDS`` and is pinned to
#: the shared fixture at ``contracts/slash_skill_contract.json`` by
#: ``tests/test_slash_skill_contract.py``. NOTE: the frontend display mirror
#: (``frontend/src/core/skills/slash.ts`` + ``slash-contract.test.ts``) previously
#: promised by this comment DOES NOT EXIST — the frontend has no slash-skill
#: activation parser today; building it is tracked as a discovery-plane
#: follow-up, and this comment is corrected rather than left overstating what CI
#: enforces.
RESERVED_SLASH_SKILL_NAMES = frozenset(
    {
        "agent",
        "approve",
        "bootstrap",
        "goal",
        "help",
        "memory",
        "models",
        "new",
        "plan",
        "project",
        "reject",
        "standup",
        "status",
        "swarm",
        "team",
    }
)
_SLASH_SKILL_RE = re.compile(r"^/([a-z0-9]+(?:-[a-z0-9]+)*)(?:\s+|$)")


@dataclass(frozen=True, slots=True)
class SlashSkillReference:
    """Parsed slash-skill command with the skill name and remaining task text."""

    name: str
    remaining_text: str


@dataclass(frozen=True, slots=True)
class ResolvedSlashSkill:
    """Slash-skill activation resolved against enabled runtime-visible skills."""

    skill: Skill
    remaining_text: str
    container_file_path: str


def parse_slash_skill_reference(text: str) -> SlashSkillReference | None:
    """Parse strict `/skill-name task` syntax, ignoring reserved control commands."""
    match = _SLASH_SKILL_RE.match(text)
    if not match:
        return None
    name = match.group(1)
    if name in RESERVED_SLASH_SKILL_NAMES:
        return None
    return SlashSkillReference(
        name=name,
        remaining_text=text[match.end() :].lstrip(),
    )


def resolve_slash_skill(
    text: str,
    skills: list[Skill],
    *,
    available_skills: set[str] | None = None,
    container_base_path: str = DEFAULT_SKILLS_CONTAINER_PATH,
) -> ResolvedSlashSkill | None:
    """Resolve text into an enabled, whitelisted skill activation if possible."""
    reference = parse_slash_skill_reference(text)
    if reference is None:
        return None
    if available_skills is not None and reference.name not in available_skills:
        return None

    skill = next((candidate for candidate in skills if candidate.name == reference.name and candidate.enabled), None)
    if skill is None:
        return None

    return ResolvedSlashSkill(
        skill=skill,
        remaining_text=reference.remaining_text,
        container_file_path=skill.get_container_file_path(container_base_path),
    )
