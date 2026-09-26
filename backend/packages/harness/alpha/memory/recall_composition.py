"""Central composition of the additive memory-type recall blocks.

Alpha's wave-2 memory types each own their store, their config gate and their
own bounded renderer. What none of them should do is reach into the agent
prompt themselves: that would make every new type a patch to a shared,
contested file, and it would make "is this type actually wired?" a question
only a human reading five call sites could answer.

This module is the single seam. It takes the shared ``MemoryConfig``, asks each
*enabled* type for its real block, and returns one bounded string plus a
per-surface status record. The lead agent appends ``result.text`` to its
existing ``<memory>`` block; nothing else in the agent changes when a new type
is added.

Design rules that are load-bearing, not stylistic:

* **Default-OFF means invisible.** A disabled type contributes no text, no
  heading, and no "disabled" notice. With every type off, this module returns
  an empty string and the host's memory block is byte-identical to before it
  existed. That is what makes the wave safe to land.
* **A broken type cannot break the turn.** Each surface is rendered inside its
  own guard. A failure is recorded as a status with the exception TYPE, the
  remaining surfaces still render, and nothing is invented to fill the gap.
* **No fabrication.** The text is whatever the owning package's renderer
  produced. If a store is empty the block is empty; if a store is corrupt the
  package's own honest "unavailable" wording is passed through rather than
  being replaced with something reassuring.
* **Deterministic order.** Surfaces render in a fixed order and the status
  record is sorted the same way, so two runs with the same state produce the
  same bytes.
* **Bounded.** The combined text is capped, and truncation is DISCLOSED in the
  output rather than silently cutting a block in half.

Capture (writing) is deliberately NOT here: ingestion belongs to the turn
middlewares, which know the turn outcome. This module only reads.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# The order surfaces are rendered in. Fixed, so the composed block is stable.
#
# L1 is deliberately NOT in this list. Its recall path is already wired and
# tested directly in the lead-agent prompt, with its own `l1_enabled()` gate
# and its own bound-pipeline resolution (an explicitly supplied app_config must
# govern both the gate and the store it reads). Folding it in here would
# re-render the same block twice and would quietly move a landed, verified seam.
SURFACE_ORDER: tuple[str, ...] = (
    "affective",
    "prospective",
    "entities",
    "social",
    "narrative",
)

# Total character cap for the composed wave-2 block. Individual packages apply
# their own (smaller) caps; this is the belt to their braces.
MAX_TOTAL_CHARS = 4_000

# Status values a surface can report.
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_DISABLED = "disabled"
STATUS_ERROR = "error"

_TRUNCATION_NOTICE = "\n[memory: composed block truncated at the configured cap]"


@dataclass(frozen=True)
class SurfaceStatus:
    """What happened for one surface during composition."""

    surface: str
    status: str
    chars: int = 0
    detail: str = ""

    @property
    def contributes_text(self) -> bool:
        return self.status == STATUS_OK and self.chars > 0


@dataclass(frozen=True)
class CompositionResult:
    """The composed block plus an auditable per-surface record."""

    text: str = ""
    surfaces: tuple[SurfaceStatus, ...] = field(default_factory=tuple)
    truncated: bool = False

    @property
    def enabled_surfaces(self) -> tuple[str, ...]:
        return tuple(s.surface for s in self.surfaces if s.status != STATUS_DISABLED)

    @property
    def failed_surfaces(self) -> tuple[str, ...]:
        return tuple(s.surface for s in self.surfaces if s.status == STATUS_ERROR)

    def status_for(self, surface: str) -> str | None:
        for item in self.surfaces:
            if item.surface == surface:
                return item.status
        return None


def _type_enabled(config: Any, name: str) -> bool:
    """A type is on only when BOTH the host memory switch and its own gate are on.

    The two-level gate is the same contract the L1 middleware uses; a type that
    is switched off must not be constructible from the host even if its own
    config says otherwise.
    """
    if config is None:
        return False
    if not getattr(config, "enabled", False):
        return False
    section = getattr(config, name, None)
    if section is None:
        return False
    return bool(getattr(section, "enabled", False))


def _affective_block(config: Any, *, user_id: str, agent_name: str | None, now: float | None) -> str:
    from alpha.memory.affective.memory import AffectiveMemory
    from alpha.memory.affective.recall import render_block

    memory = AffectiveMemory(config=config.affective)
    return str(
        render_block(
            memory,
            user_id,
            agent_name=agent_name,
            now=now,
        )
        or ""
    )


def _prospective_block(config: Any, *, user_id: str, agent_name: str | None, now: float | None) -> str:
    from alpha.memory.prospective.recall import render_block
    from alpha.memory.prospective.store import ProspectiveStore

    store = ProspectiveStore(config.prospective)
    return str(
        render_block(
            store,
            config=config.prospective,
            max_surfaced=config.prospective.max_surfaced_per_recall,
        )
        or ""
    )


def _entities_block(config: Any, *, user_id: str, agent_name: str | None, now: float | None) -> str:
    from alpha.memory.entities.recall import EntityRecall
    from alpha.memory.entities.store import EntityStore

    store = EntityStore(config=config.entities)
    recall = EntityRecall(config.entities, store)
    return str(recall.render_block(user_id=user_id) or "")


def _social_block(config: Any, *, user_id: str, agent_name: str | None, now: float | None) -> str:
    from alpha.memory.social.recall import relationship_block
    from alpha.memory.social.system import SocialMemorySystem

    system = SocialMemorySystem(config=config.social)
    block = relationship_block(system, config.social, user_id, now=now)
    return str(getattr(block, "text", "") or "")


def _narrative_block(config: Any, *, user_id: str, agent_name: str | None, now: float | None) -> str:
    from alpha.memory.narrative.recall import NarrativeRecall
    from alpha.memory.narrative.store import NarrativeStore

    store = NarrativeStore(config=config.narrative)
    recall = NarrativeRecall(store, config.narrative)
    return str(recall.story_block(scope="user", scope_id=user_id) or "")


_RENDERERS: dict[str, Callable[..., str]] = {
    "affective": _affective_block,
    "prospective": _prospective_block,
    "entities": _entities_block,
    "social": _social_block,
    "narrative": _narrative_block,
}


def compose_typed_memory_blocks(
    config: Any,
    *,
    user_id: str,
    agent_name: str | None = None,
    now: float | None = None,
    surfaces: tuple[str, ...] | None = None,
    max_total_chars: int = MAX_TOTAL_CHARS,
) -> CompositionResult:
    """Render every enabled surface and return one bounded block.

    ``config`` is the shared ``MemoryConfig``. ``user_id`` scopes every store.
    ``surfaces`` narrows the render set (diagnostics/tests); the default is
    every surface in :data:`SURFACE_ORDER`. ``max_total_chars`` caps the
    combined text and is disclosed when it bites.
    """
    wanted = tuple(surfaces) if surfaces is not None else SURFACE_ORDER
    unknown = [name for name in wanted if name not in _RENDERERS]
    if unknown:
        msg = f"unknown memory surface(s): {sorted(unknown)}"
        raise ValueError(msg)

    if not user_id or not str(user_id).strip():
        msg = "compose_typed_memory_blocks requires a non-empty user_id"
        raise ValueError(msg)

    parts: list[str] = []
    statuses: list[SurfaceStatus] = []
    used = 0
    truncated = False

    for name in wanted:
        if not _type_enabled(config, name):
            statuses.append(SurfaceStatus(surface=name, status=STATUS_DISABLED))
            continue

        renderer = _RENDERERS[name]
        try:
            text = renderer(config, user_id=user_id, agent_name=agent_name, now=now)
        except Exception as exc:  # noqa: BLE001 - one bad type must not fail the turn
            # Record the failure with its TYPE, never a fabricated replacement
            # block, and keep the other surfaces rendering.
            logger.debug("memory surface %s failed to render: %s", name, type(exc).__name__, exc_info=True)
            statuses.append(
                SurfaceStatus(
                    surface=name,
                    status=STATUS_ERROR,
                    detail=type(exc).__name__,
                )
            )
            continue

        cleaned = (text or "").strip()
        if not cleaned:
            statuses.append(SurfaceStatus(surface=name, status=STATUS_EMPTY))
            continue

        if used + len(cleaned) > max_total_chars:
            remaining = max(0, max_total_chars - used)
            if remaining > 0:
                parts.append(cleaned[:remaining])
                used += remaining
            truncated = True
            statuses.append(
                SurfaceStatus(
                    surface=name,
                    status=STATUS_OK,
                    chars=len(cleaned),
                    detail="truncated",
                )
            )
            break

        parts.append(cleaned)
        used += len(cleaned)
        statuses.append(SurfaceStatus(surface=name, status=STATUS_OK, chars=len(cleaned)))

    text = "\n\n".join(parts)
    if truncated:
        text += _TRUNCATION_NOTICE
    return CompositionResult(text=text, surfaces=tuple(statuses), truncated=truncated)


__all__ = [
    "MAX_TOTAL_CHARS",
    "STATUS_DISABLED",
    "STATUS_EMPTY",
    "STATUS_ERROR",
    "STATUS_OK",
    "SURFACE_ORDER",
    "CompositionResult",
    "SurfaceStatus",
    "compose_typed_memory_blocks",
]
