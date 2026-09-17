"""Executable Python Skills Protocol for DeerFlow (inspired by Prime Agent rlm/skill.py).

Enables skills to be importable, executable Python packages or callables
with input validation and deterministic programmatic execution.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExecutableSkillReference:
    """Specification of an executable Python skill callable."""

    import_path: str
    callable_name: str
    skill_type: str = "python"
    description: str = ""
    parameters_schema: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutableSkillReference:
        if data.get("type", "python") != "python":
            raise ValueError(f"Expected skill type 'python', got {data.get('type')}")
        imp = data.get("import") or data.get("python_import")
        if not imp:
            raise ValueError("Skill reference requires 'import' path.")
        fn = data.get("callable") or data.get("call_pattern")
        if not fn:
            raise ValueError("Skill reference requires 'callable' name.")
        return cls(
            import_path=imp,
            callable_name=fn,
            skill_type="python",
            description=data.get("description", ""),
            parameters_schema=data.get("parameters", {}),
        )


class ExecutableSkillRunner:
    """Dynamically loads and executes Python-backed skills."""

    def __init__(self):
        self._loaded_callables: dict[str, Callable[..., Any]] = {}

    def resolve(self, ref: ExecutableSkillReference) -> Callable[..., Any]:
        key = f"{ref.import_path}:{ref.callable_name}"
        if key in self._loaded_callables:
            return self._loaded_callables[key]

        module = importlib.import_module(ref.import_path)
        func = getattr(module, ref.callable_name, None)
        if func is None or not callable(func):
            raise AttributeError(f"Module '{ref.import_path}' has no callable '{ref.callable_name}'")

        self._loaded_callables[key] = func
        return func

    async def execute(
        self,
        ref: ExecutableSkillReference,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Execute the skill callable with supplied arguments."""
        func = self.resolve(ref)
        args = arguments or {}

        # Inspect parameter signature, handling positional-only parameters seamlessly
        pos_args = []
        kw_args = {}
        try:
            sig = inspect.signature(func)
            for param in sig.parameters.values():
                if param.kind == inspect.Parameter.POSITIONAL_ONLY:
                    if param.name in args:
                        pos_args.append(args[param.name])
                elif param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
                    if param.name in args:
                        kw_args[param.name] = args[param.name]
                elif param.kind == inspect.Parameter.VAR_KEYWORD:
                    for k, v in args.items():
                        if k not in kw_args and param.name != k:
                            kw_args[k] = v
                    break
        except (ValueError, TypeError):
            kw_args = args

        if inspect.iscoroutinefunction(func):
            return await func(*pos_args, **kw_args)
        return await asyncio.to_thread(func, *pos_args, **kw_args)



_global_runner = ExecutableSkillRunner()


def get_skill_runner() -> ExecutableSkillRunner:
    return _global_runner
