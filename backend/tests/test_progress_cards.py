import json

from alpha.canvas.progress_card import ProgressCardStore
from alpha.tools.builtins.progress_card_tool import update_progress_card


def test_progress_card_lifecycle():
    store = ProgressCardStore()
    card = store.get_or_create("Autonomous Refactor", card_id="card-001")
    assert card.title == "Autonomous Refactor"
    assert card.card_id == "card-001"
    assert card.percentage == 0

    # In-place update
    updated = store.update_card(
        card_id="card-001",
        phase="Testing",
        percentage=75,
        current_activity="Running pytest on backend",
        milestones=[
            {"title": "Analyze codebase", "status": "completed"},
            {"title": "Refactor modules", "status": "completed"},
            {"title": "Execute test suite", "status": "in_progress"},
        ],
    )
    assert updated.percentage == 75
    assert updated.phase == "Testing"
    assert len(updated.milestones) == 3

    # Render Markdown
    md = updated.render_markdown()
    assert "[Testing] Autonomous Refactor" in md
    assert "75%" in md
    assert "✅ Analyze codebase" in md
    assert "🔄 Execute test suite" in md

    # Canvas Widget conversion
    widget = updated.to_canvas_widget()
    assert widget.widget_id == "card-card-001"
    assert widget.kind == "dashboard"


def test_progress_card_tool():
    milestones = json.dumps([
        {"title": "Deconstruct goal", "status": "completed"},
        {"title": "Implement changes", "status": "in_progress"},
    ])
    res = update_progress_card.invoke({
        "title": "Continuous Pipeline",
        "phase": "Executing",
        "percentage": 50,
        "current_activity": "Compiling assets",
        "milestones_json": milestones,
    })
    assert "ProgressCard ID:" in res
    assert "[Executing] Continuous Pipeline" in res
    assert "50%" in res
    assert "✅ Deconstruct goal" in res
