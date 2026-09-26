import logging
from typing import Protocol

from alpha.skills.types import Skill

logger = logging.getLogger(__name__)


class NamedTool(Protocol):
    name: str


# Framework built-ins that remain available even when an active skill declares
# allowed-tools. They support controlled file/review/discovery workflows rather
# than extending the reviewed/activated skill's own business-tool authority.
# In particular, promotion through tool_search does not restore a tool removed
# by SkillToolPolicyMiddleware, and describe_skill only returns catalog metadata.
ALWAYS_AVAILABLE_BUILTIN_TOOL_NAMES = frozenset(
    {
        "describe_skill",
        "read_file",
        "review_skill_package",
        "search_project_docs",
        "autonomy_control",
        "reversible_delete",
        "tool_search",
    }
)


def allowed_tool_names_for_skills(
    skills: list[Skill],
    *,
    allow_all_when_undeclared: bool = True,
) -> set[str] | None:
    """Return the union of explicit skill allowed-tools declarations.

    None means legacy allow-all behavior. It is returned only when no loaded
    skill declares allowed-tools. Once any skill declares the field, legacy
    skills without the field contribute no tools instead of disabling the
    explicit restrictions from other skills.

    ``allow_all_when_undeclared=False`` closes a real gap: when *no* skill
    declares allowed-tools the union is empty by default, which historically
    meant "no restriction". Under OpenClaw's rule that a Skill grants no
    permissions, an undeclared skill set should yield only the framework
    built-ins rather than the whole tool registry. Defaults to True so existing
    deployments keep their behaviour.
    """
    if not skills:
        return None if allow_all_when_undeclared else set(ALWAYS_AVAILABLE_BUILTIN_TOOL_NAMES)

    allowed: set[str] = set()
    has_explicit_declaration = False
    for skill in skills:
        if skill.allowed_tools is None:
            continue
        has_explicit_declaration = True
        if not skill.allowed_tools:
            logger.info("Skill %s declared empty allowed-tools", skill.name)
        allowed.update(skill.allowed_tools)

    if not has_explicit_declaration:
        return None if allow_all_when_undeclared else set(ALWAYS_AVAILABLE_BUILTIN_TOOL_NAMES)
    return allowed


def filter_tools_by_skill_allowed_tools[ToolT: NamedTool](
    tools: list[ToolT],
    skills: list[Skill],
    *,
    always_allowed_tool_names: set[str] | frozenset[str] = frozenset(),
    allow_all_when_undeclared: bool = True,
) -> list[ToolT]:
    allowed = allowed_tool_names_for_skills(skills, allow_all_when_undeclared=allow_all_when_undeclared)
    if allowed is None:
        return tools

    allowed_with_framework_tools = allowed | set(always_allowed_tool_names)
    return [tool for tool in tools if tool.name in allowed_with_framework_tools]
