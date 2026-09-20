
from alpha.research.five_pass import (
    FivePassSearchCompiler,
    FivePassSearchPlan,
    SearchPassType,
)


def test_five_pass_search_compiler():
    question = "asyncpg connection pooling performance"
    plan = FivePassSearchCompiler.compile(question)

    assert isinstance(plan, FivePassSearchPlan)
    assert plan.original_question == question
    assert len(plan.lanes) == 5

    pass_types = [lane.pass_type for lane in plan.lanes]
    assert SearchPassType.DISCOVERY in pass_types
    assert SearchPassType.SPECIFIC_EVIDENCE in pass_types
    assert SearchPassType.ADVERSARIAL_CONTRADICTION in pass_types
    assert SearchPassType.FACT_VERIFICATION in pass_types
    assert SearchPassType.STRATEGIC_SYNTHESIS in pass_types

    # Validate the Adversarial Contradiction query searches for bugs/issues/memory leaks
    contra_lane = next(l for l in plan.lanes if l.pass_type == SearchPassType.ADVERSARIAL_CONTRADICTION)
    assert "issues" in contra_lane.query or "bugs" in contra_lane.query or "leak" in contra_lane.query

    d = plan.to_dict()
    assert d["lanes_count"] == 5
