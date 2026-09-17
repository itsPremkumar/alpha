"""Canvas Lifecycle and Widget State Manager."""

from __future__ import annotations

import threading
from typing import Any
from uuid import uuid4

from agent_workspace.canvas.widget import CanvasWidget, WidgetKind, _now


class CanvasManager:
    """Manages active canvas widgets, reactive state, and HTML renderings."""

    def __init__(self):
        self._widgets: dict[str, CanvasWidget] = {}
        self._lock = threading.Lock()

    def create_widget(
        self,
        title: str,
        kind: WidgetKind = "dashboard",
        html_content: str = "",
        data: dict[str, Any] | None = None,
        widget_id: str | None = None,
    ) -> CanvasWidget:
        wid = widget_id or f"canvas_{uuid4().hex[:8]}"
        widget = CanvasWidget(
            widget_id=wid,
            title=title,
            kind=kind,
            html_content=html_content,
            data=data or {},
        )
        with self._lock:
            self._widgets[wid] = widget
        return widget

    def update_widget(
        self,
        widget_id: str,
        data: dict[str, Any] | None = None,
        html_content: str | None = None,
    ) -> CanvasWidget | None:
        with self._lock:
            widget = self._widgets.get(widget_id)
            if not widget:
                return None
            if data is not None:
                widget.data.update(data)
            if html_content is not None:
                widget.html_content = html_content
            widget.updated_at = _now()
            return widget

    def get_widget(self, widget_id: str) -> CanvasWidget | None:
        with self._lock:
            return self._widgets.get(widget_id)

    def list_widgets(self) -> list[CanvasWidget]:
        with self._lock:
            return list(self._widgets.values())


_global_canvas = CanvasManager()


def get_canvas_manager() -> CanvasManager:
    return _global_canvas
