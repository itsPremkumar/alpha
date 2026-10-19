"""Candidate patch validation: content addressing and every recorded refusal.

An editor is untrusted input (guide §12.1), so these tests pin the gate between
"an editor proposed it" and "this repository will store and probe it":

1. **A bundle id is its content.** Two builds of the same components agree
   regardless of insertion order; content, policy version and parent each move
   the id, so no candidate can be confused with its base.
2. **Every refusal is specific and recorded.** One test per rule named by
   :func:`alpha.harness_improvement.patches.validate_proposal`, including the
   protected policy core (every member), id hygiene, privileged kinds, evidence
   and size bounds, scope leaks, unknown components and missing base content.
3. **The screens fail closed for generated code, and only for it.** Credential
   stores and network commands are refused in scripts and skills; ordinary
   prompt text that merely *mentions* ``curl`` stays allowed.
4. **An accepted proposal is a new child bundle, never an edit.** The base is
   byte-for-byte what it was and the candidate is a DRAFT whose parent is the
   base — the active pointer is not involved here at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from alpha.harness_improvement import (
    CandidateScope,
    ComponentDiff,
    ComponentKind,
    ComponentOp,
    FailureHypothesis,
    HarnessProposal,
    PatchPolicy,
    PromotionState,
    build_bundle,
    validate_component_id,
    validate_proposal,
)
from alpha.harness_improvement.components import ComponentIdError
from alpha.harness_improvement.models import PROTECTED_COMPONENT_IDS

# ---------------------------------------------------------------- fixtures


def _base() -> tuple[Any, dict[str, str]]:
    return build_bundle(
        components={
            "system_prompt/base": (ComponentKind.SYSTEM_PROMPT, "You are Alpha."),
            "tool_guidance/bash": (ComponentKind.TOOL_GUIDANCE, "Use bash carefully."),
            "skills/report": (ComponentKind.SKILL, "# Report\nWrite the report.\n"),
        },
        parent_bundle_id=None,
        created_by="test",
        alpha_runtime_version="0.1.0",
        tool_schema_version="tools-v1",
        policy_version="policy-v1",
        promotion_state=PromotionState.ACTIVE,
    )


def _hypothesis(**overrides: Any) -> FailureHypothesis:
    fields = {
        "summary": "reopen artifacts before announcing completion",
        "trace_event_ids": ("baseline.completed",),
        "expected_behavioral_change": "the artifact parses at completion time",
        "falsification_condition": "the artifact is still malformed when the run finishes",
    }
    fields.update(overrides)
    return FailureHypothesis(**fields)


def _diff(**overrides: Any) -> ComponentDiff:
    fields: dict[str, Any] = {
        "op": ComponentOp.MODIFY,
        "kind": ComponentKind.SYSTEM_PROMPT,
        "component_id": "system_prompt/base",
        "content": "Always reopen and parse each deliverable before announcing completion.",
        "rationale": "baseline announced completion without verifying the file",
        "evidence_event_ids": ("baseline.completed",),
    }
    fields.update(overrides)
    return ComponentDiff(**fields)


def _proposal(bundle_id: str, *diffs: ComponentDiff, scope: CandidateScope = CandidateScope.INVARIANT, **hypothesis: Any) -> HarnessProposal:
    return HarnessProposal(
        proposal_id="p-test",
        scope=scope,
        base_bundle_id=bundle_id,
        hypothesis=_hypothesis(**hypothesis),
        component_diffs=tuple(diffs) or (_diff(),),
    )


def _rules(validation: Any) -> list[str]:
    return [rejection.rule for rejection in validation.rejections]


# ---------------------------------------------------------------- addressing


class TestContentAddressing:
    def test_the_bundle_id_is_the_content_not_the_insertion_order(self) -> None:
        first, _ = build_bundle(components={"system_prompt/base": (ComponentKind.SYSTEM_PROMPT, "A"), "tool_guidance/bash": (ComponentKind.TOOL_GUIDANCE, "B")}, parent_bundle_id=None, created_by="t")
        second, _ = build_bundle(components={"tool_guidance/bash": (ComponentKind.TOOL_GUIDANCE, "B"), "system_prompt/base": (ComponentKind.SYSTEM_PROMPT, "A")}, parent_bundle_id=None, created_by="t")
        assert first.bundle_id == second.bundle_id
        assert first.bundle_id.startswith("sha256:")

    def test_one_byte_of_content_moves_the_id(self) -> None:
        base, _ = _base()
        changed, _ = build_bundle(
            components={
                "system_prompt/base": (ComponentKind.SYSTEM_PROMPT, "You are Alpha!"),
                "tool_guidance/bash": (ComponentKind.TOOL_GUIDANCE, "Use bash carefully."),
                "skills/report": (ComponentKind.SKILL, "# Report\nWrite the report.\n"),
            },
            parent_bundle_id=None,
            created_by="t",
        )
        assert changed.bundle_id != base.bundle_id

    def test_compatibility_and_lineage_are_part_of_the_identity(self) -> None:
        base, _ = _base()
        same_components = {
            "system_prompt/base": (ComponentKind.SYSTEM_PROMPT, "You are Alpha."),
            "tool_guidance/bash": (ComponentKind.TOOL_GUIDANCE, "Use bash carefully."),
            "skills/report": (ComponentKind.SKILL, "# Report\nWrite the report.\n"),
        }
        other_policy, _ = build_bundle(components=same_components, parent_bundle_id=None, created_by="t", alpha_runtime_version="0.1.0", tool_schema_version="tools-v1", policy_version="policy-v2")
        child, _ = build_bundle(components=same_components, parent_bundle_id=base.bundle_id, created_by="t", alpha_runtime_version="0.1.0", tool_schema_version="tools-v1", policy_version="policy-v1")
        assert other_policy.bundle_id != base.bundle_id
        assert child.bundle_id != base.bundle_id


# ---------------------------------------------------------------- id hygiene


class TestComponentIdHygiene:
    @pytest.mark.parametrize(
        "component_id",
        [
            "",
            " system_prompt/base",
            "system_prompt/base ",
            "system_prompt\\base",
            "a/b/c",
            "skills/..",
            "skills/.",
            "C:/Windows",
            "/etc",
            "~/.ssh",
            "Skills/report",
            "skills/Report",
            "skills/",
        ],
    )
    def test_every_path_shaped_id_is_refused(self, component_id: str) -> None:
        with pytest.raises(ComponentIdError):
            validate_component_id(component_id)

    @pytest.mark.parametrize("component_id", ["system_prompt/base", "tool_guidance/bash", "skills/report", "memory/facts", "middleware/checkpoint"])
    def test_logical_names_are_accepted(self, component_id: str) -> None:
        assert validate_component_id(component_id) == component_id


# ---------------------------------------------------------------- refusals


class TestProposalRefusals:
    def test_a_proposal_against_another_base_is_not_a_candidate(self) -> None:
        base, contents = _base()
        validation = validate_proposal(_proposal("sha256:not-this-bundle"), base, base_contents=contents)
        assert not validation.accepted
        assert _rules(validation) == ["base_mismatch"]
        assert validation.candidate_bundle is None

    def test_a_hypothesis_without_a_summary_is_incomplete(self) -> None:
        base, contents = _base()
        validation = validate_proposal(_proposal(base.bundle_id, summary="  "), base, base_contents=contents)
        assert not validation.accepted
        assert _rules(validation) == ["hypothesis_incomplete"]
        assert "summary" in validation.rejections[0].message

    def test_a_hypothesis_without_a_falsification_condition_cannot_be_probed(self) -> None:
        base, contents = _base()
        validation = validate_proposal(_proposal(base.bundle_id, falsification_condition=""), base, base_contents=contents)
        assert not validation.accepted
        assert _rules(validation) == ["hypothesis_incomplete"]
        assert "disconfirmed" in validation.rejections[0].message

    def test_an_empty_proposal_asks_for_a_no_change_outcome_instead(self) -> None:
        base, contents = _base()
        empty = HarnessProposal(proposal_id="p-test", scope=CandidateScope.INVARIANT, base_bundle_id=base.bundle_id, hypothesis=_hypothesis(), component_diffs=())
        validation = validate_proposal(empty, base, base_contents=contents)
        assert not validation.accepted
        assert _rules(validation) == ["no_diffs"]

    def test_a_proposal_above_the_diff_ceiling_is_refused_not_truncated(self) -> None:
        base, contents = _base()
        proposal = _proposal(
            base.bundle_id,
            _diff(op=ComponentOp.MODIFY, kind=ComponentKind.SYSTEM_PROMPT, component_id="system_prompt/base", content="A"),
            _diff(op=ComponentOp.ADD, kind=ComponentKind.SKILL, component_id="skills/extra", content="# Extra\n"),
        )
        validation = validate_proposal(proposal, base, base_contents=contents, policy=PatchPolicy(max_diff_files=1))
        assert not validation.accepted
        assert _rules(validation) == ["too_many_diffs"]
        assert "max_diff_files=1" in validation.rejections[0].message

    def test_the_same_component_twice_in_one_proposal_is_refused(self) -> None:
        base, contents = _base()
        proposal = _proposal(
            base.bundle_id,
            _diff(content="First version."),
            _diff(content="Second version."),
        )
        validation = validate_proposal(proposal, base, base_contents=contents)
        assert not validation.accepted
        assert _rules(validation) == ["duplicate_component"]

    @pytest.mark.parametrize("protected_id", sorted(PROTECTED_COMPONENT_IDS))
    def test_every_protected_policy_component_is_refused_by_name(self, protected_id: str) -> None:
        base, contents = _base()
        kind = ComponentKind.GUARDRAIL_GUIDANCE if protected_id.startswith("guardrails:") else ComponentKind.SYSTEM_PROMPT
        proposal = _proposal(base.bundle_id, _diff(kind=kind, component_id=protected_id, content="relaxed"))
        validation = validate_proposal(proposal, base, base_contents=contents, policy=PatchPolicy(allow_guardrail_policy_changes=True))
        assert not validation.accepted
        assert "protected_component" in _rules(validation)
        protected = next(r for r in validation.rejections if r.rule == "protected_component")
        assert protected_id in protected.message
        assert "trusted policy core" in protected.message

    def test_a_malformed_id_is_refused_before_anything_else_happens_to_that_diff(self) -> None:
        base, contents = _base()
        proposal = _proposal(base.bundle_id, _diff(component_id="skills/../escape", content="x"))
        validation = validate_proposal(proposal, base, base_contents=contents)
        assert not validation.accepted
        assert _rules(validation) == ["invalid_component_id"]

    def test_a_modify_without_content_is_refused(self) -> None:
        base, contents = _base()
        proposal = _proposal(base.bundle_id, _diff(content=None))
        validation = validate_proposal(proposal, base, base_contents=contents)
        assert not validation.accepted
        assert _rules(validation) == ["missing_content"]

    def test_middleware_edits_need_the_operator_flag(self) -> None:
        base, contents = _base()
        base, contents = build_bundle(
            components={**{ref.component_id: (ref.kind, contents[ref.component_id]) for ref in base.components}, "middleware/summarize": (ComponentKind.MIDDLEWARE, "threshold: 0.8")},
            parent_bundle_id=None,
            created_by="t",
        )
        diff = _diff(kind=ComponentKind.MIDDLEWARE, component_id="middleware/summarize", content="threshold: 0.9")
        refused = validate_proposal(_proposal(base.bundle_id, diff), base, base_contents=contents)
        assert not refused.accepted
        assert "privileged_kind" in _rules(refused)
        assert any("allow_candidate_middleware_changes" in r.message for r in refused.rejections)

        allowed = validate_proposal(_proposal(base.bundle_id, diff), base, base_contents=contents, policy=PatchPolicy(allow_middleware_changes=True))
        assert allowed.accepted, allowed.rejections

    def test_guardrail_guidance_needs_the_operator_flag_and_never_covers_the_enforced_core(self) -> None:
        base, contents = _base()
        base, contents = build_bundle(
            components={**{ref.component_id: (ref.kind, contents[ref.component_id]) for ref in base.components}, "guardrail_guidance/tone": (ComponentKind.GUARDRAIL_GUIDANCE, "Be careful with destructive commands.")},
            parent_bundle_id=None,
            created_by="t",
        )
        diff = _diff(kind=ComponentKind.GUARDRAIL_GUIDANCE, component_id="guardrail_guidance/tone", content="Be very careful with destructive commands.")
        refused = validate_proposal(_proposal(base.bundle_id, diff), base, base_contents=contents)
        assert not refused.accepted
        assert "privileged_kind" in _rules(refused)
        assert any("allow_candidate_guardrail_policy_changes" in r.message for r in refused.rejections)
        assert any("stays non-editable either way" in r.message for r in refused.rejections)

        allowed = validate_proposal(_proposal(base.bundle_id, diff), base, base_contents=contents, policy=PatchPolicy(allow_guardrail_policy_changes=True))
        assert allowed.accepted, allowed.rejections

    def test_a_diff_without_evidence_is_refused_unless_exploration_is_allowed(self) -> None:
        base, contents = _base()
        diff = _diff(evidence_event_ids=())
        refused = validate_proposal(_proposal(base.bundle_id, diff), base, base_contents=contents)
        assert not refused.accepted
        assert _rules(refused) == ["missing_evidence"]

        exploratory = validate_proposal(_proposal(base.bundle_id, diff), base, base_contents=contents, policy=PatchPolicy(require_evidence_for_diffs=False))
        assert exploratory.accepted, exploratory.rejections

    def test_a_diff_above_the_byte_ceiling_is_refused_not_truncated(self) -> None:
        base, contents = _base()
        proposal = _proposal(base.bundle_id, _diff(content="0123456789"))
        validation = validate_proposal(proposal, base, base_contents=contents, policy=PatchPolicy(max_changed_bytes=4))
        assert not validation.accepted
        assert _rules(validation) == ["size_limit"]
        assert "max_changed_bytes=4" in validation.rejections[0].message

    def test_a_task_scoped_edit_to_an_always_loaded_component_is_a_scope_leak(self) -> None:
        base, contents = _base()
        proposal = _proposal(base.bundle_id, _diff(), scope=CandidateScope.TASK_OR_DOMAIN)
        validation = validate_proposal(proposal, base, base_contents=contents)
        assert not validation.accepted
        assert _rules(validation) == ["scope_leak"]
        assert "scoped memory" in validation.rejections[0].message

    def test_a_task_scoped_edit_to_an_on_demand_component_is_fine(self) -> None:
        base, contents = _base()
        proposal = _proposal(base.bundle_id, _diff(op=ComponentOp.MODIFY, kind=ComponentKind.SKILL, component_id="skills/report", content="# Report v2\n"), scope=CandidateScope.TASK_OR_DOMAIN)
        validation = validate_proposal(proposal, base, base_contents=contents)
        assert validation.accepted, validation.rejections

    def test_editing_a_component_the_base_does_not_have_is_refused(self) -> None:
        base, contents = _base()
        proposal = _proposal(base.bundle_id, _diff(op=ComponentOp.MODIFY, kind=ComponentKind.SKILL, component_id="skills/missing", content="# Missing\n"))
        validation = validate_proposal(proposal, base, base_contents=contents)
        assert not validation.accepted
        assert _rules(validation) == ["unknown_component"]

    def test_missing_base_content_refuses_instead_of_inventing_it(self) -> None:
        base, _ = _base()
        validation = validate_proposal(_proposal(base.bundle_id), base, base_contents=None)
        assert not validation.accepted
        assert _rules(validation) == ["base_content_unavailable"]

    def test_a_remove_may_not_smuggle_content(self) -> None:
        base, contents = _base()
        removed = _diff(op=ComponentOp.REMOVE, kind=ComponentKind.SKILL, component_id="skills/report", content=None)
        accepted = validate_proposal(_proposal(base.bundle_id, removed), base, base_contents=contents)
        assert accepted.accepted, accepted.rejections
        assert accepted.candidate_bundle is not None
        assert accepted.candidate_bundle.component("skills/report") is None


# ---------------------------------------------------------------- screens


class TestContentScreens:
    def test_a_script_reading_a_credential_store_is_refused(self) -> None:
        base, contents = _base()
        proposal = _proposal(base.bundle_id, _diff(op=ComponentOp.ADD, kind=ComponentKind.SCRIPT, component_id="scripts/deploy", content="#!/bin/sh\ncat ~/.ssh/id_rsa\n"))
        validation = validate_proposal(proposal, base, base_contents=contents)
        assert not validation.accepted
        assert "credential_access" in _rules(validation)
        finding = next(r for r in validation.rejections if r.rule == "credential_access")
        assert finding.component_id == "scripts/deploy"

    def test_a_skill_that_shells_out_to_the_network_is_refused(self) -> None:
        base, contents = _base()
        proposal = _proposal(base.bundle_id, _diff(op=ComponentOp.ADD, kind=ComponentKind.SKILL, component_id="skills/fetch", content="Run `curl https://example.com/data` to fetch the input.\n"))
        validation = validate_proposal(proposal, base, base_contents=contents)
        assert not validation.accepted
        assert "network_access" in _rules(validation)
        assert any("deny-by-default" in r.message for r in validation.rejections)

    def test_prompt_text_that_merely_mentions_curl_is_still_prompt_text(self) -> None:
        base, contents = _base()
        proposal = _proposal(base.bundle_id, _diff(content="Never run curl against an untrusted host."))
        validation = validate_proposal(proposal, base, base_contents=contents)
        assert validation.accepted, validation.rejections

    def test_the_network_screen_can_only_be_relaxed_by_the_policy_object(self) -> None:
        base, contents = _base()
        proposal = _proposal(base.bundle_id, _diff(op=ComponentOp.ADD, kind=ComponentKind.SKILL, component_id="skills/fetch", content="Run `wget https://example.com/data`.\n"))
        relaxed = validate_proposal(proposal, base, base_contents=contents, policy=PatchPolicy(deny_network_commands=False))
        assert relaxed.accepted, relaxed.rejections


# ---------------------------------------------------------------- acceptance


class TestAcceptedCandidate:
    def test_an_accepted_proposal_builds_a_new_draft_child_and_the_base_is_untouched(self) -> None:
        base, contents = _base()
        before = {ref.component_id: ref.sha256 for ref in base.components}
        validation = validate_proposal(_proposal(base.bundle_id), base, base_contents=contents)

        assert validation.accepted
        candidate = validation.candidate_bundle
        assert candidate is not None
        assert candidate.bundle_id != base.bundle_id
        assert candidate.parent_bundle_id == base.bundle_id
        assert candidate.promotion_state is PromotionState.DRAFT
        assert candidate.components != base.components
        assert {ref.component_id: ref.sha256 for ref in base.components} == before

        # The resolved payload carries the new content plus the base's other
        # components, exactly as the store will write it.
        assert validation.contents["system_prompt/base"].startswith("Always reopen")
        assert validation.contents["tool_guidance/bash"] == "Use bash carefully."

    def test_the_same_edit_from_the_same_base_addresses_to_the_same_candidate(self) -> None:
        base, contents = _base()
        first = validate_proposal(_proposal(base.bundle_id), base, base_contents=contents)
        second = validate_proposal(_proposal(base.bundle_id), base, base_contents=contents)
        assert first.candidate_bundle is not None and second.candidate_bundle is not None
        assert first.candidate_bundle.bundle_id == second.candidate_bundle.bundle_id

    def test_an_add_carries_no_base_content_and_a_modify_does_not_lose_it(self) -> None:
        base, contents = _base()
        proposal = _proposal(
            base.bundle_id,
            _diff(op=ComponentOp.ADD, kind=ComponentKind.SKILL, component_id="skills/artifact-verification", content="# Artifact verification\n"),
        )
        validation = validate_proposal(proposal, base, base_contents=contents)
        assert validation.accepted, validation.rejections
        assert validation.contents["skills/report"] == contents["skills/report"]
        assert validation.contents["skills/artifact-verification"] == "# Artifact verification\n"


def test_the_default_policy_keeps_middleware_and_guardrails_shut() -> None:
    policy = PatchPolicy()
    assert policy.allow_middleware_changes is False
    assert policy.allow_guardrail_policy_changes is False
    assert policy.require_evidence_for_diffs is True
    assert policy.deny_network_commands is True
    assert policy.max_diff_files == 8
    assert policy.max_changed_bytes == 32768
    assert policy.to_dict()["allow_middleware_changes"] is False
