"""Configuration for the review guard middleware."""

from pydantic import BaseModel, ConfigDict, Field


class ReviewGuardConfig(BaseModel):
    """Config for the review guard middleware.

    The review guard enforces:
    - Comment density: requires minimum comment ratio in code edits
    - Role-scoped write policies: restricts which roles can write which file types
    """

    enabled: bool = Field(
        default=False,
        description="Enable the review guard. Off by default.",
    )
    min_comment_ratio: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Minimum comment-to-code ratio for write operations (0.0-1.0).",
    )
    enforce_on_extensions: list[str] = Field(
        default_factory=lambda: [".py", ".ts", ".tsx", ".js", ".jsx"],
        description="File extensions to enforce comment density on.",
    )
    role_write_policy: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "planner": [".md", ".txt", ".yaml", ".yml"],
            "coder": [".py", ".ts", ".tsx", ".js", ".jsx", ".json"],
            "reviewer": [],
        },
        description="Mapping of role -> allowed file extensions for writes. Empty list = no writes allowed.",
    )
    default_role: str = Field(
        default="coder",
        description="Default role when not specified in runtime context.",
    )

    model_config = ConfigDict(extra="forbid")


def get_review_guard_config() -> ReviewGuardConfig:
    """Resolve the review guard config from the app config singleton."""
    from agent_workspace.config import get_app_config

    app = get_app_config()
    return getattr(app, "review_guard", ReviewGuardConfig())