"""The verdict: a pure, deterministic function with a documented decision table.

This module answers one question - *may this change become the new default?* -
and it is a pure function of its arguments. It reads no clock, no filesystem, no
environment, and it consults no global. Given the same proposal, integrity
report, gate results and comparison, it always returns the same
:class:`~alpha.evolution.evidence.models.Verdict`, which is what makes the whole
gate replayable from the provenance log.

Decision table (evaluated strictly in this order; the first matching row wins and
its reasons are the verdict's reasons):

===  ===========================  =======================  ============================================
 #   Condition                    Verdict                  Why
===  ===========================  =======================  ============================================
 1   ``config.enabled`` false     insufficient_evidence    default-OFF no-op: nothing was measured,
                                                           judged or written
 2   no integrity report          insufficient_evidence    the judge was never checked
 3   integrity status ``error``   insufficient_evidence    the check could not run
 4   integrity ``suspect`` /      rejected                 the change touched the evaluation surface
     ``compromised``                                       (with the exact path + reason per finding)
 5   a human-review category      needs_human              irreversible / external / financial /
     matched, or an irreversible                             data-destroying: the gate REFUSES to
     change                                                 decide and names the category
 6   a required gate              insufficient_evidence    an ``unavailable``/``error`` gate blocks;
     ``unavailable``/``error``                              it did not pass, it did not run
 7   a required gate ``fail``     rejected                 a precondition is genuinely broken
 8   comparison unusable          insufficient_evidence    missing primary metric, no declared noise
                                                           floor, harness/unit/regime mismatch, or
                                                           too few samples
 9   primary metric not improved  rejected                 ``within_noise`` is not an improvement;
     beyond its floor                                      ``regressed`` on the primary is a rejection
10   any regression beyond its    rejected                 an improvement that quietly degrades another
     own floor                                               metric is refused
11   no rollback path             rejected                 reversible claim without a rollback
                                                           reference
12   blast radius outside policy  rejected                 too wide to decide automatically
13   otherwise                    accepted                 every condition above held
===  ===========================  =======================  ============================================

``accepted`` therefore requires **all** of: integrity clean, every required gate
``pass``, the candidate beating the incumbent beyond the primary metric's
declared noise floor, no regression beyond any metric's floor, a rollback path,
and a blast radius inside policy. Anything the gate could not establish is
``insufficient_evidence`` - never a soft accept.

``needs_human`` is a first-class outcome, not an error: for a change classified
irreversible/external/financial/data-destroying the correct behaviour is to stop
and hand the decision to a person, with the reason attached.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any

from alpha.evolution.evidence.models import ComparisonReport, EvaluationIntegrityReport, GateResult, Proposal, Verdict

if TYPE_CHECKING:  # pragma: no cover - typing only
    from alpha.evolution.evidence.config import EvolutionEvidenceConfig

__all__ = [
    "HUMAN_CATEGORIES",
    "classify_human_review",
    "decide_evidence_verdict",
]

#: The closed set of human-review categories. A match forces ``needs_human``.
HUMAN_CATEGORIES: tuple[str, ...] = ("irreversible", "external", "financial", "data_destroying")


def _matches_any(patterns: Sequence[str], values: Sequence[str]) -> str | None:
    lowered_values = [str(value).lower() for value in values]
    for pattern in patterns:
        candidate = str(pattern).lower()
        for value in lowered_values:
            if fnmatchcase(value, candidate):
                return str(pattern)
    return None


def _human_pattern_hits(proposal: Proposal, patterns: Mapping[str, Sequence[str]] | None) -> dict[str, str]:
    """Map each matched human-review category to the pattern that matched it.

    ``irreversible`` is not derived from a pattern here: it comes from the
    proposal's own ``reversible`` flag, so an undeclared category can never
    remove it.
    """

    hits: dict[str, str] = {}
    values = (proposal.kind, *proposal.touched_paths, *proposal.artifact_paths)
    for category in HUMAN_CATEGORIES:
        if category == "irreversible":
            continue
        declared = tuple(patterns.get(category, ())) if patterns else ()
        hit = _matches_any(declared, values)
        if hit is not None:
            hits[category] = hit
    return hits


def classify_human_review(
    proposal: Proposal,
    *,
    patterns: Mapping[str, Sequence[str]] | None = None,
) -> tuple[str, ...]:
    """Return the human-review categories this proposal falls into.

    ``irreversible`` is unconditional for a proposal that declares itself
    irreversible. The remaining categories come from the operator-declared
    patterns in ``config.human_classification_patterns``, matched with
    :func:`fnmatch.fnmatchcase` against the lowercased proposal ``kind`` and
    each touched/artifact path (so a path is enough to trigger them). An unknown
    pattern can only *add* a category - it can never remove one.
    """

    hits = _human_pattern_hits(proposal, patterns)
    categories: list[str] = []
    if not proposal.reversible:
        categories.append("irreversible")
    categories.extend(category for category in HUMAN_CATEGORIES if category in hits)
    return tuple(categories)


def _category_detail(category: str, pattern: str | None, proposal: Proposal) -> str:
    if category == "irreversible":
        return f"proposal {proposal.id!r} is classified irreversible (kind {proposal.kind!r}); the gate refuses to decide irreversible changes automatically"
    return f"proposal {proposal.id!r} (kind {proposal.kind!r}) matches the {category!r} human-review pattern {pattern!r}; the gate refuses to decide {category} changes automatically"


def decide_evidence_verdict(
    *,
    proposal: Proposal,
    config: EvolutionEvidenceConfig | Any,
    integrity: EvaluationIntegrityReport | None,
    gate_results: Sequence[GateResult],
    comparison: ComparisonReport | None,
) -> Verdict:
    """Decide whether ``proposal`` may become the new default. Pure and total.

    See the module docstring for the full decision table. The function never
    raises for a bad *outcome* - only for a bad *input type* - and every
    non-accepted verdict carries at least one real reason.
    """

    primary_metric = str(getattr(config, "primary_metric", "") or "")

    if not bool(getattr(config, "enabled", False)):
        return Verdict(
            proposal_id=proposal.id,
            status="insufficient_evidence",
            reasons=("the evolution evidence gate is disabled by configuration (default-off): nothing was measured, judged or written",),
            primary_metric=primary_metric or None,
        )

    if integrity is None:
        return Verdict(
            proposal_id=proposal.id,
            status="insufficient_evidence",
            reasons=("no evaluator-integrity report was produced: the judge was never checked, so nothing about this change can be trusted",),
            primary_metric=primary_metric or None,
        )
    if integrity.status == "error":
        return Verdict(
            proposal_id=proposal.id,
            status="insufficient_evidence",
            reasons=(f"the evaluator-integrity check could not run: {integrity.reason or 'no reason disclosed'}",),
            primary_metric=primary_metric or None,
        )
    if integrity.blocking:
        return Verdict(
            proposal_id=proposal.id,
            status="rejected",
            reasons=tuple(f"evaluator integrity is {integrity.status!r}: {finding.path}: {finding.reason}" for finding in integrity.findings) or (f"evaluator integrity is {integrity.status!r}: {integrity.reason}",),
            primary_metric=primary_metric or None,
        )

    patterns = dict(getattr(config, "human_classification_patterns", {}) or {})
    categories = classify_human_review(proposal, patterns=patterns)
    if categories:
        hits = _human_pattern_hits(proposal, patterns)
        return Verdict(
            proposal_id=proposal.id,
            status="needs_human",
            reasons=tuple(_category_detail(category, hits.get(category), proposal) for category in categories),
            primary_metric=primary_metric or None,
            human_categories=categories,
        )

    unavailable = [gate for gate in gate_results if gate.status in {"unavailable", "error"}]
    if unavailable:
        return Verdict(
            proposal_id=proposal.id,
            status="insufficient_evidence",
            reasons=tuple(f"required gate {gate.name!r} is {gate.status}: {gate.detail}" for gate in unavailable),
            primary_metric=primary_metric or None,
        )
    failed = [gate for gate in gate_results if gate.status == "fail"]
    if failed:
        return Verdict(
            proposal_id=proposal.id,
            status="rejected",
            reasons=tuple(f"required gate {gate.name!r} failed: {gate.detail}" for gate in failed),
            primary_metric=primary_metric or None,
        )

    if comparison is None:
        return Verdict(
            proposal_id=proposal.id,
            status="insufficient_evidence",
            reasons=("no comparison was produced: without a candidate-vs-incumbent comparison there is no evidence that this change is an improvement",),
            primary_metric=primary_metric or None,
        )
    primary = comparison.primary
    if primary is None:
        return Verdict(
            proposal_id=proposal.id,
            status="insufficient_evidence",
            reasons=(f"the primary metric {comparison.primary_metric!r} was not measured or compared at all; an unmeasured metric is never a pass",),
            primary_metric=primary_metric or None,
        )
    if not primary.is_usable_evidence:
        return Verdict(
            proposal_id=proposal.id,
            status="insufficient_evidence",
            reasons=(f"the primary metric {primary.metric!r} could not be compared: {primary.reason}",),
            primary_metric=primary_metric or None,
        )
    if not primary.is_improvement:
        return Verdict(
            proposal_id=proposal.id,
            status="rejected",
            reasons=(f"the candidate does not beat the incumbent on the primary metric {primary.metric!r} beyond its declared noise floor {primary.noise_floor!r}: {primary.reason}",),
            primary_metric=primary_metric or None,
        )
    if comparison.regressions:
        return Verdict(
            proposal_id=proposal.id,
            status="rejected",
            reasons=tuple(f"metric {item.metric!r} regressed beyond its declared noise floor {item.noise_floor!r}: {item.reason}" for item in comparison.regressions),
            primary_metric=primary_metric or None,
        )
    if not proposal.has_rollback_path:
        return Verdict(
            proposal_id=proposal.id,
            status="rejected",
            reasons=(f"proposal {proposal.id!r} declares no rollback path (reversible={proposal.reversible!r}, rollback_ref={proposal.rollback_ref!r}); a change that cannot be undone is not promoted",),
            primary_metric=primary_metric or None,
        )
    maximum = int(getattr(config, "max_touched_paths", 0) or 0)
    if proposal.effective_blast_radius > maximum:
        return Verdict(
            proposal_id=proposal.id,
            status="rejected",
            reasons=(f"effective blast radius {proposal.effective_blast_radius} exceeds the configured maximum {maximum}",),
            primary_metric=primary_metric or None,
        )
    return Verdict(
        proposal_id=proposal.id,
        status="accepted",
        reasons=(
            f"evaluator integrity is clean over {len(integrity.checked_paths)} evaluation-surface path(s)",
            f"all {len(gate_results)} required gate(s) passed",
            f"primary metric {primary.metric!r} improved by {primary.delta!r} beyond its declared noise floor {primary.noise_floor!r}",
            f"no metric regressed beyond its declared noise floor across {len(comparison.comparisons)} compared metric(s)",
            f"rollback path {proposal.rollback_ref!r} is present and blast radius {proposal.effective_blast_radius} is within the policy maximum {maximum}",
        ),
        primary_metric=primary_metric or None,
    )
