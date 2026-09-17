from agent_workspace.orchestration.discipline.consultant import GapAnalysisReport, PlanConsultant


def test_consultant_identifies_missing_error_and_tests():
    consultant = PlanConsultant()
    # Plan without error handling or verification
    report = consultant.analyze_gaps(
        task_title="Build user database migration",
        task_description="Migrate user records to the new PostgreSQL database.",
        proposed_steps=["Connect to DB", "Copy records", "Finish"],
        is_visual_or_frontend=False,
    )

    assert isinstance(report, GapAnalysisReport)
    assert report.model_family == "anthropic/claude-3-7-sonnet"
    assert report.readiness_score < 1.0
    assert any("failure recovery" in g.lower() or "error" in g.lower() for g in report.gaps_identified)
    assert any("verification" in g.lower() or "test" in g.lower() for g in report.gaps_identified)
    assert len(report.suggested_additions) >= 2


def test_consultant_frontend_ui_auditing():
    consultant = PlanConsultant()
    # Frontend task missing responsiveness and empty states
    report = consultant.analyze_gaps(
        task_title="Create User Settings Dashboard",
        task_description="Develop a dashboard UI component.",
        proposed_steps=["Create component", "Add form fields", "Verify UI in tests and handle error fallback"],
        is_visual_or_frontend=True,
    )

    assert any("responsive" in g.lower() for g in report.gaps_identified)
    assert any("empty" in g.lower() or "loading" in g.lower() for g in report.gaps_identified)
    assert any("semantic design tokens" in s.lower() or "design tokens" in s.lower() for s in report.ui_specifications)


def test_consultant_high_readiness_plan():
    consultant = PlanConsultant()
    # Comprehensive plan with error handling, test, and responsiveness
    report = consultant.analyze_gaps(
        task_title="Build responsive payments widget",
        task_description="Build responsive grid UI with loading skeleton, design tokens, error fallback, and assert tests.",
        proposed_steps=[
            "Design responsive layout using CSS grid with dark theme tokens",
            "Render loading spinner and error retry fallback",
            "Run unit assert tests to verify transactions",
        ],
        is_visual_or_frontend=True,
    )

    assert len(report.gaps_identified) == 0
    assert report.readiness_score == 1.0
