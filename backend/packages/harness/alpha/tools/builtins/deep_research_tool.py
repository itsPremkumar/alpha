"""Built-in Deep Research tool for autonomous multi-hop investigation and report generation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from langchain.tools import tool
from langgraph.config import get_config

from alpha.config.paths import get_paths
from alpha.research.engine import DeepResearchEngine
from alpha.runtime.user_context import resolve_config_user_id


def _resolve_outputs_dir() -> Path:
    """Resolve the current thread outputs directory without trusting tool paths."""

    try:
        config = get_config()
    except RuntimeError:
        config = {}
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id")
    if thread_id:
        return get_paths().sandbox_outputs_dir(
            str(thread_id),
            user_id=resolve_config_user_id(config),
        )
    return get_paths().base_dir / "deep-research"


def _safe_output_filename(value: str) -> str:
    filename = str(value or "deep_research_report.md").strip()
    path = Path(filename)
    if not filename or filename in {".", ".."} or path.is_absolute() or len(path.parts) != 1 or "/" in filename or "\\" in filename:
        raise ValueError("output_path must be a filename only, without directories")
    if path.suffix.lower() not in {".md", ".markdown"}:
        raise ValueError("output_path must use a .md or .markdown filename")
    return path.name


async def _write_report(outputs_dir: Path, filename: str, content: str) -> str:
    def write() -> str:
        outputs_dir.mkdir(parents=True, exist_ok=True)
        outputs_root = outputs_dir.resolve()
        raw_target = outputs_dir / filename
        if raw_target.is_symlink():
            raise ValueError("output_path must not be a symbolic link")
        target = raw_target.resolve(strict=False)
        try:
            target.relative_to(outputs_root)
        except ValueError as exc:
            raise ValueError("output_path resolves outside the thread outputs directory") from exc
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError("output_path must resolve to a regular file")
        target.write_text(content, encoding="utf-8")
        return str(target)

    return await asyncio.to_thread(write)


@tool("deep_research", parse_docstring=True)
async def deep_research(
    topic: str,
    depth: int = 3,
    max_sources: int = 15,
    include_adversarial: bool = True,
    output_path: str = "deep_research_report.md",
    output_filename: str | None = None,
) -> str:
    """Conduct bounded multi-lane deep research with explicit evidence status.

    Compiles a 5-pass search plan (Discovery, Specific Evidence, Adversarial Contradiction,
    Fact Verification, and Strategic Synthesis), executes bounded multi-source extraction,
    and generates a Markdown evidence report. A report with no retrieved sources is returned
    as ``no_evidence`` rather than fabricated or labeled successful.

    Args:
        topic: The research question, technical subject, or investigation target.
        depth: Investigation depth from 1 (broad landscape) to 5 (broader targeted gap follow-up). Default 3.
        max_sources: Maximum distinct primary and secondary sources to extract and cite. Default 15.
        include_adversarial: Actively execute falsification queries to uncover bottlenecks, risks, and failure modes. Default True.
        output_path: Markdown filename to save inside the current thread outputs directory. Default 'deep_research_report.md'.
        output_filename: Optional legacy alias for output_path. Both accept a filename only, never a host path.
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
    output_error: str | None = None
    try:
        filename = _safe_output_filename(dest)
        saved_path = await _write_report(_resolve_outputs_dir(), filename, report.markdown_content)
    except Exception as exc:
        output_error = str(exc)

    summary_payload: dict[str, Any] = {
        "status": report.status,
        "topic": report.topic,
        "executive_summary": report.executive_summary,
        "sources_analyzed": len(report.sources),
        "citations_registered": len(report.citations),
        "citations_verified": report.verified_citation_count,
        "contradictions_detected": len(report.contradictions),
        "adversarial_comparisons_detected": len(report.contradictions),
        "saved_report_path": saved_path,
        "output_error": output_error,
        "markdown_report": report.markdown_content,
        "core_findings_preview": report.core_findings[:3],
        "top_sources": [
            {
                "title": source.title,
                "url": source.url,
                "domain": source.domain,
                "facet": source.pass_type,
                "citation_status": source.citation_status,
            }
            for source in report.sources[:5]
        ],
    }

    return json.dumps(summary_payload, indent=2)
