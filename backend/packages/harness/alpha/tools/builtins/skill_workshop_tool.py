"""Built-in Skill Synthesis Workshop Tool.

Allows autonomous agents to compile execution experiences and completed tasks
into reusable, validated skills without human intervention.
"""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.skills.workshop import SkillWorkshopEngine


@tool("synthesize_reusable_skill", parse_docstring=True)
def synthesize_reusable_skill(
    skill_name: str,
    description: str,
    steps_json: str,
    verification_command: str = "python -m pytest tests/ -q",
    auto_publish: bool = False,
) -> str:
    """Synthesize a structured and validated SKILL.md package from execution steps.

    Args:
        skill_name: Lowercase-hyphenated unique name (<=64 chars, e.g. 'clean-temp-artifacts').
        description: Concise one-sentence summary of capability (<=60 chars, ends with a period).
        steps_json: JSON string containing a list of execution step dictionaries. Each step
                    can have 'tool', 'action', 'target', and 'command' keys.
        verification_command: Bash command to verify skill execution.
        auto_publish: If True, writes the skill immediately to skills/custom/<skill_name>/SKILL.md if valid.
    """
    try:
        raw_steps = json.loads(steps_json)
        if not isinstance(raw_steps, list):
            return "Error: steps_json must be a JSON array of step objects."
    except Exception as exc:
        return f"Error parsing steps_json: {exc}"

    try:
        draft = SkillWorkshopEngine.distill_from_trace(
            name=skill_name,
            description=description,
            trace_steps=raw_steps,
            verification_cmd=verification_command,
        )
    except Exception as exc:
        return f"Error synthesizing skill draft: {exc}"

    if not draft.is_valid:
        return (
            "Draft generated but failed quality review gates:\n"
            + "\n".join(f"- {f}" for f in draft.findings)
        )

    if auto_publish:
        try:
            target_path = SkillWorkshopEngine.publish_skill(draft, overwrite=True)
            return (
                f"Skill '{draft.name}' synthesized and successfully published to {target_path}.\n"
                f"Description: {draft.description}\n"
                f"Parameters detected: {len(draft.parameters)}"
            )
        except Exception as exc:
            return f"Draft passed validation but failed to publish: {exc}"

    return (
        f"Skill draft '{draft.name}' synthesized successfully and ready for review.\n"
        f"Description: {draft.description}\n"
        f"Parameters: {draft.parameters}\n\n"
        f"Draft Preview:\n```markdown\n{draft.markdown_content[:600]}...\n```"
    )
