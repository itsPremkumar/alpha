"""Deterministic code critic.

Walks a file or directory, parses each Python module, applies every rule in
:mod:`alpha.critique.rules`, and produces a severity-ranked report. It is the
tool used to critique this session's own work and drive the hardening pass.

Run the bundled scanner (from the repo root):
    python critique_scan.py <path> [<path> ...]
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alpha.critique.rules import RULES, Finding

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass
class CritiqueReport:
    """Severity-ranked findings for a critique run."""

    root: str
    files_scanned: int
    findings: list[Finding] = field(default_factory=list)

    def by_severity(self) -> dict[str, int]:
        counts = {"high": 0, "medium": 0, "low": 0}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts

    def by_rule(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.rule] = counts.get(f.rule, 0) + 1
        return counts

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "high")

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.path, f.line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "files_scanned": self.files_scanned,
            "by_severity": self.by_severity(),
            "by_rule": self.by_rule(),
            "findings": [f.__dict__ for f in self.sorted_findings()],
        }

    def render_markdown(self) -> str:
        sev = self.by_severity()
        lines = [
            "# Code Critique Report",
            "",
            f"- Root: `{self.root}`",
            f"- Files scanned: {self.files_scanned}",
            f"- Findings: {len(self.findings)} (high={sev['high']}, medium={sev['medium']}, low={sev['low']})",
            "",
        ]
        if not self.findings:
            lines.append("_No findings._")
            return "\n".join(lines)
        lines.append("| Severity | Rule | Location | Message |")
        lines.append("|---|---|---|---|")
        for f in self.sorted_findings():
            lines.append(f"| {f.severity} | {f.rule} | `{f.path}:{f.line}` | {f.message} |")
        return "\n".join(lines)


def critique_path(root: str | Path) -> CritiqueReport:
    """Critique a single file or every ``*.py`` under a directory."""
    root_path = Path(root)
    if root_path.is_file():
        files = [root_path]
    else:
        files = sorted(p for p in root_path.rglob("*.py") if "__pycache__" not in p.parts)

    findings: list[Finding] = []
    scanned = 0
    for f in files:
        try:
            src = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            findings.append(Finding("syntax-error", "high", str(f), getattr(exc, "lineno", 0) or 0,
                                    f"syntax error: {exc.msg}", "Fix the syntax error."))
            continue
        scanned += 1
        rel = str(f)
        for rule in RULES:
            findings.extend(rule(rel, tree, src))
    return CritiqueReport(root=str(root_path), files_scanned=scanned, findings=findings)


__all__ = ["CritiqueReport", "critique_path"]
