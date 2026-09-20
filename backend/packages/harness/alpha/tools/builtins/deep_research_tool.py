"""Built-in Deep Research tool for autonomous multi-hop investigation and report generation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from langchain.tools import tool

from alpha.research.engine import DeepResearchEngine


@tool("deep_research", parse_docstring=True)
async def deep_research(
    topic: str,
    depth: int = 3,
    max_sources: int = 15,
    include_adversarial: bool = True,
    output_path: str = "deep_research_report.md",
    output_filename: str | None = None,
) -> str:
    """Conduct autonomous deep research across multi-lane search, recursive gap analysis, and citation synthesis.

    Compiles a 5-pass search plan (Discovery, Specific Evidence, Adversarial Contradiction,
    Fact Verification, and Strategic Synthesis), executes multi-source extraction, detects
    contradictions, and generates a publication-ready Markdown report with strict citations.

    Args:
        topic: The research question, technical subject, or investigation target.
        depth: Investigation depth from 1 (broad landscape) to 5 (exhaustive recursive multi-pass). Default 3.
        max_sources: Maximum distinct primary and secondary sources to extract and cite. Default 15.
        include_adversarial: Actively execute falsification queries to uncover bottlenecks, risks, and failure modes. Default True.
        output_path: Destination file path to save the synthesized report. Default 'deep_research_report.md'.
        output_filename: Optional legacy alias for output_path.
    """
    engine = DeepResearchEngine()

    report = await engine.run_research(
        topic=topic,
        depth=depth,
        max_sources=max_sources,
        include_adversarial=include_adversarial,
    )

    dest = output_filename or output_path or "deep_research_report.md"
    saved_path: str | None = None
    if dest:
        dest_p = Path(dest)
        try:
            if dest_p.is_absolute() or len(dest_p.parts) > 1:
                dest_p.parent.mkdir(parents=True, exist_ok=True)
                dest_p.write_text(report.markdown_content, encoding="utf-8")
                saved_path = str(dest_p)
            else:
                candidates = [
                    Path("/mnt/user-data/outputs"),
                    Path("outputs"),
                    Path("."),
                ]
                target_dir = next((p for p in candidates if p.exists() and p.is_dir()), Path("."))
                target_file = target_dir / dest_p.name
                target_file.write_text(report.markdown_content, encoding="utf-8")
                saved_path = str(target_file)
        except Exception:
            try:
                dest_p.write_text(report.markdown_content, encoding="utf-8")
                saved_path = str(dest_p)
            except Exception:
                saved_path = None

    summary_payload: dict[str, Any] = {
        "status": "success",
        "topic": report.topic,
        "executive_summary": report.executive_summary,
        "sources_analyzed": len(report.sources),
        "citations_verified": len(report.citations),
        "contradictions_detected": len(report.contradictions),
        "saved_report_path": saved_path,
        "markdown_report": report.markdown_content,
        "core_findings_preview": report.core_findings[:3],
        "top_sources": [
            {"title": s.title, "url": s.url, "domain": s.domain, "facet": s.pass_type}
            for s in report.sources[:5]
        ],
    }

    return json.dumps(summary_payload, indent=2)
