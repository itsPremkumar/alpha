"""Capability Epoch detector and prompt staleness manager."""

from __future__ import annotations

import re

from agent_workspace.bots.profile import BotProfile

_EPOCH_PATTERN = re.compile(r"Capability epoch: ([0-9a-f]{12})")


class CapabilityEpochManager:
    """Manages epoch embedding and prompt freshness detection."""

    @staticmethod
    def is_prompt_stale(bot: BotProfile, prompt_text: str) -> bool:
        """Check if an existing prompt has a stale capability epoch."""
        match = _EPOCH_PATTERN.search(prompt_text)
        if not match:
            return True
        stored_epoch = match.group(1)
        current_epoch = bot.capability_fingerprint()
        return stored_epoch != current_epoch

    @staticmethod
    def embed_epoch(bot: BotProfile, prompt_text: str) -> str:
        """Embed or update the bot's capability epoch in prompt text."""
        epoch_str = f"Capability epoch: {bot.capability_fingerprint()}"
        if _EPOCH_PATTERN.search(prompt_text):
            return _EPOCH_PATTERN.sub(epoch_str, prompt_text)
        return f"{prompt_text.rstrip()}\n\n{epoch_str}\n"
