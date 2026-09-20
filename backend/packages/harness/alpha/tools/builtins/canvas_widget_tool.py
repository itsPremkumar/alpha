"""Built-in tool for rendering and updating interactive Canvas UI widgets."""

from __future__ import annotations

import json
from typing import Literal

from langchain.tools import tool

from alpha.canvas.manager import get_canvas_manager
from alpha.canvas.widget import WidgetKind


@tool("canvas_widget", parse_docstring=True)
def canvas_widget_tool(
    action: Literal["render", "update", "inspect", "list"],
    widget_id: str = "",
    title: str = "",
    kind: WidgetKind = "dashboard",
    html_content: str = "",
    data_json: str = "",
) -> str:
    """Render and manage live, interactive HTML5/JS Canvas widgets for chat and dashboards.

    Args:
        action: Operation ('render', 'update', 'inspect', 'list').
        widget_id: ID of the widget (optional for render, required for update/inspect).
        title: Title of the widget (required for 'render').
        kind: Widget type ('dashboard', 'chart', 'table', 'task_board', 'custom_html'). Defaults to 'dashboard'.
        html_content: Custom HTML snippet or widget layout (optional).
        data_json: JSON string payload for reactive data binding.
    """
    manager = get_canvas_manager()

    if action == "list":
        widgets = manager.list_widgets()
        if not widgets:
            return "No active Canvas widgets."
        out = ["=== Active Canvas Widgets ==="]
        for w in widgets:
            out.append(f"- **{w.widget_id}**: {w.title} [Kind: `{w.kind}`]")
        return "\n".join(out)

    elif action == "render":
        if not title:
            return "Error: 'title' is required for 'render'."
        payload = {}
        if data_json:
            try:
                payload = json.loads(data_json)
            except Exception:
                payload = {"raw": data_json}

        widget = manager.create_widget(
            title=title,
            kind=kind,
            html_content=html_content or f"<p>Widget {title} active.</p>",
            data=payload,
            widget_id=widget_id or None,
        )
        return (
            f"Canvas Widget rendered: {widget.widget_id}\n"
            f"- Title: {widget.title}\n"
            f"- Kind: {widget.kind}\n"
            f"- Standalone HTML length: {len(widget.render_standalone_html())} bytes"
        )

    elif action == "update":
        if not widget_id:
            return "Error: 'widget_id' is required for 'update'."
        payload = None
        if data_json:
            try:
                payload = json.loads(data_json)
            except Exception:
                payload = {"raw": data_json}

        w = manager.update_widget(widget_id, data=payload, html_content=html_content or None)
        if not w:
            return f"Error: Widget '{widget_id}' not found."
        return f"Canvas Widget {widget_id} updated successfully."

    elif action == "inspect":
        if not widget_id:
            return "Error: 'widget_id' is required for 'inspect'."
        w = manager.get_widget(widget_id)
        if not w:
            return f"Error: Widget '{widget_id}' not found."
        return (
            f"=== Canvas Widget: {w.widget_id} ===\n"
            f"Title: {w.title} ({w.kind})\n"
            f"Updated: {w.updated_at}\n"
            f"Data: {json.dumps(w.data, indent=2)}\n"
            f"HTML: {w.html_content}"
        )

    return f"Error: Unknown action '{action}'."
