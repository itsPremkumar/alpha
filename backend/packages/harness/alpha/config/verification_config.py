"""Configuration for the subagent result-verification layers."""

from typing import Literal

from pydantic import BaseModel, Field


class VerificationConfig(BaseModel):
    """Receipt ledger, acceptance checklist, and selective judge settings."""

    receipts_enabled: bool = Field(
        default=True,
        description="Stamp deterministic tool receipts on every tool result",
    )
    receipts_render_mode: Literal["always", "delegation_only"] = Field(
        default="delegation_only",
        description="Receipt-ledger rendering for the lead chain; subagent chains always render (citations are produced there). 'delegation_only' renders only while processing subagent results",
    )
    judge_enabled: bool = Field(
        default=False,
        description="Run a one-shot small-model review of completed subagent results that carry acceptance criteria",
    )
    judge_model_name: str | None = Field(
        default=None,
        description="Model for the selective judge; falls back to the parent model when unset",
    )
    completion_critics_enabled: bool = Field(
        default=True,
        description="Verify a terminal agent message against the turn's own tool history (alpha.critic) before accepting it, and send the agent back once with the critic's diagnostic when it is rejected",
    )
    # ``EmptyPatchCritic`` stays opt-in: it infers that a patch was expected from
    # keywords in the task text, so it misreads research and planning tasks.
    completion_critics_require_patch: bool = Field(
        default=False,
        description="Also run EmptyPatchCritic, which rejects a code-modification task that finished with a clean working tree",
    )
