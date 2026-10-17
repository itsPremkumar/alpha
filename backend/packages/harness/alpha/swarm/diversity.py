"""Panel decorrelation: refused at composition, measured after the fact.

**The gap this closes.** Anthropic's red-team measurements document swarms where
18/30 agents chose the same branch name, agents independently titled a story with
the same sentence, and over half picked ray-tracers or self-hosting compilers.
Low-variance voters turn an isolated error into a systemic one: a panel of clones
agrees with itself for reasons that have nothing to do with being right.  Alpha
detected domination and sycophancy *after* the fact, but nothing forced or
measured heterogeneity at composition time -- and every worker makes exactly one
``model.invoke`` per task from an isolated system+human pair with no per-worker
sampling variation, so a shared model plus a shared framing is precisely the
shape that produces correlated errors.

Three rules hold here:

- **The declared axes are the only axes.**  The design tuple is
  ``(model_family, temperature, seed, prompt_framing)``.  This build has no
  per-task temperature or seed field and nothing samples per worker, so the audit
  reports the two axes it can actually observe -- the declared ``model_override``
  and the normalized directive framing -- and **names the missing ones** instead
  of inventing values no producer honours.
- **Only a total collision refuses.**  The shipped decomposer varies solver
  framings by construction, so a decomposer ensemble never shares one tuple;
  refusing a partial collision (same model, distinct framings) would refuse every
  healthy ensemble.  A panel whose voters are *identical* on every declared axis
  is refused before registration, with the colliding axes named.
- **Measurement never fabricates agreement.**  Fewer than two outputs is
  ``None`` ("unmeasurable"), never a perfect score, and the similarity is
  *lexical* (shingle Jaccard): it answers "did the voters write the same text",
  never "are the voters right".  A unanimous vote from a near-identical panel is
  ``suspicious_unanimity``, which the aggregator may only downgrade to
  ``manual_review`` -- a guard may block, never approve.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from itertools import combinations
from typing import Any

from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode

COMPOSITION_BASIS = "declared_axes_v1"
DECORRELATION_BASIS = "lexical_shingle_jaccard_v1"
HIGH_SIMILARITY_THRESHOLD = 0.85
SHINGLE_SIZE = 3
# The modes whose decomposer semantics are a voter panel plus a synthesizer:
# ``_decompose_ensemble`` serves both ENSEMBLE and SCATTER_GATHER.
PANEL_MODES = (SwarmMode.ENSEMBLE, SwarmMode.DEBATE, SwarmMode.SCATTER_GATHER)
# Axes the design named that this build cannot observe or honour.  Named, not
# invented: a fake sampling parameter would be a decorative field nothing reads.
UNSUPPORTED_AXES = ("temperature", "seed")

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _normalized_text(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def _resolved_mode(plan: SwarmPlan) -> SwarmMode | None:
    raw = plan.mode
    if isinstance(raw, SwarmMode):
        return raw
    try:
        return SwarmMode(str(raw))
    except ValueError:
        return None


def panel_voters(plan: SwarmPlan) -> list[SwarmTaskNode]:
    """The dependency-free leaf tasks of a panel mode, in ``task_id`` order.

    For the shipped ENSEMBLE/DEBATE decompositions these are the solver /
    advocate tasks; the evaluator or judge depends on the panel and is **not** a
    voter -- a synthesizer is supposed to read everything the voters wrote.
    Non-panel modes have no panel, so the list is empty.
    """

    if _resolved_mode(plan) not in PANEL_MODES:
        return []
    return sorted(
        (task for task in plan.tasks.values() if not task.dependencies),
        key=lambda task: task.task_id,
    )


def composition_audit(plan: SwarmPlan) -> dict[str, Any] | None:
    """Audit the declared decorrelation of a panel's voters, or ``None``.

    ``None`` means there is no panel to audit (not a panel mode, or fewer than
    two voters).  A non-``None`` audit always reports the axes it observed and
    the axes this build cannot observe, so a reader can tell "the panel is
    genuinely varied" from "the variation simply is not measurable here".
    """

    voters = panel_voters(plan)
    if len(voters) < 2:
        return None
    tuples = [(str(task.model_override or ""), _normalized_text(task.objective)) for task in voters]
    models = [item[0] for item in tuples]
    framings = [item[1] for item in tuples]
    model_constant = len(set(models)) == 1
    framing_constant = len(set(framings)) == 1
    colliding = [axis for axis, constant in (("model_family", model_constant), ("framing", framing_constant)) if constant]
    identical = len(set(tuples)) == 1
    declared_models = sorted({value for value in models if value})
    if identical:
        note = "every voter shares one declared tuple; a panel of clones would turn an isolated error into a systemic one"
    elif model_constant and declared_models:
        note = f"all {len(voters)} voters declare the same model; decorrelation rests on directive framing alone"
    elif model_constant:
        note = "no voter declares a model, so every voter resolves to the process default; decorrelation rests on directive framing alone"
    else:
        note = f"{len(set(tuples))} distinct declared (model, framing) tuple(s) across {len(voters)} voters"
    return {
        "basis": COMPOSITION_BASIS,
        "panel_size": len(voters),
        "voters": [task.task_id for task in voters],
        "axes": ["model_family", "framing"],
        "unsupported_axes": list(UNSUPPORTED_AXES),
        "unsupported_reason": ("no per-task sampling field exists and each worker makes one model call per task, so temperature/seed variation is not produced or measured"),
        "declared_models": declared_models,
        "colliding_axes": colliding,
        "identical_tuples": identical,
        "note": note,
    }


def _shingles(text: str) -> frozenset[str]:
    tokens = _TOKEN_RE.findall(text)
    if not tokens:
        return frozenset()
    if len(tokens) < SHINGLE_SIZE:
        return frozenset(tokens)
    return frozenset(" ".join(tokens[index : index + SHINGLE_SIZE]) for index in range(len(tokens) - SHINGLE_SIZE + 1))


def panel_decorrelation(outputs: Sequence[Any]) -> dict[str, Any] | None:
    """Lexical pairwise diversity of voter outputs, or ``None`` if unmeasurable.

    Similarity is the mean Jaccard overlap of token shingles across every voter
    pair; ``decorrelation`` is its complement.  Blank/``None`` outputs are
    ignored.  Fewer than two usable outputs returns ``None`` -- "unmeasured" is
    not "identical" and it is not "perfectly diverse" either.  This is a
    surface-level measure: it detects copy-cat text, not shared reasoning.
    """

    texts = [_normalized_text(text) for text in outputs if isinstance(text, str) and text.strip()]
    if len(texts) < 2:
        return None
    shingle_sets = [_shingles(text) for text in texts]
    similarities: list[float] = []
    identical_pairs = 0
    for left, right in combinations(range(len(texts)), 2):
        left_set, right_set = shingle_sets[left], shingle_sets[right]
        if not left_set and not right_set:
            # Both outputs tokenized to nothing (punctuation-only); equality of
            # the normalized text is the only comparable signal left.
            similarity = 1.0 if texts[left] == texts[right] else 0.0
        elif not left_set or not right_set:
            similarity = 0.0
        else:
            similarity = len(left_set & right_set) / len(left_set | right_set)
        if similarity >= 1.0:
            identical_pairs += 1
        similarities.append(similarity)
    mean_similarity = sum(similarities) / len(similarities)
    return {
        "basis": DECORRELATION_BASIS,
        "voters": len(texts),
        "pairs": len(similarities),
        "similarity": round(mean_similarity, 6),
        "decorrelation": round(1.0 - mean_similarity, 6),
        "identical_pairs": identical_pairs,
    }


def suspicious_unanimity(
    consensus_result: Mapping[str, Any] | None,
    decorrelation: Mapping[str, Any] | None,
) -> bool:
    """The copy-cat signature: a unanimous vote over near-identical outputs.

    Pure measurement, so it can be unit-tested without an aggregator.  Only a
    ``consensus_result`` carrying at least two counted voters and full agreement
    (``1.0``) can qualify, and only a measured similarity at or above
    ``HIGH_SIMILARITY_THRESHOLD``.  Either input being missing or malformed
    returns ``False`` -- unmeasurable never fires a guard, and it never silences
    one either: the caller applies this to approvals only.
    """

    if not isinstance(consensus_result, Mapping) or not isinstance(decorrelation, Mapping):
        return False
    agreement = consensus_result.get("agreement")
    counted = consensus_result.get("counted_voters")
    similarity = decorrelation.get("similarity")
    if not isinstance(agreement, (int, float)) or isinstance(agreement, bool):
        return False
    if not isinstance(counted, (int, float)) or isinstance(counted, bool):
        return False
    if not isinstance(similarity, (int, float)) or isinstance(similarity, bool):
        return False
    if float(counted) < 2 or float(agreement) < 1.0:
        return False
    return float(similarity) >= HIGH_SIMILARITY_THRESHOLD
