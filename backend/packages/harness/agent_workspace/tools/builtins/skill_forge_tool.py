"""Built-in skill_forge tool inspired by hermes-asi-master."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.skills.forge import SkillForge, SkillRegistry

_GLOBAL_SKILL_REGISTRY = SkillRegistry()


@tool("forge_skill_from_trace", parse_docstring=True)
def forge_skill_from_trace(
    name: str,
    description: str,
    trace_steps_json: str,
) -> str:
    """Forge a tested, parameterized SKILL.md tool from a sequence of successful execution steps.

    Extracts parameters, dedups via content hash, and registers the skill for autonomous reuse.

    Args:
        name: Unique kebab-case name of the skill (e.g. 'safe-hotfix-deployer').
        description: Clear explanation of what the skill accomplishes.
        trace_steps_json: JSON array of executed steps containing {'tool': str, 'action': str, 'target': str}.
    """
    try:
        steps = json.loads(trace_steps_json) if trace_steps_json else []
    except Exception:
        steps = []

    skill = SkillForge.forge_from_trace(name=name, description=description, trace_steps=steps)
    registered = _GLOBAL_SKILL_REGISTRY.register(skill, min_pass_rate=0.70)

    return json.dumps({
        "skill_name": skill.name,
        "registered": registered,
        "parameters_count": len(skill.parameters),
        "parameters": [p.to_dict() for p in skill.parameters],
        "hash": skill.hash(),
        "skill_md_preview": skill.to_skill_md()[:300] + "...",
    }, indent=2)
