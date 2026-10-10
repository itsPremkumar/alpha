"""Configuration for the deep-agent working plane (``deepagents``' state backend).

The working plane is Alpha's equivalent of ``deepagents``' ``StateBackend``: a
bounded, thread-scoped virtual filesystem that lives in graph state rather than
on disk. See :mod:`alpha.deepagent.state` for the contract.
"""

from pydantic import BaseModel, Field

from alpha.deepagent.state import (
    MAX_WORKING_FILE_BYTES,
    MAX_WORKING_FILES,
    MAX_WORKING_SUMMARY_CHARS,
    MAX_WORKING_TOTAL_BYTES,
)


class DeepAgentConfig(BaseModel):
    """Configuration for the deep-agent working plane.

    Everything here is a **refusal**, never a clamp: an over-bounds write names
    the bound it hit so the model can split the note or delete another file,
    instead of silently receiving a truncated file whose bytes nobody wrote.
    """

    enabled: bool = Field(
        default=True,
        description="Whether the working plane tools are offered to the agent and the plane's index is projected into model requests. Disable to behave exactly as before this feature existed.",
    )
    inject_index: bool = Field(
        default=True,
        description="Whether the bounded working-plane index is injected into every model request. The tools stay usable without it, but the model then has to guess what it wrote earlier.",
    )
    max_files: int = Field(
        default=MAX_WORKING_FILES,
        ge=1,
        le=256,
        description="Most files the working plane holds per thread.",
    )
    max_file_bytes: int = Field(
        default=MAX_WORKING_FILE_BYTES,
        ge=256,
        le=262_144,
        description="Ceiling on one working file's content.",
    )
    max_total_bytes: int = Field(
        default=MAX_WORKING_TOTAL_BYTES,
        ge=1024,
        le=2_097_152,
        description="Ceiling on the summed size of every working file.",
    )
    max_summary_chars: int = Field(
        default=MAX_WORKING_SUMMARY_CHARS,
        ge=0,
        le=512,
        description="Ceiling on the per-file one-line purpose the model may declare. 0 disables summaries and degrades the index to bare paths.",
    )

    def limits(self) -> dict[str, int]:
        """The effective bounds, used by both the tool's refusals and its reply."""
        return {
            "max_files": self.max_files,
            "max_file_bytes": self.max_file_bytes,
            "max_total_bytes": self.max_total_bytes,
            "max_summary_chars": self.max_summary_chars,
        }


# Global configuration instance
_deepagent_config: DeepAgentConfig = DeepAgentConfig()


def get_deepagent_config() -> DeepAgentConfig:
    """Get the current deep-agent working-plane configuration."""
    return _deepagent_config


def set_deepagent_config(config: DeepAgentConfig) -> None:
    """Set the deep-agent working-plane configuration."""
    global _deepagent_config
    _deepagent_config = config


def load_deepagent_config_from_dict(config_dict: dict) -> None:
    """Load the deep-agent working-plane configuration from a dictionary."""
    global _deepagent_config
    _deepagent_config = DeepAgentConfig(**config_dict)
