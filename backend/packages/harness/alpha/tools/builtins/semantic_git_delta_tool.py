"""Built-in Semantic Git Time-Machine and AST Change-Delta Analyzer Tool.

Exposes ``analyze_semantic_git_delta`` to the agent.
"""

from __future__ import annotations

import json
import os
from typing import Any

from langchain.tools import tool

from alpha.lineage.semantic_git_delta import SemanticGitDeltaAnalyzer


@tool("analyze_semantic_git_delta", parse_docstring=True)
def analyze_semantic_git_delta(
    action: str = "repo",
    repo_path: str = "",
    base_ref: str = "HEAD~1",
    head_ref: str = "HEAD",
    before_sources_json: str = "{}",
    after_sources_json: str = "{}",
    max_blast_radius: int = 40,
) -> dict[str, Any]:
    """Convert a git line diff into structured semantic AST transformations and risk.

    Rather than reporting which lines changed, this tool reports what meaning
    changed: function and class renames (matched by body shape, not text),
    parameter additions and removals including whether a new parameter is
    defaulted, type annotation mutations, decorator alterations, exception
    contract changes, base class changes and visibility flips. It then computes
    a Breaking Change Risk Score in [0.0, 1.0], resolves the blast radius
    across dependent modules using the symbol dependency graph, and emits
    migration recommendations naming the call sites to update.

    Args:
        action: ``repo`` analyzes a git revision range, ``sources`` analyzes an
            inline before/after file overlay pair.
        repo_path: Repository path (defaults to the current directory).
        base_ref: Base revision-ish for the ``repo`` action.
        head_ref: Head revision-ish for the ``repo`` action.
        before_sources_json: JSON object mapping path to source for the
            ``sources`` action.
        after_sources_json: JSON object mapping path to source for the
            ``sources`` action.
        max_blast_radius: Maximum number of dependent call sites to report.

    Returns:
        dict: ``{"success": bool, "files_changed": int, "risk_score": float,
        "risk_band": str, "transformations": list, "blast_radius": list,
        "recommendations": list}``. On failure ``success`` is False and
        ``error`` describes the reason.
    """
    try:
        normalized_action = (action or "repo").strip().lower()
        root = repo_path or os.getcwd()
        analyzer = SemanticGitDeltaAnalyzer(root)

        if normalized_action == "sources":
            try:
                before = json.loads(before_sources_json) if before_sources_json else {}
                after = json.loads(after_sources_json) if after_sources_json else {}
            except (ValueError, TypeError) as exc:
                return {
                    "success": False,
                    "error": f"source overlays must be valid JSON objects: {exc}",
                    "transformations": [],
                }
            if not isinstance(before, dict) or not isinstance(after, dict):
                return {
                    "success": False,
                    "error": "source overlays must decode to JSON objects",
                    "transformations": [],
                }
            report = analyzer.analyze_sources(
                {str(k): str(v) for k, v in before.items()},
                {str(k): str(v) for k, v in after.items()},
                base_ref=base_ref,
                head_ref=head_ref,
                max_blast_radius=max_blast_radius,
            )
        elif normalized_action == "repo":
            report = analyzer.analyze(
                base_ref=base_ref,
                head_ref=head_ref,
                max_blast_radius=max_blast_radius,
            )
        else:
            return {
                "success": False,
                "error": f"unknown action '{action}'",
                "transformations": [],
            }

        payload = report.to_dict()
        payload["success"] = not report.error
        return payload
    except Exception as exc:  # pragma: no cover - defensive boundary
        return {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "transformations": [],
            "blast_radius": [],
            "recommendations": [],
        }
