"""Pluggable memory for Alpha.

The shared, backend-agnostic core: the :class:`MemoryManager` contract, the
:func:`get_memory_manager` singleton factory, and :func:`reset_memory_manager`.
Backends live under :mod:`backends` (each self-contained, exposing
``MANAGER_CLASS``); the default DeerMem backend's functional modules live in
``backends/deermem/core/``. Swap backend = drop a ``backends/<name>/`` folder +
set ``MemoryConfig.manager_class`` -- nothing else in agent-workspace changes.

DeerMem-private symbols (``format_memory_for_injection``, ``get_memory_data``,
``MemoryUpdater``, ``FileMemoryStorage``, ...) are NOT re-exported here -- import
them directly from ``alpha.agents.memory.backends.deermem.deermem.core.*``.

Memory-tree boundary (wave P4 documentation): this package is the AGENT-FACING
side -- the pluggable :class:`MemoryManager` contract, its backends (DeerMem,
OpenViking, Honcho) and the prompt-injection seam. The COGNITIVE engines
(beliefs, traces, consolidation, kibitzer nudges, session search, dreaming,
wiki vault) live in the sibling :mod:`alpha.memory` package and are consumed by
the runtime, not by backends. Neither tree may import the other's internals;
any future unification is a separate reviewed decision.

Exports are installed lazily (:pep:`562`) rather than importing
:mod:`alpha.agents.memory.manager` at package-import time. That module reads the
shared ``MemoryConfig`` at import, so an eager re-export made
``import alpha.agents.memory.<anything>`` pull the config layer with it -- which
is exactly the cycle that made a typed-memory subsystem's config impossible to
promote into ``MemoryConfig`` (an earlier revision of this package had to inline
``L1MemoryConfig`` for that reason). With lazy exports, a leaf module such as
``alpha.agents.memory.l1.paths`` imports cleanly on its own, the import graph
stays acyclic, and ``from alpha.agents.memory import get_memory_manager`` keeps
working exactly as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "MemoryConflictError": "manager",
    "MemoryCorruptionError": "manager",
    "MemoryManager": "manager",
    "MemoryManagerError": "manager",
    "MemoryReadError": "manager",
    "get_memory_manager": "manager",
    "memory_read_failures_are_fatal": "manager",
    "reset_memory_manager": "manager",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    # Explicit ``X as X`` re-exports: the runtime surface is installed lazily
    # above, so this block exists for type checkers and IDEs only.
    from alpha.agents.memory.manager import MemoryConflictError as MemoryConflictError
    from alpha.agents.memory.manager import MemoryCorruptionError as MemoryCorruptionError
    from alpha.agents.memory.manager import MemoryManager as MemoryManager
    from alpha.agents.memory.manager import MemoryManagerError as MemoryManagerError
    from alpha.agents.memory.manager import MemoryReadError as MemoryReadError
    from alpha.agents.memory.manager import (
        get_memory_manager as get_memory_manager,
    )
    from alpha.agents.memory.manager import (
        memory_read_failures_are_fatal as memory_read_failures_are_fatal,
    )
    from alpha.agents.memory.manager import (
        reset_memory_manager as reset_memory_manager,
    )

install_lazy_exports(__name__, _EXPORTS)
