"""Built-in Progress Draft Card tool for in-place streaming status updates."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.canvas.progress_card import get_progress_card_store


@tool("update_progress_card", parse_docstring=True)
def update_progress_card(
    title: str,
    card_id: str | None = None,
    phase: str | None = None,
    percentage: int | None = None,
    current_activity: str | None = None,
    milestones_json: str | None = None,
) -> str:
    """Publish or update an in-place streaming progress draft card.

    Instead of polluting the chat with repetitive conversational status messages,
    update a structured progress card with phase, percentage (0-100), active task,
    and milestone checkmarks.

    Args:
        title: Overall goal or task title.
        card_id: Card ID to update in-place. If omitted, generates a new card.
        phase: Current operational phase (e.g., 'Analyzing', 'Coding', 'Testing', 'Completed').
        percentage: Integer between 0 and 100 indicating task completion progress.
        current_activity: Granular description of the exact tool or step being run right now.
        milestones_json: Optional JSON array of milestones: [{"title": "Step 1", "status": "done"}].
    """
    store = get_progress_card_store()
    card = store.get_or_create(title=title, card_id=card_id)

    milestones_list = None
    if milestones_json:
        try:
            parsed = json.loads(milestones_json)
            if isinstance(parsed, list):
                milestones_list = parsed
        except Exception:
            pass

    updated_card = store.update_card(
        card_id=card.card_id,
        phase=phase,
        percentage=percentage,
        current_activity=current_activity,
        milestones=milestones_list,
    )

    return f"ProgressCard ID: `{updated_card.card_id}`\n\n" + updated_card.render_markdown()
