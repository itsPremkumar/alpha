"""Render caller-supplied memory blocks according to a routing plan."""

from __future__ import annotations

import html
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .models import RoutingPlan

DEFAULT_MAX_RENDER_CHARS = 12_000


@dataclass(slots=True)
class RecallStats:
    """Small, thread-safe counters for one renderer instance."""

    renders: int = 0
    available_surfaces: int = 0
    unavailable_surfaces: int = 0
    truncated_surfaces: int = 0
    total_chars: int = 0
    last_plan: dict[str, Any] | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "renders": self.renders,
                "available_surfaces": self.available_surfaces,
                "unavailable_surfaces": self.unavailable_surfaces,
                "truncated_surfaces": self.truncated_surfaces,
                "total_chars": self.total_chars,
                "last_plan": dict(self.last_plan) if self.last_plan is not None else None,
            }


class ScenarioRecall:
    """Stateful facade around the pure :func:`render` operation.

    It owns only counters; it never reads a memory store and never fabricates a
    block when a caller omits one.
    """

    def __init__(self, *, max_chars: int = DEFAULT_MAX_RENDER_CHARS) -> None:
        try:
            requested = int(max_chars)
        except (TypeError, ValueError, OverflowError):
            requested = DEFAULT_MAX_RENDER_CHARS
        self.max_chars = max(0, requested)
        self._stats = RecallStats()

    def render(
        self,
        plan: RoutingPlan,
        blocks: Mapping[str, str] | None = None,
        *,
        max_chars: int | None = None,
    ) -> str:
        if not isinstance(plan, RoutingPlan):
            plan = RoutingPlan.model_validate(plan)
        supplied = blocks or {}
        try:
            limit = self.max_chars if max_chars is None else max(0, int(max_chars))
        except (TypeError, ValueError, OverflowError):
            limit = self.max_chars
        selected = list(plan.surfaces)
        available: list[tuple[Any, str]] = []
        unavailable: list[str] = []
        truncated = 0
        for route in selected:
            value = supplied.get(route.name)
            if not isinstance(value, str) or not value.strip():
                unavailable.append(route.name)
                continue
            text = value.strip()
            # Reserve room for metadata and the unavailable disclosure.  The
            # exact cap is applied again after assembly below.
            available.append((route, text))

        if limit == 0 or not plan.enabled:
            with self._stats._lock:
                self._stats.renders += 1
                self._stats.available_surfaces += len(available)
                self._stats.unavailable_surfaces += len(unavailable)
                self._stats.last_plan = plan.to_dict()
            return ""

        section = _assemble(plan, available, unavailable, limit)
        if len(section) > limit:
            section = _assemble_bounded(plan, available, unavailable, limit)
        if "[truncated" in section:
            truncated = 1
        with self._stats._lock:
            self._stats.renders += 1
            self._stats.available_surfaces += len(available)
            self._stats.unavailable_surfaces += len(unavailable)
            self._stats.truncated_surfaces += truncated
            self._stats.total_chars += len(section)
            self._stats.last_plan = plan.to_dict()
        return section[:limit]

    def stats(self) -> dict[str, Any]:
        return self._stats.snapshot()


# Common alternate spelling for callers that think of this as a renderer.
ScenarioRecallRenderer = ScenarioRecall
RecallRenderer = ScenarioRecall


def _assemble(
    plan: RoutingPlan,
    available: list[tuple[Any, str]],
    unavailable: list[str],
    limit: int,
) -> str:
    del limit  # The caller performs the final hard cap.
    lines = [
        "<scenario_memory>",
        f"<scenario>{html.escape(plan.scenario.value, quote=False)}</scenario>",
        f"<confidence>{plan.confidence:.2f}</confidence>",
    ]
    for route, text in available:
        lines.extend(
            [
                f'<surface name="{html.escape(route.name, quote=True)}" weight="{route.weight:.2f}" budget_units="{route.budget_units}">',
                html.escape(text, quote=False),
                "</surface>",
            ]
        )
    if unavailable:
        lines.append("<unavailable_surfaces>")
        for name in unavailable:
            safe_name = html.escape(name, quote=True)
            lines.append(f'<surface name="{safe_name}">unavailable: no block supplied</surface>')
        lines.append("</unavailable_surfaces>")
    lines.append("</scenario_memory>")
    return "\n".join(lines)


