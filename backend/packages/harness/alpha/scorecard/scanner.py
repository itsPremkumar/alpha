"""Scan an Alpha installation against the frontier capability taxonomy.

The scanner is deliberately filesystem-based and dependency-free: it checks for
the presence of the module/package each capability maps to. It reports coverage
per category and overall, lists the genuine gaps, and can render a Markdown
report. It never claims a capability works — only that its implementation is
present — and says so explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alpha.scorecard.taxonomy import CATEGORIES, Capability, TAXONOMY


@dataclass
class CapabilityStatus:
    capability: Capability
    present: bool


@dataclass
class ScorecardReport:
    alpha_root: str
    statuses: list[CapabilityStatus]

    @property
    def total(self) -> int:
        return len(self.statuses)

    @property
    def present_count(self) -> int:
        return sum(1 for s in self.statuses if s.present)

    @property
    def coverage(self) -> float:
        return self.present_count / self.total if self.total else 0.0

    def by_category(self) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        for status in self.statuses:
            cat = status.capability.category
            have, tot = out.get(cat, (0, 0))
            out[cat] = (have + (1 if status.present else 0), tot + 1)
        return out

    def missing(self) -> list[Capability]:
        return [s.capability for s in self.statuses if not s.present]

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha_root": self.alpha_root,
            "total": self.total,
            "present": self.present_count,
            "coverage": round(self.coverage, 4),
            "by_category": {k: {"present": v[0], "total": v[1]} for k, v in self.by_category().items()},
            "missing": [c.key for c in self.missing()],
        }

    def render_markdown(self) -> str:
        lines = [
            "# Alpha Capability Scorecard",
            "",
            f"- Alpha root: `{self.alpha_root}`",
            f"- Coverage: **{self.present_count}/{self.total}** ({self.coverage:.0%})",
            "",
            "| Category | Present | Total |",
            "|---|---:|---:|",
        ]
        for cat, (have, tot) in self.by_category().items():
            lines.append(f"| {cat} | {have} | {tot} |")
        lines.append("")
        missing = self.missing()
        if missing:
            lines.append("## Missing / not detected")
            lines.append("")
            for cap in missing:
                lines.append(f"- **{cap.name}** (`{cap.key}`) — expected `{cap.probe}`")
        else:
            lines.append("## Missing / not detected")
            lines.append("")
            lines.append("_None — every capability in the taxonomy was detected._")
        lines.append("")
        lines.append(
            "> Presence is checked by filesystem probe only. A present capability is "
            "implemented in the tree; it is not a claim that it is enabled, configured, "
            "or verified at runtime."
        )
        return "\n".join(lines)


def scan(alpha_root: str | Path) -> ScorecardReport:
    """Probe *alpha_root* for each capability in the taxonomy."""
    root = Path(alpha_root)
    statuses = [CapabilityStatus(cap, (root / cap.probe).exists()) for cap in TAXONOMY]
    return ScorecardReport(alpha_root=str(root), statuses=statuses)


__all__ = ["CapabilityStatus", "ScorecardReport", "scan", "CATEGORIES"]
