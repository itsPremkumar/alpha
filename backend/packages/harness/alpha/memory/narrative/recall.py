"""Bounded prompt recall for a persisted narrative story."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import NarrativeConfig
from .store import NarrativeStore


class NarrativeRecall:
    """Render only an existing, readable story; never synthesize at read time."""

    def __init__(self, store: NarrativeStore, config: NarrativeConfig | None = None) -> None:
        self.store = store
        self.config = config if config is not None else store.config

    def story_block(
        self,
        scope: Any = "user",
        limit: int = 8,
        *,
        scope_id: str | None = None,
    ) -> str:
        """Return at most ``limit`` one-line chapters and the configured char cap."""

        if not self.config.enabled:
            return ""
        line_limit = max(0, int(limit))
        if line_limit == 0:
            return ""
        if self.store.story_status(scope, scope_id=scope_id) != "ok":
            return ""
        document = self.store.get_story(scope, scope_id=scope_id)
        if document is None or not document.chapters:
            return ""
        lines: list[str] = []
        for chapter in document.chapters[-line_limit:]:
            detail = chapter.entries[0] if chapter.entries else chapter.summary or "No chapter detail recorded"
            line = f"{chapter.heading}: {detail}".replace("\r", " ").replace("\n", " ")
            lines.append(line[:2_000].rstrip())
        block = "\n".join(lines)
        return block[: self.config.max_chars].rstrip()

    def story_path(self, scope: Any = "user", *, scope_id: str | None = None) -> Path:
        """Return the expected story path, whether or not the file exists."""

        return self.store.story_file(scope, scope_id=scope_id)

    def regenerate(self, scope: Any = "user", **kwargs: Any):
        """Delegate explicit regeneration to an injected facade when available."""

        regenerate = getattr(self, "_regenerate", None)
        if regenerate is not None:
            return regenerate(scope, **kwargs)
        from .synthesis import regenerate_story

        kwargs.setdefault("record_provenance", True)
        return regenerate_story(self.store, scope, config=self.config, **kwargs)


def _recall_from(owner: Any) -> NarrativeRecall:
    store = getattr(owner, "store", owner)
    if not isinstance(store, NarrativeStore):
        raise TypeError("recall owner must expose a NarrativeStore")
    config = getattr(owner, "config", store.config)
    return NarrativeRecall(store, config)


def story_block(
    owner: Any,
    scope: Any = "user",
    limit: int = 8,
    *,
    scope_id: str | None = None,
) -> str:
    """Module-level story-block wrapper."""

    return _recall_from(owner).story_block(scope, limit, scope_id=scope_id)


def story_path(
    owner: Any,
    scope: Any = "user",
    *,
    scope_id: str | None = None,
) -> Path:
    """Module-level story-path wrapper."""

    return _recall_from(owner).story_path(scope, scope_id=scope_id)


def regenerate(owner: Any, scope: Any = "user", **kwargs: Any):
    """Regenerate through a facade or a bare store without read-time synthesis."""

    method = getattr(owner, "regenerate", None)
    if callable(method) and owner is not None and not isinstance(owner, NarrativeStore):
        return method(scope, **kwargs)
    store = getattr(owner, "store", owner)
    if not isinstance(store, NarrativeStore):
        raise TypeError("regenerate owner must expose a NarrativeStore")
    config = getattr(owner, "config", store.config)
    from .synthesis import regenerate_story

    kwargs.setdefault("config", config)
    return regenerate_story(store, scope, **kwargs)


__all__ = ["NarrativeRecall", "regenerate", "story_block", "story_path"]
