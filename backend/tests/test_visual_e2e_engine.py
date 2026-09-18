"""Tests for Visual Grounding and Web E2E Verification Engine."""

from agent_workspace.verification.visual_e2e_engine import VisualE2EEngine, AccessibilityTree


def test_accessibility_tree_parsing():
    html_sample = """
    <div>
        <h1>Dashboard Control</h1>
        <input name="username" type="text" />
        <button>Submit Request</button>
        <button>Cancel</button>
    </div>
    """
    tree = AccessibilityTree()
    tree.parse_html_dom(html_sample)

    buttons = tree.find_by_role("button")
    assert len(buttons) == 2
    assert any(b.name == "Submit Request" for b in buttons)

    inputs = tree.find_by_role("textbox")
    assert len(inputs) == 1
    assert inputs[0].name == "username"


def test_visual_regression_detection():
    engine = VisualE2EEngine()
    baseline = """
    <div>
        <h1>Deploy System</h1>
        <button>Deploy Staging</button>
        <button>Rollback</button>
    </div>
    """
    # Candidate missing the Rollback button
    candidate = """
    <div>
        <h1>Deploy System</h1>
        <button>Deploy Staging</button>
    </div>
    """

    res = engine.verify_ui_flow(
        baseline_html=baseline,
        candidate_html=candidate,
        required_elements=[("button", "Rollback")],
    )
    assert res["elements_removed_count"] == 1
    assert res["has_visual_regression"] is True
    assert len(res["missing_required_elements"]) == 1
    assert res["flow_passed"] is False
