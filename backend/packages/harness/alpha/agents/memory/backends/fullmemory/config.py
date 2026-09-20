"""fullmemory backend config -- composite DeerMem + mem0oss + wiki digest.

All sub-backends are best-effort except DeerMem (the durable default):
a mem0oss/wiki failure never breaks the run (logged + skipped).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_HOST_INJECTED_KEYS = frozenset({"storage_path", "should_keep_hidden_message"})


@dataclass(frozen=True)
class FullMemoryConfig:
    mem0oss_enabled: bool = True
    mem0oss: dict[str, Any] | None = None
    wiki_enabled: bool = True
    wiki_max_chars: int = 2000
    top_k: int = 8
    max_injection_chars: int = 12000

    @classmethod
    def from_backend_config(cls, backend_config: dict[str, Any] | None) -> FullMemoryConfig:
        cfg = dict(backend_config or {})
        unknown = (
            set(cfg)
            - {
                "mem0oss_enabled",
                "mem0oss",
                "wiki_enabled",
                "wiki_max_chars",
                "top_k",
                "max_injection_chars",
            }
            - _HOST_INJECTED_KEYS
        )
        if unknown:
            raise ValueError(f"fullmemory backend_config has unknown keys: {sorted(unknown)}")
        mem0oss = cfg.get("mem0oss")
        if mem0oss is not None and not isinstance(mem0oss, dict):
            raise ValueError("fullmemory mem0oss must be a mapping")
        return cls(
            mem0oss_enabled=bool(cfg.get("mem0oss_enabled", True)),
            mem0oss=dict(mem0oss) if isinstance(mem0oss, dict) else None,
            wiki_enabled=bool(cfg.get("wiki_enabled", True)),
            wiki_max_chars=int(cfg.get("wiki_max_chars", 2000)),
            top_k=int(cfg.get("top_k", 8)),
            max_injection_chars=int(cfg.get("max_injection_chars", 12000)),
        )
