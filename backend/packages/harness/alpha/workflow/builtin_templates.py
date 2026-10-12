"""Two hand-authored workflow-template blueprints: ``code_change`` and ``bug_fix``.

A Prime-Agent-style reusable workflow template is a *graph blueprint* — a
plan/implement/review/verify chain with a bounded repair loop — not a captured
run. This module ships those two blueprints as code, so they are reviewable in
a pull request and pinned by unit tests, rather than being captured from some
run that happened once and left a template behind.

**They ship as ``draft`` with ``provenance.source == "builtin"``.** That marker
is what separates them from a *captured* draft: a captured draft is unproven
work the model or an operator turned into a template, and the lifecycle
deliberately refuses to instantiate it (a graph that never ran must not be
started with the appearance of authority). A builtin blueprint has a different
trust story — it is code that was reviewed and unit-tested here — so
``TemplateStore.instantiate`` may start it while still tagging the resulting
definition ``[builtin blueprint, unverified]``. Promotion to ``promoted`` is
still evidence-gated and unchanged: a blueprint earns that only after a real
run of this exact graph completes with per-node evidence
(:meth:`TemplateStore.verify`).

Two rules keep the seeding honest:

1. **Seeding never overwrites.** An operator may have edited, verified,
   promoted or deleted a builtin; re-seeding on a later boot must not clobber
   that state. ``seed_builtin_templates`` saves only ids absent from the store.
2. **The graphs are declared, not executed.** The ``GOAL_GATE`` acceptance
   criteria reference state keys (``metrics.*``) that a host's node runners
   populate; a runner that never sets them leaves the criterion unevaluable,
   which counts as NOT met. That is the correct fail-closed posture: the gate
   refuses rather than passing on a check that did not run.

The verification step is a ``TOOL`` node carrying ``config["verification_cmd"]``
(the same field the dynamic-workflow decomposer writes), which reports
``not_run`` until a host binds a verification executor — it is a declaration of
the check that proves the change, not a subprocess this module spawns.
"""

from __future__ import annotations

from alpha.workflow.models import NodeType, RetryPolicy, WorkflowEdge, WorkflowGraph, WorkflowNode
from alpha.workflow.templates import TemplateProvenance, TemplateState, TemplateStore, WorkflowTemplate

__all__ = [
    "BUILTIN_TEMPLATE_IDS",
    "BUILTIN_TEMPLATES",
    "bug_fix_graph",
    "code_change_graph",
    "get_builtin_template",
    "seed_builtin_templates",
]

#: The ids this module ships. A test pins this tuple so adding or removing a
#: blueprint is a deliberate, reviewable change.
BUILTIN_TEMPLATE_IDS: tuple[str, ...] = ("code_change", "bug_fix")

_BUILTIN_TAG = "builtin"


def code_change_graph() -> WorkflowGraph:
    """Plan -> implement -> test -> review -> accept, for a scoped code change.

    The ``accept`` ``GOAL_GATE`` is executor-free and measured by the runtime:
    it evaluates its criteria against run state with the safe AST evaluator, and
    an unevaluable required criterion counts as NOT met — so a change that never
    reported a passing test cannot clear the gate.
    """
    graph = WorkflowGraph()
    graph.add_node(
        WorkflowNode(
            id="plan",
            type=NodeType.AGENT,
            executor="alpha.agent",
            prompt="Design the change: identify the files to touch, the tests that must pass, and the acceptance criteria.",
            write_scope=["plan"],
        )
    )
    graph.add_node(
        WorkflowNode(
            id="implement",
            type=NodeType.AGENT,
            executor="alpha.agent",
            prompt="Implement the planned change. Keep the diff scoped to the plan; do not refactor unrelated code.",
            depends_on=["plan"],
            write_scope=["repo"],
            # Bounded repair: a failing implementation is retried a fixed number
            # of times, never looped forever.
            retry_policy=RetryPolicy(max_attempts=2, backoff="exponential"),
        )
    )
    graph.add_node(
        WorkflowNode(
            id="test",
            type=NodeType.TOOL,
            executor="alpha.tool",
            config={"verification_cmd": "pytest -q"},
            depends_on=["implement"],
            write_scope=["repo"],
        )
    )
    graph.add_node(
        WorkflowNode(
            id="review",
            type=NodeType.REVIEW,
            executor="alpha.agent",
            prompt="Review the diff for correctness, scope and test coverage. Report findings as claims.",
            depends_on=["test"],
            write_scope=["review"],
        )
    )
    graph.add_node(
        WorkflowNode(
            id="accept",
            type=NodeType.GOAL_GATE,
            config={
                "acceptance_criteria": [
                    {
                        "name": "tests_passed",
                        "expression": "metrics.tests_passed >= 1",
                        "required": True,
                    }
                ]
            },
            depends_on=["review"],
        )
    )
    graph.add_edge(WorkflowEdge(source="plan", target="implement"))
    graph.add_edge(WorkflowEdge(source="implement", target="test"))
    graph.add_edge(WorkflowEdge(source="test", target="review"))
    graph.add_edge(WorkflowEdge(source="review", target="accept"))
    return graph


