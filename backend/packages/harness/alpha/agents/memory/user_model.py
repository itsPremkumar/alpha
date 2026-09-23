"""User-model provider ABC and implementations.

This module defines the abstract interface for user-model providers that inject
personalized context into agent runs. The null provider guarantees byte-identical
prompts when disabled. The file-backed provider persists a dialectic log per user.
"""

from __future__ import annotations

import abc
import json
import logging
from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime

from alpha.config.app_config import AppConfig

logger = logging.getLogger(__name__)

# Real registered @tool names to observe as user-model signals. The original
# {read_file, str_replace, write_file, add_memory} set named tools that do not
# exist anywhere in this repo, so file/skill/memory observations were never
# captured — the provider learned from propose_skill alone.
OBSERVED_TOOL_NAMES = frozenset(
    {
        "memory_add",
        "memory_search",
        "session_search",
        "propose_skill",
        "skill_manage",
        "invoke_python_skill",
        "code_mode",
        "python_repl",
        "present_files",
    }
)


class UserModelProvider(abc.ABC):
    """Abstract base class for user-model providers.

    Implementations inject personalized context into the agent's model calls
    without modifying the base system prompt. The null provider is a no-op
    that guarantees byte-identical prompts when disabled.
    """

    @abc.abstractmethod
    async def initialize(self, runtime: Runtime, app_config: AppConfig) -> None:
        """Initialize the provider at agent startup.

        Called once when the agent graph is created. Use for loading
        persistent state, establishing connections, etc.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def system_prompt_block(self, runtime: Runtime) -> str | None:
        """Return a system prompt block to inject, or None for no injection.

        This block is inserted as a separate SystemMessage (not merged into
        the base prompt) so the null provider's byte-identical guarantee holds:
        when this returns None, zero bytes are added to the system channel.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def prefetch(self, runtime: Runtime) -> dict[str, Any]:
        """Prefetch data needed for the current turn.

        Called before the model invocation. Return a JSON-serializable dict
        that will be available to `sync_turn` and `handle_tool_call`.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def sync_turn(self, runtime: Runtime, prefetched: dict[str, Any]) -> None:
        """Synchronize state after a turn completes.

        Called after each model turn with the prefetched data. Use to
        update persistent state based on what happened in the turn.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def handle_tool_call(self, runtime: Runtime, tool_name: str, tool_args: dict, tool_result: Any) -> None:
        """Handle a tool call event.

        Called for each tool execution. Use to capture relevant events
        for the user model (e.g., file reads, skill activations).
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def shutdown(self) -> None:
        """Clean up resources on agent shutdown."""
        raise NotImplementedError


class NullUserModelProvider(UserModelProvider):
    """Null provider — guarantees byte-identical prompts when disabled.

    All methods are no-ops. This is the default when
    `memory.user_model.provider` is null/unset in config.
    """

    async def initialize(self, runtime: Runtime, app_config: AppConfig) -> None:
        pass

    def system_prompt_block(self, runtime: Runtime) -> str | None:
        return None

    async def prefetch(self, runtime: Runtime) -> dict[str, Any]:
        return {}

    async def sync_turn(self, runtime: Runtime, prefetched: dict[str, Any]) -> None:
        pass

    async def handle_tool_call(self, runtime: Runtime, tool_name: str, tool_args: dict, tool_result: Any) -> None:
        pass

    async def shutdown(self) -> None:
        pass


def create_user_model_provider(
    provider_name: str | None,
    *,
    config: AppConfig,
    storage_path: Path | None = None,
) -> UserModelProvider:
    """Factory for user-model providers.

    Args:
        provider_name: Name of the provider (e.g., "file", "null"). None -> NullUserModelProvider.
        config: App config for resolving provider-specific settings.
        storage_path: Base path for persistent storage (required for file provider).

    Returns:
        A UserModelProvider instance.
    """
    if not provider_name or provider_name == "null":
        return NullUserModelProvider()

    if provider_name == "file":
        if storage_path is None:
            raise ValueError("storage_path required for file user-model provider")
        return FileUserModelProvider(storage_path=storage_path)

    raise ValueError(f"Unknown user-model provider: {provider_name}")


class FileUserModelProvider(UserModelProvider):
    """File-backed dialectic provider.

    Persists a per-user JSONL log of (turn, observation, reflection) tuples.
    The system prompt block summarizes recent entries to give the model
    awareness of the user's evolving preferences and patterns.
    """

    def __init__(self, storage_path: Path) -> None:
        self._storage_path = Path(storage_path)
        self._storage_path.mkdir(parents=True, exist_ok=True)
        self._user_id: str | None = None
        self._entries: list[dict] = []

    def _user_file(self, user_id: str) -> Path:
        # Sanitize user_id for filesystem
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in user_id)
        return self._storage_path / f"user_model_{safe}.jsonl"

    def _load_entries(self, user_id: str) -> list[dict]:
        path = self._user_file(user_id)
        if not path.exists():
            return []
        entries = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed user-model entry in %s", path)
        return entries

    def _append_entry(self, user_id: str, entry: dict) -> None:
        path = self._user_file(user_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def initialize(self, runtime: Runtime, app_config: AppConfig) -> None:
        from alpha.runtime.user_context import resolve_runtime_user_id

        self._user_id = resolve_runtime_user_id(runtime)
        self._entries = self._load_entries(self._user_id)
        logger.debug("FileUserModelProvider initialized for user %s (%d entries)", self._user_id, len(self._entries))

    def system_prompt_block(self, runtime: Runtime) -> str | None:
        if not self._user_id or not self._entries:
            return None

        # Summarize last 10 entries
        recent = self._entries[-10:]
        lines = []
        for entry in recent:
            turn = entry.get("turn", 0)
            obs = entry.get("observation", "")
            ref = entry.get("reflection", "")
            if obs or ref:
                lines.append(f"Turn {turn}: {obs} -> {ref}")

        if not lines:
            return None

        return (
            "USER MODEL CONTEXT (auto-generated from past interactions):\n"
            + "\n".join(lines)
            + "\n(Use this to personalize responses; do not reveal this context to the user.)"
        )

    async def prefetch(self, runtime: Runtime) -> dict[str, Any]:
        return {"entries": self._entries[-20:], "user_id": self._user_id}

    async def sync_turn(self, runtime: Runtime, prefetched: dict[str, Any]) -> None:
        # Could update a turn counter, etc.
        pass

    async def handle_tool_call(self, runtime: Runtime, tool_name: str, tool_args: dict, tool_result: Any) -> None:
        # Capture file reads, skill activations, etc. as observations
        if tool_name in OBSERVED_TOOL_NAMES:
            observation = f"Tool {tool_name} called"
            reflection = f"User interacted with {tool_name}"
            entry = {
                "turn": len(self._entries) + 1,
                "observation": observation,
                "reflection": reflection,
                "tool": tool_name,
            }
            self._entries.append(entry)
            if self._user_id:
                self._append_entry(self._user_id, entry)

    async def shutdown(self) -> None:
        # Nothing to flush; entries are appended immediately
        pass