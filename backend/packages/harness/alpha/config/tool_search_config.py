"""Configuration for deferred tool loading via tool_search."""

from pydantic import BaseModel, Field, field_validator

AUTO_PROMOTE_TOP_K_MIN = 1
AUTO_PROMOTE_TOP_K_MAX = 5


def clamp_auto_promote_top_k(value: int) -> int:
    """Clamp the global MCP routing auto-promote breadth to PR2's range."""
    return max(AUTO_PROMOTE_TOP_K_MIN, min(AUTO_PROMOTE_TOP_K_MAX, int(value)))


class ToolSearchConfig(BaseModel):
    """Configuration for deferred tool loading via tool_search.

    When enabled, configured tool schemas can be listed by name and discovered
    via tool_search at runtime. MCP schemas are always deferred; first-party
    schemas are deferred when ``defer_builtin_tools`` is enabled.
    """

    enabled: bool = Field(
        default=True,
        description="Defer broad tool catalogs and enable tool_search",
    )
    defer_builtin_tools: bool = Field(
        default=True,
        description="With tool_search enabled, defer first-party tool schemas until discovered; tool execution still uses the authorized candidate set",
    )
    auto_promote_top_k: int = Field(
        default=3,
        description="Maximum number of deferred MCP tool schemas auto-promoted from routing metadata per model call",
    )

    @field_validator("auto_promote_top_k")
    @classmethod
    def _clamp_auto_promote_top_k(cls, value: int) -> int:
        return clamp_auto_promote_top_k(value)


_tool_search_config: ToolSearchConfig | None = None


def get_tool_search_config() -> ToolSearchConfig:
    """Get the tool search config, loading from AppConfig if needed."""
    global _tool_search_config
    if _tool_search_config is None:
        _tool_search_config = ToolSearchConfig()
    return _tool_search_config


def load_tool_search_config_from_dict(data: dict) -> ToolSearchConfig:
    """Load tool search config from a dict (called during AppConfig loading)."""
    global _tool_search_config
    _tool_search_config = ToolSearchConfig.model_validate(data)
    return _tool_search_config
