import os
from pathlib import Path

from pydantic import BaseModel, Field

from alpha.config.reload_boundary import format_field_description
from alpha.config.runtime_paths import project_root, resolve_path
from alpha.constants import DEFAULT_SKILLS_CONTAINER_PATH


def _legacy_skills_candidates() -> tuple[Path, ...]:
    """Return source-tree skills locations for monorepo compatibility."""
    backend_dir = Path(__file__).resolve().parents[4]
    repo_root = backend_dir.parent
    return (repo_root / "skills",)


class SkillsConfig(BaseModel):
    """Configuration for skills system"""

    use: str = Field(
        default="alpha.skills.storage.local_skill_storage:LocalSkillStorage",
        description="Class path of the SkillStorage implementation.",
    )
    path: str | None = Field(
        default=None,
        description=("Path to skills directory. If not specified, defaults to `skills` under the caller project root, falling back to the legacy repo-root location for monorepo compatibility."),
    )
    container_path: str = Field(
        default=DEFAULT_SKILLS_CONTAINER_PATH,
        description=format_field_description(
            "skills.container_path",
            field_doc="Path where skills are mounted in the sandbox container.",
        ),
    )
    deferred_discovery: bool = Field(
        default=False,
        description=("When enabled, skill metadata is not injected into the system prompt. Instead, only skill names appear in <skill_index> and the LLM discovers details on demand via the describe_skill tool."),
    )
    allowed_skills: list[str] | None = Field(
        default=None,
        description=(
            "Optional allowlist of skill names. ``None`` (the default) keeps the "
            "current behaviour of considering every enabled skill. When set, any "
            "skill not named here is never loaded, so an operator can run with a "
            "reviewed subset instead of whatever happens to be installed. This is "
            "the OpenClaw-style bundled-skill allowlist: the risk being closed is "
            "that skills activate implicitly, before anyone has reviewed them."
        ),
    )

    def is_skill_allowed(self, skill_name: str) -> bool:
        """True when ``skill_name`` passes the allowlist (or none is configured)."""
        if not self.allowed_skills:
            return True
        return skill_name in set(self.allowed_skills)

    def get_skills_path(self) -> Path:
        """
        Get the resolved skills directory path.

        Resolution order:
            1. Explicit ``path`` field
            2. ``AGENT_WORKSPACE_SKILLS_PATH`` environment variable
            3. ``skills`` under the caller project root (``project_root()``)
            4. Legacy repo-root candidates for monorepo compatibility (``_legacy_skills_candidates``)

        When none of (3) or (4) exist on disk, the project-root default is returned so callers
        can still surface a stable "no skills" location without raising.
        """
        if self.path:
            # Use configured path (can be absolute or relative to project root)
            return resolve_path(self.path)
        if env_path := os.getenv("AGENT_WORKSPACE_SKILLS_PATH"):
            return resolve_path(env_path)

        project_default = project_root() / "skills"
        if project_default.is_dir():
            return project_default

        for candidate in _legacy_skills_candidates():
            if candidate.is_dir():
                return candidate

        return project_default

    def get_skill_container_path(self, skill_name: str, category: str = "public") -> str:
        """
        Get the full container path for a specific skill.

        Args:
            skill_name: Name of the skill (directory name)
            category: Category of the skill (public or custom)

        Returns:
            Full path to the skill in the container
        """
        return f"{self.container_path}/{category}/{skill_name}"
