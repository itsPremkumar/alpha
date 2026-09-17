"""Configuration for the post-turn learning fork."""

from pydantic import BaseModel, ConfigDict, Field


class LearningForkConfig(BaseModel):
    """Config for the cheap aux-model replay that proposes memory/skill updates.

    The fork runs after each turn with a bounded digest of the latest
    conversation. It uses a whitelisted toolset (memory + proposal tools only)
    so it cannot mutate sandbox state or invoke arbitrary tools. All writes go
    through the existing MemoryManager and SkillProposalStore; exceptions are
    isolated and never affect the primary run.
    """

    enabled: bool = Field(
        default=False,
        description="Enable the learning fork. Off by default — no prompt-cache churn when disabled.",
    )
    model_name: str | None = Field(
        default=None,
        description="Model to use for the fork. Defaults to the lead agent's resolved model when None.",
    )
    max_proposals_per_run: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Max skill/memory proposals the fork may emit per run.",
    )
    digest_chars: int = Field(
        default=8000,
        ge=1000,
        le=32000,
        description="Max characters of conversation digest fed to the fork (newest-first).",
    )
    defer_when_busy: bool = Field(
        default=False,
        description="Queue the review instead of running it inline; drained when idle (review queue). Off by default — immediate fork.",
    )
    defer_max_age_seconds: float = Field(
        default=1800.0,
        ge=60.0,
        description="Aged-out queued reviews dispatch regardless of idleness after this long.",
    )

    model_config = ConfigDict(extra="forbid")


def get_learning_fork_config() -> LearningForkConfig:
    """Resolve the learning fork config from the app config singleton."""
    from agent_workspace.config import get_app_config

    app = get_app_config()
    return getattr(app, "learning_fork", LearningForkConfig())
