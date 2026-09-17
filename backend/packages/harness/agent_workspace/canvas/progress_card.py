"""Streaming Progress Draft Cards inspired by OpenClaw."""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent_workspace.canvas.widget import CanvasWidget


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class ProgressCard:
    """In-place updating visual progress indicator replacing noisy chat messages."""

    title: str
    card_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    phase: str = "Initializing"
    percentage: int = 0
    current_activity: str = ""
    milestones: list[dict[str, Any]] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    updated_at: str = field(default_factory=_now)

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, time.time() - self.start_time)

    def render_markdown(self) -> str:
        """Render a clean GitHub-flavored markdown progress card."""
        pct = max(0, min(100, self.percentage))
        total_bars = 12
        filled_bars = int(math.floor((pct / 100.0) * total_bars))
        empty_bars = total_bars - filled_bars
        bar_str = "█" * filled_bars + "░" * empty_bars

        lines = [
            f"### ⏱️ [{self.phase}] {self.title}",
            f"**Progress**: `[{bar_str}]` **{pct}%** (Elapsed: `{self.elapsed_seconds:.1f}s`)",
        ]
        if self.current_activity:
            lines.append(f"**Current Action**: {self.current_activity}")

        if self.milestones:
            lines.append("\n**Milestones**:")
            for m in self.milestones:
                status = m.get("status", "pending")
                icon = "✅" if status in ("completed", "verified", "done") else "🔄" if status == "in_progress" else "⚪"
                lines.append(f"- {icon} {m.get('title', 'Untitled')}")

        return "\n".join(lines)

    def to_canvas_widget(self) -> CanvasWidget:
        """Convert into a rich responsive CanvasWidget."""
        milestone_html = "".join(
            f"<li><strong>{m.get('status', 'pending').upper()}:</strong> {m.get('title', '')}</li>"
            for m in self.milestones
        )
        html = f"""
        <div style="font-family: sans-serif; background: #1e293b; color: #f8fafc; padding: 16px; border-radius: 8px;">
          <h2 style="margin: 0; color: #38bdf8;">[{self.phase}] {self.title}</h2>
          <div style="margin: 12px 0; background: #334155; border-radius: 4px; height: 12px; overflow: hidden;">
            <div style="background: #0284c7; width: {self.percentage}%; height: 100%;"></div>
          </div>
          <p><strong>Action:</strong> {self.current_activity}</p>
          <ul>{milestone_html}</ul>
        </div>
        """
        return CanvasWidget(
            widget_id=f"card-{self.card_id}",
            title=self.title,
            kind="dashboard",
            html_content=html,
            data=asdict(self),
        )


class ProgressCardStore:
    """Registry maintaining active progress cards."""

    def __init__(self):
        self._cards: dict[str, ProgressCard] = {}

    def get_or_create(self, title: str, card_id: str | None = None) -> ProgressCard:
        cid = card_id or uuid.uuid4().hex[:8]
        if cid not in self._cards:
            self._cards[cid] = ProgressCard(title=title, card_id=cid)
        return self._cards[cid]

    def get_card(self, card_id: str) -> ProgressCard | None:
        return self._cards.get(card_id)

    def update_card(
        self,
        card_id: str,
        phase: str | None = None,
        percentage: int | None = None,
        current_activity: str | None = None,
        milestones: list[dict[str, Any]] | None = None,
    ) -> ProgressCard:
        card = self._cards.get(card_id)
        if not card:
            raise KeyError(f"ProgressCard '{card_id}' does not exist.")

        if phase is not None:
            card.phase = phase
        if percentage is not None:
            card.percentage = percentage
        if current_activity is not None:
            card.current_activity = current_activity
        if milestones is not None:
            card.milestones = milestones
        card.updated_at = _now()
        return card


_global_progress_store = ProgressCardStore()


def get_progress_card_store() -> ProgressCardStore:
    return _global_progress_store
