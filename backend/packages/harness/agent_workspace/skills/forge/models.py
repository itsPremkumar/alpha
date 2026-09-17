from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SkillParameter:
    """A parameter extracted from a concrete execution trace."""
    name: str
    description: str = ""
    required: bool = True
    default: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SkillParameter:
        return cls(**d)


@dataclass
class ForgeTestResult:
    """Result of running a forged skill against a validation test case."""
    skill_name: str
    test_id: str
    success: bool
    duration: float = 0.0
    notes: str = ""


@dataclass
class Skill:
    """A parameterized, testable, reusable skill."""
    name: str
    description: str
    template: str  # text instructions containing {placeholders}
    parameters: list[SkillParameter] = field(default_factory=list)
    version: int = 1
    test_pass_rate: float = 0.0
    test_count: int = 0
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def instantiate(self, **kwargs: Any) -> str:
        """Render instructions with provided parameters."""
        for p in self.parameters:
            if p.required and p.name not in kwargs and p.default is None:
                raise ValueError(f"Missing required parameter: {p.name}")
        merged = {p.name: p.default for p in self.parameters if p.default is not None}
        merged.update(kwargs)
        return self.template.format(**merged)

    def hash(self) -> str:
        """Deterministic content hash for deduplication."""
        payload = json.dumps(
            {
                "name": self.name,
                "template": self.template,
                "parameters": [p.to_dict() for p in self.parameters],
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_skill_md(self) -> str:
        """Render this skill as a modern SKILL.md file with YAML frontmatter."""
        lines = [
            "---",
            f"name: {self.name}",
            f"description: \"{self.description}\"",
            f"version: {self.version}",
            "---",
            "",
            f"# {self.name}",
            "",
            self.description,
            "",
        ]
        if self.parameters:
            lines.append("## Parameters")
            for p in self.parameters:
                req_str = "Required" if p.required else f"Optional (default: {p.default})"
                lines.append(f"- `{p.name}` ({req_str}): {p.description}")
            lines.append("")

        lines.append("## Instructions")
        lines.append(self.template)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "template": self.template,
            "parameters": [p.to_dict() for p in self.parameters],
            "version": self.version,
            "test_pass_rate": self.test_pass_rate,
            "test_count": self.test_count,
            "hash": self.hash(),
            "created_at": self.created_at,
            "metadata": self.metadata,
        }
