"""mem0oss backend config -- local OSS Mem0 with Ollama (no API keys).

Follows the mem0-template pattern: frozen dataclass + ``from_backend_config``.
Host-injected ``storage_path`` / ``should_keep_hidden_message`` are accepted
and ignored; any OTHER unknown key fails fast.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_HOST_INJECTED_KEYS = frozenset({"storage_path", "should_keep_hidden_message"})
_STARTUP_POLICIES = frozenset({"fail_fast", "tolerate"})
_READ_POLICIES = frozenset({"fail_open", "fail_closed"})
_WRITE_POLICIES = frozenset({"log_and_drop", "raise"})


@dataclass(frozen=True)
class Mem0OssConfig:
    llm_provider: str = "ollama"
    llm_model: str = "llama3.1:latest"
    ollama_base_url: str = "http://localhost:11434"
    embedder_provider: str = "ollama"
    embed_model: str = "nomic-embed-text:latest"
    embedding_dims: int = 768
    vector_collection: str = "deerflow_memory"
    top_k: int = 8
    score_threshold: float = 0.1
    max_injection_chars: int = 12000
    startup_policy: str = "tolerate"
    read_policy: str = "fail_open"
    write_policy: str = "log_and_drop"

    @classmethod
    def from_backend_config(cls, backend_config: dict[str, Any] | None) -> Mem0OssConfig:
        cfg = dict(backend_config or {})
        failure_policy = cfg.pop("failure_policy", {}) or {}
        unknown = (
            set(cfg)
            - {
                "llm_provider",
                "llm_model",
                "ollama_base_url",
                "embedder_provider",
                "embed_model",
                "embedding_dims",
                "vector_collection",
                "top_k",
                "score_threshold",
                "max_injection_chars",
                "startup_policy",
            }
            - _HOST_INJECTED_KEYS
        )
        if unknown:
            raise ValueError(f"mem0oss backend_config has unknown keys: {sorted(unknown)}")
        if not isinstance(failure_policy, dict):
            raise ValueError("mem0oss failure_policy must be a mapping {read, write}")
        unknown_fp = set(failure_policy) - {"read", "write"}
        if unknown_fp:
            raise ValueError(f"mem0oss failure_policy has unknown keys: {sorted(unknown_fp)}")
        config = cls(
            llm_provider=str(cfg.get("llm_provider", "ollama")),
            llm_model=str(cfg.get("llm_model", "llama3.1:latest")),
            ollama_base_url=str(cfg.get("ollama_base_url", "http://localhost:11434")).rstrip("/"),
            embedder_provider=str(cfg.get("embedder_provider", "ollama")),
            embed_model=str(cfg.get("embed_model", "nomic-embed-text:latest")),
            embedding_dims=int(cfg.get("embedding_dims", 768)),
            vector_collection=str(cfg.get("vector_collection", "deerflow_memory")),
            top_k=int(cfg.get("top_k", 8)),
            score_threshold=float(cfg.get("score_threshold", 0.1)),
            max_injection_chars=int(cfg.get("max_injection_chars", 12000)),
            startup_policy=str(cfg.get("startup_policy", "tolerate")),
            read_policy=str(failure_policy.get("read", "fail_open")),
            write_policy=str(failure_policy.get("write", "log_and_drop")),
        )
        if config.startup_policy not in _STARTUP_POLICIES:
            raise ValueError(f"mem0oss startup_policy must be one of {sorted(_STARTUP_POLICIES)}")
        if config.read_policy not in _READ_POLICIES:
            raise ValueError(f"mem0oss failure_policy.read must be one of {sorted(_READ_POLICIES)}")
        if config.write_policy not in _WRITE_POLICIES:
            raise ValueError(f"mem0oss failure_policy.write must be one of {sorted(_WRITE_POLICIES)}")
        if not 1 <= config.top_k <= 100:
            raise ValueError("mem0oss top_k must be in [1, 100]")
        if not 0.0 <= config.score_threshold <= 1.0:
            raise ValueError("mem0oss score_threshold must be in [0, 1]")
        if config.max_injection_chars <= 0:
            raise ValueError("mem0oss max_injection_chars must be positive")
        if config.embedding_dims <= 0:
            raise ValueError("mem0oss embedding_dims must be positive")
        return config
