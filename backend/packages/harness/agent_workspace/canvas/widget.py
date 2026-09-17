"""Canvas Widget Models and Responsive HTML Renderers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

WidgetKind = Literal["dashboard", "chart", "table", "task_board", "custom_html"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class CanvasWidget:
    """An interactive visual component rendered in chat or standalone canvas."""

    widget_id: str
    title: str
    kind: WidgetKind = "dashboard"
    html_content: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def render_standalone_html(self) -> str:
        """Render self-contained HTML document with modern responsive design."""
        data_json = json.dumps(self.data, indent=2)
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{self.title} - DeerFlow Canvas</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      margin: 0; padding: 24px; background: #0f172a; color: #f8fafc;
    }}
    .canvas-container {{
      max-width: 900px; margin: 0 auto; background: #1e293b;
      border-radius: 12px; padding: 24px; box-shadow: 0 4px 20px rgba(0,0,0,0.4);
      border: 1px solid #334155;
    }}
    h1 {{ margin-top: 0; font-size: 1.5rem; color: #38bdf8; }}
    .badge {{
      display: inline-block; padding: 4px 8px; border-radius: 6px;
      font-size: 0.75rem; background: #0369a1; color: #e0f2fe; text-transform: uppercase;
    }}
    .widget-body {{ margin-top: 16px; }}
    pre {{ background: #090d16; padding: 12px; border-radius: 8px; overflow-x: auto; color: #a5f3fc; }}
  </style>
</head>
<body>
  <div class="canvas-container">
    <div style="display: flex; justify-content: space-between; align-items: center;">
      <h1>{self.title}</h1>
      <span class="badge">{self.kind}</span>
    </div>
    <div class="widget-body">
      {self.html_content}
    </div>
    <div style="margin-top: 24px;">
      <h3 style="font-size: 0.9rem; color: #94a3b8;">Reactive Data Payload</h3>
      <pre><code>{data_json}</code></pre>
    </div>
  </div>
</body>
</html>"""
