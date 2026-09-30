"""Demonstration-captured routines (observe once -> replay on demand).

Public surface:

- :class:`~alpha.routines.models.Routine`, :class:`~alpha.routines.models.RoutineStep`
- :class:`~alpha.routines.models.RoutineStore` — durable JSON registry
- :class:`~alpha.routines.recorder.RoutineRecorder` — build a routine from a demo
- :func:`~alpha.routines.recorder.replay` — validated parameter substitution
"""

from __future__ import annotations

from alpha.routines.models import (
    ROUTINE_STORE_SCHEMA_VERSION,
    Routine,
    RoutineError,
    RoutineStep,
    RoutineStore,
    RoutineStoreUnreadable,
    RoutineValidationError,
)
from alpha.routines.recorder import RoutineRecorder, capture_trace, looks_like_input, replay

__all__ = [
    "ROUTINE_STORE_SCHEMA_VERSION",
    "Routine",
    "RoutineError",
    "RoutineRecorder",
    "RoutineStep",
    "RoutineStore",
    "RoutineStoreUnreadable",
    "RoutineValidationError",
    "capture_trace",
    "looks_like_input",
    "replay",
]
