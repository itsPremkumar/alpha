"""Candidate patch validation — the gate between "an editor proposed it" and
"this repository will store and probe it".

The editor is untrusted input (guide §12.1): it may produce candidate diffs, not
execute them, and not dictate the rules that decide whether they are allowed.
Every refusal here is **specific and recorded**, because a rejection that only
says "invalid" cannot be composed around, retried differently, or audited.

The checks, in the order a rejection is reported:

1. **Base mismatch** — a proposal against a different base bundle is not a
   candidate for this run at all.
2. **Hypothesis completeness** — an absent falsification condition means the
   hypothesis cannot be probed, only believed.
3. **Protected surface** — ``policy:*`` / ``guardrails:enforced`` component ids
   and privileged kinds (``script``, ``middleware``) are refused before any
   content is inspected; a full refusal list is returned, so one malformed
   proposal cannot hide the next one.
4. **Schema and path hygiene** — component ids are validated as logical names
   (no traversal, no drive letters, no absolute paths).
5. **Content screens** — credential-store paths and network-capable commands
   are refused for generated scripts/skills; the sandbox remains the
   enforcement point, this is a fail-closed screen in front of it.
6. **Budget shapes** — diff count and changed bytes are refused, never clamped.

Nothing here runs, imports or formats candidate content: a rejection is a
*decision*, and the probe stage is the first place candidate content is ever
executed — in its own workspace, never in the active harness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from alpha.harness_improvement.components import (
    ComponentIdError,
    UnknownComponentsError,
    build_bundle,
    resolve_components,
    validate_component_id,
)
from alpha.harness_improvement.models import (
    KIND_EXTRACTION_POLICY,
    PROTECTED_COMPONENT_IDS,
    CandidateScope,
    ComponentDiff,
    ComponentKind,
    ExtractionPolicy,
    HarnessBundle,
    HarnessProposal,
    PromotionState,
)
from alpha.harness_improvement.redaction import scan_for_credential_paths, scan_for_network_commands

#: Kinds whose content screens apply: generated code and instructions that a
#: model will act on. Plain prompt text is not scanned for ``curl`` because
#: "never run curl" is a legitimate instruction.
SCREENED_KINDS: frozenset[ComponentKind] = frozenset({ComponentKind.SCRIPT, ComponentKind.SKILL, ComponentKind.SUBAGENT, ComponentKind.MIDDLEWARE})


@dataclass(frozen=True, slots=True)
class PatchPolicy:
    """The operator-decided bounds a candidate diff must satisfy.

    Defaults are the guide's conservative §17 values: no middleware changes, no
    guardrail policy changes, evidence required, eight files and 32 KiB.
    """

    max_diff_files: int = 8
    max_changed_bytes: int = 32768
    allow_middleware_changes: bool = False
    allow_guardrail_policy_changes: bool = False
    require_evidence_for_diffs: bool = True
    deny_network_commands: bool = True
    protected_component_ids: frozenset[str] = PROTECTED_COMPONENT_IDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_diff_files": self.max_diff_files,
            "max_changed_bytes": self.max_changed_bytes,
            "allow_middleware_changes": self.allow_middleware_changes,
            "allow_guardrail_policy_changes": self.allow_guardrail_policy_changes,
            "require_evidence_for_diffs": self.require_evidence_for_diffs,
            "deny_network_commands": self.deny_network_commands,
        }


@dataclass(frozen=True, slots=True)
class PatchRejection:
    """One refusal, naming the rule and the offending component."""

    rule: str
    message: str
    component_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "message": self.message, "component_id": self.component_id}


@dataclass(frozen=True, slots=True)
class PatchValidation:
    """The outcome of validating one proposal.

    ``candidate_bundle`` and ``contents`` are set only when ``accepted`` is
    True; a rejected proposal still carries every rejection so the run record
    explains the refusal instead of only recording it.
    """

    proposal_id: str
    base_bundle_id: str
    accepted: bool
    rejections: tuple[PatchRejection, ...]
    candidate_bundle: HarnessBundle | None = None
    contents: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "base_bundle_id": self.base_bundle_id,
            "accepted": self.accepted,
            "rejections": [r.to_dict() for r in self.rejections],
            "candidate_bundle_id": self.candidate_bundle.bundle_id if self.candidate_bundle else None,
        }


def _screened_refusals(diff: ComponentDiff, policy: PatchPolicy) -> list[PatchRejection]:
    """Content screens for one diff. Returns every finding, not the first."""
    rejections: list[PatchRejection] = []
    content = diff.content or ""
    if not content:
        return rejections
    if diff.kind in SCREENED_KINDS:
        credential_paths = scan_for_credential_paths(content)
        if credential_paths:
            rejections.append(
                PatchRejection(
                    rule="credential_access",
                    message=f"candidate content references credential stores: {', '.join(credential_paths)}",
                    component_id=diff.component_id,
                )
            )
        if policy.deny_network_commands:
            commands = scan_for_network_commands(content)
            if commands:
                rejections.append(
                    PatchRejection(
                        rule="network_access",
                        message=f"candidate content invokes network commands {', '.join(commands)} while the probe network policy is deny-by-default",
                        component_id=diff.component_id,
                    )
                )
    return rejections


def _diff_shape_refusals(diff: ComponentDiff, policy: PatchPolicy) -> list[PatchRejection]:
    """Structural refusals: protected surface, id hygiene, sizes, scope leaks."""
    rejections: list[PatchRejection] = []
    if diff.component_id in policy.protected_component_ids:
        # Checked before id hygiene on purpose: every protected id is
        # colon-shaped (``policy:permissions``), so the slug rule would refuse it
        # first with a generic malformed-id message. The specific refusal is the
        # one a reader (and an editor learning from the rejection) needs — this
        # names the trusted core instead of implying a typo.
        rejections.append(
            PatchRejection(
                rule="protected_component",
                message=f"component {diff.component_id!r} belongs to the trusted policy core and is not LLM-editable",
                component_id=diff.component_id,
            )
        )
        return rejections
    try:
        validate_component_id(diff.component_id)
    except ComponentIdError as exc:
        rejections.append(PatchRejection(rule="invalid_component_id", message=str(exc), component_id=diff.component_id))
        # A malformed id cannot be screened further without ambiguity about what
        # it would address; stop here for this diff.
        return rejections
    if diff.kind is ComponentKind.MIDDLEWARE and not policy.allow_middleware_changes:
        rejections.append(
            PatchRejection(
                rule="privileged_kind",
                message="middleware changes require the operator to enable allow_candidate_middleware_changes",
                component_id=diff.component_id,
            )
        )
    if diff.kind is ComponentKind.GUARDRAIL_GUIDANCE and not policy.allow_guardrail_policy_changes:
        rejections.append(
            PatchRejection(
                rule="privileged_kind",
                message="guardrail guidance changes require the operator to enable allow_candidate_guardrail_policy_changes; the enforced policy core stays non-editable either way",
                component_id=diff.component_id,
            )
        )
    if diff.op in {"add", "modify"} and diff.content is None:
        rejections.append(PatchRejection(rule="missing_content", message=f"{diff.op} requires full new content, not a path or patch", component_id=diff.component_id))
    if diff.content is not None:
        changed_bytes = len(diff.content.encode("utf-8"))
        if changed_bytes > policy.max_changed_bytes:
            rejections.append(
                PatchRejection(
                    rule="size_limit",
                    message=f"diff writes {changed_bytes} bytes, above max_changed_bytes={policy.max_changed_bytes}",
                    component_id=diff.component_id,
                )
            )
    if policy.require_evidence_for_diffs and not diff.evidence_event_ids:
        rejections.append(
            PatchRejection(
                rule="missing_evidence",
                message="diff cites no trace event; exploratory diffs are only allowed when evidence is not required",
                component_id=diff.component_id,
            )
        )
    return rejections


def validate_proposal(
    proposal: HarnessProposal,
    base_bundle: HarnessBundle,
    *,
    base_contents: dict[str, str] | None = None,
    policy: PatchPolicy | None = None,
    created_by: str = "harness_editor",
) -> PatchValidation:
    """Validate every diff of *proposal* against *base_bundle* and *policy*.

    ``base_contents`` is the ``{component_id: content}`` payload the caller
    resolved from its store. When it is ``None`` the candidate cannot be built
    and the validation records ``base_content_unavailable`` — a bundle whose
    blobs are missing must never be edited into one that appears to carry them.

    Returns a :class:`PatchValidation`. When no refusal is recorded the
    candidate bundle is computed as a new immutable bundle whose parent is the
    base — never an edit of the base itself.
    """
    active_policy = policy or PatchPolicy()
    rejections: list[PatchRejection] = []

    if proposal.base_bundle_id != base_bundle.bundle_id:
        rejections.append(
            PatchRejection(
                rule="base_mismatch",
                message=f"proposal targets {proposal.base_bundle_id!r} but the run's base bundle is {base_bundle.bundle_id!r}",
                component_id=None,
            )
        )
    if not proposal.hypothesis.summary.strip():
        rejections.append(PatchRejection(rule="hypothesis_incomplete", message="hypothesis has no summary", component_id=None))
    if not proposal.hypothesis.falsification_condition.strip():
        rejections.append(PatchRejection(rule="hypothesis_incomplete", message="hypothesis states no falsification condition, so it cannot be disconfirmed", component_id=None))
    if len(proposal.component_diffs) > active_policy.max_diff_files:
        rejections.append(
            PatchRejection(
                rule="too_many_diffs",
                message=f"proposal contains {len(proposal.component_diffs)} diffs, above max_diff_files={active_policy.max_diff_files}",
                component_id=None,
            )
        )
    if not proposal.component_diffs:
        rejections.append(PatchRejection(rule="no_diffs", message="proposal contains no component diffs; use an explicit no-change outcome instead", component_id=None))

    seen_ids: set[str] = set()
    for diff in proposal.component_diffs:
        if diff.component_id in seen_ids:
            rejections.append(PatchRejection(rule="duplicate_component", message=f"component {diff.component_id!r} is edited twice in one proposal", component_id=diff.component_id))
        seen_ids.add(diff.component_id)
        rejections.extend(_diff_shape_refusals(diff, active_policy))
        rejections.extend(_screened_refusals(diff, active_policy))
        if proposal.scope is CandidateScope.TASK_OR_DOMAIN and KIND_EXTRACTION_POLICY.get(diff.kind) is ExtractionPolicy.ALWAYS_LOADED:
            rejections.append(
                PatchRejection(
                    rule="scope_leak",
                    message=f"task/domain-scoped proposal may not edit always-loaded component {diff.component_id!r}; store task facts in scoped memory instead",
                    component_id=diff.component_id,
                )
            )

    if rejections:
        return PatchValidation(proposal_id=proposal.proposal_id, base_bundle_id=base_bundle.bundle_id, accepted=False, rejections=tuple(rejections))

    try:
        resolution = resolve_components(base_bundle, proposal.component_diffs)
    except UnknownComponentsError as exc:
        return PatchValidation(
            proposal_id=proposal.proposal_id,
            base_bundle_id=base_bundle.bundle_id,
            accepted=False,
            rejections=(PatchRejection(rule="unknown_component", message=str(exc)),),
        )

    base_snapshot = dict(base_contents) if base_contents is not None else None
    if base_snapshot is None:
        # The caller must supply content resolution; a bundle whose blobs are
        # absent cannot be edited into a candidate that claims those bytes.
        return PatchValidation(
            proposal_id=proposal.proposal_id,
            base_bundle_id=base_bundle.bundle_id,
            accepted=False,
            rejections=(PatchRejection(rule="base_content_unavailable", message="base bundle component contents were not supplied to this validator"),),
        )

    components: dict[str, tuple[ComponentKind, str]] = {}
    for component_id, (kind, content) in resolution.items():
        if content is None and component_id in seen_ids:
            continue  # REMOVE
        if content is None:
            content = base_snapshot.get(component_id, "")
        components[component_id] = (kind, content)

    bundle, contents = build_bundle(
        components=components,
        parent_bundle_id=base_bundle.bundle_id,
        created_by=created_by,
        agent_profile=base_bundle.agent_profile,
        alpha_runtime_version=base_bundle.alpha_runtime_version,
        tool_schema_version=base_bundle.tool_schema_version,
        policy_version=base_bundle.policy_version,
        source_task_type=base_bundle.source_task_type,
        promotion_state=PromotionState.DRAFT,
    )
    return PatchValidation(
        proposal_id=proposal.proposal_id,
        base_bundle_id=base_bundle.bundle_id,
        accepted=True,
        rejections=(),
        candidate_bundle=bundle,
        contents=contents,
    )