def _assemble_bounded(
    plan: RoutingPlan,
    available: list[tuple[Any, str]],
    unavailable: list[str],
    limit: int,
) -> str:
    """Retain a prefix of supplied blocks while preserving disclosures."""

    header = [
        "<scenario_memory>",
        f"<scenario>{html.escape(plan.scenario.value, quote=False)}</scenario>",
        f"<confidence>{plan.confidence:.2f}</confidence>",
    ]
    unavailable_lines: list[str] = []
    if unavailable:
        unavailable_lines.append("<unavailable_surfaces>")
        unavailable_lines.extend(f'<surface name="{html.escape(name, quote=True)}">unavailable: no block supplied</surface>' for name in unavailable)
        unavailable_lines.append("</unavailable_surfaces>")
    marker = "[truncated: caller block exceeded the character budget]"
    fixed_lines = list(header)
    for route, _ in available:
        fixed_lines.extend(
            [
                f'<surface name="{html.escape(route.name, quote=True)}" weight="{route.weight:.2f}" budget_units="{route.budget_units}">',
                "",
                "</surface>",
            ]
        )
    fixed_lines.extend([*unavailable_lines, "</scenario_memory>"])
    fixed = "\n".join(fixed_lines)
    content_budget = limit - len(fixed) - (len(marker) + 1) * len(available)
    if content_budget <= 0:
        return _bounded_fallback(plan, available, unavailable, limit)
    per_surface = content_budget // max(1, len(available))
    lines = list(header)
    for route, text in available:
        safe_text = html.escape(text, quote=False)
        was_truncated = len(safe_text) > per_surface
        piece = safe_text[:per_surface]
        lines.extend(
            [
                f'<surface name="{html.escape(route.name, quote=True)}" weight="{route.weight:.2f}" budget_units="{route.budget_units}">',
                piece,
                marker if was_truncated else "",
                "</surface>",
            ]
        )
    lines.extend(unavailable_lines)
    lines.append("</scenario_memory>")
    result = "\n".join(line for line in lines if line != "")
    return result if len(result) <= limit else _bounded_fallback(plan, available, unavailable, limit)


def _bounded_fallback(
    plan: RoutingPlan,
    available: list[tuple[Any, str]],
    unavailable: list[str],
    limit: int,
) -> str:
    """Keep metadata/unavailable disclosures when blocks exceed the cap."""

    names = ", ".join(route.name for route, _ in available) or "none"
    missing = ", ".join(unavailable) or "none"
    base = (
        "<scenario_memory>\n"
        f"<scenario>{html.escape(plan.scenario.value, quote=False)}</scenario>\n"
        f"<surfaces>{html.escape(names, quote=False)}</surfaces>\n"
        f"<unavailable_surfaces>{html.escape(missing, quote=False)}</unavailable_surfaces>\n"
        "[truncated: caller blocks exceeded the character budget]\n"
        "</scenario_memory>"
    )
    if len(base) <= limit:
        return base
    # A very small caller budget still gets a bounded, explicit section.
    return base[:limit]


def render(
    plan: RoutingPlan,
    blocks: Mapping[str, str] | None = None,
    *,
    max_chars: int | None = None,
    renderer: ScenarioRecall | None = None,
) -> str:
    """Render only the blocks supplied by the caller, in plan order."""

    active = renderer or (ScenarioRecall() if max_chars is None else ScenarioRecall(max_chars=max_chars))
    return active.render(plan, blocks, max_chars=max_chars)


render_plan = render


def stats(renderer: ScenarioRecall | None = None) -> dict[str, Any]:
    """Return renderer counters; a fresh renderer makes the function stateless."""

    return (renderer or ScenarioRecall()).stats()


__all__ = [
    "DEFAULT_MAX_RENDER_CHARS",
    "RecallRenderer",
    "RecallStats",
    "ScenarioRecall",
    "ScenarioRecallRenderer",
    "render",
    "render_plan",
    "stats",
]
