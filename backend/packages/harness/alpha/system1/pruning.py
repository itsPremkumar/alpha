"""System 1 pruning seam for the Dynamic Workflow Engine (DWE).

Two pure helpers the DWE integration calls (the integration itself lives in
``alpha/workflow/runtime.py`` and is owned by the live-runtime batch):

* :func:`prune_tool_catalog` — given the full tool catalog entries (e.g. the
  121-entry ``BUILTIN_TOOLS`` list) plus a node context, select the 4-6
  strictly needed tools. Node-pinned tools (``required_tools``) are always
  kept first; relevance is cosine similarity between the node context and each
  entry (local classifier only — deliberately NO network I/O, no cloud call:
  multi-label top-k over a large catalog is a scoring pass, while the Jev
  cloud client remains available for the single-winner choice/score/noul
  primitives).
* :func:`evaluate_loop_termination` — a ``noul()``-based loop-termination
  helper that is fail-closed: it only recommends termination when the binary
  gate certifies the objective satisfied at ``min_probability`` (default 0.8);
  engine errors or honest uncertainty keep the loop running (still bounded by
  the loop policy's ``max_iterations``), and a failure is reported with the
  real reason instead of a fabricated decision.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np

from alpha.system1.classifier import LocalReflexClassifier
from alpha.system1.engine import get_system1_engine
from alpha.system1.models import NoulResult

DEFAULT_MIN_TOOLS = 4
DEFAULT_MAX_TOOLS = 6
DEFAULT_TERMINATION_PROBABILITY = 0.8
DEFAULT_TERMINATION_QUESTION = "Has the loop objective been completely satisfied: all checks and tests passing with no remaining failures?"
# Bound on how much node context is embedded (latency + memory ceiling).
_MAX_CONTEXT_CHARS = 4000

# Keys that would leak the candidate list itself into the relevance context
# (every candidate would then score identically) — excluded on purpose.
_CANDIDATE_KEYS = frozenset({"tools", "tool", "required_tools", "candidates", "system1_pruned_tools"})

_PRUNE_CLASSIFIER: LocalReflexClassifier | None = None


@dataclass(frozen=True)
class LoopTermination:
    """Fail-closed recommendation from the noul() loop-termination gate."""

    terminate: bool
    probability: float
    reason: str
    decision: NoulResult | None = None


@dataclass(frozen=True)
class _RankedTool:
    name: str
    description: str
    relevance: float


def _prune_classifier() -> LocalReflexClassifier:
    """Shared local classifier for pruning (pure CPU, no network, no cloud)."""
    global _PRUNE_CLASSIFIER
    if _PRUNE_CLASSIFIER is None:
        _PRUNE_CLASSIFIER = LocalReflexClassifier()
    return _PRUNE_CLASSIFIER


def _entry_parts(entry: Any) -> tuple[str, str]:
    """(name, description) from a str, Mapping, or attribute-style tool entry."""
    if isinstance(entry, str):
        return entry.strip(), ""
    if isinstance(entry, Mapping):
        name = entry.get("name") or entry.get("tool_name") or entry.get("id") or ""
        description = entry.get("description") or entry.get("summary") or ""
        return str(name).strip(), str(description)
    name = getattr(entry, "name", "") or ""
    description = getattr(entry, "description", "") or getattr(entry, "summary", "") or ""
    return str(name).strip(), str(description)


def _config_text(config: Any) -> str:
    """Flatten a node config to text, excluding candidate-list keys."""
    if not isinstance(config, Mapping):
        return ""
    parts: list[str] = []
    for key, value in config.items():
        if key in _CANDIDATE_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)):
            parts.append(f"{key} {value}")
        elif isinstance(value, (list, tuple)):
            parts.append(f"{key} {' '.join(str(item) for item in value if isinstance(item, (str, int, float, bool)))}")
    return " ".join(parts)


def _node_context_text(node_context: Any) -> str:
    """Flatten a str / Mapping / WorkflowNode-like object to context text."""
    if isinstance(node_context, str):
        return node_context
    if isinstance(node_context, Mapping):
        source: Mapping[str, Any] = node_context
    else:
        source = {
            "prompt": getattr(node_context, "prompt", None),
            "type": getattr(node_context, "type", None),
            "executor": getattr(node_context, "executor", None),
            "category": getattr(node_context, "category", None),
            "config": getattr(node_context, "config", None),
        }
    parts: list[str] = []
    for key in ("prompt", "question", "objective", "type", "category", "executor"):
        value = source.get(key)
        if value:
            parts.append(str(value))
    parts.append(_config_text(source.get("config")))
    return " ".join(parts)


def _required_names(node_context: Any) -> list[str]:
    """Node-pinned tool names (``required_tools``), order preserved, deduped."""
    raw: Any = None
    if isinstance(node_context, Mapping):
        raw = node_context.get("required_tools")
        if raw is None and isinstance(node_context.get("config"), Mapping):
            raw = node_context["config"].get("required_tools")
    else:
        raw = getattr(node_context, "required_tools", None)
        if raw is None and isinstance(getattr(node_context, "config", None), Mapping):
            raw = node_context.config.get("required_tools")
    if not isinstance(raw, (list, tuple)):
        return []
    names: list[str] = []
    for item in raw:
        name, _ = _entry_parts(item)
        if name and name not in names:
            names.append(name)
    return names


@lru_cache(maxsize=8192)
def _embed_entry(classifier: LocalReflexClassifier, embed_epoch: int, name: str, description: str) -> np.ndarray:
    """Catalog-entry embedding, cached across prune calls.

    ``embed_epoch`` is part of the key: it increments when the classifier's
    embedding backend degrades at runtime (ONNX -> numpy), so vectors from two
    different embedding spaces can never be mixed. Cached vectors are only
    read (cosine dot), never mutated.
    """
    return classifier.embed(f"{name} {description}".strip())


def _score_catalog(
    active: LocalReflexClassifier,
    embed_epoch: int,
    context_vec: np.ndarray,
    ranked: Sequence[_RankedTool],
) -> list[_RankedTool]:
    return [
        _RankedTool(
            name=tool.name,
            description=tool.description,
            relevance=active.similarity(context_vec, _embed_entry(active, embed_epoch, tool.name, tool.description)),
        )
        for tool in ranked
    ]


def prune_tool_catalog(
    candidates: Sequence[Any],
    node_context: Any,
    *,
    min_tools: int = DEFAULT_MIN_TOOLS,
    max_tools: int = DEFAULT_MAX_TOOLS,
    classifier: LocalReflexClassifier | None = None,
) -> list[dict[str, Any]]:
    """Select the 4-6 strictly needed tools for ``node_context`` from ``candidates``.

    Returns plain dicts ``{"name", "description", "relevance"}`` in selection
    order: node-pinned tools first, then cosine-relevance rank. Catalogs of
    ``max_tools`` entries or fewer are returned whole (nothing to prune, input
    order preserved); larger catalogs fill to at least ``min_tools`` in rank
    order so the caller always gets a usable palette. Pure function: the same
    inputs yield the same output.
    """
    if min_tools < 1 or max_tools < min_tools:
        raise ValueError(f"require 1 <= min_tools <= max_tools, got min={min_tools}, max={max_tools}")

    seen: set[str] = set()
    ranked: list[_RankedTool] = []
    for entry in candidates:
        name, description = _entry_parts(entry)
        if not name or name in seen:
            continue
        seen.add(name)
        ranked.append(_RankedTool(name=name, description=description, relevance=0.0))

    active = classifier or _prune_classifier()
    context_text = _node_context_text(node_context)[:_MAX_CONTEXT_CHARS]
    context_vec = active.embed(context_text)
    embed_epoch = active.embed_epoch
    scored = _score_catalog(active, embed_epoch, context_vec, ranked)
    if active.embed_epoch != embed_epoch:
        # The embedder degraded mid-scan (ONNX -> numpy): redo once so the
        # context and every entry share ONE embedding backend (mixing two
        # embedding spaces would corrupt every similarity).
        context_vec = active.embed(context_text)
        embed_epoch = active.embed_epoch
        scored = _score_catalog(active, embed_epoch, context_vec, ranked)

    if len(scored) <= max_tools:
        # Nothing to prune: keep every entry, input order preserved.
        return [{"name": tool.name, "description": tool.description, "relevance": tool.relevance} for tool in scored]

    pinned = [name for name in _required_names(node_context) if name in seen]
    pinned_set = set(pinned)
    by_name = {tool.name: tool for tool in scored}
    ordered = sorted(scored, key=lambda tool: (-tool.relevance, tool.name))

    selection: list[str] = []
    for name in pinned:
        if len(selection) >= max_tools:
            break
        if name not in selection:
            selection.append(name)
    for tool in ordered:
        if len(selection) >= max_tools:
            break
        if tool.name in pinned_set:
            continue
        if len(selection) < min_tools:
            # Guaranteed palette floor: pad in rank order even at zero relevance.
            selection.append(tool.name)
        elif tool.relevance > 0.0:
            selection.append(tool.name)
        else:
            break  # ranked order: nothing below this entry is relevant

    return [{"name": by_name[name].name, "description": by_name[name].description, "relevance": by_name[name].relevance} for name in selection]


def evaluate_loop_termination(
    context: str,
    question: str = DEFAULT_TERMINATION_QUESTION,
    *,
    engine: Any = None,
    min_probability: float = DEFAULT_TERMINATION_PROBABILITY,
) -> LoopTermination:
    """noul()-based loop-termination gate, fail-closed on any doubt.

    * Terminate only when the gate returns ``decision=True`` AND
      ``probability >= min_probability`` — uncertainty keeps the loop running.
    * Engine errors return ``terminate=False`` with the real reason
      (``fail_closed: ...``); no decision is ever fabricated.
    """
    if not math.isfinite(min_probability) or not 0.0 < min_probability <= 1.0:
        raise ValueError(f"min_probability must be inside (0, 1], got {min_probability!r}")
    bounded_context = (context or "")[:_MAX_CONTEXT_CHARS]
    try:
        active = engine if engine is not None else get_system1_engine()
        decision = active.noul(bounded_context, question)
    except Exception as exc:  # noqa: BLE001 - any engine failure keeps looping, honestly disclosed
        return LoopTermination(
            terminate=False,
            probability=0.0,
            reason=f"fail_closed: System 1 noul unavailable ({type(exc).__name__}: {str(exc)[:200]}); loop keeps running",
            decision=None,
        )
    if decision.decision and decision.probability >= min_probability:
        return LoopTermination(
            terminate=True,
            probability=decision.probability,
            reason=(f"noul objective-satisfied gate met: decision=True p={decision.probability:.3f} >= min_probability {min_probability} (engine={decision.engine})"),
            decision=decision,
        )
    if decision.decision:
        reason = f"noul decided True but p={decision.probability:.3f} < min_probability {min_probability}; not terminating (honest uncertainty)"
    else:
        reason = f"noul decision=False (p={decision.probability:.3f}); objective not certified satisfied"
    return LoopTermination(terminate=False, probability=decision.probability, reason=reason, decision=decision)


__all__ = [
    "DEFAULT_MAX_TOOLS",
    "DEFAULT_MIN_TOOLS",
    "DEFAULT_TERMINATION_PROBABILITY",
    "DEFAULT_TERMINATION_QUESTION",
    "LoopTermination",
    "evaluate_loop_termination",
    "prune_tool_catalog",
]
