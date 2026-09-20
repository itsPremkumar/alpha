"""2-3. Provider fallback chain + auth rotation + UtilityModel + per-channel override.

Alpha already ships alpha.models.fallback.FallbackChatModel (per-call
failover) and alpha.models.failover (multi-key rotation). This module
adds the missing routing layer OpenClaw 2.0 has:

- build_fallback_chain(names): ordered chain from config model names
- UtilityModelRouter: cheap model for title/memory/summarization, flagship
  for reasoning (saves cost, no behavior change when unconfigured)
- ChannelModelOverride: per-channel / per-user model pin (IM channels already
  support per-user session overrides; this generalizes it)

All helpers are pure and additive; when no config is given they return the
default model name unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChannelModelOverride:
    """Per-channel / per-user model pins.

    Example:
        overrides = ChannelModelOverride(
            by_channel={"telegram": "gpt-4o-mini", "slack": "claude-sonnet-4"},
            by_user={"123456": "vip-agent-model"},
        )
        model = overrides.resolve(channel="telegram", user_id="999", default="gpt-4o")
    """

    by_channel: dict[str, str] = field(default_factory=dict)
    by_user: dict[str, str] = field(default_factory=dict)

    def resolve(self, channel: str = "", user_id: str = "", default: str = "") -> str:
        if user_id and user_id in self.by_user:
            return self.by_user[user_id]
        if channel and channel in self.by_channel:
            return self.by_channel[channel]
        return default


_UTILITY_TASKS = frozenset({"title", "memory", "summarization", "followup_suggestions", "skill_review"})


@dataclass
class UtilityModelRouter:
    """Route cheap background tasks to a utility model.

    When utility_model is empty every task uses the flagship model (current
    behavior). When set, tasks in _UTILITY_TASKS use it.
    """

    flagship_model: str = ""
    utility_model: str = ""

    def route(self, task: str, requested: str = "") -> str:
        if requested:
            return requested
        if self.utility_model and task in _UTILITY_TASKS:
            return self.utility_model
        return self.flagship_model or requested


def build_fallback_chain(primary: str, fallbacks: list[str] | None = None) -> list[str]:
    """Return ordered [primary, *fallbacks] with blanks/dupes removed."""
    chain: list[str] = []
    for name in [primary, *(fallbacks or [])]:
        if isinstance(name, str) and name.strip() and name not in chain:
            chain.append(name)
    return chain


def is_retryable_error_name(error_name: str) -> bool:
    """Mirror of FallbackChatModel retryable set (name-only, no SDK import)."""
    return error_name in {
        "APITimeoutError",
        "APIConnectionError",
        "RateLimitError",
        "InternalServerError",
        "TimeoutError",
        "ConnectError",
        "ServiceUnavailable",
        "OverloadedError",
    }
