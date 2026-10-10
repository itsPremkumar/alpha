"""Arena configuration: the operator's ceilings.

Every ceiling here exists because the upstream arena
pattern, run unguarded, measured **~6M tokens for a
16-agent quick run and ~39M for the default 100** (the
independently measured benchmark in the research dossier).
Alpha's answer is not to hide the cost but to make it
impossible to spend silently:

* the planner projects the exact call count before
  anything runs;
* a run projected above ``require_confirmation_over_calls``
  needs an explicit ``confirm=True`` from the caller;
* the run's measured budget is charged from what the
  sub-agents actually spent, and exhaustion is a
  reported terminal state, not a silent stop.

The defaults are deliberately small. An arena is a
deliberate instrument, not a default way to work.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

#: The measured per-sub-agent-call token cost of the
#: upstream pattern (~66k tokens/call against ~49k for a
#: plain agent). Used only to *estimate* cost for the
#: confirmation gate; the run's own measured budget is
#: the authority once it starts.
DEFAULT_ESTIMATE_TOKENS_PER_CALL = 60_000


class ArenaConfig(BaseModel):
    """``config.yaml -> arena`` settings."""

    enabled: bool = Field(
        default=True,
        description="Bind the arena tool. Off = the tool is absent from every agent's toolset.",
    )
    default_agents: int = Field(
        default=4,
        ge=1,
        le=2160,
        description="Competitors per run when the caller does not name one. The shipped deck deals 2,160 distinct cards.",
    )
    max_agents: int = Field(
        default=64,
        ge=1,
        le=2160,
        description="Hard ceiling on competitors per run. Refused, never clamped: a larger arena is a deliberate operator choice.",
    )
    default_wave: int = Field(
        default=4,
        ge=1,
        description="Sub-agent jobs dispatched concurrently per wave when the caller does not name one.",
    )
    max_wave: int = Field(
        default=32,
        ge=1,
        description="Hard ceiling on wave size. Waves are clamped to the process sub-agent admission controller regardless.",
    )
    estimate_tokens_per_call: int = Field(
        default=DEFAULT_ESTIMATE_TOKENS_PER_CALL,
        ge=1,
        description="Tokens used to *estimate* a run's cost for the confirmation gate. Measured spend, not this estimate, is charged once the run starts.",
    )
    require_confirmation_over_calls: int = Field(
        default=100,
        ge=1,
        description="A run projected above this many sub-agent calls needs an explicit confirm=True from the caller. 0 disables the gate.",
    )
    max_subagent_calls_per_run: int | None = Field(
        default=1000,
        ge=1,
        description="Default measured sub-agent call budget for a run. The caller may lower it; a caller may raise it only with confirm=True.",
    )
    max_tokens_per_run: int | None = Field(
        default=50_000_000,
        ge=1,
        description="Default measured token budget for a run.",
    )
    max_wall_seconds_per_run: float | None = Field(
        default=7200.0,
        ge=1,
        description="Default wall-clock budget for a run.",
    )
    max_judge_attempts: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Judge attempts per match before the run fails honestly. The orchestrator never invents a winner.",
    )
    reasoning_bank_seed: bool = Field(
        default=True,
        description="Record each match outcome into alpha.reasoning_bank, so later arenas can recall which strategies beat which.",
    )
    governance_risk_class: str = Field(
        default="execute",
        description="Declared tool governance risk class for the arena tool (spends tokens, runs sub-agents, writes work files).",
    )


def arena_config(app_config: object | None = None) -> ArenaConfig:
    """Resolve the arena config from an ``AppConfig``.

    ``None`` (no config loaded) yields the defaults, so
    the engine is importable and testable without a
    ``config.yaml``.
    """
    if app_config is None:
        return ArenaConfig()
    arena = getattr(app_config, "arena", None)
    if isinstance(arena, ArenaConfig):
        return arena
    return ArenaConfig()


__all__ = [
    "ArenaConfig",
    "DEFAULT_ESTIMATE_TOKENS_PER_CALL",
    "arena_config",
]
