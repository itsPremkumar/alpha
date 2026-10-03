"""Phase A — pathway evidence: did the change arrive by the claimed route?

The problem this solves
-----------------------
Alpha measures **whether** a candidate scored higher. It never measured
**whether the gain arrived through the mechanism the candidate was supposed to
change**. PAST-Bench (arXiv 2608.04003) reports the two are only loosely
coupled: "agents with the same headline gain can differ markedly in whether that
gain is supported by evidence of the intended pathway."

That gap is the difference between "the loop works" and "the loop works *for the
reason we think*". A routing change that raises a benchmark because it happened
to match the eval set's phrasing is indistinguishable, to a score, from a
routing change that genuinely improved routing.

The alarming case
-----------------
:class:`PathwayVerdict.NOT_ENGAGED` combined with an improved score. That means
**"this number went up and I cannot say why"** — precisely the condition that
lets a broken loop look healthy forever. It is therefore not a warning, it is a
distinct outcome the journal records as its own event kind.

Three verdicts, and what each refuses
--------------------------------------
* :attr:`PathwayVerdict.ENGAGED` — the mechanism demonstrably changed.
* :attr:`PathwayVerdict.NOT_ENGAGED` — it demonstrably did **not**. A candidate
  claiming to change routing that did not touch a routing decision is refused.
* :attr:`PathwayVerdict.INCONCLUSIVE` — the probe could not run.

:attr:`PathwayVerdict.INCONCLUSIVE` never promotes **and never silently passes**.
It is not folded into ``ENGAGED`` because "I could not check" and "I checked and
it is fine" are different facts, and only one of them is evidence.

Every probe returns ``engaged=None`` with a reason when it cannot run. A probe
that cannot run must never return ``True``: that is how an unverifiable
mechanism becomes a trusted one on the first run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "Mechanism",
    "PathwayVerdict",
    "PathwayEvidence",
    "PathwayProbe",
    "PathwayHypothesis",
    "PathwayReport",
    "assert_pathway",
    "probe_registry",
]


class Mechanism(StrEnum):
    """The mechanism a candidate claims it changes.

    The vocabulary is deliberately small and closed. A candidate that cannot name
    one of these has not stated a testable pathway, and an open-ended string here
    would let "improved everything" pass as a claim.
    """

    ROUTING = "routing"
    PROMPT = "prompt"
    MEMORY_POLICY = "memory_policy"
    RETRIEVAL = "retrieval"
    TOOL_SET = "tool_set"
    EXPERT_MIX = "expert_mix"
    BUDGET = "budget"
    UNKNOWN = "unknown"
    """The candidate stated no mechanism. Never promotes."""


class PathwayVerdict(StrEnum):
    """Whether the claimed mechanism actually moved."""

    ENGAGED = "ENGAGED"
    NOT_ENGAGED = "NOT_ENGAGED"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class PathwayEvidence:
    """One probe's observation.

    ``engaged is None`` means *could not be determined*, which is deliberately
    distinct from ``False`` (*determined not to have engaged*).
    """

    mechanism: Mechanism
    engaged: bool | None
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def verdict(self) -> PathwayVerdict:
        if self.engaged is None:
            return PathwayVerdict.INCONCLUSIVE
        return PathwayVerdict.ENGAGED if self.engaged else PathwayVerdict.NOT_ENGAGED

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism": self.mechanism.value,
            "engaged": self.engaged,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "detail": dict(self.detail),
        }


@runtime_checkable
class PathwayProbe(Protocol):
    """Checks that one mechanism actually changed between two states."""

    mechanism: Mechanism

    def assert_engaged(self, before: Any, after: Any) -> PathwayEvidence: ...


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


def _as_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            candidate = to_dict()
        except Exception:  # noqa: BLE001 - an unreadable state is inconclusive, not a crash
            return None
        return candidate if isinstance(candidate, dict) else None
    return None


class RoutingProbe:
    """Routing must change which expert/agent/tool-set a task resolves to.

    Compares the *decision*, not the configuration text. A routing edit that
    leaves every observed decision identical has not engaged, however many
    characters of config it touched.
    """

    mechanism = Mechanism.ROUTING

    def assert_engaged(self, before: Any, after: Any) -> PathwayEvidence:
        left, right = _as_mapping(before), _as_mapping(after)
        if left is None or right is None:
            return PathwayEvidence(self.mechanism, None, "routing state before/after is not a readable mapping", {})
        before_decisions = left.get("decisions")
        after_decisions = right.get("decisions")
        if not isinstance(before_decisions, (list, tuple)) or not isinstance(after_decisions, (list, tuple)):
            return PathwayEvidence(
                self.mechanism,
                None,
                "routing state carries no 'decisions' sequence to compare; a routing change cannot be evidenced without observed decisions",
                {},
            )
        changed = sum(1 for a, b in zip(before_decisions, after_decisions) if a != b)
        length_delta = abs(len(after_decisions) - len(before_decisions))
        engaged = bool(changed or length_delta)
        return PathwayEvidence(
            self.mechanism,
            engaged,
            f"{changed} of {max(len(before_decisions), len(after_decisions))} routing decision(s) changed" + (f"; observed count moved by {length_delta}" if length_delta else ""),
            {"changed": changed, "before_count": len(before_decisions), "after_count": len(after_decisions)},
        )


class PromptProbe:
    """A prompt change must alter the prompt actually delivered."""

    mechanism = Mechanism.PROMPT

    def assert_engaged(self, before: Any, after: Any) -> PathwayEvidence:
        left = before if isinstance(before, str) else (_as_mapping(before) or {}).get("prompt")
        right = after if isinstance(after, str) else (_as_mapping(after) or {}).get("prompt")
        if not isinstance(left, str) or not isinstance(right, str):
            return PathwayEvidence(self.mechanism, None, "prompt state before/after is not readable text", {})
        engaged = left != right
        return PathwayEvidence(
            self.mechanism,
            engaged,
            f"delivered prompt {'changed' if engaged else 'is byte-identical'} ({len(left)} -> {len(right)} chars)",
            {"before_chars": len(left), "after_chars": len(right)},
        )


class MemoryPolicyProbe:
    """A memory-policy change must alter what is retained or evicted."""

    mechanism = Mechanism.MEMORY_POLICY

    def assert_engaged(self, before: Any, after: Any) -> PathwayEvidence:
        left, right = _as_mapping(before), _as_mapping(after)
        if left is None or right is None:
            return PathwayEvidence(self.mechanism, None, "memory policy state before/after is not a readable mapping", {})
        before_retained = left.get("retained")
        after_retained = right.get("retained")
        if not isinstance(before_retained, (list, tuple, set)) or not isinstance(after_retained, (list, tuple, set)):
            return PathwayEvidence(
                self.mechanism,
                None,
                "memory policy state carries no 'retained' collection; retention behaviour cannot be evidenced from configuration alone",
                {},
            )
        before_set, after_set = set(before_retained), set(after_retained)
        engaged = before_set != after_set
        return PathwayEvidence(
            self.mechanism,
            engaged,
            f"retained set {'changed' if engaged else 'is identical'} (+{len(after_set - before_set)} / -{len(before_set - after_set)})",
            {"added": len(after_set - before_set), "removed": len(before_set - after_set)},
        )


class RetrievalProbe:
    """A retrieval change must alter the retrieved set or its ordering."""

    mechanism = Mechanism.RETRIEVAL

    def assert_engaged(self, before: Any, after: Any) -> PathwayEvidence:
        left, right = _as_mapping(before), _as_mapping(after)
        if left is None or right is None:
            return PathwayEvidence(self.mechanism, None, "retrieval state before/after is not a readable mapping", {})
        before_hits = left.get("retrieved")
        after_hits = right.get("retrieved")
        if not isinstance(before_hits, (list, tuple)) or not isinstance(after_hits, (list, tuple)):
            return PathwayEvidence(self.mechanism, None, "retrieval state carries no 'retrieved' sequence", {})
        engaged = list(before_hits) != list(after_hits)
        overlap = len(set(map(str, before_hits)) & set(map(str, after_hits)))
        return PathwayEvidence(
            self.mechanism,
            engaged,
            f"retrieved {'sequence or ordering changed' if engaged else 'sequence is identical'} ({overlap} shared)",
            {"overlap": overlap, "before_len": len(before_hits), "after_len": len(after_hits)},
        )


class ToolSetProbe:
    """A tool-set change must alter the tool names actually available."""

    mechanism = Mechanism.TOOL_SET

    def assert_engaged(self, before: Any, after: Any) -> PathwayEvidence:
        left, right = _as_mapping(before), _as_mapping(after)
        if left is None or right is None:
            return PathwayEvidence(self.mechanism, None, "tool state before/after is not a readable mapping", {})
        before_tools = left.get("tools")
        after_tools = right.get("tools")
        if not isinstance(before_tools, (list, tuple, set)) or not isinstance(after_tools, (list, tuple, set)):
            return PathwayEvidence(self.mechanism, None, "tool state carries no 'tools' collection", {})
        before_set, after_set = set(map(str, before_tools)), set(map(str, after_tools))
        engaged = before_set != after_set
        return PathwayEvidence(
            self.mechanism,
            engaged,
            f"available tool set {'changed' if engaged else 'is identical'} (+{len(after_set - before_set)} / -{len(before_set - after_set)})",
            {"added": sorted(after_set - before_set)[:20], "removed": sorted(before_set - after_set)[:20]},
        )


class ExpertMixProbe:
    """An expert-mix change must alter which experts are actually chosen."""

    mechanism = Mechanism.EXPERT_MIX

    def assert_engaged(self, before: Any, after: Any) -> PathwayEvidence:
        left, right = _as_mapping(before), _as_mapping(after)
        if left is None or right is None:
            return PathwayEvidence(self.mechanism, None, "expert-mix state before/after is not a readable mapping", {})
        before_mix = left.get("selected")
        after_mix = right.get("selected")
        if not isinstance(before_mix, (list, tuple)) or not isinstance(after_mix, (list, tuple)):
            return PathwayEvidence(self.mechanism, None, "expert-mix state carries no 'selected' sequence", {})
        engaged = list(before_mix) != list(after_mix)
        return PathwayEvidence(
            self.mechanism,
            engaged,
            f"selected experts {'changed' if engaged else 'are identical'} ({len(set(map(str, before_mix)))} -> {len(set(map(str, after_mix)))} distinct)",
            {"before_distinct": len(set(map(str, before_mix))), "after_distinct": len(set(map(str, after_mix)))},
        )


class BudgetProbe:
    """A budget change must alter the numbers actually applied."""

    mechanism = Mechanism.BUDGET

    _KEYS = ("max_depth", "max_attempts", "max_handoffs", "max_retries", "max_parallel_agents", "max_review_rounds")

    def assert_engaged(self, before: Any, after: Any) -> PathwayEvidence:
        left, right = _as_mapping(before), _as_mapping(after)
        if left is None or right is None:
            return PathwayEvidence(self.mechanism, None, "budget state before/after is not a readable mapping", {})
        changed = [key for key in self._KEYS if left.get(key) != right.get(key)]
        engaged = bool(changed)
        return PathwayEvidence(
            self.mechanism,
            engaged,
            f"{len(changed)} budget bound(s) {'changed' if engaged else 'are identical'}" + (f": {', '.join(changed)}" if changed else ""),
            {"changed_keys": changed},
        )


_PROBES: tuple[PathwayProbe, ...] = (
    RoutingProbe(),
    PromptProbe(),
    MemoryPolicyProbe(),
    RetrievalProbe(),
    ToolSetProbe(),
    ExpertMixProbe(),
    BudgetProbe(),
)


def probe_registry() -> dict[Mechanism, PathwayProbe]:
    """The closed mechanism -> probe mapping."""
    return {probe.mechanism: probe for probe in _PROBES}


# ---------------------------------------------------------------------------
# The assertion
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PathwayHypothesis:
    """A candidate's stated pathway."""

    mechanism: Mechanism = Mechanism.UNKNOWN
    detail: str = ""

    @property
    def is_testable(self) -> bool:
        """A hypothesis naming no mechanism is not testable, so it cannot promote."""
        return self.mechanism is not Mechanism.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {"mechanism": self.mechanism.value, "detail": self.detail, "is_testable": self.is_testable}


