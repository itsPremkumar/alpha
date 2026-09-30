"""Deterministic code critic (AST rules + severity-ranked report).

- :func:`~alpha.critique.critic.critique_path` — critique a file or directory
- :class:`~alpha.critique.critic.CritiqueReport` — findings + Markdown render
"""

from __future__ import annotations

from alpha.critique.critic import CritiqueReport, critique_path
from alpha.critique.rules import RULES, Finding

__all__ = ["RULES", "CritiqueReport", "Finding", "critique_path"]
