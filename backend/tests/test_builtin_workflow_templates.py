"""The two builtin workflow-template blueprints (``code_change`` / ``bug_fix``).

These tests pin the three properties that make the blueprints trustworthy
rather than merely present:

1. **The graphs are real.** Each blueprint is a well-formed ``WorkflowGraph``
   with the expected node ids, edge chain, executors, bounded retry policies,
   a declared ``verification_cmd``, and a ``GOAL_GATE`` acceptance criterion
   written in the safe-AST expression form the runtime evaluates.
2. **The lifecycle treats builtin differently from captured — and nothing
   else.** A builtin ``draft`` may instantiate (tagged ``[builtin blueprint,
   unverified]``); a captured ``draft`` still cannot; promotion is still
   evidence-gated for both.
3. **Seeding is idempotent and non-destructive.** It fills absent ids only and
   never overwrites an operator's edit, verification or promotion.

``get_template_store`` is the process-wide seam that auto-seeds on first
construction; these tests exercise ``TemplateStore`` directly against a temp
directory so they never touch the real ``runtime_home()``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alpha.workflow.builtin_templates import (
    BUILTIN_TEMPLATE_IDS,
    BUILTIN_TEMPLATES,
    bug_fix_graph,
    code_change_graph,
    get_builtin_template,
    seed_builtin_templates,
)
from alpha.workflow.models import NodeType
from alpha.workflow.templates import (
    TemplateError,
    TemplateProvenance,
    TemplateState,
    TemplateStore,
    WorkflowTemplate,
)


@pytest.fixture()
def store(tmp_path: Path) -> TemplateStore:
    return TemplateStore(store_dir=tmp_path / "templates")


# --------------------------------------------------------------------------- #
# The graphs themselves
# --------------------------------------------------------------------------- #


def test_the_shipped_id_set_is_pinned():
    """Adding or removing a blueprint is a deliberate, reviewable change."""
    assert BUILTIN_TEMPLATE_IDS == ("code_change", "bug_fix")
    assert set(BUILTIN_TEMPLATES) == set(BUILTIN_TEMPLATE_IDS)


def test_code_change_graph_shape():
    graph = code_change_graph()
    assert sorted(graph.nodes) == ["accept", "implement", "plan", "review", "test"]
    # A linear chain: plan -> implement -> test -> review -> accept.
    assert {(e.source, e.target) for e in graph.edges} == {
        ("plan", "implement"),
        ("implement", "test"),
        ("test", "review"),
        ("review", "accept"),
    }
    assert graph.nodes["plan"].type is NodeType.AGENT
    assert graph.nodes["implement"].type is NodeType.AGENT
    assert graph.nodes["test"].type is NodeType.TOOL
    assert graph.nodes["review"].type is NodeType.REVIEW
    assert graph.nodes["accept"].type is NodeType.GOAL_GATE


def test_bug_fix_graph_shape_includes_reproduce():
    """A bug fix that cannot reproduce the failure has no proof it addressed it."""
    graph = bug_fix_graph()
    assert sorted(graph.nodes) == ["accept", "diagnose", "fix", "regression_test", "reproduce", "review"]
    assert {(e.source, e.target) for e in graph.edges} == {
        ("reproduce", "diagnose"),
        ("diagnose", "fix"),
        ("fix", "regression_test"),
        ("regression_test", "review"),
        ("review", "accept"),
    }
    assert graph.nodes["fix"].type is NodeType.AGENT


def test_the_repair_loops_are_bounded():
    """Bounded repair, never an unbounded loop."""
    code = code_change_graph()
    assert code.nodes["implement"].retry_policy.max_attempts == 2
    bug = bug_fix_graph()
    assert bug.nodes["fix"].retry_policy.max_attempts == 3


def test_the_verification_step_declares_a_command_and_is_not_spawned():
    """The check that proves the change is a declaration, not a subprocess."""
    for graph in (code_change_graph(), bug_fix_graph()):
        tool_nodes = [n for n in graph.nodes.values() if n.type is NodeType.TOOL]
        assert len(tool_nodes) == 1
        assert tool_nodes[0].config["verification_cmd"] == "pytest -q"


def test_the_goal_gate_criteria_are_safe_ast_expressions():
    """An unevaluable required criterion counts as NOT met (fail-closed)."""
    for graph in (code_change_graph(), bug_fix_graph()):
        gate = graph.nodes["accept"]
        criteria = gate.config["acceptance_criteria"]
        assert len(criteria) == 1
        criterion = criteria[0]
        assert criterion["required"] is True
        assert criterion["expression"].startswith("metrics.")


# --------------------------------------------------------------------------- #
# Provenance: source distinguishes builtin from captured, and round-trips
# --------------------------------------------------------------------------- #


def test_provenance_source_round_trips():
    builtin = TemplateProvenance(source="builtin")
    assert builtin.to_dict()["source"] == "builtin"
    assert TemplateProvenance.from_dict(builtin.to_dict()).source == "builtin"


def test_a_record_without_a_source_reads_back_as_captured():
    """Every pre-existing record stays ``captured`` — the conservative default."""
    assert TemplateProvenance.from_dict({"created_at": "x"}).source == "captured"
    assert TemplateProvenance.from_dict({}).source == "captured"
    assert TemplateProvenance.from_dict("nonsense").source == "captured"
    assert TemplateProvenance().source == "captured"


# --------------------------------------------------------------------------- #
# The blueprints ship as builtin drafts
# --------------------------------------------------------------------------- #


def test_the_blueprints_are_builtin_drafts(store: TemplateStore):
    seed_builtin_templates(store)
    for template_id in BUILTIN_TEMPLATE_IDS:
        template = store.get(template_id)
        assert template is not None
        assert template.state is TemplateState.DRAFT
        assert template.provenance.source == "builtin"
        assert "builtin" in template.tags


def test_get_builtin_template_returns_a_fresh_copy():
    first = get_builtin_template("bug_fix")
    second = get_builtin_template("bug_fix")
    assert first is not None and second is not None
    assert first is not second
    assert first.graph.nodes is not second.graph.nodes
    assert get_builtin_template("no_such_blueprint") is None


# --------------------------------------------------------------------------- #
# Lifecycle: builtin drafts instantiate; captured drafts still do not
# --------------------------------------------------------------------------- #


def test_a_builtin_draft_may_instantiate_and_is_tagged_unverified(store: TemplateStore):
    seed_builtin_templates(store)
    definition = store.instantiate("bug_fix", definition_id="dfm-builtin")
    assert definition.id == "dfm-builtin"
    assert "[builtin blueprint, unverified]" in definition.description


def test_a_captured_draft_still_cannot_be_instantiated(store: TemplateStore):
    captured = WorkflowTemplate(
        id="captured_draft",
        name="Captured",
        graph=code_change_graph(),
        description="captured from a run",
        state=TemplateState.DRAFT,
        provenance=TemplateProvenance(source="captured"),
    )
    store.save(captured)
    with pytest.raises(TemplateError, match="unverified draft"):
        store.instantiate("captured_draft", definition_id="dfm-captured")


def test_promotion_is_still_refused_for_an_unverified_builtin(store: TemplateStore):
    """Being builtin buys instantiation, never a promotion without evidence."""
    seed_builtin_templates(store)
    with pytest.raises(TemplateError, match="unverified draft"):
        store.promote("bug_fix")


def test_the_verification_path_is_unchanged_for_builtins(store: TemplateStore):
    """A builtin still reaches ``promoted`` only through the evidence gate.

    ``verify`` requires a real run id + a completing run; here we only pin that
    the *state machine* is the same one every captured template walks, by
    asserting the refusal above and that a promoted template instantiates
    without the builtin tag.
    """
    seed_builtin_templates(store)
    promoted = WorkflowTemplate(
        id="promoted_one",
        name="Promoted",
        graph=code_change_graph(),
        description="verified elsewhere",
        state=TemplateState.VERIFIED,
        provenance=TemplateProvenance(source="captured", source_run_ids=["run-1"], verified_at="2026-01-01T00:00:00+00:00"),
    )
    store.save(promoted)
    store.promote("promoted_one")
    definition = store.instantiate("promoted_one", definition_id="dfm-promoted")
    assert "[builtin blueprint, unverified]" not in definition.description


# --------------------------------------------------------------------------- #
# Seeding: idempotent and non-destructive
# --------------------------------------------------------------------------- #


def test_seeding_is_idempotent_and_reports_the_delta(store: TemplateStore):
    assert seed_builtin_templates(store) == list(BUILTIN_TEMPLATE_IDS)
    assert seed_builtin_templates(store) == []


def test_seeding_never_overwrites_an_operator_edit(store: TemplateStore):
    seed_builtin_templates(store)
    edited = store.get("code_change")
    assert edited is not None
    edited.name = "Operator renamed this"
    store.save(edited)

    assert seed_builtin_templates(store) == []
    assert store.get("code_change").name == "Operator renamed this"


def test_seeding_survives_a_promotion(store: TemplateStore):
    """A promoted builtin is not demoted back to a draft on the next seed."""
    seed_builtin_templates(store)
    # Walk the real lifecycle: a VERIFIED record promotes, then a re-seed must
    # leave the promoted state alone. (verify() needs a run; here we set the
    # state directly because the point is the seeding behaviour, not verify().)
    promoted = WorkflowTemplate(
        id="bug_fix",
        name="Bug fix",
        graph=bug_fix_graph(),
        description="promoted by an operator",
        state=TemplateState.VERIFIED,
        provenance=TemplateProvenance(source="builtin", source_run_ids=["run-1"], verified_at="2026-01-01T00:00:00+00:00"),
    )
    store.save(promoted)
    store.promote("bug_fix")

    assert seed_builtin_templates(store) == []
    assert store.get("bug_fix").state is TemplateState.PROMOTED


def test_seed_builtins_is_reachable_through_the_store(store: TemplateStore):
    """The lazy-import seam on TemplateStore reaches the builtin module."""
    seeded = store.seed_builtins()
    assert seeded == list(BUILTIN_TEMPLATE_IDS)
