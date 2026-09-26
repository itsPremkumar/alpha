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
    deadline_seconds: float = 300.0,
) -> str:
    """Conduct bounded multi-lane deep research with explicit evidence status.

    Compiles a 5-pass search plan (Discovery, Specific Evidence, Adversarial Contradiction,
    Fact Verification, and Strategic Synthesis), executes bounded multi-source extraction,
    and generates a Markdown evidence report. A report with no retrieved sources is returned
    as ``no_evidence`` rather than fabricated or labeled successful.

    A claim is given a page citation only when the page body was actually retrieved.
    A source whose body could not be read (fetch failure, or a body quarantined by the
    prompt-injection screen) is reported as ``snippet_only`` and its statements are
    labelled as search snippets, never as page evidence. Every failed lane, failed
    retrieval, quarantine and budget overrun is listed in ``failures`` and rendered into
    the report, and ``coverage`` is ``complete``, ``partial`` or ``none`` — a partial
    investigation is never presented as a finished one.

    Args:
        topic: The research question, technical subject, or investigation target.
        depth: Investigation depth from 1 (broad landscape) to 5 (broader targeted gap follow-up). Default 3.
        max_sources: Maximum distinct primary and secondary sources to extract and cite. Default 15.
        include_adversarial: Actively execute falsification queries to uncover bottlenecks, risks, and failure modes. Default True.
        output_path: Markdown filename to save inside the current thread outputs directory. Default 'deep_research_report.md'.
        output_filename: Optional legacy alias for output_path. Both accept a filename only, never a host path.
        deadline_seconds: Wall-clock budget for the whole run (1-1800). On expiry the report covers only what was retrieved. Default 300.

    ``depth`` (1-5), ``max_sources`` (1-30) and ``deadline_seconds`` (1-1800) are clamped to those
    ceilings; any clamp is reported in ``bounds`` and in the report's coverage section.
    """
    engine = DeepResearchEngine()

    report = await engine.run_research(
        topic=topic,
        depth=depth,
        max_sources=max_sources,
        include_adversarial=include_adversarial,
        deadline_seconds=deadline_seconds,
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
        "coverage": report.coverage,
        "topic": report.topic,
        "executive_summary": report.executive_summary,
        "sources_analyzed": len(report.sources),
        "sources_read": report.page_backed_source_count,
        "sources_snippet_only": report.snippet_only_source_count,
        "citations_registered": len(report.citations),
        "citations_verified": report.verified_citation_count,
        "contradictions_detected": len(report.contradictions),
        "conflicts_detected": len(report.conflicts),
        "adversarial_comparisons_detected": len(report.contradictions),
        "saved_report_path": saved_path,
        "output_error": output_error,
        "markdown_report": report.markdown_content,
        "core_findings_preview": report.core_findings[:3],
        # A partial investigation must be readable as partial from the payload
        # alone, without opening the Markdown: every lane, retrieval, quarantine
        # and budget failure is named here with its stage, target and error type.
        "failures": [failure.to_dict() for failure in report.failures],
        "bounds": report.bounds,
        "top_sources": [
            {
                "title": source.title,
                "url": source.url,
                "domain": source.domain,
                "facet": source.pass_type,
                "citation_status": source.citation_status,
                "retrieval": source.retrieval,
                "retrieval_note": source.retrieval_note,
                "injection_risk": source.injection_risk,
                "injection_signals": source.injection_signals,
                "unsupported_findings": source.unsupported_findings,
                "content_truncated": source.content_truncated,
            }
            for source in report.sources[:5]
        ],
    }

    return json.dumps(summary_payload, indent=2)