def bug_fix_graph() -> WorkflowGraph:
    """Reproduce -> diagnose -> fix -> regression-test -> review -> accept.

    The extra ``reproduce`` step is what distinguishes a bug fix from a code
    change: a fix that cannot first reproduce the failure has no proof it
    addressed the actual cause. The ``fix`` node carries the bounded repair
    policy (three attempts), matching the design note's "bounded repair" loop.
    """
    graph = WorkflowGraph()
    graph.add_node(
        WorkflowNode(
            id="reproduce",
            type=NodeType.AGENT,
            executor="alpha.agent",
            prompt="Reproduce the reported bug with a minimal failing case. Capture the exact steps and observed output.",
            write_scope=["plan"],
        )
    )
    graph.add_node(
        WorkflowNode(
            id="diagnose",
            type=NodeType.AGENT,
            executor="alpha.agent",
            prompt="Diagnose the root cause from the reproduction. Name the file and mechanism, not just the symptom.",
            depends_on=["reproduce"],
            write_scope=["plan"],
        )
    )
    graph.add_node(
        WorkflowNode(
            id="fix",
            type=NodeType.AGENT,
            executor="alpha.agent",
            prompt="Implement the minimal fix for the diagnosed cause. Do not refactor unrelated code.",
            depends_on=["diagnose"],
            write_scope=["repo"],
            # Bounded repair: three attempts, then the run fails honestly.
            retry_policy=RetryPolicy(max_attempts=3, backoff="exponential"),
        )
    )
    graph.add_node(
        WorkflowNode(
            id="regression_test",
            type=NodeType.TOOL,
            executor="alpha.tool",
            config={"verification_cmd": "pytest -q"},
            depends_on=["fix"],
            write_scope=["repo"],
        )
    )
    graph.add_node(
        WorkflowNode(
            id="review",
            type=NodeType.REVIEW,
            executor="alpha.agent",
            prompt="Review the fix: does it address the root cause, and does the regression test actually cover it?",
            depends_on=["regression_test"],
            write_scope=["review"],
        )
    )
    graph.add_node(
        WorkflowNode(
            id="accept",
            type=NodeType.GOAL_GATE,
            config={
                "acceptance_criteria": [
                    {
                        "name": "regression_passed",
                        "expression": "metrics.regression_passed >= 1",
                        "required": True,
                    }
                ]
            },
            depends_on=["review"],
        )
    )
    graph.add_edge(WorkflowEdge(source="reproduce", target="diagnose"))
    graph.add_edge(WorkflowEdge(source="diagnose", target="fix"))
    graph.add_edge(WorkflowEdge(source="fix", target="regression_test"))
    graph.add_edge(WorkflowEdge(source="regression_test", target="review"))
    graph.add_edge(WorkflowEdge(source="review", target="accept"))
    return graph


def _builtin_code_change() -> WorkflowTemplate:
    return WorkflowTemplate(
        id="code_change",
        name="Code change",
        graph=code_change_graph(),
        description="Plan, implement, test, review and accept a scoped code change. [builtin blueprint, unverified]",
        tags=[_BUILTIN_TAG, "code"],
        state=TemplateState.DRAFT,
        provenance=TemplateProvenance(source="builtin"),
    )


def _builtin_bug_fix() -> WorkflowTemplate:
    return WorkflowTemplate(
        id="bug_fix",
        name="Bug fix",
        graph=bug_fix_graph(),
        description="Reproduce, diagnose, fix, regression-test, review and accept a bug fix. [builtin blueprint, unverified]",
        tags=[_BUILTIN_TAG, "bug"],
        state=TemplateState.DRAFT,
        provenance=TemplateProvenance(source="builtin"),
    )


#: The two blueprints, keyed by id — a module-level registry built **once** at
#: import, for enumeration (the id set, a listing endpoint). It holds static
#: instances: mutating one would persist for the process. Use
#: :func:`get_builtin_template` when you need a fresh, safe-to-mutate copy —
#: seeding goes through that path, so an edit to one blueprint's graph cannot
#: poison the store's saved copy.
BUILTIN_TEMPLATES: dict[str, WorkflowTemplate] = {
    "code_change": _builtin_code_change(),
    "bug_fix": _builtin_bug_fix(),
}


def get_builtin_template(template_id: str) -> WorkflowTemplate | None:
    """Return a fresh copy of a builtin blueprint, or ``None`` if unknown."""
    factory = {"code_change": _builtin_code_change, "bug_fix": _builtin_bug_fix}.get(template_id)
    return factory() if factory else None


def seed_builtin_templates(store: TemplateStore) -> list[str]:
    """Save every builtin blueprint whose id is absent from ``store``.

    Idempotent and non-destructive: an id already present (edited, verified,
    promoted, or re-seeded) is left exactly as the store holds it. Returns the
    ids actually seeded this call, so a caller can log or test the delta.
    """
    seeded: list[str] = []
    for template_id in BUILTIN_TEMPLATE_IDS:
        if store.get(template_id) is not None:
            continue
        store.save(get_builtin_template(template_id))
        seeded.append(template_id)
    return seeded
