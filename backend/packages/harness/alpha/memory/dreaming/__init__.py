"""3-Phase Dreaming Memory Consolidation package inspired by OpenClaw."""

from alpha.memory.dreaming.phases import (
    ConsolidationInsight,
    DreamReport,
    MemorySignal,
    run_deep_sleep_phase,
    run_dream_cycle,
    run_light_sleep_phase,
    run_rem_sleep_phase,
)
from alpha.memory.dreaming.store import DreamStore, get_dream_store

__all__ = [
    "MemorySignal",
    "ConsolidationInsight",
    "DreamReport",
    "run_light_sleep_phase",
    "run_rem_sleep_phase",
    "run_deep_sleep_phase",
    "run_dream_cycle",
    "DreamStore",
    "get_dream_store",
]
