"""Adversarial Hyperplan Review Tool.

Exposes hostile multi-perspective plan evaluation to the agent before coding starts.
"""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.planning.hyperplan import HyperplanPipeline

_GLOBAL_PIPELINE = HyperplanPipeline()


@tool
def hyperplan_review_manage(plan_title: str, plan_content: str) -> str:
    """Run hostile multi-agent audit on an execution plan across 4 orthogonal dimensions (gaps, architecture, security, testability). Returns plan_hash for execution pinning; BLOCKED plans must not execute."""
    report = _GLOBAL_PIPELINE.review_plan(plan_title, plan_content)

    summary = {
        "plan_title": report.plan_title,
        "overall_status": report.overall_status,
        "plan_hash": report.plan_hash,
        "is_blocked": report.is_blocked,
        "gatekeeper_summary": report.gatekeeper_summary,
        "verdicts": [
            {
                "role": v.reviewer_role,
                "status": v.status,
                "critique": v.critique,
                "risks": v.identified_risks,
                "missing_prerequisites": v.missing_prerequisites,
            }
            for v in report.verdicts
        ],
    }
    return json.dumps(summary, indent=2)
