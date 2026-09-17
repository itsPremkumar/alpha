"""1. Context Engine plugin slot (bootstrap/ingest/assemble/compact).

OpenClaw 2026.3.7 added a ContextEngine plugin slot with full lifecycle
hooks. DeerFlow already owns deerflow.context.engine.ContextEngine
(assemble + watchdog compaction). This module adds the missing generic
slot so operators can plug custom bootstrap/ingest logic without forking
the core engine. Additive: default engine behavior is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class ContextEngineHooks(Protocol):
    """Optional operator hooks. All methods are optional (duck-typed)."""

    def bootstrap(self, config: dict[str, Any]) -> dict[str, Any]:
        """Return extra system context at agent build time."""
        ...

    def ingest(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Pre-process history before assemble (filter/redact/annotate)."""
        ...

    def assemble(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Post-process assembled payload (reorder/pin/budget-tweak)."""
        ...

    def compact(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Custom compaction; return messages to keep."""
        ...


@dataclass
class ContextEnginePlugin:
    """Thin lifecycle wrapper around the core ContextEngine.

    Usage:
        plugin = ContextEnginePlugin()
        plugin.register(MyHooks())  # zero or more
        result = plugin.run_assemble(engine, system_prompt=..., history_messages=...)
    """

    hooks: list[Any] = field(default_factory=list)
    bootstrap_state: dict[str, Any] = field(default_factory=dict)

    def register(self, hook: Any) -> None:
        self.hooks.append(hook)

    def run_bootstrap(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        state: dict[str, Any] = {}
        for hook in self.hooks:
            fn = getattr(hook, "bootstrap", None)
            if callable(fn):
                try:
                    extra = fn(config or {})
                    if isinstance(extra, dict):
                        state.update(extra)
                except Exception:
                    continue
        self.bootstrap_state = state
        return dict(state)

    def run_ingest(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        current = list(messages)
        for hook in self.hooks:
            fn = getattr(hook, "ingest", None)
            if callable(fn):
                try:
                    out = fn(current)
                    if isinstance(out, list):
                        current = out
                except Exception:
                    continue
        return current

    def run_assemble(self, engine: Any, **kwargs: Any) -> Any:
        history = kwargs.get("history_messages")
        if isinstance(history, list):
            kwargs["history_messages"] = self.run_ingest(history)
        # Core owner does the real assemble; hooks only post-process.
        result = engine.assemble(**kwargs)
        messages = getattr(result, "messages", None)
        if isinstance(messages, list):
            for hook in self.hooks:
                fn = getattr(hook, "assemble", None)
                if callable(fn):
                    try:
                        out = fn({"messages": messages, "result": result})
                        if isinstance(out, dict) and isinstance(out.get("messages"), list):
                            messages = out["messages"]
                    except Exception:
                        continue
            try:
                result.messages = messages
            except Exception:
                pass
        return result

    def run_compact(self, engine: Any, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for hook in self.hooks:
            fn = getattr(hook, "compact", None)
            if callable(fn):
                try:
                    out = fn(messages)
                    if isinstance(out, list):
                        return out
                except Exception:
                    continue
        # Fall back to core watchdog compact (keep-recent).
        watchdog = getattr(engine, "watchdog", None)
        if watchdog is not None and hasattr(watchdog, "compact"):
            try:
                return watchdog.compact(messages)
            except Exception:
                pass
        return messages


_plugin_singleton: ContextEnginePlugin | None = None


def get_context_engine_plugin() -> ContextEnginePlugin:
    global _plugin_singleton
    if _plugin_singleton is None:
        _plugin_singleton = ContextEnginePlugin()
    return _plugin_singleton
