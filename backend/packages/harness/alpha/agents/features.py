"""Declarative feature flags and middleware positioning for create_alpha_agent.

Pure data classes and decorators — no I/O, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    # Annotation-only use. `from __future__ import annotations` (line 6) makes
    # every annotation in this module a string, so the dataclass field types below
    # never need this class at runtime. Keeping it under TYPE_CHECKING is what
    # stops `alpha.agents` -> this module from importing `langchain.agents`.
    from langchain.agents.middleware import AgentMiddleware

    from alpha.config.memory_config import MemoryConfig


def _agent_middleware_class() -> type:
    """Import ``AgentMiddleware`` on first real use.

    `Next`/`Prev` genuinely need the class at runtime (issubclass checks), but
    they are decorators applied while building an agent -- long after config
    import. Measured 2026-10-05: importing this module eagerly pulled
    `langchain.agents` and cost ~59s, which every `alpha.memory.*.config` import
    inherited through `alpha.agents.memory.l1.paths`.
    """

    global _AgentMiddleware
    try:
        return _AgentMiddleware
    except NameError:
        from langchain.agents.middleware import AgentMiddleware

        _AgentMiddleware = AgentMiddleware
        return AgentMiddleware


@dataclass
class RuntimeFeatures:
    """Declarative feature flags for ``create_alpha_agent``.

    Most features accept:
    - ``True``: use the built-in default middleware
    - ``False``: disable
    - An ``AgentMiddleware`` instance: use this custom implementation instead

    ``summarization`` and ``guardrail`` have no built-in default — they only
    accept ``False`` (disable) or an ``AgentMiddleware`` instance (custom).
    """

    sandbox: bool | AgentMiddleware = True
    memory: bool | AgentMiddleware = False
    # Explicit memory config for direct create_alpha_agent(features=...) callers.
    # The lead-agent AppConfig path passes resolved_app_config.memory directly.
    memory_config: MemoryConfig | None = None
    summarization: Literal[False] | AgentMiddleware = False
    subagent: bool | AgentMiddleware = False
    vision: bool | AgentMiddleware = False
    auto_title: bool | AgentMiddleware = False
    guardrail: Literal[False] | AgentMiddleware = False
    loop_detection: bool | AgentMiddleware = True
    token_budget: bool | AgentMiddleware = False


# ---------------------------------------------------------------------------
# Middleware positioning decorators
# ---------------------------------------------------------------------------


def Next(anchor: type[AgentMiddleware]):
    """Declare this middleware should be placed after *anchor* in the chain."""
    AgentMiddleware = _agent_middleware_class()
    if not (isinstance(anchor, type) and issubclass(anchor, AgentMiddleware)):
        raise TypeError(f"@Next expects an AgentMiddleware subclass, got {anchor!r}")

    def decorator(cls: type[AgentMiddleware]) -> type[AgentMiddleware]:
        cls._next_anchor = anchor  # type: ignore[attr-defined]
        return cls

    return decorator


def Prev(anchor: type[AgentMiddleware]):
    """Declare this middleware should be placed before *anchor* in the chain."""
    AgentMiddleware = _agent_middleware_class()
    if not (isinstance(anchor, type) and issubclass(anchor, AgentMiddleware)):
        raise TypeError(f"@Prev expects an AgentMiddleware subclass, got {anchor!r}")

    def decorator(cls: type[AgentMiddleware]) -> type[AgentMiddleware]:
        cls._prev_anchor = anchor  # type: ignore[attr-defined]
        return cls

    return decorator
