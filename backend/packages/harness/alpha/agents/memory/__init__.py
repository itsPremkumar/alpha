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
"""

from alpha.agents.memory.manager import (
    MemoryConflictError,
    MemoryCorruptionError,
    MemoryManager,
    MemoryManagerError,
    MemoryReadError,
    get_memory_manager,
    memory_read_failures_are_fatal,
    reset_memory_manager,
)

__all__ = [
    "MemoryManager",
    "MemoryManagerError",
    "MemoryReadError",
    "MemoryConflictError",
    "MemoryCorruptionError",
    "get_memory_manager",
    "memory_read_failures_are_fatal",
    "reset_memory_manager",
]
