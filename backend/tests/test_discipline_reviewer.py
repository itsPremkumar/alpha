from agent_workspace.orchestration.discipline.reviewer import PlanReviewer, ReviewVerdict, VerdictType


def test_reviewer_rejects_destructive_commands():
    reviewer = PlanReviewer()
    verdict = reviewer.review_plan(
        task_goal="Clean workspace",
        constraints=[],
        proposed_steps=["Run rm -rf /tmp/data", "Run rm -rf / and wipe files"],
    )

    assert isinstance(verdict, ReviewVerdict)
    assert verdict.approved is False
    assert verdict.verdict_type == VerdictType.REJECT_WITH_COUNTEREXAMPLE
    assert any("destructive" in v.lower() for v in verdict.invariant_violations)
    assert len(verdict.counterexamples) > 0
    assert verdict.model_family == "openai/gpt-4o"


def test_reviewer_enforces_read_only_and_data_loss_constraints():
    reviewer = PlanReviewer()
    verdict = reviewer.review_plan(
        task_goal="Audit database schema",
        constraints=["Read only mode", "No data loss"],
        proposed_steps=["Inspect tables", "Write new indexes and update schema"],
    )

    assert verdict.approved is False
    assert any("read only" in v.lower() for v in verdict.invariant_violations)


def test_reviewer_rejects_unbounded_infinite_loop():
    reviewer = PlanReviewer()
    verdict = reviewer.review_plan(
        task_goal="Listen for events",
        constraints=[],
        proposed_steps=["Initialize listener", "while true do poll()"],
    )

    assert verdict.approved is False
    assert any("unbounded execution loop" in v.lower() for v in verdict.invariant_violations)


def test_reviewer_approves_safe_and_bounded_plan():
    reviewer = PlanReviewer()
    verdict = reviewer.review_plan(
        task_goal="Add unit tests for calculation engine",
        constraints=["Ensure test isolation"],
        proposed_steps=[
            "Inspect calculation engine functions",
            "Write pytest unit test cases",
            "Execute pytest and assert all tests pass",
        ],
    )

    assert verdict.approved is True
    assert verdict.verdict_type == VerdictType.APPROVE
    assert len(verdict.invariant_violations) == 0
