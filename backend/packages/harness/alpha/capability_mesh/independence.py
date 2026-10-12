"""Correlated-error awareness and independence scoring (plan §11, Feature J).

Three near-identical completions are not three independent confirmations.
This module answers one question — *how independent are these two entities,
for this task category, on the evidence we actually have?* — and answers it
with an :class:`IndependenceScore` that carries its own justification in
``basis``.

Four rules decide the shape of this module:

* **Only two axes are ever marked by a prior, and both are documented weak
  priors.** A shared ``model_family`` costs ``MODEL_FAMILY_PRIOR`` (0.6) and a
  shared ``model_provider`` costs ``PROVIDER_PRIOR`` (0.7), read from the
  capability graph's ENTITY node attributes. Plan §11.2 allows "known
  model-family/provider relationships as weak priors **only**" and forbids
  inventing undisclosed training-lineage facts — so PROMPT, CONTEXT, SOURCE,
  TOOL_PATH and METHOD are never marked shared by a prior. They are only ever
  marked by a measurement this module can actually see.
* **A measurement always outranks a prior, and both are named.** Measured
  shared failure modes (``correlated_failure_risk``) cost
  ``risk × RISK_PENALTY`` (0.8), and a measured pair whose combined quality
  falls short of its better member contributes that shortfall at full
  strength. Every penalty appends its number to ``basis``.
* **``known=False`` means nothing was measured — it never means safe.**
  An axis nobody observed is not assumed shared, and a score of ``0.0`` with
  ``known=False`` is *unknown*, not "independent" and not "dependent".
* **Source independence is tracked separately from model independence.**
  :func:`source_independence` scores the cited source sets only; two
  different models citing the same unsupported blog do not provide
  independent factual support, and an empty citation set is *unknown*
  (``None``), never independent.

:class:`IndependenceNote` closes the loop on the plan's consensus policy
(§11.3): :meth:`VerificationMethod.MAJORITY_VOTE` is **not** independent
verification, and :func:`verify_with_independent_method` says so by name —
a vote decides which candidates deserve attention, never factual acceptance.

:class:`IndependenceScoreboard` is durable JSON using the package's atomic
write pattern (temp + fsync + ``os.replace``), keyed by the sorted pair plus
task category; a corrupt document raises :class:`IndependenceStoreError`
rather than starting empty and writing amnesia over the file. It is
single-process JSON state: atomic against a crash in one process, **never
cross-process exactly-once**.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from alpha.capability_mesh.complementarity import ComplementarityEvidence, PairMemory, correlated_failure_risk
from alpha.capability_mesh.graph import CapabilityGraph, NodeKind, node_id_for

#: The prior penalty for a shared model family (plan §11.2's weak priors).
#: A shared family raises the odds of a shared error; it is a *documented
#: starting weight*, never a measured training-lineage fact.
MODEL_FAMILY_PRIOR = 0.6

#: The prior penalty for a shared provider. Weaker than the family prior
#: because two different families served by one provider still share at least
#: their serving stack.
PROVIDER_PRIOR = 0.7

#: The penalty per unit of MEASURED shared-failure-mode risk. Distinct from
#: :data:`alpha.capability_mesh.team.RISK_PENALTY` (0.5): that one discounts a
#: team member's marginal value inside team selection, this one discounts an
#: independence claim, where a measured shared failure mode matters more.
RISK_PENALTY = 0.8

#: The weight at which a measured pair's quality shortfall reduces the
#: independence score. It is 1.0 because the shortfall is already a measured
#: quality gap — discounting it would be inventing a smaller penalty.
PAIR_SHORTFALL_PENALTY = 1.0

#: The single JSON document a durable :class:`IndependenceScoreboard` owns.
INDEPENDENCE_FILE_NAME = "independence.json"

#: The ``basis`` of a row where nothing at all was observed. A row must never
#: carry an empty justification, and "unknown" is a real answer here.
NOTHING_MEASURED_BASIS = "nothing measured; independence unknown"

#: The task category independence is read under when the caller does not name
#: one: the wildcard entry, matching the rest of the package.
DEFAULT_TASK_CATEGORY = "*"


class IndependenceAxis(StrEnum):
    """A cause two entities can share, which would correlate their errors (plan §11.1/§11.2).

    Only :attr:`MODEL_FAMILY` and :attr:`PROVIDER` may be marked by a *prior*
    (see :data:`MODEL_FAMILY_PRIOR` / :data:`PROVIDER_PRIOR`). PROMPT, CONTEXT,
    SOURCE, TOOL_PATH and METHOD are only ever marked by an observation this
    package can actually see, because a shared prompt or a shared context is
    not something a passport declares.
    """

    MODEL_FAMILY = "model_family"
    PROVIDER = "provider"
    PROMPT = "prompt"
    CONTEXT = "context"
    SOURCE = "source"
    TOOL_PATH = "tool_path"
    METHOD = "method"


class VerificationMethod(StrEnum):
    """One independent verification mode (plan §11.2's list).

    ``MAJORITY_VOTE`` is in the vocabulary **so it can be refused by name**:
    it is not an independent verification mode (§11.3), and
    :func:`verify_with_independent_method` returns a note saying exactly that
    instead of a silent pass.
    """

    LLM_REVIEW_PLUS_TEST = "llm_review_plus_test"
    MODEL_PLUS_PRIMARY_SOURCE = "model_plus_primary_source"
    CODE_PLUS_COMPILER = "code_plus_compiler"
    EXPLANATION_PLUS_CALCULATION = "explanation_plus_calculation"
    RESEARCH_PLUS_INDEPENDENT_SEARCH = "research_plus_independent_search"
    PATCH_PLUS_CLEAN_WORKTREE = "patch_plus_clean_worktree"
    MAJORITY_VOTE = "majority_vote"


#: The verification modes that genuinely verify, i.e. everything but the vote.
INDEPENDENT_METHODS = frozenset(method for method in VerificationMethod if method is not VerificationMethod.MAJORITY_VOTE)

#: One note per verification method, stating what it actually proves.
VERIFICATION_NOTES: dict[VerificationMethod, str] = {
    VerificationMethod.LLM_REVIEW_PLUS_TEST: "independent verification: a model review is checked by a deterministic test, so the two failure causes do not overlap",
    VerificationMethod.MODEL_PLUS_PRIMARY_SOURCE: "independent verification: the model's answer is checked against the primary source it claims, not against another model's recollection",
    VerificationMethod.CODE_PLUS_COMPILER: "independent verification: generated code is checked by the compiler/type checker it must satisfy",
    VerificationMethod.EXPLANATION_PLUS_CALCULATION: "independent verification: the explanation is checked by an independent arithmetic/calculation",
    VerificationMethod.RESEARCH_PLUS_INDEPENDENT_SEARCH: "independent verification: the research claim is checked by an independent source search over sources the first pass did not read",
    VerificationMethod.PATCH_PLUS_CLEAN_WORKTREE: "independent verification: the patch is applied and executed in a clean worktree, so it cannot pass on the author's local state",
    VerificationMethod.MAJORITY_VOTE: "MAJORITY_VOTE is NOT independent verification: a vote only decides which candidates deserve attention; factual acceptance requires evidence (plan §11.3)",
}


@dataclass(frozen=True)
class IndependenceNote:
    """What one verification mode proves — including the vote, which proves nothing.

    ``note`` is REQUIRED non-empty: a verification claim without its stated
    scope is an assertion, not evidence.
    """

    method: VerificationMethod
    note: str

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "method", VerificationMethod(str(self.method)))
        except ValueError as error:
            allowed = ", ".join(member.value for member in VerificationMethod)
            raise ValueError(f"unknown verification method {self.method!r}; expected one of: {allowed}") from error
        if not self.note:
            raise ValueError("note must be non-empty: a verification claim without its stated scope is an assertion")

    def to_dict(self) -> dict[str, Any]:
        return {"method": self.method.value, "note": self.note}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IndependenceNote:
        return cls(
            # Coerced (and validated with the friendly message) in __post_init__.
            method=data["method"],
            note=str(data["note"]),
        )


def verify_with_independent_method(method: VerificationMethod | str) -> IndependenceNote:
    """State what one verification mode proves, refusing to oversell a vote.

    ``MAJORITY_VOTE`` returns the §11.3 refusal by name: the note says a vote
    decides which candidates deserve attention and never decides factual
    acceptance. Every other mode returns its own scope from the
    :data:`VERIFICATION_NOTES` table.
    """
    try:
        resolved = VerificationMethod(str(method))
    except ValueError as error:
        allowed = ", ".join(member.value for member in VerificationMethod)
        raise ValueError(f"unknown verification method {method!r}; expected one of: {allowed}") from error
    return IndependenceNote(method=resolved, note=VERIFICATION_NOTES[resolved])


@dataclass(frozen=True)
class IndependenceScore:
    """How independent two entities are for one task category (plan §11).

    ``score`` is in [0, 1] where 1.0 means *fully independent on what was
    measured*. ``shared_axes`` names the axes the two entities are known to
    share — an axis nobody observed is NEVER assumed shared, so this tuple is
    empty unless a documented weak prior or a measurement put it there.
    ``known`` is True only when a MEASURED input contributed: a weak prior
    alone leaves ``known=False``, because a prior is not an observation.
    ``basis`` names exactly what was compared, so a reviewer can check the
    number instead of trusting it.
    """

    entity_a: str
    entity_b: str
    task_category: str
    score: float
    known: bool
    shared_axes: tuple[IndependenceAxis, ...]
    basis: str

    def __post_init__(self) -> None:
        for field in ("entity_a", "entity_b", "task_category", "basis"):
            if not getattr(self, field):
                raise ValueError(f"{field} must be non-empty: a score without its identity or its justification is an assertion")
        if self.entity_a == self.entity_b:
            raise ValueError(f"entity_a and entity_b must be distinct entities, got {self.entity_a!r} on both sides: an entity is not independent of itself")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)) or not math.isfinite(float(self.score)) or not 0.0 <= float(self.score) <= 1.0:
            raise ValueError(f"score must be a finite number in [0, 1], got {self.score!r}")
        object.__setattr__(self, "score", float(self.score))
        if not isinstance(self.known, bool):
            raise ValueError(f"known must be a bool, got {self.known!r}")
        axes: list[IndependenceAxis] = []
        for axis in self.shared_axes:
            try:
                axes.append(IndependenceAxis(str(axis)))
            except ValueError as error:
                allowed = ", ".join(member.value for member in IndependenceAxis)
                raise ValueError(f"unknown independence axis {axis!r}; expected one of: {allowed}") from error
        # Sorted and deduplicated: two rows describing the same comparison
        # must compare equal, and one axis is shared at most once.
        object.__setattr__(self, "shared_axes", tuple(sorted(set(axes), key=lambda member: member.value)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_a": self.entity_a,
            "entity_b": self.entity_b,
            "task_category": self.task_category,
            "score": self.score,
            "known": self.known,
            "shared_axes": [axis.value for axis in self.shared_axes],
            "basis": self.basis,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IndependenceScore:
        # The constructor re-validates, so a hand-edited document cannot
        # bypass the honesty rules by skipping __post_init__.
        return cls(
            entity_a=str(data["entity_a"]),
            entity_b=str(data["entity_b"]),
            task_category=str(data["task_category"]),
            score=float(data["score"]),
            known=bool(data["known"]),
            shared_axes=tuple(IndependenceAxis(axis) for axis in data.get("shared_axes", ())),
            basis=str(data["basis"]),
        )


class IndependenceStoreError(RuntimeError):
    """The independence document is unreadable, corrupt, or dishonest."""


class IndependenceScoreboard:
    """In-memory, optionally durable store of :class:`IndependenceScore` rows.

    One board is one directory holding one ``independence.json``. The key is
    ``(sorted(entity_a, entity_b), task_category)`` — the pair is unordered,
    so ``get("a", "b", ...)`` and ``get("b", "a", ...)`` are the same row.
    Constructed WITHOUT a directory the board is purely in-memory:
    :meth:`record` then keeps rows for the life of the object and never
    touches the filesystem. A missing file is a genuinely empty board; a
    corrupt or dishonest one raises :class:`IndependenceStoreError` — one bad
    sector must not become amnesia. Single-process JSON: atomic against a
    crash in one process, **never cross-process exactly-once**.
    """

    def __init__(self, directory: Path | str | None = None) -> None:
        self._directory = Path(directory) if directory is not None else None
        self._scores: dict[str, IndependenceScore] = self._load()

    @property
    def directory(self) -> Path | None:
        return self._directory

    @property
    def durable(self) -> bool:
        """True when this board persists to a directory."""
        return self._directory is not None

    @property
    def path(self) -> Path | None:
        return None if self._directory is None else self._directory / INDEPENDENCE_FILE_NAME

    @staticmethod
    def _key(entity_a: str, entity_b: str, task_category: str) -> str:
        first, second = sorted((entity_a, entity_b))
        return f"{first}\x1f{second}\x1f{task_category}"

    def _load(self) -> dict[str, IndependenceScore]:
        path = self.path
        if path is None:
            return {}
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeDecodeError) as error:
            raise IndependenceStoreError(f"independence board '{path}' is unreadable: {error}") from error
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            raise IndependenceStoreError(f"independence board '{path}' is not valid JSON: {error}") from error
        if not isinstance(document, dict) or not isinstance(document.get("scores", {}), dict):
            raise IndependenceStoreError(f"independence board '{path}' must be a JSON object with a 'scores' mapping")
        scores: dict[str, IndependenceScore] = {}
        for key, entry in document["scores"].items():
            try:
                scores[str(key)] = IndependenceScore.from_dict(entry)
            except (TypeError, KeyError, ValueError) as error:
                raise IndependenceStoreError(f"independence board '{path}' entry {key!r} is invalid: {error}") from error
        return scores

    def save(self) -> None:
        """Atomically rewrite the document (temp + fsync + ``os.replace``).

        Refuses by name when the board has no directory: there is nothing to
        persist TO, and silently discarding the rows would lose them.
        """
        if self._directory is None:
            raise ValueError("this IndependenceScoreboard has no directory: an in-memory board cannot be persisted (construct one with a directory)")
        path = self._directory / INDEPENDENCE_FILE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                json.dump({"scores": {key: score.to_dict() for key, score in self._scores.items()}}, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    # ----------------------------------------------------------------- writes

    def record(self, score: IndependenceScore) -> None:
        """Record (or supersede) the score for the pair's key.

        A newer row supersedes an older one for the same unordered pair plus
        task category; a durable board persists on every record.
        """
        if not isinstance(score, IndependenceScore):
            raise ValueError(f"record() expects an IndependenceScore, got {score!r}")
        self._scores[self._key(score.entity_a, score.entity_b, score.task_category)] = score
        if self._directory is not None:
            self.save()

    # ------------------------------------------------------------------ reads

    def get(self, entity_a: str, entity_b: str, task_category: str = DEFAULT_TASK_CATEGORY) -> IndependenceScore | None:
        """The row for one unordered pair and task category, or None."""
        return self._scores.get(self._key(entity_a, entity_b, task_category))

    def for_entity(self, entity_id: str) -> tuple[IndependenceScore, ...]:
        """Every row involving ``entity_id``, deterministically sorted."""
        rows = [score for score in self._scores.values() if entity_id in (score.entity_a, score.entity_b)]
        return tuple(sorted(rows, key=lambda score: (score.entity_a, score.entity_b, score.task_category)))

    def scores(self) -> tuple[IndependenceScore, ...]:
        """Every stored row, deterministically sorted."""
        return tuple(sorted(self._scores.values(), key=lambda score: (score.entity_a, score.entity_b, score.task_category)))


def _node_attribute(graph: CapabilityGraph, entity_id: str, key: str) -> str | None:
    """One ENTITY node attribute, or None when the node or attribute is absent."""
    node = graph.node(node_id_for(NodeKind.ENTITY, entity_id))
    return None if node is None else node.attribute(key)


def independence_score(
    graph: CapabilityGraph,
    pair_memory: PairMemory,
    entity_a: str,
    entity_b: str,
    *,
    task_category: str = DEFAULT_TASK_CATEGORY,
    profile_version: str,
) -> IndependenceScore:
    """Score the independence of two entities for one task category (plan §11.2).

    Three inputs are combined, and each one that contributes is named in
    ``basis``:

    * a **weak prior** from the graph's ENTITY node attributes — a shared
      ``model_family`` costs ``MODEL_FAMILY_PRIOR`` (0.6), a shared
      ``model_provider`` costs ``PROVIDER_PRIOR`` (0.7). No other axis is ever
      marked by a prior: an undisclosed training-lineage fact is never
      invented, so PROMPT/CONTEXT/SOURCE/TOOL_PATH/METHOD stay unmarked
      unless a measurement marks them.
    * a **measured** shared-failure-mode risk from
      :func:`~alpha.capability_mesh.complementarity.correlated_failure_risk`,
      costing ``risk × RISK_PENALTY`` (0.8). Its own ``known`` flag decides
      whether the row counts as measured: an absence of failure records is
      unknown, never independent.
    * a **measured** pair estimate from ``pair_memory`` under this
      ``task_category``. A SPECULATIVE pair contributes nothing (a hypothesis,
      never evidence), a pair measured under a different
      ``profile_version`` is excluded by the comparability rule, and a pair
      whose combined quality falls short of its better member contributes
      that shortfall at ``PAIR_SHORTFALL_PENALTY``.

    ``score = max(0, 1 - sum(penalties))`` and ``known`` is True only when a
    measured input contributed. With no graph attributes, no recorded failure
    modes and no pair row at all, the answer is ``score 0.0``, ``known=False``
    and the basis :data:`NOTHING_MEASURED_BASIS` — unknown is not safe.

    An entity the graph does not contain is refused by name (via
    ``correlated_failure_risk``): a typo must not read as an empty result.
    """
    if not profile_version:
        raise ValueError("independence_score requires a non-empty profile_version (no score without its profile version)")
    if not isinstance(graph, CapabilityGraph):
        raise ValueError(f"graph must be a CapabilityGraph, got {graph!r}")
    if not isinstance(pair_memory, PairMemory):
        raise ValueError(f"pair_memory must be a PairMemory, got {pair_memory!r}")
    if entity_a == entity_b:
        raise ValueError(f"entity_a and entity_b must be distinct entities, got {entity_a!r} on both sides: an entity is not independent of itself")
    if not task_category:
        raise ValueError("task_category must be non-empty (use the '*' wildcard when no narrower category applies)")

    penalties: list[tuple[float, str]] = []
    disclosures: list[str] = []
    axes: list[IndependenceAxis] = []
    measured = False

    family_a = _node_attribute(graph, entity_a, "model_family")
    family_b = _node_attribute(graph, entity_b, "model_family")
    if family_a and family_b and family_a == family_b:
        axes.append(IndependenceAxis.MODEL_FAMILY)
        penalties.append((MODEL_FAMILY_PRIOR, f"weak prior: both declare model_family '{family_a}' in the graph (-{MODEL_FAMILY_PRIOR} MODEL_FAMILY_PRIOR; a prior, not a measurement)"))
    provider_a = _node_attribute(graph, entity_a, "model_provider")
    provider_b = _node_attribute(graph, entity_b, "model_provider")
    if provider_a and provider_b and provider_a == provider_b:
        axes.append(IndependenceAxis.PROVIDER)
        penalties.append((PROVIDER_PRIOR, f"weak prior: both declare model_provider '{provider_a}' in the graph (-{PROVIDER_PRIOR} PROVIDER_PRIOR; a prior, not a measurement)"))

    risk = correlated_failure_risk(graph, entity_a, entity_b)
    if risk.known:
        measured = True
        if risk.risk > 0.0:
            penalties.append((risk.risk * RISK_PENALTY, f"measured: shared recorded failure modes {list(risk.shared_failure_modes)} give risk {risk.risk:.3f} (-{risk.risk * RISK_PENALTY:.3f} = risk × RISK_PENALTY {RISK_PENALTY})"))
        else:
            disclosures.append("measured: both entities have recorded failure modes and share none (risk 0.000)")

    pair = pair_memory.get(entity_a, entity_b, task_category)
    if pair is not None:
        if pair.evidence is ComplementarityEvidence.SPECULATIVE:
            # A hypothesis contributes nothing, and saying so is the point:
            # a zero-sample row is not evidence of independence either.
            disclosures.append(f"pair estimate under category '{task_category}' is SPECULATIVE ({pair.sample_count} samples): contributes 0 — a hypothesis, never evidence")
        elif pair.profile_version and pair.profile_version != profile_version:
            disclosures.append(f"pair estimate measured under profile_version '{pair.profile_version}' is excluded: this score is scoped to '{profile_version}' (a rubric change invalidates comparability)")
        else:
            measured = True
            value = pair.complementarity_value()
            if value is None:
                disclosures.append(f"measured pair estimate under category '{task_category}' carries no synergy number: 'together' or a solo baseline was never measured")
            elif value < 0.0:
                shortfall = -value * PAIR_SHORTFALL_PENALTY
                penalties.append((shortfall, f"measured: combined pair quality falls {-value:.3f} short of its better member alone (-{shortfall:.3f})"))
            else:
                disclosures.append(f"measured pair estimate under category '{task_category}' shows complementarity {value:+.3f}: no penalty")

    total = sum(amount for amount, _ in penalties)
    score = max(0.0, 1.0 - total)
    if not penalties and not measured and not axes:
        # Unknown is the honest answer, and anything observed-but-unusable
        # (a speculative row, a row under another rubric) travels with it
        # rather than being dropped.
        basis = NOTHING_MEASURED_BASIS if not disclosures else f"{NOTHING_MEASURED_BASIS}; " + "; ".join(disclosures)
        return IndependenceScore(entity_a=entity_a, entity_b=entity_b, task_category=task_category, score=0.0, known=False, shared_axes=(), basis=basis)

    # Every contributing input is named in order: penalties first (they moved
    # the number), then the disclosures that did not.
    parts: list[str] = [reason for _, reason in penalties]
    parts.extend(disclosures)
    if not parts:
        parts.append("every input was observable and none contributed a penalty")
    if not measured:
        parts.append("known=False: only weak priors were available, and a prior is not a measurement")
    return IndependenceScore(entity_a=entity_a, entity_b=entity_b, task_category=task_category, score=score, known=measured, shared_axes=tuple(axes), basis="; ".join(parts))


def _source_set(sources: tuple[str, ...] | list[str]) -> frozenset[str]:
    """The normalised citation set: trimmed, non-empty, deduplicated."""
    if isinstance(sources, str) or not isinstance(sources, (tuple, list)):
        raise ValueError(f"sources must be a tuple or list of strings, got {sources!r}")
    return frozenset(source.strip() for source in sources if isinstance(source, str) and source.strip())


def source_independence(*, sources_a: tuple[str, ...], sources_b: tuple[str, ...]) -> float | None:
    """Independence of the CITED SOURCES behind two answers (plan §11.2).

    ``1 - |A ∩ B| / |A ∪ B|`` over the two citation sets: identical citations
    give 0.0, disjoint citations give 1.0. ``None`` when either side cited
    nothing — an empty record is *unknown*, never independent, because "no
    sources were cited" says nothing about whether the same unsupported blog
    sits behind both answers.

    Source independence is deliberately SEPARATE from model independence
    (:func:`independence_score`): two different models citing the same
    unsupported blog do not provide independent factual support, and this
    function is the only one that can see that.
    """
    set_a = _source_set(sources_a)
    set_b = _source_set(sources_b)
    if not set_a or not set_b:
        return None
    overlap = len(set_a & set_b) / len(set_a | set_b)
    return 1.0 - overlap


__all__ = [
    "DEFAULT_TASK_CATEGORY",
    "INDEPENDENCE_FILE_NAME",
    "INDEPENDENT_METHODS",
    "MODEL_FAMILY_PRIOR",
    "NOTHING_MEASURED_BASIS",
    "PAIR_SHORTFALL_PENALTY",
    "PROVIDER_PRIOR",
    "RISK_PENALTY",
    "VERIFICATION_NOTES",
    "IndependenceAxis",
    "IndependenceNote",
    "IndependenceScore",
    "IndependenceScoreboard",
    "IndependenceStoreError",
    "VerificationMethod",
    "independence_score",
    "source_independence",
    "verify_with_independent_method",
]
