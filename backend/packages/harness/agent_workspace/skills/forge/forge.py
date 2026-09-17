from __future__ import annotations

from typing import Any

from .models import Skill, SkillParameter


class SkillForge:
    """
    Automated Skill Forge: Converts execution traces into parameterized SKILL.md modules.
    Pipeline:
      Trace -> Extract Pattern -> Parameterize -> Validate -> Register
    """

    @staticmethod
    def extract_parameters(trace_steps: list[dict[str, Any]]) -> tuple[str, list[SkillParameter]]:
        """
        Scans trace steps, detects concrete strings (file paths, branch names, queries),
        and replaces them with template placeholders.
        """
        parameters: dict[str, SkillParameter] = {}
        template_lines: list[str] = []

        for i, step in enumerate(trace_steps):
            tool = step.get("tool", "exec")
            action = step.get("action", "")
            target = step.get("target", "")
            cmd = step.get("command", "")

            # Look for file path parameterization
            if target and ("/" in target or "\\" in target or "." in target):
                param_name = "target_path"
                if param_name not in parameters:
                    parameters[param_name] = SkillParameter(
                        name=param_name,
                        description="Target file or path to operate on",
                        required=True,
                    )
                target_placeholder = f"{{{param_name}}}"
                line = f"{i+1}. Call `{tool}` with target `{target_placeholder}` for action '{action}'"
            elif cmd:
                line = f"{i+1}. Run command: `{cmd}`"
            else:
                line = f"{i+1}. Step `{tool}`: {action}"

            template_lines.append(line)

        template = "\n".join(template_lines)
        return template, list(parameters.values())

    @classmethod
    def forge_from_trace(
        cls,
        name: str,
        description: str,
        trace_steps: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> Skill:
        """Forges a new Skill from a list of successful execution steps."""
        template, params = cls.extract_parameters(trace_steps)
        return Skill(
            name=name,
            description=description,
            template=template,
            parameters=params,
            metadata=metadata or {},
        )