@dataclass(frozen=True)
class PathwayReport:
    """The outcome of checking one hypothesis."""

    verdict: PathwayVerdict
    mechanism: Mechanism
    reason: str
    evidence: PathwayEvidence | None = None
    score_improved: bool | None = None
    """Whether the candidate's score rose. Carried so the alarming combination
    (improved score + NOT_ENGAGED) is representable rather than merely implied."""

    @property
    def is_anomalous_improvement(self) -> bool:
        """Score up, mechanism provably unchanged. The condition that lets a
        broken loop look healthy."""
        return self.verdict is PathwayVerdict.NOT_ENGAGED and self.score_improved is True

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "mechanism": self.mechanism.value,
            "reason": self.reason,
            "score_improved": self.score_improved,
            "is_anomalous_improvement": self.is_anomalous_improvement,
            "evidence": self.evidence.to_dict() if self.evidence else None,
        }


def assert_pathway(
    hypothesis: PathwayHypothesis,
    before: Any,
    after: Any,
    *,
    score_improved: bool | None = None,
    probes: dict[Mechanism, PathwayProbe] | None = None,
) -> PathwayReport:
    """Check that ``hypothesis`` actually happened.

    Three outcomes, and the mapping to promotion is deliberately asymmetric:

    * ``ENGAGED`` -> does not block promotion on its own.
    * ``NOT_ENGAGED`` -> **blocks** promotion. A candidate that claims to change
      routing and did not is not a routing candidate.
    * ``INCONCLUSIVE`` -> **blocks** promotion, and says why it could not check.
      Never treated as ``ENGAGED``.

    ``score_improved`` is recorded, not acted on here. Whether an anomalous
    improvement blocks is :mod:`alpha.intelligence.regression`'s gate decision —
    this module's job is to make the anomaly *representable*.
    """
    if not hypothesis.is_testable:
        return PathwayReport(
            verdict=PathwayVerdict.NOT_ENGAGED,
            mechanism=hypothesis.mechanism,
            reason="the candidate named no testable mechanism; an untestable pathway cannot be evidenced",
        )

    registry = probes if probes is not None else probe_registry()
    probe = registry.get(hypothesis.mechanism)
    if probe is None:
        return PathwayReport(
            verdict=PathwayVerdict.INCONCLUSIVE,
            mechanism=hypothesis.mechanism,
            reason=f"no probe is registered for mechanism {hypothesis.mechanism.value!r}",
        )

    try:
        evidence = probe.assert_engaged(before, after)
    except Exception as exc:  # noqa: BLE001 - a broken probe is inconclusive, never a pass
        return PathwayReport(
            verdict=PathwayVerdict.INCONCLUSIVE,
            mechanism=hypothesis.mechanism,
            reason=f"probe {type(probe).__name__} raised {type(exc).__name__}: {exc}",
        )

    return PathwayReport(
        verdict=evidence.verdict,
        mechanism=hypothesis.mechanism,
        reason=evidence.reason,
        evidence=evidence,
        score_improved=score_improved,
    )
