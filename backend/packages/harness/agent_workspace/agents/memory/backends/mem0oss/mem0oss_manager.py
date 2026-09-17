"""mem0oss memory backend -- local OSS Mem0 library with Ollama (free, no keys).

Uses ``mem0.Memory.from_config`` with Ollama LLM + Ollama embeddings +
local vector store when the ``mem0ai`` package is installed; otherwise
degrades to a bounded in-memory fact stub so the Gateway still boots
(``startup_policy: tolerate`` default). All failures follow the configured
read (fail_open/fail_closed) and write (log_and_drop/raise) policies.

License: Apache-2.0 (mem0ai/mem0) + Ollama local models -- safe to ship.
Install for full quality: ``pip install mem0ai ollama`` + ``ollama pull
llama3.1 nomic-embed-text``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, ClassVar, Literal

from pydantic import PrivateAttr

from agent_workspace.agents.memory.manager import (
    MemoryManager,
    MemoryManagerError,
    MemoryReadError,
)

from .config import Mem0OssConfig

logger = logging.getLogger(__name__)


def _message_texts(messages: list[Any], limit: int = 20) -> list[dict[str, str]]:
    """Coerce LangChain messages / dicts to mem0 [{role, content}]."""
    out: list[dict[str, str]] = []
    for msg in messages[-limit:]:
        role, content = "user", ""
        if isinstance(msg, dict):
            role = str(msg.get("role", msg.get("type", "user")))
            content = str(msg.get("content", ""))[:2000]
        else:
            role = getattr(msg, "role", None) or getattr(msg, "type", "user")
            role = str(role)
            content = str(getattr(msg, "content", msg))[:2000]
        role = {"human": "user", "ai": "assistant"}.get(role, role)
        if role not in ("user", "assistant", "system"):
            continue
        if content.strip():
            out.append({"role": role, "content": content.strip()})
    return out


def _format_facts(facts: list[dict[str, Any]], max_chars: int) -> str:
    lines: list[str] = []
    used = 0
    for fact in facts:
        content = str(fact.get("content", fact.get("memory", ""))).strip()
        if not content:
            continue
        line = f"- {content}"
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    return "<memory>\nKnown facts about this user:\n" + "\n".join(lines) + "\n</memory>"


class Mem0OssManager(MemoryManager):
    supports_search: ClassVar[bool] = True

    _config: Mem0OssConfig = PrivateAttr()
    _client: Any = PrivateAttr(default=None)
    _degraded: bool = PrivateAttr(default=False)
    _stub: Any = PrivateAttr(default=None)

    def model_post_init(self, __context: Any) -> None:
        self._config = Mem0OssConfig.from_backend_config(self.backend_config)
        self._stub = defaultdict(list)
        try:
            from mem0 import Memory

            mem_config = {
                "llm": {
                    "provider": self._config.llm_provider,
                    "config": {
                        "model": self._config.llm_model,
                        "temperature": 0.1,
                        "max_tokens": 2000,
                        "ollama_base_url": self._config.ollama_base_url,
                    },
                },
                "embedder": {
                    "provider": self._config.embedder_provider,
                    "config": {
                        "model": self._config.embed_model,
                        "embedding_dims": self._config.embedding_dims,
                        "ollama_base_url": self._config.ollama_base_url,
                    },
                },
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "collection_name": self._config.vector_collection,
                        "embedding_model_dims": self._config.embedding_dims,
                        "path": "/tmp/qdrant",
                    },
                },
            }
            self._client = Memory.from_config(mem_config)
        except Exception as exc:
            self._client = None
            self._degraded = True
            if self._config.startup_policy == "fail_fast":
                raise MemoryManagerError(f"mem0oss backend needs `pip install mem0ai` + Ollama running ({self._config.ollama_base_url}); got {exc.__class__.__name__}. Or set startup_policy: tolerate for stub mode.") from exc
            logger.warning("mem0oss degraded stub mode (%s); install mem0ai + Ollama for full recall", exc.__class__.__name__)

    @classmethod
    def from_config(
        cls,
        backend_config: dict[str, Any] | None = None,
        *,
        mode: Literal["middleware", "tool"] = "middleware",
        **host_hooks: Any,
    ) -> Mem0OssManager:
        return cls(backend_config=backend_config, mode=mode)

    # -- tier 1 ---------------------------------------------------------
    def add(
        self,
        thread_id: str,
        messages: list[Any],
        *,
        agent_name: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        texts = _message_texts(messages)
        if not texts:
            return
        uid = user_id or "default"
        if self._client is None:
            bucket = self._stub[uid]
            for item in texts:
                if item["role"] == "user" and item["content"] not in [f["content"] for f in bucket]:
                    bucket.append({"content": item["content"], "score": 0.5})
            del self._stub[uid][200:]
            return
        try:
            self._client.add(texts, user_id=uid, agent_id=agent_name or "lead_agent", run_id=thread_id)
        except Exception as exc:
            if self._config.write_policy == "raise":
                raise MemoryManagerError(f"mem0oss add failed: {exc.__class__.__name__}") from exc
            logger.warning("mem0oss add dropped (%s)", exc.__class__.__name__)

    def get_context(
        self,
        user_id: str | None,
        *,
        agent_name: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        uid = user_id or "default"
        try:
            if self._client is None:
                facts = list(self._stub.get(uid, []))[: self._config.top_k]
            else:
                result = self._client.get_all(user_id=uid, agent_id=agent_name, run_id=thread_id)
                raw = result.get("results", result if isinstance(result, list) else [])
                facts = [{"content": str(r.get("memory", r.get("content", ""))), "score": float(r.get("score", 0.5))} for r in (raw or []) if isinstance(r, dict)]
                facts = [f for f in facts if f["content"].strip()][: self._config.top_k]
            return _format_facts(facts, self._config.max_injection_chars)
        except MemoryReadError:
            raise
        except Exception as exc:
            if self._config.read_policy == "fail_closed":
                raise MemoryReadError(f"mem0oss read failed: {exc.__class__.__name__}") from exc
            logger.warning("mem0oss read fail-open (%s)", exc.__class__.__name__)
            return ""

    # -- tier 2 ----------------------------------------------------------
    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        uid = user_id or "default"
        try:
            if self._client is None:
                terms = [t.lower() for t in query.split() if len(t) > 2]
                scored = []
                for fact in self._stub.get(uid, []):
                    text = fact["content"].lower()
                    score = sum(text.count(t) for t in terms)
                    if score > 0:
                        scored.append({**fact, "score": float(score)})
                scored.sort(key=lambda f: f["score"], reverse=True)
                return scored[:top_k]
            result = self._client.search(query, user_id=uid, agent_id=agent_name, limit=top_k)
            raw = result.get("results", result if isinstance(result, list) else [])
            out = []
            for r in raw or []:
                if not isinstance(r, dict):
                    continue
                if float(r.get("score", 1.0)) < self._config.score_threshold:
                    continue
                out.append(
                    {
                        "id": str(r.get("id", "")),
                        "content": str(r.get("memory", "")),
                        "category": str((r.get("categories") or ["context"])[0]),
                        "confidence": float(r.get("score", 0.5)),
                    }
                )
            return out[:top_k]
        except Exception as exc:
            if self._config.read_policy == "fail_closed":
                raise MemoryReadError(f"mem0oss search failed: {exc.__class__.__name__}") from exc
            return []

    def get_memory(
        self,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        uid = user_id or "default"
        if self._client is None:
            return {"facts": list(self._stub.get(uid, [])), "degraded": True}
        try:
            result = self._client.get_all(user_id=uid, agent_id=agent_name)
            raw = result.get("results", result if isinstance(result, list) else [])
            return {"facts": raw or [], "degraded": False}
        except Exception as exc:
            return {"facts": [], "degraded": True, "error": exc.__class__.__name__}

    def shutdown_flush(self, timeout: float) -> bool:
        return True

    def warm(self) -> bool | None:
        return True if self._client is not None else None
