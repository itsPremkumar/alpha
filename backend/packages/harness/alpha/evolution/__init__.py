"""Bounded evolution: versioned surfaces improve behind benchmark gates."""

from alpha.evolution.engine import EvolutionEngine, get_evolution_engine


def get_update_engine():
    """Lazy accessor for the Phase-2 source update transaction engine."""
    from alpha.evolution.update_engine import get_update_engine as _get_update_engine

    return _get_update_engine()


__all__ = ["EvolutionEngine", "get_evolution_engine", "get_update_engine"]
