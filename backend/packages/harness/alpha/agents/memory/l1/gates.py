"""Configuration for the L1 typed-memory pipeline (scene segmentation + typed extraction).

Adapted from TencentDB-Agent-Memory ``MemoryCore/src/core/`` (MIT,
see ``docs/THIRD_PARTY_MEMORY_NOTICES.md``).

``L1MemoryConfig`` lives with the rest of the memory schema in
:mod:`alpha.config.memory_config` (import direction: config -> nothing inside
the agent memory package, which would be circular). This module only holds
gate helpers that the pipeline and middleware read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alpha.config.memory_config import MemoryConfig


def l1_enabled(cfg: MemoryConfig | None = None) -> bool:
    """True when the L1 pipeline may run: master gate AND L1 gate.

    Accepts an optional host ``MemoryConfig`` so call sites that already
    resolved config do not re-read the singleton. Every consumer (middleware
    registration, recall injection, pipeline entry) funnels through this one
    helper so the two-level gate can never drift between call sites.
    """
    from alpha.config.memory_config import get_memory_config

    memory_cfg = cfg if cfg is not None else get_memory_config()
    if not memory_cfg.enabled:
        return False
    return bool(memory_cfg.l1.enabled)
