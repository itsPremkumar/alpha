import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from alpha.constants import DEFAULT_SKILLS_CONTAINER_PATH

SKILL_MD_FILE = "SKILL.md"

#: A skill name is an identifier, and the same identifier in every place that
#: matters: the registry key, the operator's ``extensions_config.skills``
#: enable/disable key, the per-user ``_skill_states.json`` toggle key, the
#: ``/slash`` activation name, and the text rendered into the system prompt. It
#: is defined here — in the leaf module both the parser and the storages already
#: import — so the loader that admits a name and the path helpers that act on
#: one cannot drift apart. ``skills/slash.py::parse_slash_skill_reference``
#: independently requires this same grammar for the name a user can type, which
#: is why a name outside it could never have been slash-activated but was still
#: loaded, listed, and rendered.
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Longest accepted skill name. Matches ``_skill_name_pattern``'s counterpart in
#: the storage path validators.
SKILL_NAME_MAX_LENGTH = 64


def validate_skill_name(name: str) -> str:
    """Validate and normalise a skill *name*; return the normalised form.

    Raises ``ValueError`` when the name is not a hyphen-case identifier. Every
    caller that is handed a skill name from an untrusted manifest must run this
    before storing it as a key — a name outside the grammar is not merely
    inelegant, it is a key the operator cannot reach: they cannot type it after
    ``/``, and they cannot spell it in ``extensions_config.skills`` if they are
    going by the directory they installed it into.
    """
    normalized = name.strip()
    if not SKILL_NAME_PATTERN.fullmatch(normalized):
        raise ValueError("Skill name must be hyphen-case using lowercase letters, digits, and hyphens only.")
    if len(normalized) > SKILL_NAME_MAX_LENGTH:
        raise ValueError(f"Skill name must be {SKILL_NAME_MAX_LENGTH} characters or fewer.")
    return normalized


class SkillCategory(StrEnum):
    """Source category for a skill.

    - ``PUBLIC``: built-in skill bundled with the platform, read-only.
    - ``CUSTOM``: user-authored skill that can be edited or deleted.
    - ``INTEGRATION``: managed third-party integration skill, read-only.
    - ``LEGACY``: global custom skill from before user-isolation migration,
      presented as read-only (visible but not editable/deletable). These
      skills are mounted at ``/mnt/skills/legacy/<name>/`` in the sandbox.
    """

    PUBLIC = "public"
    CUSTOM = "custom"
    INTEGRATION = "integrations"
    LEGACY = "legacy"


@dataclass(frozen=True)
class SecretRequirement:
    """A request-scoped secret a skill declares it needs (issue #3861).

    ``name`` is both the key looked up in the request's ``context.secrets`` and
    the environment variable name injected into the skill's sandbox subprocess
    when the skill is activated.
    """

    name: str
    optional: bool = False


@dataclass(frozen=True)
class Skill:
    """Represents a skill with its metadata and file path"""

    name: str
    description: str
    license: str | None
    skill_dir: Path
    skill_file: Path
    relative_path: Path  # Relative path from category root to skill directory
    category: SkillCategory  # 'public' or 'custom'
    allowed_tools: tuple[str, ...] | None = None
    enabled: bool = False  # Whether this skill is enabled
    required_secrets: tuple[SecretRequirement, ...] = field(default_factory=tuple)
    # Whether declared secrets may bind when the skill is in-context via an
    # autonomous model load (skill_context), or only on explicit /slash
    # activation. Frontmatter: ``secrets-autonomous`` (default true).
    secrets_autonomous: bool = True

    @property
    def skill_path(self) -> str:
        """Returns the relative path from the category root (skills/{category}) to this skill's directory"""
        path = self.relative_path.as_posix()
        return "" if path == "." else path

    def get_container_path(self, container_base_path: str = DEFAULT_SKILLS_CONTAINER_PATH) -> str:
        """
        Get the full path to this skill in the container.

        Args:
            container_base_path: Base path where skills are mounted in the container

        Returns:
            Full container path to the skill directory
        """
        category_base = f"{container_base_path}/{self.category}"
        skill_path = self.skill_path
        if skill_path:
            return f"{category_base}/{skill_path}"
        return category_base

    def get_container_file_path(self, container_base_path: str = DEFAULT_SKILLS_CONTAINER_PATH) -> str:
        """
        Get the full path to this skill's main file (SKILL.md) in the container.

        Args:
            container_base_path: Base path where skills are mounted in the container

        Returns:
            Full container path to the skill's SKILL.md file
        """
        return f"{self.get_container_path(container_base_path)}/SKILL.md"

    def __repr__(self) -> str:
        return f"Skill(name={self.name!r}, description={self.description!r}, category={self.category!r})"
