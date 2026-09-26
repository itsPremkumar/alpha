"""Package-local gate for narrative capture and recall.

The gate is deliberately independent of the host ``MemoryConfig`` so enabling
this wave cannot silently change existing memory behavior.
"""

from __future__ import annotations

from .config import NarrativeConfig, narrative_enabled

__all__ = ["NarrativeConfig", "narrative_enabled"]
