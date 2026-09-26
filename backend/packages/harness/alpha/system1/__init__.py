"""System 1 fast reflex harness (dual-process decision layer).

Non-autoregressive decision primitives for the fast, zero-token path of the
dual-process architecture (Kahneman System 1 vs System 2):

* :class:`~alpha.system1.models.ChoiceRequest` / ``ChoiceResult`` — one winner
  from an unordered candidate set plus the full probability distribution.
* :class:`~alpha.system1.models.ScoreRequest` / ``ScoreResult`` — ordered
  continuous 0.0-1.0 score plus confidence.
* :class:`~alpha.system1.models.NoulRequest` / ``NoulResult`` — strict binary
  decision plus probability.

The default engine is the local free classifier (no API key, zero token cost,
CPU-only); the cloud Jev client activates ONLY when ``$JEV_API_KEY`` is
present in the environment. Registered as capability ``system1_reflex`` in
:mod:`alpha.capabilities.catalog`.

Invariant reminder: every result is a probability-bearing decision object with
its provenance disclosed — a System 1 output is a proposal for the
deterministic runtime to enforce, never a bypass of safety policy.
"""

from alpha.system1.classifier import LocalReflexClassifier
from alpha.system1.engine import System1Engine, get_system1_engine, reset_system1_engine
from alpha.system1.models import (
    ENGINE_JEV,
    ENGINE_LOCAL,
    ChoiceRequest,
    ChoiceResult,
    DecisionType,
    NoulRequest,
    NoulResult,
    ScoreRequest,
    ScoreResult,
)
from alpha.system1.pruning import (
    DEFAULT_MAX_TOOLS,
    DEFAULT_MIN_TOOLS,
    DEFAULT_TERMINATION_PROBABILITY,
    DEFAULT_TERMINATION_QUESTION,
    LoopTermination,
    evaluate_loop_termination,
    prune_tool_catalog,
)

__all__ = [
    "DEFAULT_MAX_TOOLS",
    "DEFAULT_MIN_TOOLS",
    "DEFAULT_TERMINATION_PROBABILITY",
    "DEFAULT_TERMINATION_QUESTION",
    "ENGINE_JEV",
    "ENGINE_LOCAL",
    "ChoiceRequest",
    "ChoiceResult",
    "DecisionType",
    "LocalReflexClassifier",
    "LoopTermination",
    "NoulRequest",
    "NoulResult",
    "ScoreRequest",
    "ScoreResult",
    "System1Engine",
    "evaluate_loop_termination",
    "get_system1_engine",
    "prune_tool_catalog",
    "reset_system1_engine",
]
