"""Visual Engineering Worker (Enterprise Visual Specialist Profile).

Enterprise Frontend and Generative UI Engine:
- Category: visual-engineering
- Model Assignment: anthropic/claude-3-7-sonnet (reasoning: max)
- Strengths: Frontend UI/UX, CSS design tokens, responsive layouts, generative interactive widgets
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class VisualWidgetSpec:
    """Specification of a visual engineering deliverable."""
    widget_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    component_name: str = ""
    html_markup: str = ""
    css_styles: str = ""
    interactive_js: str = ""
    design_tokens: dict[str, str] = field(default_factory=dict)
    responsive_breakpoints: dict[str, str] = field(default_factory=dict)
    model_family: str = "anthropic/claude-3-7-sonnet"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def render_standalone_html(self) -> str:
        """Render complete, valid HTML5 document embedding styles and script."""
        token_vars = "\n  ".join([f"{k}: {v};" for k, v in self.design_tokens.items()])
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{self.component_name}</title>
<style>
:root {{
  {token_vars}
}}
{self.css_styles}
</style>
</head>
<body>
{self.html_markup}
<script>
{self.interactive_js}
</script>
</body>
</html>"""


class VisualEngineeringWorker:
    """Specialist worker producing high-fidelity frontend and generative UI artifacts."""

    def __init__(self, model_id: str = "anthropic/claude-3-7-sonnet"):
        self.model_id: str = model_id

    def build_component(
        self,
        component_name: str,
        requirements: str,
        primary_color: str = "#2563EB",
        theme: str = "dark",
    ) -> VisualWidgetSpec:
        """Synthesize a complete responsive UI widget with semantic design tokens."""
        is_dark = theme.lower() == "dark"
        bg_color = "#0F172A" if is_dark else "#FFFFFF"
        card_bg = "#1E293B" if is_dark else "#F8FAFC"
        text_color = "#F8FAFC" if is_dark else "#0F172A"
        border_color = "#334155" if is_dark else "#E2E8F0"

        tokens = {
            "--primary": primary_color,
            "--bg-surface": bg_color,
            "--bg-card": card_bg,
            "--text-main": text_color,
            "--border-subtle": border_color,
            "--radius-md": "8px",
            "--font-sans": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
        }

        css = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background-color: var(--bg-surface);
  color: var(--text-main);
  font-family: var(--font-sans);
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  padding: 16px;
}
.widget-card {
  background-color: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 24px;
  width: 100%;
  max-width: 480px;
  box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3);
  transition: transform 0.2s ease;
}
.widget-card:hover {
  transform: translateY(-2px);
}
.widget-btn {
  background-color: var(--primary);
  color: #FFFFFF;
  border: none;
  border-radius: 6px;
  padding: 10px 16px;
  font-weight: 600;
  cursor: pointer;
  margin-top: 16px;
  width: 100%;
}
.widget-btn:hover {
  filter: brightness(1.1);
}
@media (max-width: 640px) {
  .widget-card { padding: 16px; }
}
"""

        markup = f"""
<div class="widget-card" id="widget-{component_name.lower()}">
  <h2>{component_name}</h2>
  <p style="margin-top: 8px; opacity: 0.8;">{requirements}</p>
  <button class="widget-btn" onclick="handleWidgetAction()">Action</button>
  <div id="status-message" style="margin-top: 12px; font-size: 0.9em; opacity: 0.9;"></div>
</div>
"""

        js = """
function handleWidgetAction() {
  const el = document.getElementById('status-message');
  el.textContent = 'Action dispatched at ' + new Date().toLocaleTimeString();
}
"""

        return VisualWidgetSpec(
            component_name=component_name,
            html_markup=markup.strip(),
            css_styles=css.strip(),
            interactive_js=js.strip(),
            design_tokens=tokens,
            responsive_breakpoints={"mobile": "640px", "tablet": "768px", "desktop": "1024px"},
            model_family=self.model_id,
        )
