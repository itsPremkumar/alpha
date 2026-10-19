"""The conservative composer (guide §7 step 7, §11.2).

The composer's job is to produce the *cheapest evidence-supported* successor,
and its most important output is often "no change". Four rules make that
mechanical rather than aspirational:

* **Only a supported benefit adopts an edit.** A ``positive`` contrast is
  necessary but not sufficient: the targeted failure must not have been
  observed again (``targeted_failure_resolved is not False``).
* **Contradictions are resolved explicitly, by edit cost.** When two candidates
  touch the same component, the cheaper intervention wins — tool guidance
  before skill before script before subagent before always-on prompt text —
  and the loser is recorded with the conflict as its reason instead of being
  dropped silently.
* **The baseline is retained when nothing is supported.** A neutral, confounded
  or negative contrast retains the base component, and an empty adoption set
  returns the baseline bundle itself with ``retained_baseline=True``.
* **Policy can never be weakened.** A diff naming a protected component is
  refused here as well as in the patch validator, so a composer bug cannot
  promote a policy edit that validation would have caught.

Every adopted or rejected edit travels with the evidence refs that justify the
decision; the decision record is the audit trail, not a summary of one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from alpha.harness_improvement.components import build_bundle, resolve_components
from alpha.harness_improvement.models import (
    PRIVILEGED_KINDS,
    PROTECTED_COMPONENT_IDS,
    AdoptedEdit,
    CandidateScope,
    ComponentDiff,
    ComponentKind,
    CompositionDecision,
    ContrastReport,
    ContrastVerdict,
    HarnessBundle,
    HarnessProposal,
    PromotionState,
    RejectedEdit,
)

#: The guide's edit-cost order, made numeric: lower wins a conflict.
EDIT_COST_RANK: dict[ComponentKind, int] = {
    ComponentKind.TOOL_GUIDANCE: 0,
    ComponentKind.SKILL: 1,
    ComponentKind.SCRIPT: 2,
    ComponentKind.SUBAGENT: 3,
    ComponentKind.MEMORY_REFERENCE: 4,
    ComponentKind.SYSTEM_PROMPT: 5,
    ComponentKind.GUARDRAIL_GUIDANCE: 6,
    ComponentKind.MIDDLEWARE: 7,
}


@dataclass(frozen=True, slots=True)
class ComposerInput:
    """One candidate, complete: proposal, evidence, bundle and its content."""

    proposal: HarnessProposal
    contrast: ContrastReport
    candidate_bundle: HarnessBundle
    candidate_contents: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal": self.proposal.to_dict(),
            "contrast": self.contrast.to_dict(),
            "candidate_bundle_id": self.candidate_bundle.bundle_id,
        }


def edit_cost_rank(diff: ComponentDiff) -> int:
    return EDIT_COST_RANK.get(diff.kind, 99)


def _eligible(inputs: ComposerInput) -> str | None:
    """Return a rejection reason, or ``None`` when the candidate is adoptable."""
    contrast = inputs.contrast
    if contrast.verdict is not ContrastVerdict.POSITIVE:
        return f"contrast verdict is {contrast.verdict}, not positive"
    if contrast.targeted_failure_resolved is False:
        return "the targeted failure was observed again under this candidate"
    if contrast.regressions:
        return f"candidate shows regressions: {'; '.join(contrast.regressions)}"
    if not contrast.evidence_refs:
        return "contrast carries no evidence refs"
    return None


def compose_successor(
    *,
    base_bundle: HarnessBundle,
    base_contents: Mapping[str, str],
    inputs: Sequence[ComposerInput],
    created_by: str = "harness_composer",
    policy_version: str | None = None,
) -> tuple[CompositionDecision, HarnessBundle, dict[str, str]]:
    """Compose the successor bundle and the decision record justifying it.

    Returns ``(decision, successor_bundle, successor_contents)``. When no edit
    is adopted the successor **is** the baseline bundle, and ``retained_baseline``
    is True — "no change" is a result, not a failure.
    """
    adopted: list[AdoptedEdit] = []
    rejected: list[RejectedEdit] = []
    claimed: dict[str, ComposerInput] = {}

    for item in inputs:
        reason = _eligible(item)
        if reason is not None:
            for diff in item.proposal.component_diffs:
                rejected.append(RejectedEdit(diff=diff, candidate_id=item.proposal.proposal_id, reason=reason))
            continue
        for diff in item.proposal.component_diffs:
            if diff.component_id in PROTECTED_COMPONENT_IDS:
                rejected.append(RejectedEdit(diff=diff, candidate_id=item.proposal.proposal_id, reason="component belongs to the trusted policy core; the composer cannot change it"))
                continue
            if diff.kind in PRIVILEGED_KINDS and diff.kind is ComponentKind.MIDDLEWARE:
                rejected.append(RejectedEdit(diff=diff, candidate_id=item.proposal.proposal_id, reason="middleware edits are not adopted by the composer; they require the operator-gated promotion path"))
                continue
            incumbent = claimed.get(diff.component_id)
            if incumbent is None:
                claimed[diff.component_id] = item
                adopted.append(
                    AdoptedEdit(
                        diff=diff,
                        candidate_id=item.proposal.proposal_id,
                        justification=f"{item.contrast.verdict} contrast with {len(item.contrast.evidence_refs)} evidence refs" + (f"; improvements: {'; '.join(item.contrast.improvements)}" if item.contrast.improvements else ""),
                        evidence_refs=item.contrast.evidence_refs,
                    )
                )
                continue
            incumbent_best = min((d for d in incumbent.proposal.component_diffs if d.component_id == diff.component_id), key=edit_cost_rank)
            if (edit_cost_rank(diff), item.proposal.proposal_id) < (edit_cost_rank(incumbent_best), incumbent.proposal.proposal_id):
                # Cheaper intervention wins; displace the incumbent's edit.
                adopted = [a for a in adopted if not (a.diff.component_id == diff.component_id and a.candidate_id == incumbent.proposal.proposal_id)]
                rejected.append(
                    RejectedEdit(
                        diff=incumbent_best,
                        candidate_id=incumbent.proposal.proposal_id,
                        reason=f"superseded by a cheaper effective edit to the same component from candidate {item.proposal.proposal_id}",
                    )
                )
                claimed[diff.component_id] = item
                adopted.append(
                    AdoptedEdit(
                        diff=diff,
                        candidate_id=item.proposal.proposal_id,
                        justification=f"{item.contrast.verdict} contrast; cheaper edit class than the conflicting candidate",
                        evidence_refs=item.contrast.evidence_refs,
                    )
                )
            else:
                rejected.append(
                    RejectedEdit(
                        diff=diff,
                        candidate_id=item.proposal.proposal_id,
                        reason=f"conflicts with an equally or more effective edit from candidate {incumbent.proposal.proposal_id}; contradictions are resolved by edit cost then candidate id",
                    )
                )

    if not adopted:
        decision = CompositionDecision(
            baseline_bundle_id=base_bundle.bundle_id,
            successor_bundle_id=base_bundle.bundle_id,
            adopted=(),
            rejected=tuple(rejected),
            retained_baseline=True,
            rationale="no candidate edit carried supported evidence, so the baseline harness is retained unchanged",
        )
        return decision, base_bundle, dict(base_contents)

    diffs = tuple(entry.diff for entry in adopted)
    resolution = resolve_components(base_bundle, diffs)
    components: dict[str, tuple[ComponentKind, str]] = {}
    for component_id, (kind, content) in resolution.items():
        if content is None and any(d.component_id == component_id and d.op == "remove" for d in diffs):
            continue  # removed component
        components[component_id] = (kind, content if content is not None else base_contents.get(component_id, ""))

    successor, contents = build_bundle(
        components=components,
        parent_bundle_id=base_bundle.bundle_id,
        created_by=created_by,
        agent_profile=base_bundle.agent_profile,
        alpha_runtime_version=base_bundle.alpha_runtime_version,
        tool_schema_version=base_bundle.tool_schema_version,
        policy_version=policy_version or base_bundle.policy_version,
        source_task_type=base_bundle.source_task_type,
        promotion_state=PromotionState.DRAFT,
    )
    decision = CompositionDecision(
        baseline_bundle_id=base_bundle.bundle_id,
        successor_bundle_id=successor.bundle_id,
        adopted=tuple(adopted),
        rejected=tuple(rejected),
        retained_baseline=False,
        rationale=f"adopted {len(adopted)} evidence-supported edit(s) across scope(s) {sorted({str(a.diff.kind) for a in adopted})}",
    )
    return decision, successor, contents


@dataclass(frozen=True, slots=True)
class DeliverableCandidate:
    """One deliverable produced by a probe or successor run, ready to select."""

    source: str  # "baseline" | "successor"
    bundle_id: str
    probe_run_id: str
    validators: tuple[Any, ...]
    deliverable_refs: tuple[str, ...]
    errored: bool = False


def select_deliverable(
    *,
    baseline: DeliverableCandidate,
    successor: DeliverableCandidate | None,
    successor_supported: bool,
    successor_scope: CandidateScope | None = None,
) -> dict[str, Any]:
    """Choose between the baseline and successor deliverables (guide §7 step 8).

    Harness selection and deliverable selection are separate decisions: the
    successor's deliverable is preferred only when it ran, did not error, passed
    every check the baseline passed (and at least one more when the baseline
    failed one), and the harness change itself is supported. Otherwise the
    baseline deliverable is returned **with the reason**, never a silent
    fallback.
    """
    from alpha.harness_improvement.models import ValidatorStatus

    def passed_count(validators: Sequence[Any]) -> tuple[int, int]:
        passed = sum(1 for v in validators if v.status is ValidatorStatus.PASSED)
        failed = sum(1 for v in validators if v.status is ValidatorStatus.FAILED)
        return passed, failed

    if successor is None or not successor_supported:
        return {
            "selected": "baseline",
            "reason": "no supported successor deliverable was available",
            "bundle_id": baseline.bundle_id,
            "probe_run_id": baseline.probe_run_id,
            "deliverable_refs": list(baseline.deliverable_refs),
            "scope": None,
        }
    if successor.errored:
        return {
            "selected": "baseline",
            "reason": "the successor probe errored, so its deliverable cannot be selected",
            "bundle_id": baseline.bundle_id,
            "probe_run_id": baseline.probe_run_id,
            "deliverable_refs": list(baseline.deliverable_refs),
            "scope": None,
        }
    base_passed, _ = passed_count(baseline.validators)
    succ_passed, succ_failed = passed_count(successor.validators)
    if succ_failed > 0:
        return {
            "selected": "baseline",
            "reason": f"the successor deliverable failed {succ_failed} deterministic check(s), so the baseline deliverable is retained",
            "bundle_id": baseline.bundle_id,
            "probe_run_id": baseline.probe_run_id,
            "deliverable_refs": list(baseline.deliverable_refs),
            "scope": None,
        }
    if succ_passed < base_passed:
        return {
            "selected": "baseline",
            "reason": f"the successor passed fewer checks than the baseline ({succ_passed} < {base_passed})",
            "bundle_id": baseline.bundle_id,
            "probe_run_id": baseline.probe_run_id,
            "deliverable_refs": list(baseline.deliverable_refs),
            "scope": None,
        }
    return {
        "selected": "successor",
        "reason": f"the successor deliverable passed {succ_passed} checks (baseline: {base_passed}) with no failures",
        "bundle_id": successor.bundle_id,
        "probe_run_id": successor.probe_run_id,
        "deliverable_refs": list(successor.deliverable_refs),
        "scope": str(successor_scope) if successor_scope else None,
    }
