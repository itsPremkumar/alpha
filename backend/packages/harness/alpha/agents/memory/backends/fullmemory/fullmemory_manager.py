"""fullmemory backend -- complete $0 long-term memory composite.

Composition (all free, self-hosted, MIT/Apache-2.0):

- DeerMem (durable default): facts + FTS5 + debounce queue. Always on.
- mem0oss (semantic facts): local OSS Mem0 + Ollama. Best-effort.
- wiki vault (compiled knowledge): Markdown system/* + hot.md digest
  + keyword search. Best-effort, zero dependencies.

Read path merges ``deermem context + mem0oss facts + wiki digest`` within
``max_injection_chars``. Write path fans out to DeerMem + mem0oss and
appends a raw episode to the wiki vault (Dreaming cron compiles episodes
into wiki pages out of band).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, Literal

from pydantic import PrivateAttr

from alpha.agents.memory.manager import MemoryManager

from .config import FullMemoryConfig

logger = logging.getLogger(__name__)


class FullMemoryManager(MemoryManager):
    supports_search: ClassVar[bool] = True

    _config: FullMemoryConfig = PrivateAttr()
    _deermem: Any = PrivateAttr(default=None)
    _mem0oss: Any = PrivateAttr(default=None)
    _storage_root: str = PrivateAttr(default="")

    def model_post_init(self, __context: Any) -> None:
        self._config = FullMemoryConfig.from_backend_config(self.backend_config)
        self._storage_root = str((self.backend_config or {}).get("storage_path", ".agent-workspace/data"))
        # DeerMem is the durable core -- fail fast if it cannot build.
        from alpha.agents.memory.backends.deermem.deer_mem import DeerMem

        self._deermem = DeerMem(backend_config=self.backend_config, mode=self.mode)
        # mem0oss is best-effort (stub when mem0ai/Ollama missing).
        if self._config.mem0oss_enabled:
            try:
                from alpha.agents.memory.backends.mem0oss.mem0oss_manager import Mem0OssManager

                self._mem0oss = Mem0OssManager(backend_config=self._config.mem0oss or {}, mode=self.mode)
            except Exception as exc:
                logger.warning("fullmemory: mem0oss disabled (%s)", exc.__class__.__name__)
                self._mem0oss = None

    @classmethod
    def from_config(
        cls,
        backend_config: dict[str, Any] | None = None,
        *,
        mode: Literal["middleware", "tool"] = "middleware",
        **host_hooks: Any,
    ) -> FullMemoryManager:
        return cls(backend_config=backend_config, mode=mode)

    # -- wiki helper (import-light, stdlib) --------------------------------
    def _wiki_digest(self, user_id: str | None) -> str:
        if not self._config.wiki_enabled:
            return ""
        try:
            from alpha.memory.wiki_vault import ensure_user_vault

            vault = ensure_user_vault(self._storage_root, user_id or "default")
            return vault.digest(max_chars=self._config.wiki_max_chars)
        except Exception as exc:
            logger.warning("fullmemory: wiki digest skipped (%s)", exc.__class__.__name__)
            return ""

    def _wiki_append_episode(self, thread_id: str, messages: list[Any], user_id: str | None) -> None:
        if not self._config.wiki_enabled:
            return
        try:
            from alpha.memory.wiki_vault import ensure_user_vault

            vault = ensure_user_vault(self._storage_root, user_id or "default")
            texts: list[str] = []
            for msg in messages[-6:]:
                content = msg.get("content", "") if isinstance(msg, dict) else str(getattr(msg, "content", ""))
                if isinstance(content, str) and content.strip():
                    texts.append(content.strip()[:500])
            if texts:
                vault.append_episode("\n".join(texts), session_id=thread_id)
        except Exception:
            pass

    # -- tier 1 -------------------------------------------------------------
    def add(
        self,
        thread_id: str,
        messages: list[Any],
        *,
        agent_name: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        self._deermem.add(thread_id, messages, agent_name=agent_name, user_id=user_id, trace_id=trace_id)
        if self._mem0oss is not None:
            try:
                self._mem0oss.add(thread_id, messages, agent_name=agent_name, user_id=user_id, trace_id=trace_id)
            except Exception as exc:
                logger.warning("fullmemory: mem0oss add skipped (%s)", exc.__class__.__name__)
        self._wiki_append_episode(thread_id, messages, user_id)

    def get_context(
        self,
        user_id: str | None,
        *,
        agent_name: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        parts: list[str] = []
        try:
            base = self._deermem.get_context(user_id, agent_name=agent_name, thread_id=thread_id)
            if base.strip():
                parts.append(base)
        except Exception as exc:
            logger.warning("fullmemory: deermem read skipped (%s)", exc.__class__.__name__)
        if self._mem0oss is not None:
            try:
                extra = self._mem0oss.get_context(user_id, agent_name=agent_name, thread_id=thread_id)
                if extra.strip() and extra.strip() not in (parts[0] if parts else ""):
                    parts.append(extra)
            except Exception:
                pass
        digest = self._wiki_digest(user_id)
        if digest.strip():
            parts.append("<wiki>\n" + digest.strip() + "\n</wiki>")
        merged = "\n".join(parts)
        return merged[: self._config.max_injection_chars]

    # -- tier 2 --------------------------------------------------------------
    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for source in (self._mem0oss, self._deermem):
            if source is None:
                continue
            try:
                for fact in source.search(query, top_k=top_k, user_id=user_id, agent_name=agent_name, category=category):
                    content = str(fact.get("content", ""))
                    if content and content not in seen:
                        seen.add(content)
                        results.append(fact)
            except Exception:
                continue
        try:
            from alpha.memory.wiki_vault import ensure_user_vault

            vault = ensure_user_vault(self._storage_root, user_id or "default")
            for hit in vault.search(query, limit=3):
                if hit["excerpt"] not in seen:
                    seen.add(hit["excerpt"])
                    results.append({"content": f"[{hit['path']}] {hit['excerpt'][:400]}", "score": 0.4})
        except Exception:
            pass
        return results[:top_k]

    def get_memory(
        self,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {}
        try:
            data["deermem"] = self._deermem.get_memory(user_id=user_id, agent_name=agent_name)
        except Exception as exc:
            data["deermem_error"] = exc.__class__.__name__
        if self._mem0oss is not None:
            try:
                data["mem0oss"] = self._mem0oss.get_memory(user_id=user_id, agent_name=agent_name)
            except Exception as exc:
                data["mem0oss_error"] = exc.__class__.__name__
        try:
            from alpha.memory.wiki_vault import ensure_user_vault

            vault = ensure_user_vault(self._storage_root, user_id or "default")
            data["wiki_digest"] = vault.digest(max_chars=1000)
        except Exception as exc:
            data["wiki_error"] = exc.__class__.__name__
        return data

    def shutdown_flush(self, timeout: float) -> bool:
        ok = True
        try:
            ok = bool(self._deermem.shutdown_flush(timeout)) and ok
        except Exception:
            ok = False
        return ok

    def warm(self) -> bool | None:
        try:
            return self._deermem.warm()
        except Exception:
            return None
